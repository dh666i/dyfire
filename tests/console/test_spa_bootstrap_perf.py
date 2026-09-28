from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event

from spark_console.config import Settings
from spark_console.db import create_schema, session_scope
from spark_console.models import (
    SparkTask,
    SparkTaskTargetIdentity,
    TaskRun,
    User,
)
from spark_console.security import PasswordService, SessionService
from spark_console.web.app import create_app


def test_app_bootstrap_aggregates_local_days_and_batches_task_identities(tmp_path):
    cookie_key = tmp_path / "cookie.key"
    cookie_key.write_bytes(b"c" * 32)
    session_key = tmp_path / "session.key"
    session_key.write_bytes(b"s" * 32)
    settings = Settings(tmp_path, f"sqlite:///{tmp_path}/test.db", cookie_key, session_key)
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    create_schema(engine)
    shanghai = ZoneInfo("Asia/Shanghai")
    # Use yesterday so a test running across Shanghai midnight remains stable.
    activity_day = datetime.now(shanghai).date() - timedelta(days=1)
    midnight = datetime.combine(activity_day, time.min, tzinfo=shanghai)

    with session_scope(engine) as db:
        owner = User(
            username="bootstrap-user",
            password_hash=PasswordService().hash("test-only"),
            role="user",
            must_change_password=False,
        )
        outsider = User(
            username="bootstrap-outsider",
            password_hash="unused",
            role="user",
            must_change_password=False,
        )
        db.add_all([owner, outsider])
        db.flush()
        tasks = [
            SparkTask(
                owner_user_id=owner.id,
                target_name=f"Friend {index}",
                send_time="09:30",
                message_template="test",
            )
            for index in range(3)
        ]
        foreign_task = SparkTask(
            owner_user_id=outsider.id,
            target_name="Private friend",
            send_time="09:30",
            message_template="test",
        )
        db.add_all([*tasks, foreign_task])
        db.flush()
        db.add_all(
            SparkTaskTargetIdentity(task_id=task.id, sec_uid=f"sec-{index}")
            for index, task in enumerate(tasks)
        )
        db.add_all(
            [
                # This instant is still the prior UTC date, but is Shanghai today.
                TaskRun(
                    task_id=tasks[0].id,
                    scheduled_for=(midnight + timedelta(minutes=5)).astimezone(timezone.utc),
                    status="success",
                ),
                # Failed runs remain absent from the success activity count.
                TaskRun(
                    task_id=tasks[1].id,
                    scheduled_for=(midnight + timedelta(hours=1)).astimezone(timezone.utc),
                    status="failed",
                ),
                # Just before Shanghai midnight belongs to the preceding day.
                TaskRun(
                    task_id=tasks[2].id,
                    scheduled_for=(midnight - timedelta(seconds=1)).astimezone(timezone.utc),
                    status="success",
                ),
                # Owner scoping must also apply to the SQL aggregate.
                TaskRun(
                    task_id=foreign_task.id,
                    scheduled_for=(midnight + timedelta(minutes=10)).astimezone(timezone.utc),
                    status="success",
                ),
            ]
        )
        token, record = SessionService(session_key.read_bytes()).create_record(owner.id)
        db.add(record)

    identity_selects = []

    def count_identity_selects(_connection, _cursor, statement, _parameters, _context, _many):
        sql = statement.lower()
        if "from spark_task_target_identities" in sql and "where" in sql:
            identity_selects.append(sql)

    event.listen(engine, "before_cursor_execute", count_identity_selects)
    try:
        with TestClient(create_app(settings, engine), base_url="https://testserver") as client:
            client.cookies.set("spark_session", token)
            response = client.get("/api/app/bootstrap")
    finally:
        event.remove(engine, "before_cursor_execute", count_identity_selects)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert [task["sec_uid"] for task in payload["tasks"]] == ["sec-0", "sec-1", "sec-2"]
    activity = {item["date"]: item["count"] for item in payload["activity"]["days"]}
    assert activity[activity_day.isoformat()] == 1
    assert activity[(activity_day - timedelta(days=1)).isoformat()] == 1
    assert payload["activity"]["total"] == 2
    # One batched identity lookup plus task_avatar_map's bound-id query; this
    # count stays constant as task count grows, instead of issuing db.get per task.
    assert len(identity_selects) <= 2
    engine.dispose()
