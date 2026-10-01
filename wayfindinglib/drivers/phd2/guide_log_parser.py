"""Purpose: Ingest and parse PHD2-format guide log text files.

Description: Reads the calibration sections and frame-by-frame guiding rows
of a guide log for SQLite storage and tracking analysis. PHD2 writes this
format (``PHD2_GuideLog_*.txt``), and so does the Ekos internal guider in
KStars (``guide_log-*.txt``, whose first line says "PHD2 log version 2.5"),
so one parser reads both.

Two details of the format matter for correctness:

* Distances in the frame rows are in **pixels** of the guide camera, not
  arcseconds. The pixel scale comes from the "Pixel scale = ..." line of
  each guiding section, and every distance is multiplied by it.
* Correction directions are single letters (``W``, ``E``, ``N``, ``S``).
  Pulses are returned signed: east and north positive, west and south
  negative.
"""

import csv
import logging
import os
import re
from datetime import datetime
from typing import Any

from wayfindinglib.models.session.guide_log import GuideCalibration, GuidingSection, ParsedGuideLog
from wayfindinglib.models.session.telemetry import GuidingSampleSource

logger = logging.getLogger(__name__)

_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
_GUIDING_BEGINS = re.compile(r"^Guiding Begins at\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_GUIDING_ENDS = re.compile(r"^Guiding Ends at\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_CALIBRATION_BEGINS = re.compile(r"^Calibration Begins at\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_CALIBRATION_SPEEDS = re.compile(
    r"^Calibration guide speeds:\s*RA = (-?[\d.]+) a-s/s, Dec = (-?[\d.]+) a-s/s"
)
_PIXEL_SCALE_LINE = re.compile(
    r"^Pixel scale = (?P<scale>[\d.]+) arc-sec/px, Binning = (?P<binning>\d+), "
    r"Focal length = (?P<focal>[\d.]+) mm"
)
_MOUNT_RATES_LINE = re.compile(
    r"xAngle = (?P<x_angle>-?[\d.]+), xRate = (?P<x_rate>-?[\d.]+), "
    r"yAngle = (?P<y_angle>-?[\d.]+), yRate = (?P<y_rate>-?[\d.]+)"
)
_POINTING_FIELDS = {
    "right_ascension_hours": re.compile(r"\bRA = (-?[\d.]+) hr"),
    "declination_degrees": re.compile(r"\bDec = (-?[\d.]+) deg"),
    "hour_angle_hours": re.compile(r"Hour angle = (-?[\d.]+) hr"),
    "altitude_degrees": re.compile(r"\bAlt = (-?[\d.]+) deg"),
    "azimuth_degrees": re.compile(r"\bAz = (-?[\d.]+) deg"),
}
_PIER_SIDE = re.compile(r"Pier side = (East|West)")
_EAST_OR_NORTH = {"E", "EAST", "N", "NORTH"}


def _parse_timestamp(text: str) -> float:
    """Convert a log timestamp to seconds since the Unix epoch.

    Guide logs carry no time zone, so the time is read as local time on
    the machine doing the parsing. That is correct when that machine is in
    the same time zone as the telescope.

    Returns
    -------
    timestamp : `float`
        Seconds since the Unix epoch.
    """
    return datetime.strptime(text, _TIMESTAMP_FORMAT).timestamp()


def _read_pointing(line: str, section: GuidingSection) -> None:
    """Fill a section's sky position from its "RA = ..., Dec = ..." line."""
    for attribute, pattern in _POINTING_FIELDS.items():
        match = pattern.search(line)
        if match:
            setattr(section, attribute, float(match.group(1)))
    pier_match = _PIER_SIDE.search(line)
    if pier_match:
        section.pier_side = pier_match.group(1)


def _signed_pulse_ms(duration_text: str, direction_text: str) -> float:
    """Turn a correction duration and direction into a signed pulse.

    Returns
    -------
    pulse_ms : `float`
        The duration in milliseconds: positive for east or north,
        negative for west or south.
    """
    duration_ms = float(duration_text or 0.0)
    return duration_ms if direction_text.strip().upper() in _EAST_OR_NORTH else -duration_ms


def _build_sample(
    row: dict[str, str],
    section: GuidingSection,
    pixel_scale_arcsec_per_px: float,
    target_name: str | None,
    source: GuidingSampleSource,
) -> dict[str, Any]:
    """Convert one frame row into a sample ready for the guiding log.

    The measured error is taken from the ``RARawDistance`` and
    ``DECRawDistance`` columns: the star's offset as measured, before the
    guide algorithm filtered it.

    Returns
    -------
    sample : `dict` [`str`, `Any`]
        Times are epoch seconds; ``dra`` and ``ddec`` are arcseconds;
        pulses are signed milliseconds.
    """
    epoch_time = section.started_at + float(row["Time"])
    star_mass_text = row.get("StarMass", "")
    snr_text = row.get("SNR", "")
    return {
        "timestamp": epoch_time,
        "time": epoch_time,
        "target_name": target_name,
        "dra": float(row["RARawDistance"]) * pixel_scale_arcsec_per_px,
        "ddec": float(row["DECRawDistance"]) * pixel_scale_arcsec_per_px,
        "pulse_ra": _signed_pulse_ms(row.get("RADuration", "0"), row.get("RADirection", "")),
        "pulse_dec": _signed_pulse_ms(row.get("DECDuration", "0"), row.get("DECDirection", "")),
        "snr": float(snr_text) if snr_text else None,
        "star_mass": float(star_mass_text) if star_mass_text else None,
        "source": source.value,
    }


def parse_guide_log_file(
    file_path: str,
    target_name: str | None = None,
    fallback_pixel_scale_arcsec_per_px: float | None = None,
) -> ParsedGuideLog:
    """Read a PHD2-format guide log, keeping each section's context.

    Parameters
    ----------
    file_path : `str`
        Path to the guide log text file.
    target_name : `str` or `None`, optional
        Celestial target name to attach to the samples.
    fallback_pixel_scale_arcsec_per_px : `float` or `None`, optional
        Pixel scale to use for a guiding section whose header does not give
        one. Normally unneeded, since PHD2 and Ekos always write it. Without
        a scale, a section's rows are counted but not turned into samples,
        because guessing the unit would put wrong numbers in the database.

    Returns
    -------
    parsed_log : `ParsedGuideLog`
        The calibrations and guiding sections found. An unreadable or
        missing file gives an empty result.
    """
    parsed_log = ParsedGuideLog(written_by_ekos=False)
    if not os.path.isfile(file_path):
        return parsed_log

    with open(file_path, encoding="utf-8", errors="replace") as log_file:
        lines = [line.strip() for line in log_file]
    non_empty_lines = [line for line in lines if line]
    if non_empty_lines:
        parsed_log.program_version = non_empty_lines[0]
        parsed_log.written_by_ekos = non_empty_lines[0].startswith("KStars")
    source = (
        GuidingSampleSource.EKOS_GUIDE_LOG
        if parsed_log.written_by_ekos
        else GuidingSampleSource.PHD2_GUIDE_LOG
    )

    section: GuidingSection | None = None
    calibration: GuideCalibration | None = None
    column_names: list[str] | None = None

    for line in lines:
        if not line:
            continue

        begins = _GUIDING_BEGINS.match(line)
        if begins:
            calibration = None
            section = GuidingSection(started_at=_parse_timestamp(begins.group(1)))
            if fallback_pixel_scale_arcsec_per_px is not None:
                section.pixel_scale_arcsec_per_px = fallback_pixel_scale_arcsec_per_px
            parsed_log.sections.append(section)
            column_names = None
            continue

        ends = _GUIDING_ENDS.match(line)
        if ends:
            if section is not None:
                section.ended_at = _parse_timestamp(ends.group(1))
            section = None
            column_names = None
            continue

        calibration_begins = _CALIBRATION_BEGINS.match(line)
        if calibration_begins:
            section = None
            calibration = GuideCalibration(started_at=_parse_timestamp(calibration_begins.group(1)))
            parsed_log.calibrations.append(calibration)
            continue

        if calibration is not None:
            speeds = _CALIBRATION_SPEEDS.match(line)
            if speeds:
                calibration.ra_rate_arcsec_per_second = float(speeds.group(1))
                calibration.dec_rate_arcsec_per_second = float(speeds.group(2))
            continue

        if section is None:
            continue

        scale_line = _PIXEL_SCALE_LINE.match(line)
        if scale_line:
            section.pixel_scale_arcsec_per_px = float(scale_line.group("scale"))
            section.binning = int(scale_line.group("binning"))
            section.focal_length_mm = float(scale_line.group("focal"))
            continue

        if line.startswith("RA = "):
            _read_pointing(line, section)
            continue

        rates = _MOUNT_RATES_LINE.search(line)
        if rates and line.startswith("Mount"):
            section.ra_rate_arcsec_per_second = float(rates.group("x_rate"))
            section.dec_rate_arcsec_per_second = float(rates.group("y_rate"))
            continue

        if line.startswith("Frame,"):
            column_names = [name.strip() for name in line.split(",")]
            continue

        if column_names is None or not line[0].isdigit():
            continue

        values = next(csv.reader([line]))
        if len(values) < len(column_names):
            continue
        row = dict(zip(column_names, (value.strip() for value in values), strict=False))
        section.frames_total += 1
        if row.get("ErrorCode", "0") not in ("0", ""):
            section.frames_with_error_code += 1
            continue
        if section.pixel_scale_arcsec_per_px is None:
            section.frames_without_pixel_scale += 1
            continue
        try:
            section.samples.append(
                _build_sample(row, section, section.pixel_scale_arcsec_per_px, target_name, source)
            )
        except KeyError, ValueError:
            logger.debug("Skipping unparseable guide log row: %s", line)

    return parsed_log


def parse_phd2_guide_log(
    file_path: str,
    target_name: str | None = None,
    fallback_pixel_scale_arcsec_per_px: float | None = None,
) -> list[dict[str, Any]]:
    """Parse a guide log into one flat list of guiding samples.

    Parameters
    ----------
    file_path : `str`
        Path to the guide log text file.
    target_name : `str` or `None`, optional
        Celestial target name to attach to the samples.
    fallback_pixel_scale_arcsec_per_px : `float` or `None`, optional
        See `parse_guide_log_file`.

    Returns
    -------
    samples : `list` [`dict` [`str`, `Any`]]
        Samples from every guiding section, in file order, ready for
        SQLite storage. Errors are in arcseconds; pulses are signed
        milliseconds.
    """
    parsed_log = parse_guide_log_file(file_path, target_name, fallback_pixel_scale_arcsec_per_px)
    return [sample for section in parsed_log.sections for sample in section.samples]
