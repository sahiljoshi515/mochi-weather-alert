"""Fetch the weather snapshot once from the Open-Meteo archive and write src/plugin/data/.

    uv run python src/plugin/tools_dev/fetch_snapshot.py [--pause 0.6]

For each of the 30 cities it makes two requests:

1. The archive with ``timezone=auto`` over the snapshot range, exactly as the task was specified.
   It gives the city's IANA time zone and the ``utc_offset_seconds`` the API applied. The API
   applies that ONE offset to the whole series (it does not shift at a daylight-saving change), so
   its "local" days always have 24 rows.
2. The same hours in UTC, one day wider on each side, so every local calendar day in the range is
   complete whatever the city's offset.

The UTC series is converted to wall-clock time in the city's zone with zoneinfo, which gives the
real local calendar days: 23 rows on a day clocks go forward, 25 when they go back. The auto series
is cross-checked against the UTC series shifted by the API's offset, value for value.

Writes cities.json, hourly.csv.gz (city, local time with its UTC offset, the four variables; a
missing value is an empty field), provenance.json, and the page's ui/snapshot.js.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

API = "https://archive-api.open-meteo.com/v1/archive"
START, END = "2026-07-01", "2026-09-30"
UTC_START, UTC_END = "2026-06-30", "2026-10-01"
VARIABLES = ["temperature_2m", "relative_humidity_2m", "precipitation", "wind_speed_10m"]
UNITS = {"temperature_2m": "°C", "relative_humidity_2m": "%", "precipitation": "mm", "wind_speed_10m": "km/h"}
# name, country, latitude, longitude
CITIES = [
    ("Athens", "Greece", 37.9838, 23.7275),
    ("Auckland", "New Zealand", -36.8485, 174.7633),
    ("Bangkok", "Thailand", 13.7563, 100.5018),
    ("Buenos Aires", "Argentina", -34.6037, -58.3816),
    ("Cairo", "Egypt", 30.0444, 31.2357),
    ("Cape Town", "South Africa", -33.9249, 18.4241),
    ("Denver", "United States", 39.7392, -104.9903),
    ("Dubai", "United Arab Emirates", 25.2048, 55.2708),
    ("Honolulu", "United States", 21.3069, -157.8583),
    ("Houston", "United States", 29.7604, -95.3698),
    ("Jakarta", "Indonesia", -6.2088, 106.8456),
    ("Lagos", "Nigeria", 6.5244, 3.3792),
    ("Lima", "Peru", -12.0464, -77.0428),
    ("London", "United Kingdom", 51.5074, -0.1278),
    ("Madrid", "Spain", 40.4168, -3.7038),
    ("Mexico City", "Mexico", 19.4326, -99.1332),
    ("Moscow", "Russia", 55.7558, 37.6173),
    ("Mumbai", "India", 19.0760, 72.8777),
    ("Nairobi", "Kenya", -1.2921, 36.8219),
    ("Oslo", "Norway", 59.9139, 10.7522),
    ("Perth", "Australia", -31.9505, 115.8605),
    ("Phoenix", "United States", 33.4484, -112.0740),
    ("Reykjavik", "Iceland", 64.1466, -21.9426),
    ("Santiago", "Chile", -33.4489, -70.6693),
    ("Seoul", "South Korea", 37.5665, 126.9780),
    ("Singapore", "Singapore", 1.3521, 103.8198),
    ("Sydney", "Australia", -33.8688, 151.2093),
    ("Tokyo", "Japan", 35.6762, 139.6503),
    ("Toronto", "Canada", 43.6532, -79.3832),
    ("Vancouver", "Canada", 49.2827, -123.1207),
]
DATA = Path(__file__).resolve().parents[1] / "data"
UI = Path(__file__).resolve().parents[3] / "ui"


def template(timezone: str, start: str, end: str) -> str:
    query = f"start_date={start}&end_date={end}&hourly={','.join(VARIABLES)}&timezone={timezone}"
    return f"{API}?latitude={{lat}}&longitude={{lon}}&{query}"


def fetch(url: str, attempts: int = 5) -> dict:
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=60) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == attempts:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == attempts:
                raise
        time.sleep(3 * attempt)
    raise RuntimeError("unreachable")


def number(value: object) -> str:
    """A CSV field: the API's number as its shortest text, or empty for a missing value."""
    if value is None:
        return ""
    if isinstance(value, bool):
        raise TypeError(f"unexpected boolean {value!r}")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    raise TypeError(f"unexpected value {value!r}")


def hours_between(first: str, last: str) -> list[datetime]:
    start = datetime.fromisoformat(first).replace(tzinfo=UTC)
    stop = datetime.fromisoformat(last).replace(tzinfo=UTC) + timedelta(days=1)
    return [start + timedelta(hours=index) for index in range(int((stop - start).total_seconds() // 3600))]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pause", type=float, default=0.6, help="seconds between requests")
    args = parser.parse_args()
    fetched_at = datetime.now(UTC).replace(microsecond=0)
    auto_template = template("auto", START, END)
    utc_template = template("UTC", UTC_START, UTC_END)
    expected_utc = [hour.strftime("%Y-%m-%dT%H:%M") for hour in hours_between(UTC_START, UTC_END)]

    cities = []
    provenance_cities = {}
    rows_out: list[list[str]] = []
    nulls: Counter = Counter()
    day_lengths: dict[str, Counter] = {}
    for index, (name, country, lat, lon) in enumerate(CITIES):
        if index:
            time.sleep(args.pause)
        auto = fetch(auto_template.format(lat=lat, lon=lon))
        time.sleep(args.pause)
        utc = fetch(utc_template.format(lat=lat, lon=lon))
        zone_name = auto["timezone"]
        offset = int(auto["utc_offset_seconds"])
        zone = ZoneInfo(zone_name)
        if utc["hourly"]["time"] != expected_utc:
            raise RuntimeError(f"{name}: the UTC series is not the complete hourly grid {UTC_START}..{UTC_END}")
        if utc.get("utc_offset_seconds") != 0:
            raise RuntimeError(f"{name}: the UTC series came back with offset {utc.get('utc_offset_seconds')}")
        if auto["hourly_units"] != {"time": "iso8601", **UNITS} or utc["hourly_units"] != auto["hourly_units"]:
            raise RuntimeError(f"{name}: unexpected units {auto['hourly_units']}")
        utc_series = utc["hourly"]
        by_instant = {expected_utc[i]: i for i in range(len(expected_utc))}

        # 1. The auto series must be the UTC series shifted by the API's one offset. A zone on a
        # half hour (Asia/Kolkata) has no UTC row at the shifted instant: the API then serves the
        # next whole hour, which is checked instead and recorded.
        cross_check = "equals the UTC series shifted by utc_offset_seconds"
        shift = timedelta(seconds=offset)
        if offset % 3600:
            shift = timedelta(seconds=offset - offset % 3600)
            cross_check = f"half-hour zone: equals the UTC series shifted by {int(shift.total_seconds())} s (offset rounded down)"
        mismatches = 0
        for i, local_fixed in enumerate(auto["hourly"]["time"]):
            instant = (datetime.fromisoformat(local_fixed) - shift).strftime("%Y-%m-%dT%H:%M")
            j = by_instant.get(instant)
            if j is None:
                raise RuntimeError(f"{name}: auto row {local_fixed} falls outside the UTC fetch")
            for variable in VARIABLES:
                if auto["hourly"][variable][i] != utc_series[variable][j]:
                    mismatches += 1
        if mismatches:
            raise RuntimeError(f"{name}: {mismatches} values differ between the auto and the shifted UTC series")

        # 2. Real local calendar days from the UTC series.
        lengths: Counter = Counter()
        kept = 0
        for j, instant_text in enumerate(expected_utc):
            local = datetime.fromisoformat(instant_text).replace(tzinfo=UTC).astimezone(zone)
            day = local.strftime("%Y-%m-%d")
            if not START <= day <= END:
                continue
            values = [utc_series[variable][j] for variable in VARIABLES]
            for variable, value in zip(VARIABLES, values, strict=True):
                if value is None:
                    nulls[variable] += 1
            rows_out.append([name, local.isoformat(timespec="minutes"), *(number(value) for value in values)])
            lengths[day] += 1
            kept += 1
        days = (datetime.fromisoformat(END) - datetime.fromisoformat(START)).days + 1
        if len(lengths) != days:
            raise RuntimeError(f"{name}: {len(lengths)} local days, expected {days}")
        day_lengths[name] = lengths
        cities.append({"name": name, "country": country, "lat": lat, "lon": lon, "timezone": zone_name})
        provenance_cities[name] = {
            "timezone": zone_name,
            "utc_offset_seconds": offset,
            "api_latitude": auto["latitude"],
            "api_longitude": auto["longitude"],
            "elevation_m": auto.get("elevation"),
            "hours": kept,
            "cross_check": cross_check,
        }
        odd = {day: count for day, count in sorted(lengths.items()) if count != 24}
        print(f"{name:13} {zone_name:32} offset {offset:6d}  rows {kept}  odd days {odd or '-'}", flush=True)

    DATA.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["city", "time", *VARIABLES])
    writer.writerows(rows_out)
    packed = io.BytesIO()
    with gzip.GzipFile(fileobj=packed, mode="wb", mtime=0) as compressed:
        compressed.write(buffer.getvalue().encode("utf-8"))
    (DATA / "hourly.csv.gz").write_bytes(packed.getvalue())
    (DATA / "cities.json").write_text(json.dumps(cities, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    short = [{"city": c, "date": d, "hours": n} for c, lengths in day_lengths.items() for d, n in sorted(lengths.items()) if n < 24]
    long = [{"city": c, "date": d, "hours": n} for c, lengths in day_lengths.items() for d, n in sorted(lengths.items()) if n > 24]
    tz_version = None
    for folder in ("/usr/share/zoneinfo", "/usr/lib/zoneinfo"):
        version = Path(folder, "+VERSION")
        if version.is_file():
            tz_version = version.read_text(encoding="utf-8").strip()
    provenance = {
        "source": "Open-Meteo historical weather API (archive-api.open-meteo.com), ERA5-based reanalysis, hourly",
        "licence": "Open-Meteo data is offered under CC BY 4.0 (https://open-meteo.com/en/terms)",
        "request_template": auto_template,
        "utc_request_template": utc_template,
        "fetched_at": fetched_at.isoformat().replace("+00:00", "Z"),
        "start": START,
        "end": END,
        "variables": {variable: UNITS[variable] for variable in VARIABLES},
        "local_time": (
            "Each row's time is wall-clock time in the city's IANA zone with its UTC offset, converted from the UTC "
            "series with Python's zoneinfo (tz database " + (tz_version or "unknown") + "). The API's timezone=auto "
            "series applies utc_offset_seconds to the whole range without a daylight-saving shift; it was checked "
            "to equal the UTC series shifted by that offset. A local day therefore has 23 rows when clocks go "
            "forward and 25 when they go back."
        ),
        "rows": len(rows_out),
        "missing_values": {variable: nulls.get(variable, 0) for variable in VARIABLES},
        "short_days": short,
        "long_days": long,
        "cities": provenance_cities,
        "sha256": {
            "hourly.csv.gz": hashlib.sha256((DATA / "hourly.csv.gz").read_bytes()).hexdigest(),
            "cities.json": hashlib.sha256((DATA / "cities.json").read_bytes()).hexdigest(),
        },
    }
    (DATA / "provenance.json").write_text(json.dumps(provenance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    snapshot = {
        "start": START,
        "end": END,
        "fetchedAt": provenance["fetched_at"],
        "source": "Open-Meteo historical weather API",
        "cities": [{"name": c["name"], "country": c["country"], "timezone": c["timezone"]} for c in cities],
        "shortDays": short,
        "longDays": long,
    }
    UI.mkdir(parents=True, exist_ok=True)
    (UI / "snapshot.js").write_text(
        "// The pinned snapshot's bounds and cities, written by src/plugin/tools_dev/fetch_snapshot.py. Do not edit.\n"
        f"export const SNAPSHOT = {json.dumps(snapshot, indent=2, ensure_ascii=False)};\n",
        encoding="utf-8",
    )
    size = (DATA / "hourly.csv.gz").stat().st_size
    print(f"\nwrote {len(rows_out)} rows for {len(cities)} cities; hourly.csv.gz {size} bytes; missing {dict(nulls) or 'none'}")
    print(f"short days: {short}\nlong days: {long}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
