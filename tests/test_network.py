"""Live checks against Open-Meteo (set NANOAGENT_NETWORK=1): a whole run on each kind of data today,
the day's rain and wind against Open-Meteo's own daily aggregates, and a watch on the assumption that
the IFS archive's day two days back is final (it equals the first hours of the runs it is stitched from)."""

from __future__ import annotations

import json
import tempfile
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from helpers import Run, task_for

from plugin import openmeteo, sources
from plugin.rules import day_metrics, stamp_minutes

pytestmark = pytest.mark.network


class Live:
    """A Run's fake slot, filled with the real clock and Open-Meteo."""

    def __init__(self) -> None:
        self.clock = lambda: datetime.now(UTC)


def get(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": openmeteo.USER_AGENT, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(request, timeout=60) as answer:
        return json.loads(openmeteo.decoded(answer.read(), answer.headers.get("Content-Encoding")))


@pytest.mark.parametrize("offset", [-30, 3, 40])
def test_a_whole_run_today_on_each_kind_of_data(offset: int, monkeypatch) -> None:
    live = Live()
    monkeypatch.setattr(sources, "CLOCK", lambda: live.clock())
    now = datetime.now(UTC)
    run = Run(task_for((now + timedelta(days=offset)).date().isoformat()), live, clock=now, agent_after=timedelta(seconds=5))
    run.gold()
    verdict = run.verdict()
    assert verdict["valid"], verdict["message"]
    assert run.daily["source"]["kind"] == {-30: "archive", 3: "forecast", 40: "outlook"}[offset]


def test_rain_and_wind_match_open_meteos_own_daily_values() -> None:
    date = (datetime.now(UTC) - timedelta(days=20)).date().isoformat()
    sources.CACHE.clear()
    _, found = sources.pin(date)
    series = {entry["city"]: entry for entry in found[0].cities}
    for city, lat, lon, zone in (("Tokyo", "35.6762", "139.6503", "Asia/Tokyo"), ("Dubai", "25.2048", "55.2708", "Asia/Dubai")):
        daily = get(
            f"{openmeteo.ARCHIVE}?latitude={lat}&longitude={lon}&start_date={date}&end_date={date}&daily=precipitation_sum,wind_speed_10m_max&timezone={zone}&models=ecmwf_ifs"
        )["daily"]
        metrics = day_metrics(series[city], 1)
        assert float(metrics.rain_mm) == pytest.approx(daily["precipitation_sum"][0], abs=0.051), city
        assert float(metrics.max_wind_kmh) == pytest.approx(daily["wind_speed_10m_max"][0], abs=0.051), city


def test_the_ifs_archive_two_days_back_equals_the_runs_it_is_stitched_from() -> None:
    day = (datetime.now(UTC) - timedelta(days=2)).date()
    archive = get(f"{openmeteo.ARCHIVE}?latitude=51.5074&longitude=-0.1278&start_date={day}&end_date={day}&hourly=temperature_2m&models=ecmwf_ifs&timezone=GMT")["hourly"]
    by_time = dict(zip(archive["time"], archive["temperature_2m"], strict=True))
    for hour in (0, 6, 12, 18):
        run = f"{day}T{hour:02d}:00"
        first = get(f"{openmeteo.SINGLE_RUN}?latitude=51.5074&longitude=-0.1278&hourly=temperature_2m&models=ecmwf_ifs&run={run}&forecast_hours=6&timezone=GMT")["hourly"]
        for time, value in zip(first["time"], first["temperature_2m"], strict=True):
            assert by_time[time] == value, (time, value, by_time[time])
    assert stamp_minutes(f"{day}T00:00") is not None and Path(tempfile.gettempdir()).is_dir()
