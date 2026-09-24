"""Tools for finding and reading image files.

This module scans folders to find telescope images (FITS files) and reads
the basic information saved inside them (like camera temperature or exposure
time).
"""

import logging
import os
import re
from datetime import datetime
from typing import Any

from astrometricslib.drivers.camera_profile_store import record_name_for_camera, resolve_camera_profile
from astrometricslib.drivers.filter_detection import get_filter_type
from astrometricslib.drivers.fits_access import read_header
from astrometricslib.drivers.image import AstrometricsImage
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.frame_optics import resolve_frame_telescope
from astrometricslib.utilities.enums import FilterType
from astrometricslib.utilities.warn_once import warn_once

logger = logging.getLogger(__name__)

# The ISO or gain written on a record when the image header gives none, the
# camera's config section gives no `default_iso`, and so nothing is known.
# These are stand-ins, not measurements. They are the values this module used
# before `default_iso` existed. A warning is logged whenever one is used.
PLACEHOLDER_RECORD_ISO = "800"
PLACEHOLDER_FOLDER_ISO = "0"


def _coerce_header_number(value: Any, cast: type) -> Any:
    """Try to convert a value from an image file into a number.

    Different cameras save data differently (sometimes as text, sometimes
    as numbers). This function tries to safely turn whatever it finds
    into the number type we need (like an integer or decimal).

    Parameters
    ----------
    value : `Any`
        The raw value read from the file.
    cast : `type`
        The type of number we want (e.g., `int` or `float`).

    Returns
    -------
    number : `Any`
        The converted number, or `None` if it couldn't be converted.
    """
    if value is None:
        return None
    try:
        return cast(value)
    except TypeError, ValueError:
        return None


def _populate_acquisition_conditions(record: FrameRecord, header: Any) -> None:
    """Copy sky and equipment settings from the image file to the records.

    This reads information like the telescope's position, camera temperature,
    and focus position. If some information is missing (since different cameras
    save different things), it just leaves that part blank.

    Parameters
    ----------
    record : `FrameRecord`
        The record being filled with information.
    header : `Any`
        The data header from the image file.
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


def _camera_default_iso(camera_name: str, config: Any) -> str | None:
    """Look up the ISO or gain configured for a camera whose header lacks one.

    Parameters
    ----------
    camera_name : `str`
        The camera's name, written in any spelling.
    config : `AppConfiguration`
        The application settings.

    Returns
    -------
    default_iso : `str` or `None`
        The camera's ``default_iso`` from the config, or `None` when it has
        none. Every known spelling of the camera is tried, because the config
        section may be named differently from the image header.
    """
    profile = resolve_camera_profile(camera_name)
    names = [camera_name]
    if not profile.is_generic_fallback:
        names += [profile.camera_name, *profile.name_aliases]
        if profile.record_name:
            names.append(profile.record_name)
    for name in names:
        configured = config.get_camera_default_iso(name)
        if configured:
            return configured
    return None


def read_iso_or_gain(header: Any, camera_name: str, config: Any, placeholder: str) -> str:
    """Read the ISO (or gain) an image was taken at.

    The header's ``ISOSPEED`` is used first, then its ``GAIN``. If it has
    neither, the camera's ``default_iso`` from the config is used, and if
    there is none of those either, `placeholder` is used with a warning.

    Parameters
    ----------
    header : `Any`
        The image header.
    camera_name : `str`
        The camera's name, used to find its ``default_iso``.
    config : `AppConfiguration`
        The application settings.
    placeholder : `str`
        The stand-in to use when nothing else gives a value.

    Returns
    -------
    iso : `str`
        The ISO or gain, as text.
    """
    value = header.get("ISOSPEED", header.get("GAIN"))
    if value is not None:
        return str(value)
    configured = _camera_default_iso(camera_name, config)
    if configured is not None:
        return configured
    warn_once(
        logger,
        f"Images from camera {camera_name!r} carry no ISO or gain, and its config section has no "
        f"default_iso; assuming {placeholder!r}. Add default_iso to the camera's section.",
    )
    return placeholder


def _record_camera_name(header_camera_name: str, config: Any) -> str:
    """Give the camera name spelling that records and folders use.

    Parameters
    ----------
    header_camera_name : `str`
        The camera as written in the image header, or ``"Unknown"`` when the
        header has none.
    config : `AppConfiguration`
        The application settings.

    Returns
    -------
    camera_name : `str`
        The camera's ``record_name`` from its profile, or the header's text
        when it has none. When the header names no camera, the configured
        primary camera is used, or ``"Unknown"`` if none is configured.
    """
    if header_camera_name and header_camera_name != "Unknown":
        return record_name_for_camera(header_camera_name)
    primary_camera_name = config.get_primary_camera_name()
    if primary_camera_name:
        return record_name_for_camera(primary_camera_name)
    warn_once(
        logger,
        "An image header names no camera and no default_primary_camera is configured; "
        "the camera is recorded as 'Unknown'.",
    )
    return "Unknown"


def create_frame_record_from_fits(path: str, camera: str | None = None, config: Any = None) -> FrameRecord:
    """Read an image file and create a record for it.

    This function opens a telescope image, reads its settings (like exposure
    time, date, and camera used), and creates a `FrameRecord` so the rest
    of the program knows about it without having to open the file again.

    Parameters
    ----------
    path : `str`
        The full file path to the image.
    camera : `str`, optional
        The name of the camera, if it needs to be forced to a specific value.
    config : `AppConfiguration`, optional
        The application settings, used to find the camera's default ISO and
        the optic that took the frame. The system configuration is loaded
        when this is left out.

    Returns
    -------
    record : `FrameRecord`
        The record containing the image's information.
    """
    if config is None:
        from astrometricslib.utilities.config_loader import get_configuration

        config = get_configuration()
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
            record.camera = _record_camera_name(
                str(header.get("INSTRUME", header.get("CAMERA", "Unknown"))), config
            )

        record.iso = read_iso_or_gain(header, record.camera, config, PLACEHOLDER_RECORD_ISO)

        # The focal length is read here, before the optic is chosen, because
        # the optic is chosen by matching it.
        _populate_acquisition_conditions(record, header)
        record.telescope = resolve_frame_telescope(
            record.camera, record.focal_length_mm, path, config.get_observatory_setups()
        ).telescope_name
    except Exception as e:
        logger.warning(f"Failed to parse FITS header for {filename}: {e}")

    return record


def refresh_acquisition_conditions(frame: FrameRecord) -> bool:
    """Re-read the basic equipment settings from an image file.

    If new things need to be tracked (like a new temperature sensor),
    this allows going back and reading those new values from images already
    found, without having to do all the heavy processing again.

    Parameters
    ----------
    frame : `FrameRecord`
        The frame record to update.

    Returns
    -------
    refreshed : `bool`
        True if the file was read successfully, False if there was an error.
    """
    try:
        # AstrometricsImage.header, not fits.getheader(path): the latter
        # only ever reads HDU0, while a real frame's header can live in
        # HDU1 when HDU0 carries no data (AstrometricsImage._load_header
        # falls back to HDU1 in that case, matching the read
        # create_frame_record_from_fits used when this frame was first
        # indexed). Reading HDU0's empty header here would overwrite
        # every field _populate_acquisition_conditions sets with None,
        # even though the initial scan recorded them correctly.
        header = AstrometricsImage(frame.path).header
    except Exception as header_error:
        logger.debug("Could not refresh header conditions for %s: %s", frame.path, header_error)
        return False

    _populate_acquisition_conditions(frame, header)
    return True


def scan_target_directory(target: Target, frames_root_path: str, refresh_headers: bool = False) -> None:
    """Look through folders to find new images for a specific target.

    This function searches the hard drive for any image folders that match
    the target's name. If it finds new images, it adds them to the target's
    list.

    Parameters
    ----------
    target : `Target`
        The target (like a galaxy or nebula) being looked for.
    frames_root_path : `str`
        The main folder where all images are kept.
    refresh_headers : `bool`, optional
        If True, it will also re-read the settings of images already known
        about.
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
    """Sort new image files into the correct folders.

    This reads new images, figures out what kind they are (like a dark frame,
    flat frame, or regular light frame), and moves them into the organized
    folder structure based on the camera and telescope used.

    Parameters
    ----------
    scan_list : `list` of `str`
        A list of folders to check for new images.
    target_id : `str`
        The name of the target for regular light frames.
    config : `AppConfiguration`
        Application settings to know where the main folder is.
    telescope_name : `str`
        The name of the telescope used.

    Returns
    -------
    processed_count : `int`
        The number of files that were successfully moved.
    """
    import shutil

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
            header = read_header(file_path)
            frame_type = header.get("FRAME", header.get("IMAGETYP", "Light")).replace(" ", "").lower()
            camera = _record_camera_name(header.get("INSTRUME", header.get("CAMERA", "Unknown")), config)

            if "light" in frame_type:
                if target_id:
                    dest_path = os.path.join(frames_path, "lights", target_id, telescope_name, camera)
                else:
                    continue
            elif "dark" in frame_type:
                exposure = float(header.get("EXPTIME", 0))
                gain = read_iso_or_gain(header, camera, config, PLACEHOLDER_FOLDER_ISO)
                dest_path = os.path.join(frames_path, "darks", camera, str(gain), str(exposure))
            elif "bias" in frame_type:
                gain = read_iso_or_gain(header, camera, config, PLACEHOLDER_FOLDER_ISO)
                dest_path = os.path.join(frames_path, "biases", camera, str(gain))
            elif "flat" in frame_type:
                filter_enum = get_filter_type(header)
                filter_name = filter_enum.name if hasattr(filter_enum, "name") else str(filter_enum)
                gain = read_iso_or_gain(header, camera, config, PLACEHOLDER_FOLDER_ISO)
                dest_path = os.path.join(frames_path, "flats", telescope_name, camera, filter_name, str(gain))
            else:
                dest_path = os.path.join(frames_path, "others")

            if dest_path:
                os.makedirs(dest_path, exist_ok=True)
                shutil.move(file_path, os.path.join(dest_path, file))
                processed_count += 1
        except Exception as e:
            logger.error(f"Error classifying frame {file}: {e}")

    return processed_count
