"""The pinned snapshot: the cities and every hourly row, read once per process from src/plugin/data/."""

from __future__ import annotations

import csv
import gzip
import io
import json
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from plugin.rules import SNAPSHOT_END, SNAPSHOT_START, SOURCE_VARIABLES

DATA = Path(__file__).resolve().parent / "data"

Row = tuple[str, dict[str, Decimal | None]]  # (local time with offset, {source variable: value})


@dataclass(frozen=True)
class City:
    name: str
    country: str
    lat: float
    lon: float
    timezone: str


@dataclass(frozen=True)
class Snapshot:
    cities: tuple[City, ...]  # in the order list_cities gives them
    days: dict[tuple[str, str], list[Row]]  # (city, YYYY-MM-DD) -> the local day's rows, in time order
    fetched_at: str
    start: str
    end: str

    def city(self, name: str) -> City:
        for city in self.cities:
            if city.name == name:
                return city
        known = ", ".join(city.name for city in self.cities)
        raise ValueError(f"unknown city {name!r}; the cities are {known}")

    def day(self, name: str, date: str) -> list[Row]:
        """The local day's rows; a known city has rows for every day of the snapshot."""
        self.city(name)
        rows = self.days.get((name, date))
        if not rows:
            raise ValueError(f"{name} has no hourly rows on {date}: the snapshot runs {self.start} to {self.end}")
        return rows


def value(text: str) -> Decimal | None:
    return None if text == "" else Decimal(text)


@lru_cache(maxsize=1)
def snapshot() -> Snapshot:
    """The whole snapshot, parsed once."""
    provenance = json.loads((DATA / "provenance.json").read_text(encoding="utf-8"))
    if (provenance["start"], provenance["end"]) != (SNAPSHOT_START, SNAPSHOT_END):
        raise RuntimeError("the snapshot's provenance disagrees with the rules' snapshot range")
    cities = tuple(City(**entry) for entry in json.loads((DATA / "cities.json").read_text(encoding="utf-8")))
    days: dict[tuple[str, str], list[Row]] = {}
    with gzip.open(DATA / "hourly.csv.gz", "rb") as compressed:
        reader = csv.DictReader(io.TextIOWrapper(compressed, encoding="utf-8", newline=""))
        for row in reader:
            values = {variable: value(row[variable]) for variable in SOURCE_VARIABLES}
            days.setdefault((row["city"], row["time"][:10]), []).append((row["time"], values))
    return Snapshot(cities=cities, days=days, fetched_at=provenance["fetched_at"], start=SNAPSHOT_START, end=SNAPSHOT_END)
