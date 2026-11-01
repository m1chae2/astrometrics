"""Purpose: Read the library limits that the capture analysis quotes.

Description: The capture analysis judges clipping and dark temperatures with
limits the science library owns: the saturated-pixel metric lists the blob
size and the clipped-frame share, and a calibration query reports the
temperature tolerance it matched darks with. This module reads them from
those rows, so the analysis keeps no copy of the numbers.
"""

from typing import Any

from astrometricslib.exceptions import ConfigurationError
from wayfindinglib.models.session.capture_quality import CaptureLimits

__all__ = ["SATURATED_PIXEL_METRIC", "read_capture_limits"]

SATURATED_PIXEL_METRIC = "frame.saturated_pixel_fraction"
"""The stored metric whose declaration lists the saturation limits."""


def read_capture_limits(astrometrics: Any) -> CaptureLimits:
    """Read the clipping limits and the dark tolerance from the library.

    Parameters
    ----------
    astrometrics : `Any`
        The science library's high-level interface (``Astrometrics``).

    Returns
    -------
    limits : `CaptureLimits`
        The fewest saturated pixels of a clipped star, the share of frames
        that makes an exposure length clipped, and the dark temperature
        tolerance in degrees Celsius.

    Raises
    ------
    ConfigurationError
        If the library does not list a limit it should. That is a fault in
        the library's declaration, not in the night's data.
    """
    (declaration,) = astrometrics.quality.metrics(name=SATURATED_PIXEL_METRIC, detail="full")
    by_unit = {limit.unit: limit.value for limit in declaration.limits}
    tolerance = astrometrics.processing.calibration.query(kind="dark").dark_temperature_tolerance_c
    if "pixels" not in by_unit or "fraction" not in by_unit or tolerance is None:
        raise ConfigurationError(
            f"The library lists no saturation limits for {SATURATED_PIXEL_METRIC} or no dark tolerance."
        )
    return CaptureLimits(
        saturated_star_minimum_pixels=round(by_unit["pixels"]),
        clipped_frame_fraction=by_unit["fraction"],
        dark_temperature_tolerance_c=tolerance,
    )
