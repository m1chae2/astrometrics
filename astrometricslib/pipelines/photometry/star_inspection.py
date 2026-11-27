"""Purpose: Measure one clicked star with the pipelines' own numbers.

Description: `measure_star_profile` is the owner behind
``astrometrics.quality.detections.measure_profile``. It reads the photometry
circle, sky ring and crowding limits the photometry pipeline uses, finds the
neighbor stars with the astrometry run's star finder, and measures the star
with `star_profile.measure_star_profile_in_file`. It then adds the stacking
limit for a trailed frame, so the viewer can say whether the star's shape
would set the frame aside. Nothing is stored.
"""

from collections.abc import Sequence

from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.quality.star_profile import StarProfileMeasurement
from astrometricslib.pipelines.shared.quality.star_profile import (
    PhotometryGeometry,
    measure_star_profile_in_file,
)

__all__ = ["measure_star_profile"]


def measure_star_profile(
    path: str,
    position: Sequence[float],
    *,
    radius_px: float | None = None,
    cut_angle_deg: float = 0.0,
    cut_half_length_px: float | None = None,
    search_radius_px: float | None = None,
) -> StarProfileMeasurement:
    """Measure one star's radial profile and a straight cut through it.

    Parameters
    ----------
    path : `str`
        A FITS image.
    position : `Sequence` [`float`]
        The star's ``(x, y)`` in 1-based pixels.
    radius_px : `float`, optional
        The radius of the profile. Taken from the star width when left out.
    cut_angle_deg : `float`, optional
        The cut's direction, from +y toward +x.
    cut_half_length_px : `float`, optional
        How far the cut reaches each way from the center.
    search_radius_px : `float`, optional
        Move first to the brightest peak within this many pixels.

    Returns
    -------
    profile : `StarProfileMeasurement`
        The profile, the cut, the ring around the star and the trailing
        limit.

    Raises
    ------
    InvalidArgumentError
        If ``position`` is not two numbers.
    """
    from astrometricslib.foundation.errors import InvalidArgumentError
    from astrometricslib.pipelines.astrometry.processing.detections import find_star_centers
    from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import (
        APERTURE_RADIUS_PX,
        CROWDING_MARGIN_PX,
        CROWDING_NEIGHBOR_PEAK_FRACTION,
        SKY_ANNULUS_INNER_RADIUS_PX,
        SKY_ANNULUS_OUTER_RADIUS_PX,
    )
    from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import TRAILED_ELONGATION_LIMIT

    if len(position) != 2:
        raise InvalidArgumentError("position must be the star's (x, y) in 1-based pixels.")
    x, y = (float(value) for value in position)
    geometry = PhotometryGeometry(
        aperture_radius_px=float(resolve(APERTURE_RADIUS_PX).value),  # type: ignore[arg-type]
        sky_inner_px=float(resolve(SKY_ANNULUS_INNER_RADIUS_PX).value),  # type: ignore[arg-type]
        sky_outer_px=float(resolve(SKY_ANNULUS_OUTER_RADIUS_PX).value),  # type: ignore[arg-type]
        crowding_margin_px=float(resolve(CROWDING_MARGIN_PX).value),  # type: ignore[arg-type]
        crowding_neighbor_peak_fraction=float(resolve(CROWDING_NEIGHBOR_PEAK_FRACTION).value),  # type: ignore[arg-type]
    )
    measurement = measure_star_profile_in_file(
        path,
        x,
        y,
        radius_px,
        cut_angle_deg,
        cut_half_length_px,
        search_radius_px,
        find_neighbors=find_star_centers,
        photometry_geometry=geometry,
    )
    measurement.trailing_elongation_limit = float(resolve(TRAILED_ELONGATION_LIMIT).value)
    return measurement
