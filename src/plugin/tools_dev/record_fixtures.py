"""Record the Open-Meteo answers the offline tests replay: every request the answer key and the agent's
tools make for each scenario (a date at a frozen clock), stored as gzip files with an index under
tests/fixtures/openmeteo/. Answers already recorded are reused, so a re-run fetches only new ones.

    uv run python src/plugin/tools_dev/record_fixtures.py

Each location in a request counts as one Open-Meteo call (30 per request); a full recording is about
26 requests. The clock skew check is off here: the scenarios' clocks are in the past on purpose.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from email.message import Message
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from plugin import openmeteo, sources

FIXTURES = ROOT / "tests" / "fixtures" / "openmeteo"
PAUSE = 8  # seconds between requests: 30 locations each, under Open-Meteo's per-minute limit
T0 = "2026-10-16T04:00:00+00:00"  # the offline tests' frozen clock: today is 2026-10-16 (UTC)
ARCHIVE = [
    "1940-01-01", "1940-06-01", "1943-06-01", "2000-02-29", "2016-12-31", "2017-01-01", "2017-01-02", "2024-02-29",
    "2026-03-29", "2026-04-05", "2026-07-07", "2026-09-06", "2026-09-27", "2026-10-04", "2026-10-14",
]  # fmt: skip
FORECAST = ["2026-10-15", "2026-10-16", "2026-10-17", "2026-10-25", "2026-10-29"]
OUTLOOK = ["2026-10-30", "2026-11-01", "2027-03-14", "2027-04-04", "2027-04-30"]
SCENARIOS = [
    *[{"date": date, "clock": T0, "about": "archive"} for date in ARCHIVE],
    *[{"date": date, "clock": T0, "about": "forecast"} for date in FORECAST],
    *[{"date": date, "clock": T0, "about": "outlook"} for date in OUTLOOK],
    {"date": "2026-10-14", "clock": "2026-10-16T00:05:00+00:00", "about": "midnight: the forecast 10 min before, the archive at the clock"},
    {"date": "2026-10-16", "clock": "2026-10-15T15:05:00+00:00", "about": "a newer run counts as published between the two readings"},
    {"date": "2027-04-15", "clock": "2026-10-07T00:05:00+00:00", "about": "the outlook gains a month on the 7th: refused 10 min before"},
]


class Recorder:
    """An opener that fetches each URL once from Open-Meteo, keeps the answer, and replays it."""

    def __init__(self, answers: dict) -> None:
        self.answers = answers  # url -> {"status", "body"}
        self.fetched = 0
        self.requested: list[str] = []

    def open(self, request, timeout=None):
        url = request.full_url
        self.requested.append(url)
        for attempt in range(6):
            if url in self.answers:
                break
            real = urllib.request.Request(url, headers={"User-Agent": openmeteo.USER_AGENT, "Accept-Encoding": "gzip"})
            try:
                with urllib.request.urlopen(real, timeout=60) as answer:
                    body = openmeteo.decoded(answer.read(), answer.headers.get("Content-Encoding"))
                    found = {"status": 200, "body": json.loads(body)}
            except urllib.error.HTTPError as error:
                body = openmeteo.decoded(error.read() or b"", error.headers.get("Content-Encoding") if error.headers else None)
                found = {"status": error.code, "body": json.loads(body)}
            print(f"  fetched {found['status']} {url[:110]}…", flush=True)
            if found["status"] in openmeteo.RETRIED:  # never record a rate limit or an outage: wait it out
                time.sleep(PAUSE * (attempt + 1))
                continue
            self.answers[url] = found
            self.fetched += 1
            time.sleep(PAUSE)
        found = self.answers[url]
        headers = Message()
        headers["Content-Encoding"] = "gzip"
        body = gzip.compress(json.dumps(found["body"]).encode())
        if found["status"] != 200:
            raise urllib.error.HTTPError(url, found["status"], "recorded", headers, io.BytesIO(body))
        return Wrapped(body, headers)


class Wrapped(io.BytesIO):
    def __init__(self, body: bytes, headers: Message) -> None:
        super().__init__(body)
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def load() -> tuple[dict, dict]:
    path = FIXTURES / "index.json"
    if not path.is_file():
        return {}, {}
    index = json.loads(path.read_text(encoding="utf-8"))
    answers = {}
    for url, entry in index["answers"].items():
        answers[url] = {"status": entry["status"], "body": json.loads(gzip.decompress((FIXTURES / entry["file"]).read_bytes()))}
    return index, answers


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    _, answers = load()
    recorder = Recorder(answers)
    openmeteo.OPENER = recorder
    sources.SKEW_LIMIT = timedelta(days=100000)
    scenarios = []
    for scenario in SCENARIOS:
        clock = datetime.fromisoformat(scenario["clock"])
        sources.CLOCK = lambda clock=clock: clock
        sources.CACHE.clear()
        recorder.requested = []
        outcome = {}
        try:
            _, found = sources.pin(scenario["date"])
            outcome["pinned"] = [list(candidate.key) for candidate in found]
        except (ValueError, RuntimeError) as error:
            outcome["pinned_error"] = str(error)
        try:
            outcome["read"] = list(sources.read(scenario["date"]).key)
        except (ValueError, RuntimeError) as error:
            outcome["read_error"] = str(error)
        scenarios.append({**scenario, **outcome, "urls": sorted(set(recorder.requested))})
        print(scenario["date"], scenario["clock"], json.dumps(outcome)[:200], flush=True)
    entries = {}
    for url, found in sorted(answers.items()):
        name = hashlib.sha256(url.encode()).hexdigest()[:20] + ".json.gz"
        data = json.dumps(found["body"], separators=(",", ":"), sort_keys=True).encode()
        (FIXTURES / name).write_bytes(gzip.compress(data, mtime=0))
        entries[url] = {"file": name, "status": found["status"]}
    index = {"clock": T0, "scenarios": scenarios, "answers": entries}
    (FIXTURES / "index.json").write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")
    size = sum((FIXTURES / entry["file"]).stat().st_size for entry in entries.values())
    print(f"{len(entries)} answers ({recorder.fetched} fetched now), {size // 1024} KB")


if __name__ == "__main__":
    main()
