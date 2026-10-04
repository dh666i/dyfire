from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from spark_console.schedule_slots import (
    SHANGHAI,
    next_runs,
    plan,
    stagger_from,
    suggest_windows,
    window_load,
)


def test_stagger_skips_occupied_minutes():
    assert stagger_from("09:30", 3, {"09:35"}) == ["09:30", "09:40", "09:45"]


def test_stagger_single_keeps_preferred():
    assert stagger_from("09:30", 1, set()) == ["09:30"]


def test_suggestions_prefer_empty_global_slots():
    counts = {"09:30": 8, "09:31": 8, "09:32": 8}
    now = datetime(2026, 9, 17, 16, 0, tzinfo=SHANGHAI)
    picked = suggest_windows(counts, set(), 3, "09:30", now=now, limit=3)
    assert picked
    assert picked[0]["start"] != "09:30"
    assert picked[0]["load"] == 0
    assert len(picked[0]["times"]) == 3


def test_suggestions_stay_in_daytime():
    now = datetime(2026, 9, 17, 10, 0, tzinfo=SHANGHAI)
    picked = suggest_windows({}, set(), 2, "09:30", now=now)
    assert picked
    for item in picked:
        hour = int(item["start"].split(":")[0])
        assert 8 <= hour <= 22


def test_next_runs_share_the_first_slot_calendar_day():
    now = datetime(2026, 9, 17, 1, 40, tzinfo=timezone.utc)  # 09:40 Shanghai
    runs = next_runs(["09:30", "09:31", "09:32"], now)
    days = {value.astimezone(SHANGHAI).date() for value in runs}
    assert days == {datetime(2026, 9, 18).date()}
    assert [value.astimezone(SHANGHAI).strftime("%H:%M") for value in runs] == [
        "09:30",
        "09:31",
        "09:32",
    ]


def test_default_gap_is_five_minutes():
    assert stagger_from("09:30", 3, set()) == ["09:30", "09:35", "09:40"]


def test_window_load_sums_global_counts():
    assert window_load(["09:30", "09:31"], {"09:30": 4, "09:31": 1}) == 5


def _plan(counts=None, occupied=None, count=1, preferred="09:30", mode="manual", now=None):
    with patch("spark_console.schedule_slots.enabled_counts", return_value=counts or {}), patch(
        "spark_console.schedule_slots.account_occupied", return_value=occupied or set()
    ):
        return plan(None, "account", count, preferred, mode=mode, now=now or datetime(2026, 10, 4, 9, 29, tzinfo=SHANGHAI))


def test_automatic_default_avoids_neighboring_load_and_has_five_minute_lead():
    body = _plan({"09:30": 4, "09:31": 4}, mode="auto")
    assert body["times"] == ["09:36"]
    assert body["nearby_load"] == 0
    assert body["entries"][0]["day_offset"] == 0
    assert datetime.fromisoformat(body["entries"][0]["next_run_local"]) >= datetime(2026, 10, 4, 9, 34, tzinfo=SHANGHAI)


def test_default_after_daytime_window_starts_tomorrow_and_reports_date():
    body = _plan(mode="auto", now=datetime(2026, 10, 4, 22, 4, tzinfo=SHANGHAI))
    assert body["times"] == ["08:00"]
    assert body["entries"][0]["day_offset"] == 1
    assert body["entries"][0]["next_run_local"].startswith("2026-10-05T08:00")


def test_manual_first_is_preserved_and_following_friends_avoid_busy_neighbors():
    body = _plan({"10:05": 20}, count=3, preferred="10:00")
    assert body["times"] == ["10:00", "10:10", "10:15"]
    assert len(body["entries"]) == 3
    assert body["gap_minutes"] == 5


def test_manual_skips_account_neighbors_not_only_identical_minutes():
    body = _plan(occupied={"10:03"}, count=2, preferred="10:00")
    assert body["times"] == ["10:08", "10:13"]


def test_midnight_batch_preserves_chronological_dates_and_unique_daily_times():
    body = _plan(count=3, preferred="23:58", now=datetime(2026, 10, 4, 23, 50, tzinfo=SHANGHAI))
    assert body["times"] == ["23:58", "00:03", "00:08"]
    assert [entry["day_offset"] for entry in body["entries"]] == [0, 1, 1]
    runs = [datetime.fromisoformat(entry["next_run_at"]) for entry in body["entries"]]
    assert all((later - earlier).total_seconds() == 300 for earlier, later in zip(runs, runs[1:]))


def test_hundred_friends_fit_without_duplicate_minutes_or_reduced_gap():
    body = _plan(count=100, mode="auto", now=datetime(2026, 10, 4, 7, 30, tzinfo=SHANGHAI))
    assert body["status"] == "ready"
    assert len(set(body["times"])) == 100
    runs = [datetime.fromisoformat(entry["next_run_at"]) for entry in body["entries"]]
    assert all((later - earlier).total_seconds() >= 300 for earlier, later in zip(runs, runs[1:]))


def test_full_account_returns_unavailable_without_duplicate_or_partial_plan():
    occupied = {f"{minute // 60:02d}:{minute % 60:02d}" for minute in range(0, 1440, 5)}
    body = _plan(occupied=occupied, count=3, mode="auto")
    assert body["status"] == "unavailable"
    assert body["times"] == [] and body["entries"] == []
    assert stagger_from("09:30", 100, occupied) == []


def test_spacing_applies_across_daily_midnight_boundary():
    body = _plan(occupied={"23:59"}, count=1, preferred="00:01")
    assert body["times"] == ["00:04"]


def test_server_setting_cannot_shorten_safe_account_gap(monkeypatch):
    monkeypatch.setenv("SPARK_TASK_STAGGER_MINUTES", "1")
    assert stagger_from("09:30", 2) == ["09:30", "09:35"]
