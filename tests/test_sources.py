"""The data a date is judged on, fetched: the answer key's one or two candidates, the agent's read, the
fallback down a forecast's runs, refusals, retries, the clock check, and one fetch per date per run."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta

import pytest
from conftest import T0
from fakes import index, recorded

from plugin import openmeteo, sources
from plugin.rules import cities_problem, series_sha256


def at(text: str) -> datetime:
    return datetime.fromisoformat(text)


def run_url(run: str, date: str) -> str:
    return openmeteo.run_url(run, date)


def test_an_archive_day_pins_one_source_with_every_city(offline) -> None:
    _, found = sources.pin("2026-07-07")
    assert len(found) == 1 and found[0].key == ("archive", "ecmwf_ifs", "", 1)
    assert cities_problem("2026-07-07", 1, list(found[0].cities)) is None
    assert found[0].sha256 == series_sha256("2026-07-07", found[0].key, list(found[0].cities))
    assert len(offline.urls) == 1 and "models=ecmwf_ifs" in offline.urls[0] and "start_date=2026-07-06&end_date=2026-07-08" in offline.urls[0]


def test_1940_asks_from_the_first_day_and_leaves_the_eastern_cities_unavailable(offline) -> None:
    _, found = sources.pin("1940-01-01")
    assert "start_date=1940-01-01&end_date=1940-01-02" in offline.urls[0] and "models=era5" in offline.urls[0]
    auckland = next(series for series in found[0].cities if series["city"] == "Auckland")
    assert auckland["times"][0] == "1940-01-01T00:00", "no 1939 hours: Auckland's day began on 1939-12-31 in UTC"


def test_midnight_pins_the_forecast_and_the_archive(offline) -> None:
    offline.clock = lambda: at("2026-10-16T00:05:00+00:00")
    _, found = sources.pin("2026-10-14")
    assert [candidate.key[0] for candidate in found] == ["forecast", "archive"]
    assert found[0].clock + 10 == found[1].clock


def test_a_run_published_between_the_readings_pins_both_runs(offline) -> None:
    offline.clock = lambda: at("2026-10-15T15:05:00+00:00")
    _, found = sources.pin("2026-10-16")
    assert [candidate.key[2] for candidate in found] == ["2026-10-15T00:00", "2026-10-15T06:00"]


def test_the_agent_reads_ten_minutes_behind_and_once_per_date(offline) -> None:
    offline.clock = lambda: at("2026-10-15T15:05:00+00:00")
    first = sources.read("2026-10-16")
    assert first.key[2] == "2026-10-15T00:00"
    offline.clock = lambda: at("2026-10-15T23:00:00+00:00")
    assert sources.read("2026-10-16") is first, "a run fetches a date once"
    assert len(offline.urls) == 1


def test_a_date_past_the_outlook_is_refused(offline) -> None:
    with pytest.raises(ValueError, match="the last day the ECMWF seasonal outlook covers"):
        sources.pin("2027-05-01")


def test_a_date_that_came_into_range_minutes_ago_is_refused_until_the_first_time_that_works(offline) -> None:
    """WA-03: on the 7th the outlook gains a month at 00:00 UTC; for ten minutes the agent's tools (which
    read the clock ten minutes behind) would refuse the new month, so the answer key refuses it first,
    naming the earliest time a task on it works, 00:10 UTC, whenever in those ten minutes it is asked."""
    for minute in (0, 5, 9):
        offline.clock = lambda minute=minute: at(f"2026-10-07T00:{minute:02d}:30+00:00")
        with pytest.raises(ValueError, match=r"came into range only in the last 10 minutes .*: a task on it works from 2026-10-07T00:10 UTC\.$"):
            sources.pin("2027-04-15")
    offline.clock = lambda: at("2026-10-07T00:10:00+00:00")
    _, found = sources.pin("2027-04-15")
    assert len(found) == 1 and found[0].key[0] == "outlook", "from 00:10 the date works"
    sources.CACHE.clear()
    offline.clock = lambda: at("2026-10-07T00:12:00+00:00")
    assert sources.read("2027-04-15").key[0] == "outlook", "an agent reading after the answer key is never refused"


def test_an_unpublished_run_falls_back_to_the_next_newest(offline) -> None:
    newest, second = "2026-10-15T18:00", "2026-10-15T12:00"
    offline.overrides[run_url(newest, "2026-10-17")] = {"status": 400, "reason": "The requested model run is not available. Model: ecmwf_ifs"}
    body = recorded(run_url(newest, "2026-10-17"))["body"]
    offline.overrides[run_url(second, "2026-10-17")] = body
    _, found = sources.pin("2026-10-17")
    assert found[0].key[2] == second and found[0].skipped == (newest,)


def test_an_incomplete_run_falls_back_too(offline) -> None:
    newest, second = "2026-10-15T18:00", "2026-10-15T12:00"
    body = recorded(run_url(newest, "2026-10-17"))["body"]
    holed = copy.deepcopy(body)
    for location in holed:
        location["hourly"]["temperature_2m"][-3] = None
    offline.overrides[run_url(newest, "2026-10-17")] = holed
    offline.overrides[run_url(second, "2026-10-17")] = body
    _, found = sources.pin("2026-10-17")
    assert found[0].key[2] == second and found[0].skipped == (newest,)


def test_no_published_run_is_a_plain_error(offline) -> None:
    for run in ("2026-10-15T18:00", "2026-10-15T12:00", "2026-10-15T06:00"):
        offline.overrides[run_url(run, "2026-10-17")] = {"status": 400, "reason": "The requested model run is not available."}
    with pytest.raises(RuntimeError, match="has not published ECMWF IFS run 2026-10-15T18:00 UTC completely"):
        sources.pin("2026-10-17")


def test_rate_limits_are_retried_and_outages_reported(offline) -> None:
    from fakes import Turns

    url = next(url for url in index()["answers"] if "start_date=2026-07-06" in url)
    body = recorded(url)["body"]
    offline.overrides[url] = Turns({"status": 429, "reason": "Too many requests"}, body)
    _, found = sources.pin("2026-07-07")
    assert found and len([seen for seen in offline.urls if seen == url]) == 2
    sources.CACHE.clear()
    offline.overrides[url] = {"status": 503, "reason": "down"}
    with pytest.raises(RuntimeError, match=r"Open-Meteo did not answer \(HTTP 503 after 3 tries\)"):
        sources.read("2026-07-07")


def test_a_clock_far_off_open_meteos_is_refused(offline, monkeypatch) -> None:
    monkeypatch.setattr(sources, "CLOCK", lambda: T0 + timedelta(minutes=30))
    with pytest.raises(RuntimeError, match="clock is 30 min off Open-Meteo's"):
        sources.pin("2026-07-07")


def test_an_answer_for_the_wrong_number_of_places_is_refused(offline) -> None:
    url = next(url for url in index()["answers"] if "start_date=2026-07-06" in url)
    offline.overrides[url] = recorded(url)["body"][:29]
    with pytest.raises(RuntimeError, match="answered for 29 locations"):
        sources.pin("2026-07-07")


def test_every_request_names_its_model_and_asks_in_utc(offline) -> None:
    for scenario in index()["scenarios"]:
        for url in scenario["urls"]:
            assert "timezone=GMT" in url and "models=" in url and "best_match" not in url
            assert url.split("?")[0] in (openmeteo.ARCHIVE, openmeteo.SINGLE_RUN, openmeteo.SEASONAL)
