"""The data a date is judged on, fetched for all 30 cities.

The answer key (pin_alerts) reads the clock once and applies the date rules at two readings, now minus
DELTA_MINUTES and now, pinning one source, or two when the rules choose differently; it accepts a date
only when both readings allow it. The agent's tools apply the rules at now minus DELTA_MINUTES, which
always falls between the two as long as the agent reads within DELTA_MINUTES of the answer key; they
fetch once per date per process, so every call of a run sees the same data. A forecast falls back down
its runs, newest first, when Open-Meteo has not published one (completely).
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from plugin import days, openmeteo
from plugin.rules import (
    CITIES,
    DELTA_MINUTES,
    SOURCE_VARIABLES,
    Choice,
    DataKind,
    choose,
    cities_problem,
    date_text,
    series_sha256,
    source_key,
    source_label,
    source_request,
    stamp_minutes,
    stamp_text,
    stamps_inside,
    usable,
)

SKEW_LIMIT = timedelta(minutes=5)
# The clock: tests put a function returning an aware UTC datetime here.
CLOCK = lambda: datetime.now(UTC)
CACHE: dict[str, Fetched] = {}
LOCK = threading.Lock()


@dataclass(frozen=True)
class Fetched:
    """One source's data for a date: what the answer key pins and the agent's receipt records."""

    date: str
    clock: int  # the clock reading the rules were applied at, UTC minutes
    key: tuple[str, str, str, int]  # (kind, model, run, step hours)
    fetched_at: str  # when the answer came, UTC
    request: str
    skipped: tuple[str, ...]  # newer forecast runs that were not (completely) published, newest first
    cities: tuple[dict, ...]  # each city's series, as rules.series_problem checks it
    sha256: str

    def source(self) -> dict:
        kind, model, run, step = self.key
        return {
            "kind": kind,
            "model": model,
            "run": run,
            "step_hours": step,
            "today": date_text(self.clock // 1440),
            "label": source_label(kind, model, run, step),
        }


def epoch_minutes(instant: datetime) -> int:
    return int((instant - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds()) // 60


def now() -> datetime:
    found = CLOCK()
    if found.tzinfo is None:
        raise RuntimeError("the clock must be timezone-aware")
    return found.astimezone(UTC)


def check_skew(response: openmeteo.Response) -> None:
    """A clock far off Open-Meteo's would put a date in the wrong kind of data: refuse it."""
    if response.date is None:
        return
    off = abs(now() - response.date)
    if off > SKEW_LIMIT:
        raise RuntimeError(
            f"this computer's clock is {int(off.total_seconds() // 60)} min off Open-Meteo's "
            f"({response.date:%Y-%m-%d %H:%M} UTC): set it right and run again"
        )


def series_of(date: str, step_hours: int, locations: list[dict]) -> list[dict]:
    """Every city's series: its local day's window and the readings at the stamps inside it."""
    cities = []
    for (name, _country, _lat, _lon, timezone), location in zip(CITIES, locations, strict=True):
        start, end, start_offset, end_offset = days.window(timezone, date)
        hourly = location["hourly"]
        keep = [index for index, time in enumerate(hourly["time"]) if (minute := stamp_minutes(time)) is not None and start <= minute <= end]
        cities.append(
            {
                "city": name,
                "timezone": timezone,
                "start": stamp_text(start),
                "end": stamp_text(end),
                "start_offset": start_offset,
                "end_offset": end_offset,
                "grid": [location.get("latitude"), location.get("longitude"), location.get("elevation")],
                "times": [stamp_text(stamp_minutes(hourly["time"][index])) for index in keep],
                **{variable: [hourly[variable][index] for index in keep] for variable in SOURCE_VARIABLES},
            }
        )
    problem = cities_problem(date, step_hours, cities)
    if problem:
        raise RuntimeError(f"Open-Meteo's data for {date} is not usable: {problem}")
    return cities


def complete(cities: list[dict], step_hours: int) -> bool:
    """Whether a forecast run has every reading every city's day needs (else the run is incomplete)."""
    for series in cities:
        start, end = stamp_minutes(series["start"]), stamp_minutes(series["end"])
        index = {stamp_minutes(time): position for position, time in enumerate(series["times"])}
        for variable in SOURCE_VARIABLES:
            wanted = stamps_inside(start, end, step_hours * 60, accumulated=variable == "precipitation")
            if any(stamp not in index or series[variable][index[stamp]] is None for stamp in wanted):
                return False
    return True


def fetched(choice: Choice, clock: int, run: str, response: openmeteo.Response, skipped: list[str]) -> Fetched:
    check_skew(response)
    cities = series_of(choice.date, choice.step_hours, response.locations)
    key = source_key(choice.kind, choice.model, run, choice.step_hours)
    return Fetched(
        date=choice.date,
        clock=clock,
        key=key,
        fetched_at=now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        request=response.url,
        skipped=tuple(skipped),
        cities=tuple(cities),
        sha256=series_sha256(choice.date, key, cities),
    )


def fetch(choice: Choice, clock: int) -> Fetched:
    """The data ``choice`` names, for every city: an archive or outlook request, or the newest
    candidate forecast run Open-Meteo has published completely."""
    if choice.kind == DataKind.FORECAST.value:
        skipped: list[str] = []
        for run in choice.runs:
            try:
                response = openmeteo.get(source_request(choice.date, source_key(choice.kind, choice.model, run, choice.step_hours)))
            except openmeteo.NotPublished:
                skipped.append(run)
                continue
            found = fetched(choice, clock, run, response, skipped)
            if complete(list(found.cities), choice.step_hours):
                return found
            skipped.append(run)
        raise RuntimeError(
            f"Open-Meteo has not published ECMWF IFS run {choice.runs[0]} UTC completely, nor an earlier run "
            f"covering {choice.date}; try again later"
        )
    response = openmeteo.get(source_request(choice.date, source_key(choice.kind, choice.model, "", choice.step_hours)))
    return fetched(choice, clock, "", response, [])


def pin(date: str) -> tuple[int, list[Fetched]]:
    """The answer key's data: the clock reading (UTC minutes) and the sources the rules choose at
    now - DELTA_MINUTES and at now, one or two, each fetched once."""
    latest = epoch_minutes(now())
    # Refused when the rules refuse the date at either reading: a date that came into range within the last
    # DELTA_MINUTES would still be refused by the agent's tools, which read the clock that much behind.
    refused = usable(date, latest).problem
    if refused:
        raise ValueError(refused)
    readings = [latest - DELTA_MINUTES, latest]
    pairs = [(reading, choose(date, reading)) for reading in readings]
    if same(pairs[0][1], pairs[1][1]):
        pairs = pairs[1:]
    with ThreadPoolExecutor(max_workers=len(pairs)) as pool:
        found = list(pool.map(lambda pair: fetch(pair[1], pair[0]), pairs))
    distinct: dict[tuple, Fetched] = {}
    for candidate in found:
        distinct[candidate.key] = candidate  # the later reading's wins a tie
    return latest, list(distinct.values())


def same(first: Choice, second: Choice) -> bool:
    return (first.kind, first.model, first.runs, first.step_hours) == (second.kind, second.model, second.runs, second.step_hours)


def read(date: str) -> Fetched:
    """The agent's data for ``date``: the rules at now - DELTA_MINUTES, fetched once per process."""
    with LOCK:
        if date in CACHE:
            return CACHE[date]
    reading = epoch_minutes(now()) - DELTA_MINUTES
    choice = choose(date, reading)
    if choice.problem:
        raise ValueError(choice.problem)
    found = fetch(choice, reading)
    with LOCK:
        return CACHE.setdefault(date, found)


def cached(date: str) -> Fetched | None:
    with LOCK:
        return CACHE.get(date)
