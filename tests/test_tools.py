"""The tools on the real snapshot: what they give, how fast, how compact, and how they fail."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest
from slimconfig import build_output, plain_output

from contract import AlertInput, CitiesInput, DailyInput, HourlyInput, ReportInput
from plugin.alerts import get_daily, get_hourly, list_cities, pin_alerts, report_alerts
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
    assert {row.city: row.hours for row in listed.cities if row.hours != 24} == {"Santiago": 23}
    assert next(row for row in listed.cities if row.city == "Auckland").timezone == "Pacific/Auckland"
    later = list_cities(build_output({"date": "2026-09-27"}, CitiesInput), tmp_path)
    assert {row.city: row.hours for row in later.cities if row.hours != 24} == {"Auckland": 23}
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


@pytest.mark.parametrize(
    ("cities", "error"),
    [
        (NAMES[:11], "1 to 10"),
        ([], "1 to 10"),
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


def test_report_alerts_writes_and_replaces_the_report(tmp_path: Path) -> None:
    first = build_output({"date": DATE, "alerts": [{"city": "Cairo", "values": {"max_temp": 98.8}, "matched": ["max_temp >= 95"]}], "unavailable": []}, ReportInput)
    written = report_alerts(first, tmp_path)
    assert written.path == f"weather-alert/{DATE}.json" and (written.alerts, written.unavailable) == (1, 0)
    assert json.loads((tmp_path / "weather-alert" / f"{DATE}.json").read_text()) == plain_output(first)
    second = build_output({"date": DATE, "alerts": [], "unavailable": [{"city": "Lima", "variables": ["rain"]}]}, ReportInput)
    report_alerts(second, tmp_path)
    assert json.loads((tmp_path / "weather-alert" / f"{DATE}.json").read_text()) == plain_output(second)
    doubled = build_output({"date": DATE, "alerts": [{"city": "Cairo", "values": {}, "matched": []}], "unavailable": [{"city": "Cairo", "variables": ["rain"]}]}, ReportInput)
    with pytest.raises(ValueError, match="once"):
        report_alerts(doubled, tmp_path)
    with pytest.raises(ValueError, match="outside the snapshot"):
        report_alerts(build_output({"date": "2027-01-01", "alerts": [], "unavailable": []}, ReportInput), tmp_path)
