"""Purpose: Replay a real guide log, line by line, at the pace it was recorded.

Description: The simulated observatory profile of the TPM run needs guiding
that looks real. This module reads a guide log written by Ekos's internal
guider (the PHD2 log format) and turns it into a list of items. Each item is
one log line, the time to wait before it, and, for a guide step, the PHD2
event that stands for it. Two other scripts replay the items:

- `mock_phd2.py` sends the events to the app, as PHD2's event server does.
- `exposure_driver.py` appends the lines to a growing log file, as the
  guider does on the telescope computer.

Both play the same items at the same pace, so the live guiding the app sees
and the log files it pulls every few minutes agree.

The replay is the same every run: the log is a fixed file, and only the
dates in its headers change (they are set to the time of the replay, so the
app files the samples under the current night).

Safety: this module only reads one file and builds text. It opens no network
connection.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

#: The longest pause the replay keeps between two guide sections. The real log
#: has gaps of a minute or more (calibration, dithering). A run lasts only a
#: few
#: minutes, so a long gap would leave the replay silent. Design estimate.
GAP_CAP_SECONDS = 10.0

#: One guide step row of the log: ``frame,time,"Mount",dx,dy,...``.
_STEP_ROW = re.compile(r'^(\d+),([\d.]+),"([^"]*)",(.*)$')
#: The date in a header line such as ``Guiding Begins at 2026-10-03 20:37:12``.
_HEADER_DATE = re.compile(r"((?:Begins|Ends|enabled) at )(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
#: The dither line of the log, for example
#: ``INFO: DITHER by 1.211, -0.886, new lock pos = ...``.
_DITHER_LINE = re.compile(r"DITHER by (-?[\d.]+), (-?[\d.]+)")

_DIRECTION_NAMES = {"E": "East", "W": "West", "N": "North", "S": "South"}


@dataclass(frozen=True)
class ReplayItem:
    """One line of the log and how to replay it.

    Attributes
    ----------
    delay_seconds : `float`
        How long to wait before this line.
    line : `str`
        The log line, without its newline.
    event : `dict` or `None`
        The PHD2 event that stands for the line (a GuideStep,
        GuidingDithered, SettleBegin or SettleDone), or `None` for a line
        with no event.
    """

    delay_seconds: float
    line: str
    event: dict | None


def _guide_step_event(frame: int, seconds: float, mount: str, rest: str) -> dict | None:
    """Build the PHD2 GuideStep event for one log row.

    Parameters
    ----------
    frame : `int`
        The frame number in the log.
    seconds : `float`
        The time column: seconds since the guide section began.
    mount : `str`
        The mount column.
    rest : `str`
        The remaining comma-separated columns after the mount.

    Returns
    -------
    event : `dict` or `None`
        The GuideStep event, or `None` if the row has too few columns.
    """
    fields = rest.split(",")
    if len(fields) < 15:
        return None
    try:
        event = {
            "Event": "GuideStep",
            "Frame": frame,
            "Time": seconds,
            "Mount": mount,
            "dx": float(fields[0]),
            "dy": float(fields[1]),
            "RADistanceRaw": float(fields[2]),
            "DECDistanceRaw": float(fields[3]),
            "RADistanceGuide": float(fields[4]),
            "DECDistanceGuide": float(fields[5]),
            "StarMass": float(fields[12]),
            "SNR": float(fields[13]),
        }
        # Real PHD2 leaves out the duration and direction of an axis it did
        # not pulse (see `fixtures/phd2_contract_events.jsonl`).
        for axis, duration_column, direction_column in (("RA", 6, 7), ("DEC", 8, 9)):
            duration = float(fields[duration_column] or 0)
            if duration:
                event[f"{axis}Duration"] = duration
                event[f"{axis}Direction"] = _DIRECTION_NAMES.get(
                    fields[direction_column], fields[direction_column]
                )
        # Real PHD2 adds ErrorCode only when it is not zero.
        error_code = int(fields[14] or 0)
        if error_code:
            event["ErrorCode"] = error_code
        return event
    except ValueError:
        return None


def _star_lost_event(frame: int, seconds: float, fields: list[str]) -> dict:
    """Build the PHD2 StarLost event for a guide row the guider dropped.

    Parameters
    ----------
    frame : `int`
        The frame number in the log.
    seconds : `float`
        The time column of the row.
    fields : `list` [`str`]
        The columns after the mount column (at least 15).

    Returns
    -------
    event : `dict`
        The StarLost event: PHD2 sends this instead of a GuideStep when it
        cannot find the guide star.
    """
    return {
        "Event": "StarLost",
        "Frame": frame,
        "Time": seconds,
        "StarMass": float(fields[12] or 0),
        "SNR": float(fields[13] or 0),
        "AvgDist": 0.0,
        "ErrorCode": int(fields[14] or 0),
        "Status": "Star lost",
    }


def row_event(raw_line: str) -> dict | None:
    """Return the PHD2 event that stands for one log line, if it has one.

    Parameters
    ----------
    raw_line : `str`
        One line of the guide log.

    Returns
    -------
    event : `dict` or `None`
        A GuideStep for a guide row, a StarLost for a row the guider dropped
        (mount column ``DROP`` and an error code), a GuidingDithered for a
        dither line, a SettleBegin or a SettleDone for a settling line,
        else `None`.
    """
    match = _STEP_ROW.match(raw_line)
    if match:
        fields = match.group(4).split(",")
        if match.group(3) == "DROP" and len(fields) >= 15:
            try:
                return _star_lost_event(int(match.group(1)), float(match.group(2)), fields)
            except ValueError:
                return None
        return _guide_step_event(int(match.group(1)), float(match.group(2)), match.group(3), match.group(4))
    dither_match = _DITHER_LINE.search(raw_line)
    if dither_match:
        return {
            "Event": "GuidingDithered",
            "dx": float(dither_match.group(1)),
            "dy": float(dither_match.group(2)),
        }
    if "Settling started" in raw_line:
        return {"Event": "SettleBegin"}
    if "Settling complete" in raw_line:
        # Real PHD2 sends "Error" only when Status is not zero.
        return {"Event": "SettleDone", "Status": 0}
    return None


def timed_lines(path: Path) -> list[tuple[datetime, str, dict | None]]:
    """Read a guide log and give every line its clock time.

    A guide row's time is its section's start plus its time column. A header
    with a date ("Begins at", "Ends at", "enabled at") has that date. Any
    other line (calibration notes, dither and settle notes) takes the time of
    the line before it. The times are the log's own clock (local time).

    Parameters
    ----------
    path : `pathlib.Path`
        The guide log file.

    Returns
    -------
    lines : `list` [`tuple`]
        ``(time, line, event)`` for every line in file order. `event` is from
        `row_event`.
    """
    result: list[tuple[datetime, str, dict | None]] = []
    section_start: datetime | None = None
    current: datetime | None = None
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        date_match = _HEADER_DATE.search(raw_line)
        step_match = _STEP_ROW.match(raw_line)
        if date_match:
            current = datetime.strptime(date_match.group(2), _DATE_FORMAT)
            if "Begins at" in raw_line:
                section_start = current
        elif step_match and section_start is not None:
            current = section_start + timedelta(seconds=float(step_match.group(2)))
        if current is None:
            current = datetime.min
        result.append((current, raw_line, row_event(raw_line)))
    return result


def load_replay_items(path: Path) -> list[ReplayItem]:
    """Read a guide log into replay items.

    Guide step rows wait for the time between two rows. A "Guiding Begins"
    header waits for the real gap since the previous section ended, capped at
    `GAP_CAP_SECONDS`. Every other line (headers, calibration, dither notes)
    waits nothing. "Settling started" and "Settling complete" lines carry a
    SettleBegin and a SettleDone event.

    Parameters
    ----------
    path : `pathlib.Path`
        The guide log file.

    Returns
    -------
    items : `list` [`ReplayItem`]
        The lines in file order.
    """
    items: list[ReplayItem] = []
    previous_seconds = 0.0
    previous_section_end: datetime | None = None
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        delay = 0.0
        event: dict | None = None
        match = _STEP_ROW.match(raw_line)
        date_match = _HEADER_DATE.search(raw_line)
        if match:
            seconds = float(match.group(2))
            delay = max(0.0, seconds - previous_seconds)
            previous_seconds = seconds
            event = row_event(raw_line)
        elif (
            "Settling started" in raw_line or "Settling complete" in raw_line or _DITHER_LINE.search(raw_line)
        ):
            event = row_event(raw_line)
        elif date_match:
            when = datetime.strptime(date_match.group(2), _DATE_FORMAT)
            if "Begins at" in raw_line:
                previous_seconds = 0.0
                if previous_section_end is not None:
                    delay = min(GAP_CAP_SECONDS, max(0.0, (when - previous_section_end).total_seconds()))
            elif "Ends at" in raw_line:
                previous_section_end = when
        items.append(ReplayItem(delay_seconds=delay, line=raw_line, event=event))
    return items


def render_line(item: ReplayItem, now: datetime) -> str:
    """Return the log line with its header date set to `now`.

    Parameters
    ----------
    item : `ReplayItem`
        The item to write.
    now : `datetime.datetime`
        The time to put in a "Begins at", "Ends at" or "enabled at" header.

    Returns
    -------
    line : `str`
        The line, unchanged unless it has such a header.
    """
    return _HEADER_DATE.sub(lambda match: match.group(1) + now.strftime(_DATE_FORMAT), item.line)


def event_json_line(event: dict, timestamp: float) -> bytes:
    """Encode one PHD2 event as a line of JSON with a time stamp.

    Parameters
    ----------
    event : `dict`
        The event.
    timestamp : `float`
        Seconds since the epoch, as PHD2 puts in every event.

    Returns
    -------
    line : `bytes`
        The JSON text and a newline, the shape PHD2's event server sends.
    """
    return (json.dumps({**event, "Timestamp": timestamp, "Host": "tpm-sandbox", "Inst": 1}) + "\n").encode()


def replay(
    items: list[ReplayItem],
    handle: Callable[[ReplayItem], None],
    should_stop: Callable[[], bool],
    sleep: Callable[[float], None],
    loop: bool = True,
) -> None:
    """Hand each item to `handle` after its delay, until the end or a stop.

    Parameters
    ----------
    items : `list` [`ReplayItem`]
        The items from `load_replay_items`.
    handle : callable
        Called with each item once its delay has passed.
    should_stop : callable
        Returns `True` when the replay should end.
    sleep : callable
        Waits for the given seconds. A test passes a fast stand-in.
    loop : `bool`, optional
        Start again from the first item after the last one (default).
    """
    while items and not should_stop():
        for item in items:
            if should_stop():
                return
            if item.delay_seconds > 0:
                sleep(item.delay_seconds)
            handle(item)
        if not loop:
            return
