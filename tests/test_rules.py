"""The shared rules: the grammar, the calendar, the date policy at any clock (the date and clock
matrices), a day's aggregates, the canonical hash, and exact arithmetic."""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

import pytest

from plugin.rules import (
    CITY_NAMES,
    DataKind,
    Units,
    canonical_series,
    choice_problem,
    choose,
    civil_from_days,
    convert,
    date_problem,
    date_text,
    day_metrics,
    day_number,
    days_from_civil,
    evaluate,
    forecast_runs,
    holds,
    outlook_last_day,
    parse_condition,
    parse_conditions,
    round_half_up,
    series_problem,
    series_sha256,
    stamp_minutes,
    stamp_text,
    stamps_inside,
    truncate,
    weekday,
    windows_of,
)

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def minutes(text: str) -> int:
    instant = datetime.fromisoformat(text)
    return int((instant - EPOCH).total_seconds()) // 60


# ----- Grammar -----


@pytest.mark.parametrize("text", ["max_temp >= 95", "rain>0.5", " wind<=  20.25 ", "humidity < .5", "min_temp >= -10"])
def test_conditions_parse(text: str) -> None:
    assert parse_condition(text).text.split()[0] in ("max_temp", "rain", "wind", "humidity", "min_temp")


@pytest.mark.parametrize("text", ["max_temp => 95", "temp >= 9", "rain > 0.555", "wind >= ٢٠", "rain > 1e3", "max_temp >= 95 F", ""])
def test_malformed_conditions_are_refused(text: str) -> None:
    with pytest.raises(ValueError, match="is not a condition"):
        parse_condition(text)


def test_conditions_must_be_one_to_six_and_distinct() -> None:
    with pytest.raises(ValueError, match="1 to 6"):
        parse_conditions([])
    with pytest.raises(ValueError, match="1 to 6"):
        parse_conditions([f"wind >= {n}" for n in range(7)])
    with pytest.raises(ValueError, match="given twice"):
        parse_conditions(["rain > 0.5", "rain>0.50"])


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("2026-10-16", None),
        ("1940-01-01", None),
        ("9999-12-31", None),
        ("1939-12-31", "before 1940-01-01"),
        ("2026-13-01", "not a calendar day"),
        ("2026-02-29", "not a calendar day"),
        ("2024-02-29", None),
        ("2000-02-29", None),
        ("1900-02-29", "not a calendar day"),
        ("26-10-16", "YYYY-MM-DD"),
        ("2026/10/16", "YYYY-MM-DD"),
        ("2026-10-16T00:00", "YYYY-MM-DD"),
        (" 2026-10-16", "YYYY-MM-DD"),
        ("2026-10-16\n", "YYYY-MM-DD"),
        ("", "YYYY-MM-DD"),
        ("today", "YYYY-MM-DD"),
        ("٢٠٢٦-١٠-١٦", "YYYY-MM-DD"),
        ("0000-01-01", "before"),
        ("10000-01-01", "YYYY-MM-DD"),
    ],
)
def test_dates(text: str, problem: str | None) -> None:
    found = date_problem(text)
    assert (found is None) if problem is None else (problem in found)


# ----- The calendar -----


def test_the_day_number_matches_the_proleptic_calendar() -> None:
    randomly = random.Random(7)
    for _ in range(3000):
        day = date.fromordinal(randomly.randint(date(1, 1, 1).toordinal(), date(9999, 12, 31).toordinal()))
        number = days_from_civil(day.year, day.month, day.day)
        assert number == (day - date(1970, 1, 1)).days
        assert civil_from_days(number) == (day.year, day.month, day.day)
        assert weekday(day.isoformat()) == day.strftime("%A")


def test_stamps_round_trip() -> None:
    for text in ("1940-01-01T00:00", "2026-10-16T04:00", "2024-02-29T23:59"):
        assert stamp_text(stamp_minutes(text)) == text
        assert stamp_minutes(text + "Z") == stamp_minutes(text) == minutes(text + "+00:00")
    assert stamp_minutes("2026-10-16T24:00") is None and stamp_minutes("2026-10-16 04:00") is None


# ----- The date policy (the date matrix, at the recorded clock) -----

CLOCK = minutes("2026-10-16T04:00+00:00")  # today is 2026-10-16


@pytest.mark.parametrize(
    ("text", "kind", "model", "runs"),
    [
        ("1940-01-01", "archive", "era5", ()),
        ("2016-12-31", "archive", "era5", ()),
        ("2017-01-01", "archive", "era5", ()),
        ("2017-01-02", "archive", "ecmwf_ifs", ()),
        ("2024-02-29", "archive", "ecmwf_ifs", ()),
        ("2026-10-06", "archive", "ecmwf_ifs", ()),
        ("2026-10-14", "archive", "ecmwf_ifs", ()),
        ("2026-10-15", "forecast", "ecmwf_ifs", ("2026-10-14T06:00", "2026-10-14T00:00", "2026-10-13T18:00")),
        ("2026-10-16", "forecast", "ecmwf_ifs", ("2026-10-15T06:00", "2026-10-15T00:00", "2026-10-14T18:00")),
        ("2026-10-17", "forecast", "ecmwf_ifs", ("2026-10-15T18:00", "2026-10-15T12:00", "2026-10-15T06:00")),
        ("2026-10-25", "forecast", "ecmwf_ifs", ("2026-10-15T12:00", "2026-10-15T00:00", "2026-10-14T12:00")),
        ("2026-10-29", "forecast", "ecmwf_ifs", ("2026-10-15T12:00",)),
        ("2026-10-30", "outlook", "ecmwf_seas5_ensemble_mean", ()),
        ("2026-11-01", "outlook", "ecmwf_seas5_ensemble_mean", ()),
        ("2027-04-30", "outlook", "ecmwf_seas5_ensemble_mean", ()),
    ],
)
def test_the_date_matrix(text: str, kind: str, model: str, runs: tuple) -> None:
    choice = choose(text, CLOCK)
    assert (choice.kind, choice.model, choice.runs) == (kind, model, runs)
    assert choice.step_hours == (6 if kind == "outlook" else 1) and not choice.problem


@pytest.mark.parametrize("text", ["2027-05-01", "2030-01-01"])
def test_dates_past_the_outlook_are_refused_with_the_ranges(text: str) -> None:
    choice = choose(text, CLOCK)
    assert choice.kind == "" and "2027-04-30, the last day the ECMWF seasonal outlook covers" in choice.problem
    assert "Today is 2026-10-16 (UTC)" in choice.problem and "archive to 2026-10-14" in choice.problem
    assert "forecast run 2026-10-15 to 2026-10-29" in choice.problem and "seasonal outlook 2026-10-30 to 2027-04-30" in choice.problem


def test_every_run_candidate_covers_every_city_and_counts_as_published() -> None:
    for offset in range(-1, 14):
        day = CLOCK // 1440 + offset
        for run in forecast_runs(day, CLOCK):
            horizon = {0: 360, 6: 144, 12: 360, 18: 144}[run % 1440 // 60] * 60
            assert run <= day * 1440 - 14 * 60, "the run starts before the earliest city's day"
            assert run + horizon >= day * 1440 + 35 * 60, "the run reaches past the latest city's day"
            assert run + 9 * 60 <= CLOCK, "the run counts as published"


def test_yesterday_and_today_use_runs_fixed_by_the_date() -> None:
    for hour in range(24):
        clock = CLOCK - 4 * 60 + hour * 60  # every hour of 2026-10-16
        assert choose("2026-10-16", clock).runs[0] == "2026-10-15T06:00"
        assert choose("2026-10-15", clock).runs[0] == "2026-10-14T06:00"


def test_the_outlook_gains_a_month_on_the_seventh() -> None:
    sixth, seventh = days_from_civil(2026, 10, 6), days_from_civil(2026, 10, 7)
    assert date_text(outlook_last_day(sixth)) == "2027-03-31"
    assert date_text(outlook_last_day(seventh)) == "2027-04-30"
    assert date_text(outlook_last_day(days_from_civil(2026, 12, 31))) == "2027-06-30"
    assert date_text(outlook_last_day(days_from_civil(2027, 8, 31))) == "2028-02-29"


# ----- The clock matrix -----


@pytest.mark.parametrize(
    ("text", "clock", "earlier", "later"),
    [
        # midnight UTC: T-2 is the archive at the clock, a forecast ten minutes before
        ("2026-10-14", "2026-10-16T00:05", "forecast", "archive"),
        # midnight UTC: T+13 becomes a forecast day as T advances
        ("2026-10-30", "2026-10-17T00:05", "outlook", "forecast"),
        # the 7th: the outlook reaches a month further
        ("2027-04-15", "2026-10-07T00:05", "", "outlook"),
        # new year, and a leap day
        ("2028-01-13", "2027-12-31T00:05", "outlook", "forecast"),
        ("2024-02-27", "2024-02-29T00:05", "forecast", "archive"),
    ],
)
def test_the_clock_matrix_across_midnight(text: str, clock: str, earlier: str, later: str) -> None:
    now = minutes(clock + "+00:00")
    assert choose(text, now - 10).kind == earlier
    assert choose(text, now).kind == later


@pytest.mark.parametrize("hour", [3, 9, 15, 21])
def test_the_clock_matrix_across_run_publication(hour: int) -> None:
    # a run starting at hour - 9 counts as published at the hour: the newest run moves by six hours
    now = minutes(f"2026-10-16T{hour:02d}:05+00:00")
    before, after = choose("2026-10-20", now - 10).runs[0], choose("2026-10-20", now).runs[0]
    assert stamp_minutes(after) - stamp_minutes(before) == 360


def test_a_source_follows_the_rules_only_with_its_skipped_runs() -> None:
    runs = choose("2026-10-17", CLOCK).runs
    key = ("forecast", "ecmwf_ifs", runs[1], 1)
    assert choice_problem("2026-10-17", CLOCK, key, [runs[0]]) is None
    assert "not all tried first" in choice_problem("2026-10-17", CLOCK, key, [])
    assert "not" in choice_problem("2026-10-17", CLOCK, ("archive", "ecmwf_ifs", "", 1), [])
    assert choice_problem("2026-07-07", CLOCK, ("archive", "ecmwf_ifs", "", 1), []) is None
    assert choice_problem("2016-07-07", CLOCK, ("archive", "ecmwf_ifs", "", 1), []) is not None


def test_windows_of_name_each_range() -> None:
    assert windows_of(CLOCK // 1440) == {
        "archive_first": "1940-01-01",
        "archive_last": "2026-10-14",
        "forecast_first": "2026-10-15",
        "forecast_last": "2026-10-29",
        "outlook_first": "2026-10-30",
        "outlook_last": "2027-04-30",
    }


# ----- A day's aggregates -----


def series(start: str, end: str, offset: int, values: dict, step: int = 1, times: list[str] | None = None) -> dict:
    first, last = stamp_minutes(start), stamp_minutes(end)
    stamps = times or [stamp_text(minute) for minute in range(first, last + 1, step * 60) if minute % (step * 60) == 0]
    return {
        "city": "London",
        "timezone": "Europe/London",
        "start": start,
        "end": end,
        "start_offset": offset,
        "end_offset": offset,
        "grid": [51.5, -0.12, 11.0],
        "times": stamps,
        **{name: [values.get(name, 1.0)] * len(stamps) for name in ("temperature_2m", "relative_humidity_2m", "precipitation", "wind_speed_10m")},
    }


def test_instant_readings_use_start_to_end_and_totals_end_at_each_stamp() -> None:
    assert stamps_inside(0, 1440, 60, accumulated=False)[0] == 0 and stamps_inside(0, 1440, 60, accumulated=False)[-1] == 1380
    assert stamps_inside(0, 1440, 60, accumulated=True)[0] == 60 and stamps_inside(0, 1440, 60, accumulated=True)[-1] == 1440
    assert len(stamps_inside(-330, 1110, 60, accumulated=False)) == 24  # Mumbai's half-hour offset
    assert len(stamps_inside(15 * 60, 39 * 60, 360, accumulated=False)) == 4  # Tokyo, six-hourly


def test_a_day_counts_its_readings_and_misses_an_absent_stamp() -> None:
    day = series("2026-07-06T23:00", "2026-07-07T23:00", 60, {"temperature_2m": 20.0, "precipitation": 0.1})
    metrics = day_metrics(day, 1)
    assert metrics.samples == 24 and metrics.missing == () and metrics.rain_mm == Decimal("2.4")
    gap = dict(day)
    gap["times"] = day["times"][:5] + day["times"][6:]
    for name in ("temperature_2m", "relative_humidity_2m", "precipitation", "wind_speed_10m"):
        gap[name] = day[name][:5] + day[name][6:]
    assert set(day_metrics(gap, 1).missing) == {"temperature_2m", "relative_humidity_2m", "precipitation", "wind_speed_10m"}
    null = dict(day)
    null["wind_speed_10m"] = list(day["wind_speed_10m"])
    null["wind_speed_10m"][3] = None
    assert day_metrics(null, 1).missing == ("wind_speed_10m",)
    tail = dict(day)  # the rain stamp at the day's end is the last hour's total
    tail["precipitation"] = list(day["precipitation"])
    tail["precipitation"][0] = None
    assert day_metrics(tail, 1).missing == (), "the stamp at the start is the hour before the day"


def test_the_canonical_hash_reads_85_and_85_point_0_the_same() -> None:
    one = series("2026-07-06T23:00", "2026-07-07T23:00", 60, {"relative_humidity_2m": 85})
    two = series("2026-07-06T23:00", "2026-07-07T23:00", 60, {"relative_humidity_2m": 85.0})
    key = ("archive", "ecmwf_ifs", "", 1)
    assert canonical_series("2026-07-07", key, [one]) == canonical_series("2026-07-07", key, [two])
    assert series_sha256("2026-07-07", key, [one]) != series_sha256("2026-07-07", ("archive", "era5", "", 1), [one])


@pytest.mark.parametrize(
    ("change", "words"),
    [
        ({"start": "2026-07-06T19:00"}, "not local midnight"),
        ({"start_offset": 900}, "offsets must be whole minutes"),
        ({"start_offset": 600, "end_offset": 600}, "solar time"),
        ({"timezone": "Europe/Paris"}, "time zone"),
        ({"times": ["2026-07-07T00:30"]}, "off the 1-hourly grid"),
        ({"city": "Atlantis"}, "not one of the cities"),
    ],
)
def test_a_series_is_checked_by_hand(change: dict, words: str) -> None:
    day = series("2026-07-06T23:00", "2026-07-07T23:00", 60, {})
    assert series_problem("2026-07-07", 1, day) is None
    broken = {**day, **change}
    if "times" in change:
        for name in ("temperature_2m", "relative_humidity_2m", "precipitation", "wind_speed_10m"):
            broken[name] = [1.0]
    assert words in series_problem("2026-07-07", 1, broken)


# ----- Exact arithmetic and evaluation -----


def test_conversions_are_exact() -> None:
    units = Units("F", "mph", "in")
    assert convert("max_temp", Fraction(100), units) == 212
    assert convert("wind", Fraction("1.609344"), units) == 1
    assert convert("rain", Fraction("25.4"), units) == 1
    assert convert("wind", Fraction("3.6"), Units("C", "ms", "mm")) == 1


@pytest.mark.parametrize(("value", "expected"), [("24.95", "25.0"), ("71.25", "71.3"), ("-0.05", "-0.1"), ("0.04", "0.0"), ("83.25", "83.3"), ("24.979121927", "25.0")])
def test_rounding_is_half_up(value: str, expected: str) -> None:
    assert round_half_up(Fraction(value)) == Decimal(expected)


def test_truncation_keeps_nine_decimals_toward_zero() -> None:
    assert str(truncate(Fraction(1, 3))) == "0.333333333"
    assert str(truncate(Fraction(-1, 3))) == "-0.333333333"
    assert str(truncate(Fraction("0.45"))) == "0.450000000"


def test_equality_meets_greater_or_equal_only() -> None:
    condition = parse_condition("max_temp >= 95")
    assert holds(Fraction(95), condition) and not holds(Fraction(95), parse_condition("max_temp > 95"))
    assert holds(Fraction(95), parse_condition("max_temp <= 95")) and not holds(Fraction(95), parse_condition("max_temp < 95"))


def test_all_and_any_and_the_unavailable() -> None:
    hot = day_metrics(series("2026-07-06T23:00", "2026-07-07T23:00", 60, {"temperature_2m": 36.0, "precipitation": 0.0}), 1)
    conditions = parse_conditions(["max_temp >= 95", "rain > 0.5"])
    units = Units("F", "kmh", "in")
    assert [alert.city for alert in evaluate([hot], conditions, "any", units).alerts] == ["London"]
    assert not evaluate([hot], conditions, "all", units).alerts
    missing = series("2026-07-06T23:00", "2026-07-07T23:00", 60, {"temperature_2m": 36.0})
    missing["temperature_2m"] = [None] * len(missing["times"])
    found = evaluate([day_metrics(missing, 1)], conditions, "any", units)
    assert not found.alerts and found.unavailable == {"London": ("max_temp",)}


def test_the_cities_are_thirty_in_order() -> None:
    assert len(CITY_NAMES) == 30 and list(CITY_NAMES) == sorted(CITY_NAMES)
    assert DataKind.OUTLOOK.value == "outlook"
    assert day_number("1970-01-02") == 1
    assert (EPOCH + timedelta(minutes=CLOCK)).isoformat() == "2026-10-16T04:00:00+00:00"
