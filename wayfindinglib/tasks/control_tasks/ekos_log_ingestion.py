"""Purpose: Ekos session log ingestion.

Description: The download, parse and store chain for the logs Ekos writes on
the telescope computer. Two kinds of file are read. The guide logs
(``guide_log-*.txt``, PHD2 format) give the guiding error frame by frame;
they are stored as guiding samples. The analyze logs (``ekos-*.analyze``)
give the rest of a session (pointing, temperature, exposures, autofocus,
plate-solve and guider states); they are stored as session records.

Ekos is the incumbent control software, still in charge of the hardware
(Delegated phase, `Wayfinding_Library_Architecture.md` §2.1.3). This module
only reads what it recorded. It issues no commands and computes no
corrections.

Reading is safe to repeat. Guiding samples are replaced rather than added,
and a session record is keyed by its file name, so running the ingestion
twice leaves the same data as running it once.

Each session is also tied to the equipment it used, worked out from the
session's own data (the guide log's focal length and pixel scale, and the
frames captured in the same hours). That identity is what later lets
measurements from different equipment be kept apart.
"""

from __future__ import annotations

import bisect
import collections
import logging
import os
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from astrometricslib import AstrometricsError, observing_night_id
from wayfindinglib.drivers.ekos.analyze_log_parser import parse_ekos_analyze_log
from wayfindinglib.drivers.phd2.guide_log_parser import parse_guide_log_file
from wayfindinglib.models.equipment_and_site.equipment_fingerprint import build_equipment_fingerprint
from wayfindinglib.models.session.ekos_session import EkosSessionContext, SessionEquipmentAttribution
from wayfindinglib.models.session.guide_log import GuidingSection
from wayfindinglib.models.session.guiding_run import GuidingRunSummary

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext

logger = logging.getLogger(__name__)

GUIDE_SCALE_AGREEMENT_FRACTION = 0.02
"""Two guide plate scales agree if they differ by no more than this fraction.

The scale in a log is rounded to two decimals (6.39), and the configured
one is computed from pixel size and focal length, so they differ slightly
even when everything is right. 2 percent is well above that rounding (0.2
percent at 6.39) and well below a real change of optics.
"""

SESSION_TIME_MARGIN_SECONDS = 1800.0
"""How far outside a session's own time span a log or frame still counts.

Guiding sometimes starts a few minutes before the first entry of the
analyze log and frames are stamped at the start of exposure, so a margin of
half an hour avoids missing either without reaching a different night.
"""

FrameLookup = Callable[[float, float], list[tuple[str | None, str | None]]]
"""Given a start and end time, return each frame's (telescope, camera)."""


@dataclass
class EkosLogIngestionSummary:
    """What one ingestion run read and stored.

    Attributes
    ----------
    guide_log_files_read : `int`
        Guide log files that were parsed.
    guide_samples_stored : `int`
        Guiding samples written to the guiding log.
    guide_frames_with_error_code : `int`
        Guide frames the logs marked as failed (for example a lost star).
        They are not stored as samples; they are counted here so a night
        with many lost frames is visible.
    guide_frames_without_pixel_scale : `int`
        Guide frames left out because no pixel scale was known.
    guiding_runs_stored : `int`
        Guiding runs recorded, one per continuous stretch of guiding.
    guide_log_files_with_no_samples : `list` [`str`]
        Names of guide logs that yielded nothing (for example a log that
        holds only a calibration).
    analyze_files_read : `int`
        Analyze files that were parsed.
    session_contexts_stored : `int`
        Session records written.
    analyze_files_skipped : `list` [`str`]
        Names of analyze files that could not be read (empty or corrupt).
    nights : `list` [`str`]
        The observing nights the stored data belongs to, sorted.
    """

    guide_log_files_read: int = 0
    guide_samples_stored: int = 0
    guide_frames_with_error_code: int = 0
    guide_frames_without_pixel_scale: int = 0
    guiding_runs_stored: int = 0
    guide_log_files_with_no_samples: list[str] = field(default_factory=list)
    analyze_files_read: int = 0
    session_contexts_stored: int = 0
    analyze_files_skipped: list[str] = field(default_factory=list)
    nights: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return the summary as plain data.

        Returns
        -------
        summary : `dict` [`str`, `Any`]
            Every field of the summary, ready to serialise.
        """
        return asdict(self)


def _summarize_run(
    section: GuidingSection, run_index: int, file_name: str, written_by_ekos: bool
) -> GuidingRunSummary:
    """Reduce one guiding section to its run summary.

    Returns
    -------
    run : `GuidingRunSummary`
        The facts about the run that the samples alone cannot show.
    """
    return GuidingRunSummary(
        id=f"{file_name}#{run_index}",
        session_id=observing_night_id(section.started_at),
        source_file_name=file_name,
        written_by_ekos=written_by_ekos,
        started_at=section.started_at,
        ended_at=section.ended_at,
        pixel_scale_arcsec_per_px=section.pixel_scale_arcsec_per_px,
        focal_length_mm=section.focal_length_mm,
        ra_rate_arcsec_per_second=section.ra_rate_arcsec_per_second,
        dec_rate_arcsec_per_second=section.dec_rate_arcsec_per_second,
        frames_total=section.frames_total,
        frames_lost=section.frames_with_error_code,
        frames_without_pixel_scale=section.frames_without_pixel_scale,
        samples_stored=len(section.samples),
        declination_degrees=section.declination_degrees,
        altitude_degrees=section.altitude_degrees,
        azimuth_degrees=section.azimuth_degrees,
        pier_side=section.pier_side,
    )


def attribute_session_equipment(
    context: EkosSessionContext,
    guide_sections: list[GuidingSection],
    configured_guide_pixel_scale_arcsec_per_px: float | None,
    frame_lookup: FrameLookup | None,
) -> SessionEquipmentAttribution:
    """Work out which equipment a session used, from the session's own data.

    Parameters
    ----------
    context : `EkosSessionContext`
        The session to attribute.
    guide_sections : `list` [`GuidingSection`]
        Every guiding section read in this run. The ones that fall inside
        the session's time span give the guide scope's focal length and the
        guide camera's pixel scale.
    configured_guide_pixel_scale_arcsec_per_px : `float` or `None`
        The plate scale the configuration gives for the active guide
        equipment, used only to flag a disagreement with the log.
    frame_lookup : `FrameLookup` or `None`
        Finds the library frames taken in a time span, which name the
        imaging telescope and camera. `None` leaves those unknown.

    Returns
    -------
    attribution : `SessionEquipmentAttribution`
        The session's equipment and its fingerprint. A part that could not
        be determined is left `None` and shows as ``unknown`` in the
        fingerprint, so such a session is never pooled with a known setup.
    """
    window_start = context.started_at - SESSION_TIME_MARGIN_SECONDS
    window_end = context.ended_at + SESSION_TIME_MARGIN_SECONDS

    guide_optics: collections.Counter[tuple[float, float]] = collections.Counter()
    for section in guide_sections:
        overlaps = (
            section.started_at <= window_end and (section.ended_at or section.started_at) >= window_start
        )
        if overlaps and section.focal_length_mm is not None and section.pixel_scale_arcsec_per_px is not None:
            guide_optics[section.focal_length_mm, section.pixel_scale_arcsec_per_px] += max(
                len(section.samples), 1
            )
    guide_focal_length_mm = guide_pixel_scale = None
    if guide_optics:
        (guide_focal_length_mm, guide_pixel_scale), _ = guide_optics.most_common(1)[0]

    imaging_telescope_name = imaging_camera_name = None
    frames_matched = 0
    if frame_lookup is not None:
        gear = [pair for pair in frame_lookup(window_start, window_end) if pair != (None, None)]
        frames_matched = len(gear)
        if gear:
            (imaging_telescope_name, imaging_camera_name), _ = collections.Counter(gear).most_common(1)[0]

    matches_configuration = None
    if guide_pixel_scale is not None and configured_guide_pixel_scale_arcsec_per_px:
        relative_difference = (
            abs(guide_pixel_scale - configured_guide_pixel_scale_arcsec_per_px)
            / configured_guide_pixel_scale_arcsec_per_px
        )
        matches_configuration = relative_difference <= GUIDE_SCALE_AGREEMENT_FRACTION

    return SessionEquipmentAttribution(
        guide_focal_length_mm=guide_focal_length_mm,
        guide_pixel_scale_arcsec_per_px=guide_pixel_scale,
        configured_guide_pixel_scale_arcsec_per_px=configured_guide_pixel_scale_arcsec_per_px,
        guide_scale_matches_configuration=matches_configuration,
        imaging_telescope_name=imaging_telescope_name,
        imaging_camera_name=imaging_camera_name,
        imaging_frames_matched=frames_matched,
        equipment_fingerprint=build_equipment_fingerprint(
            imaging_telescope_name, imaging_camera_name, guide_focal_length_mm, guide_pixel_scale
        ),
    )


def build_frame_lookup(astrometrics: Any) -> FrameLookup:
    """Build a frame lookup from the science library's target records.

    Parameters
    ----------
    astrometrics : `Any`
        The science library's high-level interface (``Astrometrics``).

    Returns
    -------
    frame_lookup : `FrameLookup`
        Finds the (telescope, camera) of every light frame taken in a time
        span. The frames are read once and indexed by time, so each lookup
        is fast.
    """
    frames = sorted(
        (frame.timestamp, frame.telescope, frame.camera)
        for target in astrometrics.targets.list()
        for frame in target.frames
        if frame.timestamp is not None and str(frame.role).upper().endswith("LIGHT")
    )
    timestamps = [frame[0] for frame in frames]

    def frame_lookup(start: float, end: float) -> list[tuple[str | None, str | None]]:
        """Return the (telescope, camera) of each light frame in a time span.

        Returns
        -------
        gear : `list` [`tuple`]
            One (telescope, camera) pair per frame between `start` and `end`.
        """
        first = bisect.bisect_left(timestamps, start)
        last = bisect.bisect_right(timestamps, end)
        return [(frame[1], frame[2]) for frame in frames[first:last]]

    return frame_lookup


def _sorted_paths(directory: str, prefixes: tuple[str, ...], suffix: str) -> list[str]:
    """List the files in `directory` with the given name prefixes and suffix.

    Returns
    -------
    paths : `list` [`str`]
        Matching files, in name order. Empty if `directory` does not exist.
    """
    if not os.path.isdir(directory):
        return []
    return sorted(
        os.path.join(directory, name)
        for name in os.listdir(directory)
        if name.startswith(prefixes) and name.endswith(suffix)
    )


def ingest_ekos_session_logs_from_directory(
    context: ControlContext,
    records: Any,
    directory: str,
    frame_lookup: FrameLookup | None = None,
) -> EkosLogIngestionSummary:
    """Read every Ekos log in a local directory and store what they hold.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the configured guide plate scale, used as a fallback and
        for comparison, and stores the session records.
    records : `ControlRecordStore`
        Stores the guiding samples.
    directory : `str`
        Folder holding ``guide_log*.txt``, ``PHD2_GuideLog_*.txt`` and
        ``ekos-*.analyze`` files.
    frame_lookup : `FrameLookup` or `None`, optional
        Finds the library frames taken in a time span, to name the imaging
        equipment. Without it the imaging equipment is left unknown.

    Returns
    -------
    summary : `EkosLogIngestionSummary`
        What was read and stored.
    """
    summary = EkosLogIngestionSummary()
    configured_guide_scale = context.guider_plate_scale_arcsec_per_px()
    nights: set[str] = set()
    all_sections: list[GuidingSection] = []

    for guide_log_path in _sorted_paths(directory, ("guide_log", "PHD2_GuideLog"), ".txt"):
        parsed_log = parse_guide_log_file(
            guide_log_path, fallback_pixel_scale_arcsec_per_px=configured_guide_scale
        )
        summary.guide_log_files_read += 1
        all_sections.extend(parsed_log.sections)
        samples = [sample for section in parsed_log.sections for sample in section.samples]
        summary.guide_frames_with_error_code += sum(s.frames_with_error_code for s in parsed_log.sections)
        summary.guide_frames_without_pixel_scale += sum(
            s.frames_without_pixel_scale for s in parsed_log.sections
        )
        for run_index, section in enumerate(parsed_log.sections):
            context.save_guiding_run(
                _summarize_run(
                    section, run_index, os.path.basename(guide_log_path), parsed_log.written_by_ekos
                )
            )
            summary.guiding_runs_stored += 1
        if not samples:
            summary.guide_log_files_with_no_samples.append(os.path.basename(guide_log_path))
            continue
        records.replace_guiding_samples(samples)
        summary.guide_samples_stored += len(samples)

    for analyze_path in _sorted_paths(directory, ("ekos-",), ".analyze"):
        parsed = parse_ekos_analyze_log(analyze_path)
        if parsed is None:
            summary.analyze_files_skipped.append(os.path.basename(analyze_path))
            continue
        record, _guide_stats = parsed
        summary.analyze_files_read += 1
        record.equipment = attribute_session_equipment(
            record, all_sections, configured_guide_scale, frame_lookup
        )
        context.save_ekos_session_context(record)
        summary.session_contexts_stored += 1
        nights.add(record.session_id)

    summary.nights = sorted(nights)
    return summary


def fetch_and_ingest_ekos_session_logs(
    context: ControlContext,
    records: Any,
    destination_dir: str,
    frame_lookup: FrameLookup | None = None,
) -> EkosLogIngestionSummary:
    """Download Ekos's logs from the telescope computer, then ingest them.

    Files already present and unchanged in `destination_dir` are not
    downloaded again. The recent KStars text logs are fetched too, into a
    ``kstars_logs`` folder, for reading by hand; they are not ingested. If
    the configured remote-transfer driver cannot fetch these logs, whatever
    is already in `destination_dir` is still ingested.

    Parameters
    ----------
    context : `ControlContext`
        Supplies `remote_transfer_driver` and the ingestion inputs.
    records : `ControlRecordStore`
        Stores the guiding samples.
    destination_dir : `str`
        Local folder to download into and read from.
    frame_lookup : `FrameLookup` or `None`, optional
        See `ingest_ekos_session_logs_from_directory`.

    Returns
    -------
    summary : `EkosLogIngestionSummary`
        What was read and stored.
    """
    driver = context.remote_transfer_driver
    for method_name in ("download_guide_logs", "download_ekos_analyze_logs", "download_kstars_logs"):
        download = getattr(driver, method_name, None)
        if download is None:
            logger.info("Remote-transfer driver has no %s; reading local files only", method_name)
            continue
        download(destination_dir)
    return ingest_ekos_session_logs_from_directory(context, records, destination_dir, frame_lookup)


def ingest_ekos_logs(context: ControlContext, destination_dir: str, download: bool = True) -> dict[str, Any]:
    """Read Ekos's guide and session logs and store what they record.

    Names the imaging equipment of each session from the science
    library's frames when that library can be read.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the drivers, the log database and the science library.
    destination_dir : `str`
        Local folder for the logs.
    download : `bool`, optional
        If `False`, read only what is already in `destination_dir` and do
        not contact the telescope computer.

    Returns
    -------
    summary : `dict` [`str`, `Any`]
        What was read and stored (see `EkosLogIngestionSummary`).
    """
    frame_lookup = None
    try:
        frame_lookup = build_frame_lookup(context.astrometrics)
    except (AstrometricsError, sqlite3.Error, OSError) as error:
        logger.warning("Could not read the frame library to name imaging equipment: %s", error)

    if download:
        summary = fetch_and_ingest_ekos_session_logs(
            context, context.records, destination_dir, frame_lookup
        )
    else:
        summary = ingest_ekos_session_logs_from_directory(
            context, context.records, destination_dir, frame_lookup
        )
    return summary.as_dict()
