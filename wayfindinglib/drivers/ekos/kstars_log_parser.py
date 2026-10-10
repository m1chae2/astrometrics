"""Purpose: Read dither events from a KStars text log.

Description: With logging on in Ekos, KStars writes one ``log_<time>.txt``
per run on the telescope computer. Each line starts with a local time and
a time-zone abbreviation, for example
``[2026-10-02T21:35:25.235 MDT INFO ][ org.kde.kstars.ekos.guide] - "..."``.
The guider logs each dither (a small deliberate move of the guide star
between exposures) in a fixed sequence of messages. This module turns that
sequence into one `DitherEvent` per dither, recording whether it worked and
how long the guider waited afterwards.

The messages it reads are:

1. ``Dithering by 2.5 pixels.`` starts the dither.
2. ``Warning: Dithering failed.`` means the guide star never reached its new
   position within the allowed tries. Guiding carries on anyway.
3. ``Post-dither settling for 30 seconds...`` is the wait before the next
   exposure may start.
4. ``Dithering completed successfully.`` ends the dither, whether or not
   step 2 happened.
"""

import logging
import os
import re
from datetime import UTC, datetime, timedelta, timezone

from wayfindinglib.drivers.ekos.analyze_log_parser import _UTC_OFFSET_HOURS_BY_ABBREVIATION
from wayfindinglib.models.session.live_session_status import DitherEvent

logger = logging.getLogger(__name__)

_LINE_PATTERN = re.compile(r"^\[(?P<time>\d{4}-\d{2}-\d{2}T[\d:.]+) (?P<zone>[A-Z]{2,4}) [A-Z]+ ?\]")
"""The start of a KStars log line: local time, then a zone abbreviation."""

_DITHER_START_PATTERN = re.compile(r"Dithering by (?P<amplitude>[\d.]+) pixels")
_SETTLE_PATTERN = re.compile(r"Post-dither settling for (?P<seconds>[\d.]+) seconds")


def _line_to_epoch(time_text: str, zone_abbreviation: str) -> float | None:
    """Convert a log line's local time to seconds since the Unix epoch.

    Parameters
    ----------
    time_text : `str`
        The ISO-style local time, for example ``2026-10-02T21:35:25.235``.
    zone_abbreviation : `str`
        The zone abbreviation that followed it, for example ``MDT``.

    Returns
    -------
    epoch_seconds : `float` or `None`
        The instant in UTC seconds, or `None` if the zone or time is
        unreadable.
    """
    offset_hours = _UTC_OFFSET_HOURS_BY_ABBREVIATION.get(zone_abbreviation)
    if offset_hours is None:
        return None
    try:
        local_time = datetime.fromisoformat(time_text)
    except ValueError:
        return None
    return local_time.replace(tzinfo=timezone(timedelta(hours=offset_hours))).astimezone(UTC).timestamp()


def parse_dither_events(file_path: str) -> list[DitherEvent]:
    """Read every dither from one KStars text log.

    Parameters
    ----------
    file_path : `str`
        Path to a ``log_*.txt`` file.

    Returns
    -------
    events : `list` [`DitherEvent`]
        One event per dither, in time order. Empty if the file is missing
        or holds no dithers. A dither the log cuts off (the file ends
        before its last message) keeps `succeeded=None`.
    """
    if not os.path.isfile(file_path):
        return []
    events: list[DitherEvent] = []
    current: DitherEvent | None = None
    with open(file_path, "rb") as log_file:
        lines = log_file.read().decode("utf-8", errors="replace").replace("\x00", "").split("\n")

    for line in lines:
        if "org.kde.kstars.ekos.guide" not in line:
            continue
        header = _LINE_PATTERN.match(line)
        if header is None:
            continue
        timestamp = _line_to_epoch(header.group("time"), header.group("zone"))
        if timestamp is None:
            continue

        start_match = _DITHER_START_PATTERN.search(line)
        if start_match is not None:
            current = DitherEvent(timestamp=timestamp, amplitude_px=float(start_match.group("amplitude")))
            events.append(current)
        elif current is None:
            continue
        elif "Dithering failed" in line:
            current.succeeded = False
        elif (settle_match := _SETTLE_PATTERN.search(line)) is not None:
            current.settle_seconds = float(settle_match.group("seconds"))
        elif "Dithering completed successfully" in line:
            if current.succeeded is None:
                current.succeeded = True
            current.duration_seconds = timestamp - current.timestamp
            current = None
    return events
