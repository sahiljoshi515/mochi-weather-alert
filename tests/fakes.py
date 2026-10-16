"""Open-Meteo and the clock, faked for offline tests: recorded answers replayed by URL, scripted
errors, and answers that change between the answer key's fetch and the agent's."""

from __future__ import annotations

import gzip
import io
import json
import urllib.error
from datetime import datetime
from email.message import Message
from email.utils import format_datetime
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "openmeteo"


def index() -> dict:
    return json.loads((FIXTURES / "index.json").read_text(encoding="utf-8"))


def recorded(url: str) -> dict:
    """The recorded answer to ``url``: {"status", "body"} (body as parsed JSON)."""
    entry = index()["answers"].get(url)
    if entry is None:
        raise KeyError(f"no recorded answer for {url}")
    body = json.loads(gzip.decompress((FIXTURES / entry["file"]).read_bytes()))
    return {"status": entry["status"], "body": body}


class Answer(io.BytesIO):
    def __init__(self, body: bytes, date: str) -> None:
        super().__init__(body)
        self.headers = Message()
        self.headers["Content-Encoding"] = "gzip"
        self.headers["Date"] = date

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class Turns:
    """Answers served in turn, the last one repeating: the answer key and the agent see different ones."""

    def __init__(self, *answers) -> None:
        self.answers = answers


class FakeOpenMeteo:
    """An opener: answers from recordings (or ``overrides``), with the Date header of ``clock()``.

    overrides: url -> a JSON body (a list of locations), {"status": code, "reason": text}, an exception
    to raise, or Turns(...) of those."""

    def __init__(self, clock, overrides: dict | None = None, fail_unknown: bool = True) -> None:
        self.clock = clock
        self.overrides = overrides or {}
        self.fail_unknown = fail_unknown
        self.urls: list[str] = []
        self.served: dict[str, int] = {}

    def answer_for(self, url: str):
        if url in self.overrides:
            found = self.overrides[url]
            if isinstance(found, Turns):
                turn = self.served.get(url, 0)
                self.served[url] = turn + 1
                return found.answers[min(turn, len(found.answers) - 1)]
            return found
        entry = recorded(url)
        if entry["status"] != 200:
            return {"status": entry["status"], "reason": entry["body"].get("reason", "")}
        return entry["body"]

    def open(self, request, timeout=None):
        url = request.full_url
        self.urls.append(url)
        found = self.answer_for(url)
        date = format_datetime(self.clock(), usegmt=True)
        if isinstance(found, dict) and "status" in found and "reason" in found:
            headers = Message()
            headers["Content-Encoding"] = "gzip"
            body = gzip.compress(json.dumps({"reason": found["reason"], "error": True}).encode())
            raise urllib.error.HTTPError(url, found["status"], found["reason"], headers, io.BytesIO(body))
        if isinstance(found, Exception):
            raise found
        return Answer(gzip.compress(json.dumps(found).encode()), date)


def frozen(instant: datetime):
    """A clock that always reads ``instant`` (aware, UTC)."""
    return lambda: instant
