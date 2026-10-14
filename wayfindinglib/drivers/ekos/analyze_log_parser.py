"""Purpose: Parse Ekos "analyze" session log files.

Description: Ekos writes one ``ekos-<date>.analyze`` file per session under
``~/.local/share/kstars/analyze/`` on the telescope computer. It is a
comma-separated text log with one record per line, each starting with a
record type (``GuideStats``, ``CaptureComplete``, ``MountCoords`` and so
on) followed by seconds since the file's ``AnalyzeStartTime`` line.

Two quirks of real files are handled here. A file that Ekos preallocated
can end in a run of NUL bytes, which are ignored. And the start time is
written in local time with a time-zone abbreviation (``MDT``), which is
turned into a fixed offset so daylight saving time cannot shift it.
"""

import logging
import os
from datetime import UTC, datetime, timedelta, timezone

from astrometricslib import observing_night_id
from wayfindinglib.models.session.ekos_session import (
    EkosAbortedCapture,
    EkosAutofocusRun,
    EkosCapture,
    EkosFocusSample,
    EkosGuideStat,
    EkosMountPosition,
    EkosSessionContext,
    EkosStateEvent,
    EkosTemperatureReading,
)

logger = logging.getLogger(__name__)

_UTC_OFFSET_HOURS_BY_ABBREVIATION = {
    "UTC": 0,
    "GMT": 0,
    "EST": -5,
    "EDT": -4,
    "CST": -6,
    "CDT": -5,
    "MST": -7,
    "MDT": -6,
    "PST": -8,
    "PDT": -7,
}
"""Offsets for the North American zone abbreviations Ekos writes."""

_PIER_SIDE_BY_CODE = {0: "West", 1: "East"}
"""INDI's pier-side codes: 0 is west, 1 is east; anything else is unknown."""

_NOT_MEASURED = -1.0
"""The value Ekos writes when a per-exposure measurement could not be made."""

_AUTOFOCUS_VALUES_PER_POINT = 4
"""Each autofocus point is four ``|``-separated values."""


def _start_time_to_epoch(start_text: str, zone_abbreviation: str) -> float:
    """Convert the ``AnalyzeStartTime`` fields to seconds since the Unix epoch.

    Parameters
    ----------
    start_text : `str`
        Local date and time, for example ``2026-09-23 20:31:48.442``.
    zone_abbreviation : `str`
        The time-zone abbreviation Ekos wrote, for example ``MDT``.

    Returns
    -------
    timestamp : `float`
        Seconds since the Unix epoch. An abbreviation this module does not
        know is read as the parsing machine's local time.
    """
    naive_start = datetime.strptime(start_text, "%Y-%m-%d %H:%M:%S.%f")
    offset_hours = _UTC_OFFSET_HOURS_BY_ABBREVIATION.get(zone_abbreviation.strip().upper())
    if offset_hours is None:
        logger.warning("Unknown time zone %r in Ekos log; using this machine's local time", zone_abbreviation)
        return naive_start.timestamp()
    return naive_start.replace(tzinfo=timezone(timedelta(hours=offset_hours))).astimezone(UTC).timestamp()


def _measured_or_none(value: float) -> float | None:
    """Drop Ekos's "not measured" marker.

    Returns
    -------
    value : `float` or `None`
        `value`, or `None` if Ekos wrote its "not measured" marker.
    """
    return None if value <= _NOT_MEASURED else value


def _parse_autofocus_run(fields: list[str], timestamp: float, succeeded: bool) -> EkosAutofocusRun:
    """Build an autofocus run from an ``Autofocus*`` record.

    The curve is written as repeated ``position|size|size|flag`` groups.
    For a finished run the last group is the chosen position, not a
    measurement, so it is split off.

    Returns
    -------
    run : `EkosAutofocusRun`
        The parsed run.
    """
    temperature_c = float(fields[2]) if len(fields) > 2 and fields[2] else None
    filter_name = fields[5] if len(fields) > 5 else ""
    curve_text = fields[6] if len(fields) > 6 else ""

    values = curve_text.split("|") if curve_text else []
    groups = [
        values[index : index + _AUTOFOCUS_VALUES_PER_POINT]
        for index in range(0, len(values) - _AUTOFOCUS_VALUES_PER_POINT + 1, _AUTOFOCUS_VALUES_PER_POINT)
    ]
    points = [
        EkosFocusSample(position=int(float(group[0])), half_flux_radius_px=_measured_or_none(float(group[1])))
        for group in groups
    ]
    final_position = None
    final_size = None
    if succeeded and points:
        chosen = points.pop()
        final_position = chosen.position
        final_size = chosen.half_flux_radius_px
    return EkosAutofocusRun(
        timestamp=timestamp,
        succeeded=succeeded,
        temperature_c=temperature_c,
        filter_name=filter_name,
        curve=points,
        final_position=final_position,
        final_half_flux_radius_px=final_size,
    )


def parse_ekos_analyze_log(file_path: str) -> tuple[EkosSessionContext, list[EkosGuideStat]] | None:
    """Read one Ekos analyze log.

    Parameters
    ----------
    file_path : `str`
        Path to an ``ekos-*.analyze`` file.

    Returns
    -------
    parsed : `tuple` [`EkosSessionContext`, `list` [`EkosGuideStat`]] or `None`
        The session context and the guiding measurements, or `None` if the
        file is missing or has no ``AnalyzeStartTime`` line to anchor its
        times to. Records that cannot be read are skipped.
    """
    if not os.path.isfile(file_path):
        return None
    with open(file_path, "rb") as log_file:
        text = log_file.read().decode("utf-8", errors="replace").replace("\x00", "")

    file_name = os.path.basename(file_path)
    started_at: float | None = None
    mount_positions: list[EkosMountPosition] = []
    temperatures: list[EkosTemperatureReading] = []
    captures: list[EkosCapture] = []
    aborted_captures: list[EkosAbortedCapture] = []
    autofocus_runs: list[EkosAutofocusRun] = []
    align_events: list[EkosStateEvent] = []
    guide_state_events: list[EkosStateEvent] = []
    mount_state_events: list[EkosStateEvent] = []
    guide_stats: list[EkosGuideStat] = []
    last_event_time: float | None = None

    for line in text.split("\n"):
        fields = [field.strip() for field in line.strip().split(",")]
        record_type = fields[0]
        if record_type == "AnalyzeStartTime" and len(fields) >= 3:
            try:
                started_at = _start_time_to_epoch(fields[1], fields[2])
            except ValueError:
                logger.warning("Unreadable AnalyzeStartTime in %s: %r", file_name, line)
            continue
        if started_at is None or len(fields) < 2:
            continue
        try:
            timestamp = started_at + float(fields[1])
        except ValueError:
            continue

        try:
            if record_type == "GuideStats" and len(fields) >= 9:
                guide_stats.append(
                    EkosGuideStat(
                        timestamp=timestamp,
                        ra_error_arcsec=float(fields[2]),
                        dec_error_arcsec=float(fields[3]),
                        ra_pulse=float(fields[4]),
                        dec_pulse=float(fields[5]),
                        snr=float(fields[6]),
                        sky_background=float(fields[7]),
                        star_count=int(float(fields[8])),
                    )
                )
            elif record_type == "MountCoords" and len(fields) >= 7:
                mount_positions.append(
                    EkosMountPosition(
                        timestamp=timestamp,
                        declination_degrees=float(fields[3]),
                        azimuth_degrees=float(fields[4]),
                        altitude_degrees=float(fields[5]),
                        pier_side=_PIER_SIDE_BY_CODE.get(int(float(fields[6]))),
                    )
                )
            elif record_type == "Temperature" and len(fields) >= 3:
                temperatures.append(
                    EkosTemperatureReading(timestamp=timestamp, temperature_c=float(fields[2]))
                )
            elif record_type == "CaptureComplete" and len(fields) >= 9:
                captures.append(
                    EkosCapture(
                        completed_at=timestamp,
                        exposure_seconds=float(fields[2]),
                        filter_name=fields[3],
                        half_flux_radius_px=_measured_or_none(float(fields[4])),
                        file_path=fields[5],
                        star_count=int(float(fields[6])),
                        median_adu=float(fields[7]),
                        eccentricity=_measured_or_none(float(fields[8])),
                    )
                )
            elif record_type == "CaptureAborted" and len(fields) >= 3:
                aborted_captures.append(
                    EkosAbortedCapture(timestamp=timestamp, exposure_seconds=float(fields[2]))
                )
            elif record_type in ("AutofocusComplete", "AutofocusAborted"):
                autofocus_runs.append(
                    _parse_autofocus_run(fields, timestamp, record_type == "AutofocusComplete")
                )
            elif record_type == "AlignState" and len(fields) >= 3:
                align_events.append(EkosStateEvent(timestamp=timestamp, state=fields[2]))
            elif record_type == "GuideState" and len(fields) >= 3:
                guide_state_events.append(EkosStateEvent(timestamp=timestamp, state=fields[2]))
            elif record_type == "MountState" and len(fields) >= 3:
                mount_state_events.append(EkosStateEvent(timestamp=timestamp, state=fields[2]))
            else:
                continue
        except ValueError, IndexError:
            logger.debug("Skipping unreadable %s record in %s: %r", record_type, file_name, line)
            continue
        last_event_time = timestamp

    if started_at is None:
        return None

    context_id = file_name.removeprefix("ekos-").removesuffix(".analyze")
    context = EkosSessionContext(
        id=context_id,
        session_id=observing_night_id(started_at),
        started_at=started_at,
        ended_at=last_event_time if last_event_time is not None else started_at,
        source_file_name=file_name,
        mount_positions=mount_positions,
        temperatures=temperatures,
        captures=captures,
        aborted_captures=aborted_captures,
        autofocus_runs=autofocus_runs,
        align_events=align_events,
        guide_state_events=guide_state_events,
        mount_state_events=mount_state_events,
    )
    return context, guide_stats
