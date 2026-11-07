"""Purpose: Read the Ekos session logs without the library's driver code.

Description: Ekos writes an analyze log (``ekos-*.analyze``) for each
session. These two functions read the parts a script usually wants: which
frames the session saved and when, and how Ekos names its time zones. They
only read files. The full session record, with its guiding, focus and
temperature readings, is private to the library; read it through
`ObservationReview.query` (``kind="ekos_sessions"``) once the log has been
ingested.
"""

from dataclasses import dataclass
from pathlib import Path

from wayfindinglib.drivers.ekos.analyze_log_parser import (
    UTC_OFFSET_HOURS_BY_ABBREVIATION,
    parse_ekos_analyze_log,
)

__all__ = [
    "SavedCapture",
    "ekos_utc_offset_hours",
    "read_ekos_captures",
]


@dataclass(frozen=True)
class SavedCapture:
    """One exposure that Ekos finished and saved.

    Attributes
    ----------
    file_path : `str`
        Where Ekos saved the frame on the telescope computer.
    completed_at : `float`
        When the exposure finished, in seconds since the Unix epoch (UTC).
    exposure_seconds : `float`
        Exposure length, in seconds.
    """

    file_path: str
    completed_at: float
    exposure_seconds: float


def read_ekos_captures(file_path: str | Path) -> list[SavedCapture] | None:
    """Read the frames an Ekos analyze log says were saved.

    Parameters
    ----------
    file_path : `str` or `pathlib.Path`
        Path to an ``ekos-*.analyze`` file.

    Returns
    -------
    captures : `list` [`SavedCapture`] or `None`
        The finished exposures in file order, or `None` when the file is
        missing or has no ``AnalyzeStartTime`` line to anchor its times to.
        Records that cannot be read are skipped.
    """
    parsed = parse_ekos_analyze_log(str(file_path))
    if parsed is None:
        return None
    return [
        SavedCapture(
            file_path=capture.file_path,
            completed_at=capture.completed_at,
            exposure_seconds=capture.exposure_seconds,
        )
        for capture in parsed[0].captures
    ]


def ekos_utc_offset_hours(zone_abbreviation: str) -> int | None:
    """Give the offset from UTC of a time zone name that Ekos writes.

    Ekos names the zone of its local times with a North American
    abbreviation such as ``"MST"``.

    Parameters
    ----------
    zone_abbreviation : `str`
        The abbreviation, in any letter case.

    Returns
    -------
    hours : `int` or `None`
        Hours to add to UTC to get local time (negative west of Greenwich),
        or `None` for a name this library does not know.
    """
    return UTC_OFFSET_HOURS_BY_ABBREVIATION.get(zone_abbreviation.strip().upper())
