"""Purpose: Equipment Fingerprint.

Description: A short, stable text that identifies one complete equipment
setup: the imaging telescope and camera, and the guide scope's focal length
and guide camera's pixel scale. Measurements from two different setups must
never be pooled into one baseline, because what counts as normal depends on
the equipment (a guide error of 2 arcseconds means something different at
two pixel scales). Keying history by this fingerprint means that when the
equipment changes, the new setup starts with an empty history by itself.

Names are compared after removing case, spaces and punctuation, because the
same camera is written several ways across FITS headers, frame records and
the configuration file. The guide optics are identified by their numbers
(rounded), not by name, since those are what the guide log records.
"""

import re

UNKNOWN_EQUIPMENT_FIELD = "unknown"
"""Stands in for any part of the setup that could not be determined."""

_FOCAL_LENGTH_DECIMALS = 0
"""Guide focal length is rounded to whole millimetres (121.05 -> 121)."""

_PIXEL_SCALE_DECIMALS = 1
"""Guide pixel scale is rounded to 0.1 arcsecond per pixel (6.39 -> 6.4)."""


def _normalize_name(name: str | None) -> str:
    """Reduce a device name to lowercase letters and digits.

    Returns
    -------
    normalized_name : `str`
        The normalized name, or `UNKNOWN_EQUIPMENT_FIELD` if `name` is empty.
    """
    if not name:
        return UNKNOWN_EQUIPMENT_FIELD
    return re.sub(r"[^a-z0-9]", "", name.lower()) or UNKNOWN_EQUIPMENT_FIELD


def _rounded_number(value: float | None, decimals: int) -> str:
    """Format a number rounded to `decimals` places.

    Returns
    -------
    text : `str`
        The rounded number, or `UNKNOWN_EQUIPMENT_FIELD` if `value` is `None`.
    """
    if value is None:
        return UNKNOWN_EQUIPMENT_FIELD
    return f"{round(value, decimals):.{decimals}f}"


def build_equipment_fingerprint(
    imaging_telescope_name: str | None,
    imaging_camera_name: str | None,
    guide_focal_length_mm: float | None,
    guide_pixel_scale_arcsec_per_px: float | None,
) -> str:
    """Build the fingerprint of one equipment setup.

    Parameters
    ----------
    imaging_telescope_name : `str` or `None`
        Name of the imaging telescope.
    imaging_camera_name : `str` or `None`
        Name of the imaging camera.
    guide_focal_length_mm : `float` or `None`
        Focal length of the guide scope, in millimetres.
    guide_pixel_scale_arcsec_per_px : `float` or `None`
        Guide camera plate scale, in arcseconds per pixel.

    Returns
    -------
    fingerprint : `str`
        For example
        ``"telescope=apertura75q|camera=zwoasi533mmpro|guide_focal_mm=121|guide_scale=6.4"``.
        A part that is not known is written as ``unknown``. Two setups that
        differ in any known part have different fingerprints.
    """
    return "|".join((
        f"telescope={_normalize_name(imaging_telescope_name)}",
        f"camera={_normalize_name(imaging_camera_name)}",
        f"guide_focal_mm={_rounded_number(guide_focal_length_mm, _FOCAL_LENGTH_DECIMALS)}",
        f"guide_scale={_rounded_number(guide_pixel_scale_arcsec_per_px, _PIXEL_SCALE_DECIMALS)}",
    ))
