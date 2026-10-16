"""Each city's local day, from the pinned tzdata: right at every clock change from 1940 to 2027, by an
independent test (the zone read straight from the tzdata wheel), and accepted by the verifier's hand
checks, which cannot use time zones."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import oracle

from plugin import days
from plugin.rules import CITIES, series_problem, stamp_text

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def instant(minutes: int) -> datetime:
    return EPOCH + timedelta(minutes=minutes)


def check(name: str, timezone: str, day: date) -> int:
    """Checks one window; returns its length in hours."""
    text = day.isoformat()
    start, end, start_offset, end_offset = days.window(timezone, text)
    local = lambda minutes: oracle.local_date(instant(minutes), timezone)
    assert local(start) == day and local(start - 1) == day - timedelta(days=1), (name, text)
    assert local(end - 1) == day and local(end) == day + timedelta(days=1), (name, text)
    assert instant(start).astimezone(oracle.zone(timezone)).utcoffset() == timedelta(minutes=start_offset)
    series = {
        "city": name, "timezone": timezone, "start": stamp_text(start), "end": stamp_text(end),
        "start_offset": start_offset, "end_offset": end_offset, "grid": [0.0, 0.0, 0.0], "times": [],
        "temperature_2m": [], "relative_humidity_2m": [], "precipitation": [], "wind_speed_10m": [],
    }  # fmt: skip
    assert series_problem(text, 1, series) is None, (name, text, series_problem(text, 1, series))
    return (end - start) // 60


def transitions(timezone: str, first: date, last: date) -> list[date]:
    """The days the zone's UTC offset changes on (noon to noon)."""
    found = []
    day = first
    previous = None
    while day <= last:
        noon = datetime(day.year, day.month, day.day, 12, tzinfo=UTC)
        offset = noon.astimezone(oracle.zone(timezone)).utcoffset()
        if previous is not None and offset != previous:
            found += [day - timedelta(days=1), day]
        previous = offset
        day += timedelta(days=1)
    return found


def test_every_window_from_1940_to_2027_on_every_change_and_every_fortnight() -> None:
    lengths: dict[int, int] = {}
    for name, _country, _lat, _lon, timezone in CITIES:
        sample = {date(1940, 1, 1) + timedelta(days=step) for step in range(0, 32000, 14)}
        sample |= set(transitions(timezone, date(1940, 1, 1), date(2027, 12, 31)))
        for day in sorted(sample):
            if day <= date(2027, 12, 31):
                hours = check(name, timezone, day)
                lengths[hours] = lengths.get(hours, 0) + 1
    assert set(lengths) <= {22, 23, 24, 25, 26}
    assert lengths.get(23) and lengths.get(25), "clock changes are covered"


def test_the_known_short_and_long_days() -> None:
    known = {
        ("America/Santiago", "2026-09-06"): 23,
        ("Pacific/Auckland", "2026-09-27"): 23,
        ("Europe/London", "2026-10-25"): 25,
        ("Africa/Cairo", "2026-10-29"): 25,
        ("America/Denver", "2026-11-01"): 25,
        ("America/Denver", "2027-03-14"): 23,
        ("Australia/Sydney", "2027-04-04"): 25,
        ("Asia/Kolkata", "2026-07-07"): 24,
    }
    for (timezone, text), hours in known.items():
        name = next(city[0] for city in CITIES if city[4] == timezone)
        assert check(name, timezone, date.fromisoformat(text)) == hours, (timezone, text)


def test_a_midnight_clock_change_starts_the_day_at_the_jump() -> None:
    # Santiago springs forward at local midnight: 2026-09-06 begins at 01:00 local, 04:00 UTC.
    start, end, start_offset, _ = days.window("America/Santiago", "2026-09-06")
    assert stamp_text(start) == "2026-09-06T04:00" and start_offset == -180 and (end - start) == 23 * 60
