"""Relationship observations must not invent streaks or leak other accounts."""
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, inspect, select, text

from spark_console.config import Settings
from spark_console.crypto import CookieCipher
from spark_console.db import create_engine_for, create_schema, session_scope
from spark_console.models import (
    DouyinAccount, DouyinContactIdentity, DouyinStreakMilestone,
    DouyinStreakObservation, DouyinStreakSnapshot, SparkTask,
    SparkTaskTargetIdentity, TaskRun, User, utc_now,
)
from spark_console.retention import RetentionPolicy, retain
from spark_console.security import SessionService
from spark_console.services.accounts import AccountService
from spark_console.services.audits import AuditService
from spark_console.services.streaks import (
    contact_snapshot_map, list_streaks, record_observations, streak_history,
    task_snapshot_map,
)
from spark_console.web.auth import WebAuth
from spark_console.web.streak_routes import build_streak_router


@pytest.fixture
def fixture(tmp_path):
    settings = Settings(tmp_path, f'sqlite:///{tmp_path / "streak.db"}', tmp_path / "cookie", tmp_path / "session")
    engine = create_engine_for(settings)
    create_schema(engine)
    sessions = SessionService(b"s" * 32)
    app = FastAPI()
    app.include_router(build_streak_router(engine, WebAuth(sessions)))
    ids = {}
    with session_scope(engine) as db:
        for name, role in (("owner", "user"), ("other", "user"), ("admin", "admin")):
            user = User(username=name, password_hash="unused", role=role, must_change_password=False)
            db.add(user)
            db.flush()
            token, session = sessions.create_record(user.id)
            db.add(session)
            ids[name] = user.id
            ids[f"{name}_token"] = token
            account = DouyinAccount(owner_user_id=user.id, display_name=name, encrypted_cookies=b"cipher", cookie_nonce=b"nonce", cookie_version=1)
            db.add(account)
            db.flush()
            ids[f"{name}_account"] = account.id
    with TestClient(app, base_url="https://testserver") as client:
        client.cookies.set("spark_session", ids["owner_token"])
        yield engine, client, ids
    engine.dispose()


def observation(uid="friend", days=99, state="active", **extra):
    return {"sec_uid": uid, "days": days, "state": state, "source": "douyin_dom", "platform_text": f"{days}天", **extra}


def test_missing_unknown_stale_and_malformed_reads_do_not_erase_observed_value(fixture):
    engine, _, ids = fixture
    stamp = utc_now() - timedelta(hours=2)
    with session_scope(engine) as db:
        assert record_observations(db, ids["owner_account"], [observation()], observed_at=stamp) == 1
        assert record_observations(db, ids["owner_account"], [], observed_at=stamp + timedelta(minutes=1)) == 0
        bad = [observation(days=None, state="unknown"), observation(days=100, state="unknown"), observation(sec_uid=""),
               observation(source=[]), observation(state={}), observation(uid="bool", days=True, state="unknown"),
               observation(observed_at=utc_now() + timedelta(days=1))]
        assert record_observations(db, ids["owner_account"], bad, observed_at=stamp + timedelta(minutes=2)) == 0
        assert record_observations(db, ids["owner_account"], [observation(days=88)], observed_at=stamp - timedelta(minutes=1)) == 0
        value = contact_snapshot_map(db, ids["owner_account"], ["friend", "unseen"])
        assert value["friend"]["days"] == 99
        assert value["friend"]["observed_at"] == stamp.isoformat()
        assert value["unseen"]["days"] is None and value["unseen"]["state"] == "unknown"
        assert db.scalar(select(func.count()).select_from(DouyinStreakObservation)) == 1
        assert db.scalar(select(func.count()).select_from(TaskRun)) == 0


def test_unchanged_read_updates_freshness_without_duplicate_history(fixture):
    engine, _, ids = fixture
    old = utc_now() - timedelta(days=2)
    with session_scope(engine) as db:
        record_observations(db, ids["owner_account"], [observation()], observed_at=old)
        assert contact_snapshot_map(db, ids["owner_account"], ["friend"])["friend"]["stale"]
        record_observations(db, ids["owner_account"], [observation(platform_text="平台显示99天")])
        assert not contact_snapshot_map(db, ids["owner_account"], ["friend"])["friend"]["stale"]
        assert db.scalar(select(func.count()).select_from(DouyinStreakObservation)) == 1


def test_partial_observations_preserve_days_and_only_explicit_broken_state_changes_it(fixture):
    engine, _, ids = fixture
    old = utc_now() - timedelta(hours=1)
    with session_scope(engine) as db:
        record_observations(db, ids["owner_account"], [observation(days=120)], observed_at=old)
        record_observations(db, ids["owner_account"], [observation(days=None, state="recovering", recover_current=1, recover_total=3)], observed_at=old + timedelta(minutes=1))
        row = contact_snapshot_map(db, ids["owner_account"], ["friend"])["friend"]
        assert (row["days"], row["state"], row["recover_current"], row["recover_total"]) == (120, "recovering", 1, 3)
        record_observations(db, ids["owner_account"], [observation(days=None, state="broken")])
        row = contact_snapshot_map(db, ids["owner_account"], ["friend"])["friend"]
        assert row["state"] == "broken" and row["days"] == 120 and row["recover_current"] is None
        assert row["expires_at"] is None


def test_milestones_record_first_observation_and_crossing_without_fabricated_dates(fixture):
    engine, _, ids = fixture
    old = utc_now() - timedelta(days=5)
    with session_scope(engine) as db:
        record_observations(db, ids["owner_account"], [observation(days=519)], observed_at=old)
        record_observations(db, ids["owner_account"], [observation(days=521)], observed_at=old + timedelta(days=1))
        record_observations(db, ids["owner_account"], [observation(days=1, state="broken")], observed_at=old + timedelta(days=2))
        record_observations(db, ids["owner_account"], [observation(days=1001)], observed_at=old + timedelta(days=3))
        view = streak_history(db, db.get(User, ids["owner"]), account_id=ids["owner_account"], sec_uid="friend")
        assert [(m["days"], m["kind"]) for m in view["milestones"]] == [(100, "first_observed"), (365, "first_observed"), (520, "crossed"), (1000, "crossed")]
        assert view["milestones"][0]["observed_at"] == old.isoformat()
        assert view["milestones"][2]["observed_at"] == (old + timedelta(days=1)).isoformat()
        assert [row["days"] for row in view["items"]] == [1001, 1, 521, 519]
        assert view["current"]["days"] == 1001


def add_task(db, owner, account, name="同名好友", uid=None):
    task = SparkTask(owner_user_id=owner, douyin_account_id=account, target_name=name,
                     send_time="09:30", message_template="hello", enabled=False)
    db.add(task)
    db.flush()
    if uid:
        db.add(SparkTaskTargetIdentity(task_id=task.id, sec_uid=uid))
        db.flush()
    return task


def test_task_map_uses_account_and_stable_uid_never_display_name(fixture):
    engine, _, ids = fixture
    with session_scope(engine) as db:
        record_observations(db, ids["owner_account"], [observation(days=100)])
        record_observations(db, ids["other_account"], [observation(days=520)])
        tasks = [add_task(db, ids["owner"], ids["owner_account"], uid="friend"),
                 add_task(db, ids["owner"], ids["owner_account"]),
                 add_task(db, ids["other"], ids["other_account"], uid="friend")]
        views = task_snapshot_map(db, tasks)
        assert [views[task.id]["days"] for task in tasks] == [100, None, 520]


def test_api_enforces_login_ownership_admin_and_bounded_parameters(fixture):
    engine, client, ids = fixture
    with session_scope(engine) as db:
        record_observations(db, ids["owner_account"], [observation()])
        record_observations(db, ids["other_account"], [observation(days=520)])
    response = client.get("/api/streaks")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["total"] == 1
    assert client.get("/api/streaks", params={"account_id": ids["other_account"]}).status_code == 404
    assert client.get("/api/streaks/history", params={"account_id": ids["other_account"], "sec_uid": "friend"}).status_code == 404
    for params in ({"limit": 101}, {"offset": -1}, {"filter": "invented"}, {"order": "unknown"}):
        assert client.get("/api/streaks", params=params).status_code == 422
    client.cookies.clear()
    assert client.get("/api/streaks").status_code == 401
    client.cookies.set("spark_session", ids["admin_token"])
    assert client.get("/api/streaks").json()["total"] == 2
    assert client.get("/api/streaks", params={"account_id": ids["other_account"]}).json()["items"][0]["days"] == 520


def test_filters_sort_pagination_and_unknown_contacts(fixture):
    engine, client, ids = fixture
    with session_scope(engine) as db:
        db.add_all([DouyinContactIdentity(account_id=ids["owner_account"], sec_uid=f"u{i}", nickname=f"Friend {i}") for i in range(4)])
        record_observations(db, ids["owner_account"], [observation("u0", 120), observation("u1", 40, "broken"), observation("u2", 2, "recovering")])
        task = add_task(db, ids["owner"], ids["owner_account"], uid="u0")
        task_id = task.id
        legacy_id = add_task(db, ids["owner"], ids["owner_account"]).id
    data = client.get("/api/streaks", params={"limit": 2}).json()
    assert data["total"] == 4 and data["has_more"] and data["next_offset"] == 2
    assert [r["days"] for r in data["items"]] == [120, 40]
    assert data["metadata"]["may_have_more"] and data["metadata"]["identity_unresolved"]
    second = client.get("/api/streaks", params={"offset": 2, "limit": 2}).json()
    assert [r["days"] for r in second["items"]] == [2, None]
    assert second["items"][1]["state"] == "unknown" and not second["has_more"]
    for filter_name, total in (("active", 1), ("has_streak", 3), ("needs_attention", 2), ("without_task", 3)):
        assert client.get("/api/streaks", params={"filter": filter_name}).json()["total"] == total
    item = client.get("/api/streaks", params={"task_id": task_id}).json()["items"][0]
    assert item["sec_uid"] == "u0" and item["task_ids"] == [task_id]
    unresolved = client.get("/api/streaks", params={"task_id": legacy_id}).json()
    assert unresolved["items"] == [] and unresolved["metadata"]["identity_unresolved"]


def test_history_is_bounded_and_missing_snapshot_explicit_unknown(fixture):
    engine, client, ids = fixture
    old = utc_now() - timedelta(days=10)
    with session_scope(engine) as db:
        for i in range(5):
            record_observations(db, ids["owner_account"], [observation(days=i + 1)], observed_at=old + timedelta(days=i))
    params = {"account_id": ids["owner_account"], "sec_uid": "friend", "limit": 2}
    body = client.get("/api/streaks/history", params=params).json()
    assert body["total"] == 5 and body["has_more"] and len(body["items"]) == 2
    assert [r["days"] for r in body["items"]] == [5, 4]
    params["sec_uid"] = "missing"
    body = client.get("/api/streaks/history", params=params).json()
    assert body["current"]["state"] == "unknown" and body["current"]["days"] is None
    assert body["milestones"] == [] and body["items"] == []


def test_schema_upgrade_is_additive_idempotent_and_preserves_business_rows(fixture):
    engine, _, ids = fixture
    with engine.begin() as conn:
        for table in (DouyinStreakObservation.__table__, DouyinStreakMilestone.__table__, DouyinStreakSnapshot.__table__):
            table.drop(conn)
        conn.execute(text("DELETE FROM schema_migrations WHERE version=26"))
    create_schema(engine)
    create_schema(engine)
    assert {"douyin_streak_snapshots", "douyin_streak_observations", "douyin_streak_milestones"} <= set(inspect(engine).get_table_names())
    with session_scope(engine) as db:
        assert db.get(DouyinAccount, ids["owner_account"]).encrypted_cookies == b"cipher"
        assert db.scalar(text("SELECT COUNT(*) FROM schema_migrations WHERE version=26")) == 1


@pytest.mark.parametrize("deletion", ["account_service", "user_cascade"])
def test_account_and_user_removal_cascade_all_relationship_data(fixture, deletion):
    engine, _, ids = fixture
    with session_scope(engine) as db:
        record_observations(db, ids["owner_account"], [observation(days=520)])
        if deletion == "account_service":
            AccountService(db, CookieCipher(b"c" * 32), AuditService(db)).delete_owned(ids["owner"], ids["owner_account"])
        else:
            db.execute(delete(User).where(User.id == ids["owner"]))
    with session_scope(engine) as db:
        for model in (DouyinStreakSnapshot, DouyinStreakObservation, DouyinStreakMilestone):
            assert db.scalar(select(func.count()).select_from(model)) == 0


def test_retention_prunes_only_old_history_in_bounded_batches_preserving_snapshot_and_milestones(fixture):
    engine, _, ids = fixture
    now = utc_now()
    with session_scope(engine) as db:
        for i in range(3):
            record_observations(db, ids["owner_account"], [observation(days=100 + i)], observed_at=now - timedelta(days=370 - i))
        record_observations(db, ids["owner_account"], [observation(days=365)], observed_at=now)
        dry = retain(db, RetentionPolicy(batch_size=2), now=now)
        assert dry.categories["streak_observations"].candidates == 3
        assert db.scalar(select(func.count()).select_from(DouyinStreakObservation)) == 4
        applied = retain(db, RetentionPolicy(batch_size=2), dry_run=False, now=now)
        assert applied.categories["streak_observations"].deleted == 2
        assert db.scalar(select(func.count()).select_from(DouyinStreakSnapshot)) == 1
        assert db.scalar(select(func.count()).select_from(DouyinStreakMilestone)) == 2
        assert db.scalar(select(func.count()).select_from(DouyinStreakObservation)) == 2


def test_each_relationship_history_has_a_hard_size_cap(fixture, monkeypatch):
    from spark_console.services import streaks
    monkeypatch.setattr(streaks, "MAX_HISTORY_ROWS", 3)
    engine, _, ids = fixture
    old = utc_now() - timedelta(hours=1)
    with session_scope(engine) as db:
        for i in range(5):
            record_observations(db, ids["owner_account"], [observation(days=100 + i)], observed_at=old + timedelta(minutes=i))
        result = streak_history(db, db.get(User, ids["owner"]), account_id=ids["owner_account"], sec_uid="friend")
        assert result["total"] == 3
        assert [row["days"] for row in result["items"]] == [104, 103, 102]
        assert result["current"]["days"] == 104 and len(result["milestones"]) == 1
