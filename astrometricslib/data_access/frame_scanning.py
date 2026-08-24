"""Frame scanning, FITS header parsing, and target directory indexing.

Recursively scans directories to discover fits/fit frame files,
extracts metadata from headers, and maps them to hydrated FrameRecord
objects. The pure FITS-header classification logic
(`get_filter_type`) lives in tasks/target_tasks/frame_scan_tasks.py
instead -- everything in this module touches the filesystem.
"""

import logging
import os
import re
from datetime import datetime
from typing import Any

from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.tasks.target_tasks.frame_scan_tasks import get_filter_type
from astrometricslib.utilities.enums import FilterType
from astrometricslib.utilities.image import AstrometricsImage

logger = logging.getLogger(__name__)


def _coerce_header_number(value: Any, cast: type) -> Any:
    """Convert a FITS header value to a number, or `None` if it cannot be.

    Header values arrive as strings, numbers, or astropy `Undefined`
    sentinels depending on the writer, so every read is guarded rather
    than trusted.

    Parameters
    ----------
    value : `Any`
        Raw header value.
    cast : `type`
        `int` or `float`.

    Returns
    -------
    number : `Any`
        The converted number, or `None` if `value` is absent or
        non-numeric.
    """
    if value is None:
        return None
    try:
        return cast(value)
    except TypeError, ValueError:
        return None


def _populate_acquisition_conditions(record: FrameRecord, header: Any) -> None:
    """Copy sky and equipment state from a FITS header onto a frame record.

    Read at index time because the header parse is already happening;
    none of this touches pixel data, so it adds no measurable cost to
    scanning. Every field is optional: the two cameras in this library
    write different subsets (only the cooled ZWO reports sensor and
    focuser telemetry; only the DSLR writes a pixel scale), and a
    missing key simply leaves its field `None`.

    Parameters
    ----------
    record : `FrameRecord`
        Frame record to populate, mutated in place.
    header : `Any`
        The FITS header to read from.
    """
    pier_side = header.get("PIERSIDE")
    # Recorded because a meridian flip mid-session moves the field
    # abruptly. Without it, tracking analysis reads that jump as a bump
    # or cable snag rather than a normal, expected flip.
    if pier_side is not None and str(pier_side).strip():
        record.pier_side = str(pier_side).strip().upper()

    record.airmass = _coerce_header_number(header.get("AIRMASS"), float)
    record.altitude_degrees = _coerce_header_number(header.get("OBJCTALT"), float)
    record.azimuth_degrees = _coerce_header_number(header.get("OBJCTAZ"), float)

    # SECPIX1 is arcsec/pixel directly; SCALE is the same quantity under
    # a different writer's spelling. Recorded so a FWHM in pixels can be
    # expressed in arcsec, which is comparable across cameras and focal
    # lengths where a pixel count is not.
    record.pixel_scale_arcsec = _coerce_header_number(header.get("SECPIX1", header.get("SCALE")), float)

    # FOCALLEN identifies the optic, which decides which frames may be
    # stacked together. Left None when the header does not carry it;
    # `scripts/backfill_focal_length` exists to fill those in
    # deliberately rather than having the library guess.
    record.focal_length_mm = _coerce_header_number(header.get("FOCALLEN"), float)
    record.binning = _coerce_header_number(header.get("XBINNING"), int)

    record.sensor_temperature_c = _coerce_header_number(header.get("CCD-TEMP"), float)
    record.focuser_position = _coerce_header_number(header.get("FOCUSPOS"), int)
    record.focuser_temperature_c = _coerce_header_number(header.get("FOCUSTEM"), float)


def create_frame_record_from_fits(path: str, camera: str | None = None) -> FrameRecord:
    """Parse a FITS file and build a hydrated FrameRecord from it.

    Parameters
    ----------
    path : `str`
        Absolute path to the FITS file.
    camera : `str`, optional
        Fallback camera name used if not found in the header.

    Returns
    -------
    record : `FrameRecord`
        Frame record populated from the FITS header metadata. On
        parse failure, a partially-populated record with default
        values is returned and the failure is logged.
    """
    filename = os.path.basename(path)
    record = FrameRecord(
        path=path,
        filter=FilterType.NONE,
        role="LIGHT",
        iso="800",
        offset="0",
        exposure="1.0",
        camera=camera or "Unknown",
        telescope="Unknown",
    )

    try:
        image = AstrometricsImage(path)
        header = image.header
        record.filter = image.filter_type
        record.iso = str(header.get("ISOSPEED", header.get("GAIN", "800")))
        record.offset = str(header.get("OFFSET", header.get("BLKLEVEL", "0")))
        record.exposure = str(header.get("EXPTIME", "1.0"))
        record.timestamp = image.timestamp

        if image.timestamp:
            record.date = datetime.fromtimestamp(image.timestamp).strftime("%Y-%m-%d %H:%M:%S")
        else:
            # Fallback to filename date extraction if timestamp is missing
            date_match = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})", filename)
            if date_match:
                raw = date_match.group(1)
                parts = raw.split("T")
                d_part = parts[0]
                t_part = parts[1].replace("-", ":")
                record.date = f"{d_part} {t_part}"

        if not camera:
            record.camera = (
                str(header.get("INSTRUME", header.get("CAMERA", "Unknown")))
                .replace("ZWO CCD", "ZWO")
                .replace("ASI533", "ASI 533")
            )

        # Assume ISO 800 for Nikon cameras if not correctly identified
        if "Nikon" in record.camera:
            record.iso = "800"

        # Heuristic for telescope mapping
        if "Nikkor 300mm" in path:
            record.telescope = "Nikkor 300mm"
        else:
            record.telescope = "Apertura 75Q"

        _populate_acquisition_conditions(record, header)
    except Exception as e:
        logger.warning(f"Failed to parse FITS header for {filename}: {e}")

    return record


def refresh_acquisition_conditions(frame: FrameRecord) -> bool:
    """Re-read one already-tracked frame's header conditions.

    Only the header-derived acquisition fields are replaced. Everything
    a pipeline measured -- registration facts, background level,
    saturation, measured FWHM -- is left untouched, because none of it
    comes from the header and re-deriving it would cost orders of
    magnitude more (a header read is ~10ms against ~4s for the pixels).

    Exists because `scan_target_directory` only builds records for files
    it has not seen before, so fields added to `FrameRecord` after a
    frame was first indexed would otherwise stay `None` forever on every
    existing record.

    Parameters
    ----------
    frame : `FrameRecord`
        The frame record to refresh, mutated in place.

    Returns
    -------
    refreshed : `bool`
        `True` if the header was read and applied; `False` if the file
        is missing or unreadable.
    """
    from astropy.io import fits

    try:
        header = fits.getheader(frame.path)
    except Exception as header_error:
        logger.debug("Could not refresh header conditions for %s: %s", frame.path, header_error)
        return False

    _populate_acquisition_conditions(frame, header)
    return True


def scan_target_directory(target: Target, frames_root_path: str, refresh_headers: bool = False) -> None:
    """Scan the lights directory for target match variants.

    Recursively scans the lights directory inside the frames root
    folder for directories matching one of the target's id variants,
    and populates ``target.frames`` with unique FrameRecord entries.

    Parameters
    ----------
    target : `Target`
        The Target object to populate.
    frames_root_path : `str`
        Base directory containing imaging files.
    refresh_headers : `bool`, optional
        Whether to also re-read header-derived acquisition conditions on
        frames already tracked (default `False`). Without this, a field
        added to `FrameRecord` after a frame was first indexed stays
        `None` on that frame forever, since only unseen files get a new
        record built. Costs ~10ms per frame -- header reads only, no
        pixel data.
    """
    variants = [
        target.id,
        target.id.replace(" ", "_"),
        target.id.replace("_", " "),
        target.id.replace(" ", ""),
    ]
    variants = list(dict.fromkeys(variants))

    found_directory = None
    for d in variants:
        path = os.path.join(frames_root_path, "lights", d)
        if os.path.exists(path):
            found_directory = path
            break

    if not found_directory:
        m = re.match(r"^([A-Za-z]+)\s*(\d+)$", target.id)
        if m:
            prefix, num = m.groups()
            aggressive_variants = [f"{prefix} {num}", f"{prefix}_{num}", f"{prefix}{num}"]
            for d in aggressive_variants:
                path = os.path.join(frames_root_path, "lights", d)
                if os.path.exists(path) and d not in variants:
                    found_directory = path
                    break

    if not found_directory:
        return

    for root, _, files in os.walk(found_directory):
        for file in files:
            if file.lower().endswith((".fits", ".fit")):
                if "_stacked" in file.lower() or "starless" in file.lower() or "starmask" in file.lower():
                    continue
                file_path = os.path.join(root, file)
                # Check if already tracked
                if not any(frame.path == file_path for frame in target.frames):
                    frame_record = create_frame_record_from_fits(file_path)
                    target.frames.append(frame_record)

    if refresh_headers:
        for frame in target.frames:
            refresh_acquisition_conditions(frame)

    target.recalculate_total_exposure()


def classify_and_sort_fits_files(scan_list: list[str], target_id: str, config, telescope_name: str) -> int:  # ruff: ignore[missing-type-function-argument]
    """Classify and sort FITS files into the library directory tree.

    Walks source directories, reads FITS headers, classifies frames by
    type (light/dark/bias/flat), normalizes camera names, and moves
    files into the correct library directory structure.

    Parameters
    ----------
    scan_list : `list` [`str`]
        Source directory paths to scan for FITS files.
    target_id : `str`
        Target identifier used for routing light frames.
    config
        Application configuration providing ``frames_path``.
    telescope_name : `str`
        Telescope name used in the directory structure for lights and
        flats.

    Returns
    -------
    processed_count : `int`
        Number of files successfully classified and moved.
    """
    import shutil

    from astropy.io import fits

    processed_count = 0
    fits_files = []

    for src in scan_list:
        for root, _dirs, files in os.walk(src):
            # Avoid recursively walking into destination structures if
            # they exist inside src
            if any(part in root.split(os.sep) for part in [telescope_name, "darks", "biases", "flats"]):
                continue
            for file in files:
                if file.lower().endswith((".fits", ".fit")):
                    fits_files.append((os.path.join(root, file), file))

    frames_path = config.get_frames_path()

    for file_path, file in fits_files:
        try:
            dest_path = ""
            with fits.open(file_path, memmap=False) as hdul:
                header = hdul[0].header
                frame_type = header.get("FRAME", header.get("IMAGETYP", "Light")).replace(" ", "").lower()
                camera = header.get("INSTRUME", header.get("CAMERA", "Unknown"))
                camera = camera.replace("ZWO CCD", "ZWO").replace("ASI533", "ASI 533")
                if camera == "Unknown":
                    camera = "ZWO ASI 533MM Pro"

                if "light" in frame_type:
                    if target_id:
                        dest_path = os.path.join(frames_path, "lights", target_id, telescope_name, camera)
                    else:
                        continue
                elif "dark" in frame_type:
                    exposure = float(header.get("EXPTIME", 0))
                    gain = str(header.get("ISOSPEED", header.get("GAIN", "0")))
                    dest_path = os.path.join(frames_path, "darks", camera, str(gain), str(exposure))
                elif "bias" in frame_type:
                    gain = str(header.get("ISOSPEED", header.get("GAIN", "0")))
                    dest_path = os.path.join(frames_path, "biases", camera, str(gain))
                elif "flat" in frame_type:
                    filter_enum = get_filter_type(header)
                    filter_name = filter_enum.name if hasattr(filter_enum, "name") else str(filter_enum)
                    gain = str(header.get("ISOSPEED", header.get("GAIN", "0")))
                    dest_path = os.path.join(
                        frames_path, "flats", telescope_name, camera, filter_name, str(gain)
                    )
                else:
                    dest_path = os.path.join(frames_path, "others")

            if dest_path:
                os.makedirs(dest_path, exist_ok=True)
                shutil.move(file_path, os.path.join(dest_path, file))
                processed_count += 1
        except Exception as e:
            logger.error(f"Error classifying frame {file}: {e}")

    return processed_count
