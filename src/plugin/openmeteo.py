"""Open-Meteo over HTTPS: one request per source covering all 30 cities, through an opener tests replace.

Hosts (nanoagent.yaml permissions): archive-api, single-runs-api and seasonal-api.open-meteo.com.
Weather data by Open-Meteo.com, CC BY 4.0.
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime

from plugin.rules import CITIES, ENDPOINTS, SOURCE_VARIABLES, source_request

ARCHIVE = ENDPOINTS["archive"]
SINGLE_RUN = ENDPOINTS["forecast"]
SEASONAL = ENDPOINTS["outlook"]
USER_AGENT = "nanoagent-weather-alert/0.4.0 (+https://github.com/sahiljoshi515/mochi-weather-alert)"
UNITS = {"temperature_2m": "°C", "relative_humidity_2m": "%", "precipitation": "mm", "wind_speed_10m": "km/h"}
TRIES = 3
TIMEOUT = 15  # seconds per try
BACKOFF = (1, 3)  # seconds before the second and third tries
RETRIED = (429, 500, 502, 503, 504)
# What a request goes through: tests put an object with open(request, timeout) here, and sleep.
OPENER = urllib.request.build_opener()
SLEEP = time.sleep


class NotPublished(Exception):
    """Open-Meteo has no such model run (yet)."""


@dataclass(frozen=True)
class Response:
    url: str
    locations: list[dict]  # one per city, in CITIES order
    date: datetime | None  # the server's Date header


def decoded(body: bytes, encoding: str | None) -> bytes:
    return gzip.decompress(body) if (encoding or "").lower() == "gzip" or body[:2] == b"\x1f\x8b" else body


def reason_of(body: bytes) -> str:
    try:
        found = json.loads(body)
    except ValueError:
        return body[:200].decode("utf-8", "replace")
    return str(found.get("reason") if isinstance(found, dict) else found)


def checked(url_: str, found: object) -> list[dict]:
    """The answer's locations, one per city, each in UTC with the four hourly variables in metric units."""
    locations = found if isinstance(found, list) else [found]
    if len(locations) != len(CITIES):
        raise RuntimeError(f"Open-Meteo answered for {len(locations)} locations, not the {len(CITIES)} cities")
    for city, location in zip(CITIES, locations, strict=True):
        if not isinstance(location, dict) or not isinstance(location.get("hourly"), dict):
            raise TypeError(f"Open-Meteo's answer for {city[0]} has no hourly data")
        if location.get("utc_offset_seconds") != 0:
            raise RuntimeError(f"Open-Meteo's answer for {city[0]} is not in UTC")
        units = location.get("hourly_units") or {}
        wrong = [name for name, unit in UNITS.items() if units.get(name) != unit]
        if wrong:
            raise RuntimeError(f"Open-Meteo gave {', '.join(wrong)} in unexpected units: {units}")
        hourly = location["hourly"]
        if not isinstance(hourly.get("time"), list) or any(len(hourly.get(name) or []) != len(hourly["time"]) for name in SOURCE_VARIABLES):
            raise RuntimeError(f"Open-Meteo's answer for {city[0]} lacks a variable's readings")
    return locations


def run_url(run: str, date: str) -> str:
    """The request for forecast run ``run`` covering ``date``."""
    return source_request(date, ("forecast", "ecmwf_ifs", run, 1))


def get(url_: str) -> Response:
    """The answer to one request for every city: retried on 429/5xx and network errors; a model run
    Open-Meteo does not have raises NotPublished, any other refusal ValueError."""
    problem = ""
    for attempt in range(TRIES):
        request = urllib.request.Request(url_, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
        try:
            with OPENER.open(request, timeout=TIMEOUT) as answer:
                body = decoded(answer.read(), answer.headers.get("Content-Encoding"))
                stamp = answer.headers.get("Date")
        except urllib.error.HTTPError as error:
            reason = reason_of(decoded(error.read() or b"", error.headers.get("Content-Encoding") if error.headers else None))
            if error.code == 400 and "not available" in reason:
                raise NotPublished(reason) from None
            if error.code == 400:
                raise ValueError(f"Open-Meteo refused the request: {reason}") from None
            problem = f"HTTP {error.code}"
            if error.code not in RETRIED:
                break
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            problem = str(getattr(error, "reason", error)) or type(error).__name__
        else:
            try:
                found = json.loads(body)
            except ValueError:
                raise RuntimeError("Open-Meteo's answer is not JSON") from None
            date = None
            if stamp:
                try:
                    date = parsedate_to_datetime(stamp)
                except (TypeError, ValueError):
                    date = None
            return Response(url_, checked(url_, found), date)
        if attempt < TRIES - 1:
            SLEEP(BACKOFF[attempt])
    raise RuntimeError(f"Open-Meteo did not answer ({problem} after {TRIES} tries); try again later")
