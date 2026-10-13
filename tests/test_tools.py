"""The tools on the real snapshot: what they give, how fast, how compact, and how they fail."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest
from slimconfig import build_output, plain_output

from contract import AlertInput, CitiesInput, DailyInput, HourlyInput, ReportInput
from plugin.alerts import RUN, get_daily, get_hourly, list_cities, pin_alerts, report_alerts
from plugin.data import snapshot
from plugin.rules import aggregate

DATE = "2026-09-06"
ARGUMENTS = {"date": DATE, "match": "any", "temperature_unit": "F", "wind_unit": "mph", "rain_unit": "in", "conditions": ["max_temp >= 95", "rain > 0.5"]}
NAMES = [city.name for city in snapshot().cities]


def test_pin_alerts_pins_every_city_fast(tmp_path: Path) -> None:
    started = time.perf_counter()
    pinned = pin_alerts(build_output(ARGUMENTS, AlertInput), tmp_path)
    assert time.perf_counter() - started < 5
    assert pinned.date == DATE and [row.city for row in pinned.metric] == NAMES
    santiago = next(row for row in pinned.metric if row.city == "Santiago")
    assert santiago.hours == 23 and santiago.humidity_hours == 23 and santiago.missing == []
    assert all(row.hours == 24 for row in pinned.metric if row.city != "Santiago")
    alerted = {alert.city for alert in pinned.alerts}
    for alert in pinned.alerts:
        assert set(alert.values) == {"max_temp", "rain"} and alert.matched
    assert pinned.unavailable == []
    hot = {row.city for row in pinned.metric if Fraction(Decimal(str(row.max_temp_c))) * 9 / 5 + 32 >= 95}
    wet = {row.city for row in pinned.metric if Fraction(Decimal(str(row.rain_mm))) / Fraction("25.4") > Fraction("0.5")}
    assert alerted == hot | wet


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"date": "2026-10-01"}, "outside the snapshot"),
        ({"date": "2026-09-31"}, "not a calendar day"),
        ({"conditions": ["max_temp >= 95", "max_temp >= 95.0"]}, "given twice"),
        ({"conditions": ["hot > 1"]}, "not a condition"),
        ({"conditions": []}, "1 to 6"),
    ],
)
def test_pin_alerts_refuses_bad_input(tmp_path: Path, change: dict, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        pin_alerts(build_output({**ARGUMENTS, **change}, AlertInput), tmp_path)


def test_list_cities_gives_every_city_with_its_day_length(tmp_path: Path) -> None:
    listed = list_cities(build_output({"date": DATE}, CitiesInput), tmp_path)
    assert [row.city for row in listed.cities] == NAMES
    assert {row.city: row.hours_available for row in listed.cities if row.hours_available != 24} == {"Santiago": 23}
    assert next(row for row in listed.cities if row.city == "Auckland").timezone == "Pacific/Auckland"
    later = list_cities(build_output({"date": "2026-09-27"}, CitiesInput), tmp_path)
    assert {row.city: row.hours_available for row in later.cities if row.hours_available != 24} == {"Auckland": 23}
    assert len(json.dumps(plain_output(listed))) < 3500
    with pytest.raises(ValueError, match="outside the snapshot"):
        list_cities(build_output({"date": "2026-06-30"}, CitiesInput), tmp_path)


def test_get_daily_gives_metric_aggregates_for_the_requested_cities(tmp_path: Path) -> None:
    found = get_daily(build_output({"cities": NAMES[:10], "date": DATE}, DailyInput), tmp_path)
    assert [row.city for row in found.cities] == NAMES[:10]
    pinned = {row.city: row for row in pin_alerts(build_output(ARGUMENTS, AlertInput), tmp_path).metric}
    for row in found.cities:
        pin = pinned[row.city]
        assert (row.max_temp_c, row.min_temp_c, row.rain_mm, row.max_wind_kmh, row.hours, row.missing) == (
            pin.max_temp_c, pin.min_temp_c, pin.rain_mm, pin.max_wind_kmh, pin.hours, pin.missing
        )
        mean = Fraction(int(pin.humidity_sum_pct), pin.humidity_hours)
        assert abs(Fraction(str(row.mean_humidity_pct)) - mean) <= Fraction(1, 20000)
    assert len(json.dumps(plain_output(found))) < 2500
    assert next(row for row in found.cities if row.city == "Athens").mean_humidity_pct == 31.4583  # 755/24
    everything = get_daily(build_output({"cities": NAMES, "date": DATE}, DailyInput), tmp_path)
    assert [row.city for row in everything.cities] == NAMES
    assert len(json.dumps(plain_output(everything))) < 7000  # about 1,600 tokens for the whole day


@pytest.mark.parametrize(
    ("cities", "error"),
    [
        (NAMES + ["Atlantis"], "1 to 30"),
        ([], "1 to 30"),
        (["Athens", "Athens"], "distinct"),
        (["Athens", "Atlantis"], "unknown city Atlantis"),
        (["athens"], "unknown city athens"),
    ],
)
def test_get_daily_refuses_bad_city_lists(tmp_path: Path, cities: list[str], error: str) -> None:
    with pytest.raises(ValueError, match=error):
        get_daily(build_output({"cities": cities, "date": DATE}, DailyInput), tmp_path)


def test_get_hourly_gives_the_local_day_with_its_offsets(tmp_path: Path) -> None:
    hours = get_hourly(build_output({"city": "Santiago", "date": DATE}, HourlyInput), tmp_path)
    assert hours.timezone == "America/Santiago" and len(hours.rows) == 23
    assert hours.rows[0].time == "2026-09-06T01:00-03:00" and hours.rows[-1].time == "2026-09-06T23:00-03:00"
    assert all(row.temp_c is not None and row.humidity_pct is not None for row in hours.rows)
    assert len(json.dumps(plain_output(hours))) < 3500
    auckland = get_hourly(build_output({"city": "Auckland", "date": "2026-09-27"}, HourlyInput), tmp_path)
    assert [row.time[11:] for row in auckland.rows][:3] == ["00:00+12:00", "01:00+12:00", "03:00+13:00"]
    with pytest.raises(ValueError, match="unknown city"):
        get_hourly(build_output({"city": "Nowhere", "date": DATE}, HourlyInput), tmp_path)


def test_the_local_day_is_not_the_utc_day(tmp_path: Path) -> None:
    """Santiago's 23-hour local day aggregates differently from the 24 UTC hours of the same date."""
    rows = []
    for date in ("2026-09-05", "2026-09-06", "2026-09-07"):
        for row in get_hourly(build_output({"city": "Santiago", "date": date}, HourlyInput), tmp_path).rows:
            if datetime.fromisoformat(row.time).astimezone(UTC).strftime("%Y-%m-%d") == DATE:
                rows.append({"temperature_2m": Decimal(str(row.temp_c)), "relative_humidity_2m": Decimal(str(row.humidity_pct)), "precipitation": Decimal(str(row.rain_mm)), "wind_speed_10m": Decimal(str(row.wind_kmh))})
    utc_day = aggregate("Santiago", rows)
    local = get_daily(build_output({"cities": ["Santiago"], "date": DATE}, DailyInput), tmp_path).cities[0]
    assert utc_day.hours == 24 and local.hours == 23
    utc_mean = Fraction(utc_day.humidity_sum_pct) / utc_day.humidity_hours
    assert utc_mean != Fraction(str(local.mean_humidity_pct)) and utc_day.humidity_hours == 24
    assert round(float(utc_mean), 1) != local.mean_humidity_pct


def report(date, examined, alerts=(), unavailable=()):
    return build_output({"date": date, "examined": list(examined), "alerts": list(alerts), "unavailable": list(unavailable)}, ReportInput)


def saved(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "weather-alert" / f"{DATE}.json").read_text())


def test_report_alerts_merges_each_call_by_city_and_counts_its_own_entries(tmp_path: Path) -> None:
    cairo = {"city": "Cairo", "values": {"max_temp": 98.0}, "matched": ["max_temp >= 95"]}
    first = report_alerts(report(DATE, NAMES[:10], alerts=[cairo]), tmp_path)
    assert first.path == f"weather-alert/{DATE}.json" and (first.alerts, first.unavailable, first.examined) == (1, 0, 10)
    marker = json.loads((tmp_path / "weather-alert" / "run.json").read_text())
    assert marker["run"] == RUN
    assert saved(tmp_path) == {"date": DATE, "run": RUN, "examined": NAMES[:10], "alerts": [cairo], "unavailable": []}
    lima = {"city": "Lima", "variables": ["rain"]}
    second = report_alerts(report(DATE, NAMES[10:20], unavailable=[lima]), tmp_path)
    assert (second.alerts, second.unavailable, second.examined) == (0, 1, 10)  # this call's entries, not totals
    # a later call restates the cities it examines: Cairo's values replaced, nothing else touched
    fixed = {**cairo, "values": {"max_temp": 98.8}}
    third = report_alerts(report(DATE, NAMES[20:30] + ["Cairo"], alerts=[fixed]), tmp_path)
    assert (third.alerts, third.unavailable, third.examined) == (1, 0, 11)
    assert saved(tmp_path) == {"date": DATE, "run": RUN, "examined": NAMES, "alerts": [fixed], "unavailable": [lima]}
    # an examined city with no entry has no alert: Cairo's goes; Lima turns from unavailable into an alert
    lima_alert = {"city": "Lima", "values": {"max_temp": 70.0}, "matched": []}
    fourth = report_alerts(report(DATE, ["Cairo", "Lima"], alerts=[lima_alert]), tmp_path)
    assert (fourth.alerts, fourth.unavailable, fourth.examined) == (1, 0, 2)
    assert saved(tmp_path)["alerts"] == [lima_alert] and saved(tmp_path)["unavailable"] == []
    assert sorted(path.name for path in (tmp_path / "weather-alert").iterdir()) == [f"{DATE}.json", "run.json"]


@pytest.mark.parametrize(
    "leftover",
    [
        "not json at all",
        json.dumps({"date": DATE, "alerts": [{"city": "Lima", "values": {}, "matched": []}], "unavailable": []}),  # an older version's shape
        json.dumps({"date": DATE, "run": "deadbeefdeadbeef", "examined": NAMES, "alerts": [{"city": "Lima", "values": {}, "matched": []}], "unavailable": []}),  # another run's
        json.dumps({"date": "2026-07-01", "run": RUN, "examined": NAMES, "alerts": [], "unavailable": []}),  # another date's content
    ],
)
def test_the_first_call_of_a_run_replaces_a_leftover_file(tmp_path: Path, leftover: str) -> None:
    (tmp_path / "weather-alert").mkdir()
    (tmp_path / "weather-alert" / f"{DATE}.json").write_text(leftover)
    (tmp_path / "weather-alert" / "run.json").write_text(json.dumps({"run": "deadbeefdeadbeef"}))
    out = report_alerts(report(DATE, ["Athens"]), tmp_path)
    assert (out.alerts, out.unavailable, out.examined) == (0, 0, 1)
    assert saved(tmp_path) == {"date": DATE, "run": RUN, "examined": ["Athens"], "alerts": [], "unavailable": []}
    assert json.loads((tmp_path / "weather-alert" / "run.json").read_text())["run"] == RUN


@pytest.mark.parametrize(
    ("examined", "alerts", "unavailable", "error"),
    [
        ([], [], [], "1 to 30"),
        (NAMES + ["Atlantis"], [], [], "1 to 30"),
        (["Athens", "Athens"], [], [], "1 to 30"),
        (["Atlantis"], [], [], "unknown city Atlantis"),
        (["Athens"], [{"city": "Cairo", "values": {}, "matched": []}], [], "must be listed in examined"),
        (["Athens", "Cairo"], [{"city": "Cairo", "values": {}, "matched": []}], [{"city": "Cairo", "variables": ["rain"]}], "once"),
    ],
)
def test_report_alerts_refuses_bad_calls(tmp_path: Path, examined, alerts, unavailable, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        report_alerts(report(DATE, examined, alerts, unavailable), tmp_path)
    with pytest.raises(ValueError, match="outside the snapshot"):
        report_alerts(report("2027-01-01", ["Athens"]), tmp_path)


def test_concurrent_calls_merge_without_losing_entries(tmp_path: Path) -> None:
    """Several report_alerts calls of one turn run at once: every call's cities land in the file."""
    chunks = [NAMES[start : start + 5] for start in range(0, 30, 5)]
    calls = [report(DATE, chunk, alerts=[{"city": chunk[0], "values": {"max_temp": 90.0}, "matched": []}]) for chunk in chunks]
    with ThreadPoolExecutor(max_workers=6) as pool:
        outputs = list(pool.map(lambda call: report_alerts(call, tmp_path), calls))
    assert all((out.alerts, out.examined) == (1, 5) for out in outputs)
    final = saved(tmp_path)
    assert final["examined"] == NAMES and sorted(a["city"] for a in final["alerts"]) == sorted(chunk[0] for chunk in chunks)
