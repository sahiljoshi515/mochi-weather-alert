"""Each city's local calendar day as a window of UTC minutes, from the pinned tzdata package.

The host's own time zone database never matters: the tz path is emptied, so every ZoneInfo comes from
the `tzdata` wheel this plugin locks, the same on macOS, Linux and WSL.
"""

from __future__ import annotations

import zoneinfo
from datetime import UTC, datetime, timedelta
from functools import cache

from plugin.rules import DATE

zoneinfo.reset_tzpath(to=[])


@cache
def zone(name: str) -> zoneinfo.ZoneInfo:
    return zoneinfo.ZoneInfo(name)


def local_midnight(name: str, year: int, month: int, day: int) -> datetime:
    """The first instant of a local calendar day, in UTC: local midnight, or the instant clocks jump
    past it when midnight does not exist that day (fold 0 takes the offset before the change)."""
    return datetime(year, month, day, tzinfo=zone(name)).astimezone(UTC)


def offset_minutes(name: str, instant: datetime) -> int:
    """The zone's UTC offset at ``instant``, in whole minutes."""
    offset = instant.astimezone(zone(name)).utcoffset()
    seconds = int(offset.total_seconds())
    if seconds % 60:
        raise ValueError(f"{name} is {offset} from UTC on {instant:%Y-%m-%d}: not a whole minute")
    return seconds // 60


def epoch_minutes(instant: datetime) -> int:
    seconds = int((instant - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds())
    if seconds % 60:
        raise ValueError(f"{instant} is not on a whole minute")
    return seconds // 60


def window(name: str, date: str) -> tuple[int, int, int, int]:
    """(start, end, start offset, end offset) of the local day ``date`` in zone ``name``: start and end
    in UTC minutes since 1970, the offsets in minutes east of UTC at those instants."""
    year, month, day = (int(part) for part in DATE.fullmatch(date).groups())
    start = local_midnight(name, year, month, day)
    following = datetime(year, month, day) + timedelta(days=1)
    end = local_midnight(name, following.year, following.month, following.day)
    return epoch_minutes(start), epoch_minutes(end), offset_minutes(name, start), offset_minutes(name, end)
