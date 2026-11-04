"""Rolling ERCOT telemetry counts may reset; lost coverage may not."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from wattdocket.production import pipeline
from wattdocket.production.sources_grid import ERCOT_URL, GridSourceError, collect_ercot, parse_ercot
from wattdocket.production.storage import LocalStore, digest, json_bytes

UTC = timezone.utc
CHICAGO = ZoneInfo("America/Chicago")
NOW = datetime(2026, 11, 4, 22, 7, tzinfo=UTC)


def payload_at(published="2026-11-04T16:06:00-06:00"):
    published = datetime.fromisoformat(published)
    midnight = published.astimezone(CHICAGO).replace(hour=0, minute=0, second=0, microsecond=0)
    fuel = {key: {"gen": value} for key, value in {
        "Coal and Lignite": 10, "Natural Gas": 20, "Nuclear": 30,
        "Wind": 10, "Solar": 0, "Hydro": 0, "Other": 0, "Power Storage": -12,
    }.items()}
    data = {}
    cursor = (midnight - timedelta(days=1)).astimezone(UTC)
    while cursor <= published:
        local = cursor.astimezone(CHICAGO)
        data.setdefault(local.date().isoformat(), {})[local.strftime("%Y-%m-%d %H:%M:%S%z")] = deepcopy(fuel)
        cursor += timedelta(minutes=5)
    return {"lastUpdated": published.strftime("%Y-%m-%d %H:%M:%S%z"), "data": data}


class PayloadFetcher:
    def __init__(self, payload):
        self.payload = payload
        self.receipts = {}

    def get_json(self, url):
        assert url == ERCOT_URL
        return self.payload


def collect(payload, now=NOW):
    return collect_ercot(PayloadFetcher(payload), now=now)


def test_healthy_operating_day_rollover_explains_575_to_482_rows():
    previous = collect(payload_at("2026-11-03T23:51:00-06:00"), datetime(2026, 11, 4, 5, 52, tzinfo=UTC))
    current = collect(payload_at())
    assert previous.record_count == 575
    assert current.record_count == 482
    validation = current.data["validation"]
    assert validation["rolling_window_count_change_safe"] is True
    assert validation["record_count_semantics"] == "rolling_operating_days"
    assert [day["complete_fraction"] for day in validation["daily_coverage"]] == [1, 1]
    assert current.observation_end == "2026-11-04T22:05:00Z"
    assert current.data["regions"][0]["readings"][-1]["generation_mw"] == 70
    assert all(row["intensity_lb_co2e_per_mwh"] is None for row in current.data["regions"][0]["readings"])


@pytest.mark.parametrize("published,expected_previous", [
    ("2026-03-09T00:06:00-05:00", 276),
    ("2026-11-02T00:06:00-06:00", 300),
])
def test_rolling_coverage_honors_short_and_long_dst_days(published, expected_previous):
    now = datetime.fromisoformat(published) + timedelta(minutes=1)
    result = collect(payload_at(published), now)
    assert result.record_count == expected_previous + 2
    assert result.data["validation"]["rolling_window_count_change_safe"] is True
    assert result.data["regions"][0]["days"][0]["expected_intervals"] == expected_previous
    stamps = [row["interval_start_utc"] for row in result.data["regions"][0]["readings"]]
    assert len(stamps) == len(set(stamps))


@pytest.mark.parametrize("damage", ["missing_previous", "missing_current", "null_fuels", "one_day", "stale", "lagging"])
def test_partial_or_stale_telemetry_cannot_explain_a_count_drop(damage):
    payload = payload_at()
    now = NOW
    if damage in {"missing_previous", "missing_current", "null_fuels"}:
        day = payload["data"]["2026-11-03" if damage == "missing_previous" else "2026-11-04"]
        for key in list(day)[:20]:
            if damage == "null_fuels":
                day[key]["Natural Gas"]["gen"] = None
            else:
                del day[key]
    elif damage == "one_day":
        del payload["data"]["2026-11-03"]
    elif damage == "stale":
        now += timedelta(hours=3)
    else:
        day = payload["data"]["2026-11-04"]
        for key in list(day)[-4:]:
            del day[key]
    result = collect(payload, now)
    assert result.data["validation"]["rolling_window_count_change_safe"] is False
    if damage == "null_fuels":
        assert result.data["regions"][0]["readings"][-194]["generation_mw"] is None


@pytest.mark.parametrize("damage,error", [
    ("third_day", "operating-day window"),
    ("outside_day", "operating-day window"),
    ("duplicate_utc", "duplicate ERCOT timestamp"),
    ("wrong_offset", "invalid local UTC offset"),
    ("overfull_day", "interval capacity"),
])
def test_malformed_window_refuses_before_count_validation(damage, error):
    payload = payload_at()
    current = payload["data"]["2026-11-04"]
    first = next(iter(current))
    if damage == "third_day":
        payload["data"]["2026-11-02"] = {"2026-11-02 00:00:00-0600": deepcopy(current[first])}
    elif damage == "outside_day":
        payload["data"]["2026-11-02"] = payload["data"].pop("2026-11-03")
    elif damage == "duplicate_utc":
        current[first.replace(" ", "T")] = deepcopy(current[first])
    elif damage == "wrong_offset":
        current["2026-11-04 12:00:00-0500"] = deepcopy(current[first])
    else:
        previous = payload["data"]["2026-11-03"]
        key = next(iter(previous))
        previous[key.replace(" ", "T")] = deepcopy(previous[key])
    with pytest.raises(GridSourceError, match=error):
        parse_ercot(payload, now=NOW)


@pytest.mark.parametrize("validation", ["qualified", "false", "missing", "wrong_semantics"])
def test_pipeline_count_exemption_requires_ercot_validation(tmp_path, validation):
    store = LocalStore(tmp_path / "archive")
    old_result = collect(payload_at("2026-11-03T23:51:00-06:00"), datetime(2026, 11, 4, 5, 52, tzinfo=UTC)).as_dict()
    previous = {"id": "ercot", "record_count": 575, "result": old_result,
                "result_sha256": digest(json_bytes(old_result)), "last_success_at": "2026-11-04T05:52:00Z",
                "retrieved_at": "2026-11-04T05:52:00Z", "observation_end": old_result["observation_end"]}
    store.put("state/sources/ercot.json", json_bytes(previous))
    current = collect(payload_at())
    if validation == "false":
        current.data["validation"]["rolling_window_count_change_safe"] = False
    elif validation == "missing":
        del current.data["validation"]
    elif validation == "wrong_semantics":
        current.data["validation"]["record_count_semantics"] = "inventory"
    assert pipeline.POLICIES["ercot"]["allow_rolling_window_count"] is True
    state = pipeline.collect_one("ercot", lambda fetcher, **kwargs: current, store, NOW,
                                 force=True, fetcher_factory=lambda *args, **kwargs: PayloadFetcher({}))
    if validation == "qualified":
        assert state["record_count"] == 482
        assert state["error"] is None
        assert state["status"] == "current"
        assert state["result"] == current.as_dict()
    else:
        assert state["record_count"] == 575
        assert "Unexplained inventory drop" in state["error"]
        assert state["result"] == old_result
        assert state["result_sha256"] == previous["result_sha256"]
        assert state["last_success_at"] == previous["last_success_at"]
