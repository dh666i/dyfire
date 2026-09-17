from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from spark_console.schedule_slots import (
    SHANGHAI,
    next_runs,
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
