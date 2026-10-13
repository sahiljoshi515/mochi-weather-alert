"""The shared rules: grammar, exact conversions, half-up rounding, aggregates and evaluation."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest

from plugin.rules import (
    DayMetrics,
    Units,
    aggregate,
    convert,
    date_problem,
    evaluate,
    holds,
    metrics_from_plain,
    needed_variables,
    parse_condition,
    parse_conditions,
    plain_report,
    round_half_up,
)

METRIC = Units("C", "kmh", "mm")
IMPERIAL = Units("F", "mph", "in")


def metrics(city, *, max_temp="30.0", min_temp="20.0", rain="0.0", wind="10.0", humidity_sum="1200", hours=24, missing=()):
    return DayMetrics(
        city=city,
        hours=hours,
        max_temp_c=None if max_temp is None else Decimal(max_temp),
        min_temp_c=None if min_temp is None else Decimal(min_temp),
        rain_mm=None if rain is None else Decimal(rain),
        max_wind_kmh=None if wind is None else Decimal(wind),
        humidity_sum_pct=None if humidity_sum is None else Decimal(humidity_sum),
        humidity_hours=0 if humidity_sum is None else hours,
        missing=tuple(missing),
    )


# ----- Grammar -----


@pytest.mark.parametrize(
    ("text", "canonical"),
    [
        ("max_temp >= 95", "max_temp >= 95"),
        ("max_temp>=95.00", "max_temp >= 95"),
        ("  rain > .5 ", "rain > 0.5"),
        ("wind <= +007.10", "wind <= 7.1"),
        ("humidity < 100", "humidity < 100"),
        ("min_temp < -0.00", "min_temp < 0"),
        ("min_temp <= -3.25", "min_temp <= -3.25"),
    ],
)
def test_parses_conditions_into_their_canonical_form(text: str, canonical: str) -> None:
    found = parse_condition(text)
    assert found.text == canonical
    assert found.threshold == Decimal(canonical.split()[2])


@pytest.mark.parametrize(
    "text",
    ["temp >= 3", "max_temp = 3", "max_temp == 3", "max_temp >= 1.234", "max_temp >=", ">= 3", "max_temp >= 3 and rain > 0", "", "max_temp ≥ 3", "MAX_TEMP >= 3", None],
)
def test_refuses_what_is_not_a_condition(text) -> None:
    with pytest.raises(ValueError, match="is not a condition"):
        parse_condition(text)


def test_refuses_too_few_too_many_or_repeated_conditions() -> None:
    with pytest.raises(ValueError, match="1 to 6"):
        parse_conditions([])
    with pytest.raises(ValueError, match="1 to 6"):
        parse_conditions([f"max_temp >= {n}" for n in range(7)])
    with pytest.raises(ValueError, match="given twice"):
        parse_conditions(["rain > 0", "wind > 5", "rain > 0.0"])
    assert [c.text for c in parse_conditions(["rain > 0", "rain >= 0"])] == ["rain > 0", "rain >= 0"]
    assert needed_variables(parse_conditions(["wind > 5", "humidity < 30", "wind >= 1"])) == ("wind", "humidity")


@pytest.mark.parametrize(
    ("date", "problem"),
    [
        ("2026-07-01", None),
        ("2026-09-30", None),
        ("2026-06-30", "outside the snapshot"),
        ("2026-10-01", "outside the snapshot"),
        ("2026-09-31", "not a calendar day"),
        ("2026-02-30", "not a calendar day"),
        ("2026-13-01", "not a calendar day"),
        ("26-09-06", "YYYY-MM-DD"),
        ("2026/09/06", "YYYY-MM-DD"),
        ("Sep 6", "YYYY-MM-DD"),
        (20260906, "YYYY-MM-DD"),
    ],
)
def test_checks_dates(date, problem) -> None:
    found = date_problem(date)
    assert (found is None) == (problem is None)
    if problem:
        assert problem in found


# ----- Arithmetic -----


def test_converts_exactly() -> None:
    assert convert("max_temp", Fraction(35), IMPERIAL) == 95
    assert convert("min_temp", Fraction(Decimal("-40.0")), IMPERIAL) == -40
    assert convert("wind", Fraction(Decimal("40.2")), IMPERIAL) == Fraction(402, 10) / Fraction(1609344, 1000000)
    assert convert("wind", Fraction(36), Units("C", "ms", "mm")) == 10
    assert convert("rain", Fraction(Decimal("12.7")), IMPERIAL) == Fraction(1, 2)
    assert convert("humidity", Fraction(71, 2), IMPERIAL) == Fraction(71, 2)
    assert convert("max_temp", Fraction(Decimal("33.3")), METRIC) == Fraction(333, 10)


@pytest.mark.parametrize(
    ("value", "rounded"),
    [
        (Fraction(2495, 100), "25.0"),
        (Fraction(7125, 100), "71.3"),
        (Fraction(7175, 100), "71.8"),
        (Fraction(1715, 24), "71.5"),
        (Fraction(24979, 1000), "25.0"),
        (Fraction(-5, 100), "-0.1"),
        (Fraction(-4, 100), "0.0"),
        (Fraction(49, 1000), "0.0"),
        (Fraction(0), "0.0"),
        (Fraction(95), "95.0"),
        (Fraction(-1049, 10), "-104.9"),
    ],
)
def test_rounds_half_up_away_from_zero(value: Fraction, rounded: str) -> None:
    found = round_half_up(value)
    assert str(found) == rounded
    assert found == Decimal(rounded)


def test_compares_the_exact_value_at_the_boundary() -> None:
    at = Fraction(95)
    assert holds(at, parse_condition("max_temp >= 95")) is True
    assert holds(at, parse_condition("max_temp > 95")) is False
    assert holds(at, parse_condition("max_temp <= 95")) is True
    assert holds(at, parse_condition("max_temp < 95")) is False
    # 40.2 km/h is 24.979 mph: reported as 25.0, but not at least 25
    mph = convert("wind", Fraction(Decimal("40.2")), IMPERIAL)
    assert round_half_up(mph) == Decimal("25.0")
    assert holds(mph, parse_condition("wind >= 25")) is False
    assert holds(mph, parse_condition("wind < 25")) is True


# ----- Aggregates -----


def rows(count: int, **overrides):
    found = []
    for hour in range(count):
        row = {"temperature_2m": Decimal(10 + hour), "relative_humidity_2m": Decimal(50), "precipitation": Decimal("0.1"), "wind_speed_10m": Decimal(hour)}
        for variable, values in overrides.items():
            row[variable] = values[hour]
        found.append(row)
    return found


def test_aggregates_a_23_hour_day_over_its_own_rows() -> None:
    day = aggregate("Santiago", rows(23))
    assert day.hours == 23 and day.missing == ()
    assert day.max_temp_c == 32 and day.min_temp_c == 10
    assert day.rain_mm == Decimal("2.3")  # 23 × 0.1, exactly
    assert day.max_wind_kmh == 22
    assert day.humidity_sum_pct == 1150 and day.humidity_hours == 23


def test_a_missing_hour_marks_the_variable_and_is_never_counted_as_zero() -> None:
    temps = [Decimal(20)] * 24
    temps[5] = None
    day = aggregate("Lima", rows(24, temperature_2m=temps))
    assert day.missing == ("temperature_2m",)
    assert day.max_temp_c == 20 and day.min_temp_c == 20
    rain = [Decimal(1)] * 24
    rain[0] = None
    day = aggregate("Lima", rows(24, precipitation=rain))
    assert day.missing == ("precipitation",) and day.rain_mm == 23
    humidity = [None] * 24
    day = aggregate("Lima", rows(24, relative_humidity_2m=humidity))
    assert day.missing == ("relative_humidity_2m",) and day.humidity_sum_pct is None and day.humidity_hours == 0
    assert aggregate("Nowhere", []).missing == ("temperature_2m", "relative_humidity_2m", "precipitation", "wind_speed_10m")


def test_pinned_numbers_round_trip_exactly() -> None:
    day = metrics_from_plain("Oslo", 24, 12.3, -0.5, 1.2, 33.3, 1711.0, 24, ["precipitation"])
    assert day.max_temp_c == Decimal("12.3") and day.min_temp_c == Decimal("-0.5") and day.rain_mm == Decimal("1.2")
    assert day.max_wind_kmh == Decimal("33.3") and day.humidity_sum_pct == 1711 and day.missing == ("precipitation",)
    assert metrics_from_plain("Oslo", 24, None, None, None, None, None, 0, []).max_temp_c is None


# ----- Evaluation -----


def test_boundary_tie_and_rounding_in_one_day() -> None:
    days = [
        metrics("Boundary", max_temp="35.0", humidity_sum="1710"),  # 95.0 °F exactly; humidity 71.25
        metrics("Below", max_temp="34.9", humidity_sum="1722"),  # 94.82 °F; humidity 71.75
        metrics("Windy", max_temp="20.0", wind="40.2"),  # 24.979 mph
    ]
    conditions = parse_conditions(["max_temp >= 95", "wind >= 25"])
    found = evaluate(days, conditions, "any", IMPERIAL)
    assert [alert.city for alert in found.alerts] == ["Boundary"]
    assert found.alerts[0].values == {"max_temp": Decimal("95.0"), "wind": Decimal("6.2")}
    assert found.alerts[0].matched == ("max_temp >= 95",)
    assert found.unavailable == {}
    strict = evaluate(days, parse_conditions(["max_temp > 95"]), "any", IMPERIAL)
    assert strict.alerts == ()
    humid = evaluate(days, parse_conditions(["humidity >= 71.25"]), "all", IMPERIAL)
    assert {alert.city: alert.values["humidity"] for alert in humid.alerts} == {"Boundary": Decimal("71.3"), "Below": Decimal("71.8")}
    assert evaluate(days, parse_conditions(["humidity > 71.25"]), "all", IMPERIAL).alerts[0].city == "Below"


def test_all_needs_every_condition_and_any_needs_one() -> None:
    days = [metrics("Hot", max_temp="36.0", rain="0.0"), metrics("Wet", max_temp="25.0", rain="12.7"), metrics("Both", max_temp="36.0", rain="20.0")]
    conditions = parse_conditions(["max_temp >= 95", "rain >= 0.5"])
    every = evaluate(days, conditions, "all", IMPERIAL)
    assert [alert.city for alert in every.alerts] == ["Both"]
    assert every.alerts[0].matched == ("max_temp >= 95", "rain >= 0.5")
    some = evaluate(days, conditions, "any", IMPERIAL)
    assert [(alert.city, alert.matched) for alert in some.alerts] == [
        ("Hot", ("max_temp >= 95",)),
        ("Wet", ("rain >= 0.5",)),
        ("Both", ("max_temp >= 95", "rain >= 0.5")),
    ]
    assert some.alerts[1].values == {"max_temp": Decimal("77.0"), "rain": Decimal("0.5")}


def test_a_missing_needed_variable_makes_the_city_unavailable() -> None:
    days = [
        metrics("Gap", max_temp="40.0", missing=["temperature_2m"]),
        metrics("Fine", max_temp="40.0"),
        metrics("Other", max_temp="40.0", missing=["precipitation"]),
        metrics("Empty", max_temp=None, min_temp=None, missing=["temperature_2m"]),
    ]
    found = evaluate(days, parse_conditions(["max_temp >= 30", "min_temp >= 0"]), "any", METRIC)
    assert [alert.city for alert in found.alerts] == ["Fine", "Other"]
    assert found.unavailable == {"Gap": ("max_temp", "min_temp"), "Empty": ("max_temp", "min_temp")}
    only_rain = evaluate(days, parse_conditions(["rain >= 0"]), "all", METRIC)
    assert [alert.city for alert in only_rain.alerts] == ["Gap", "Fine", "Empty"]
    assert only_rain.unavailable == {"Other": ("rain",)}


def test_plain_report_is_the_report_file_shape() -> None:
    days = [metrics("A", max_temp="35.0"), metrics("B", max_temp="10.0", missing=["temperature_2m"])]
    report = plain_report("2026-08-01", evaluate(days, parse_conditions(["max_temp >= 95"]), "all", IMPERIAL))
    assert report == {
        "date": "2026-08-01",
        "alerts": [{"city": "A", "values": {"max_temp": 95.0}, "matched": ["max_temp >= 95"]}],
        "unavailable": [{"city": "B", "variables": ["max_temp"]}],
    }
