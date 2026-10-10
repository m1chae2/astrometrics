"""Choosing which detected source is the standard star in a spectral frame.

The scripts that measure one bright star in a frame (the instrument-response
derivation and the focus sweep) need to know which detected source is the
star. The telescope is pointed at the star, so the rule is: take the source
nearest the frame centre, unless the caller gives the position. The rule
lives here so two scripts share it without importing each other.
"""

import numpy as np

from astrometricslib.foundation.errors import ProcessingError
from astrometricslib.models.stellar_source import StellarObject

# How far, in pixels, the standard star's zero order may sit from the frame
# centre before the script refuses to guess. The telescope is pointed at the
# standard star, so it lands near the centre. Validated only on Vega: the
# original stack put the star 10 px from the centre, and in the rebuilt stack
# (where the star was not detected) the nearest wrong source was 33 px away.
# 25 px falls between those two cases; it has not been tested on other stars.
MAXIMUM_STAR_OFFSET_FROM_CENTER_PX = 25.0


def select_standard_star(
    stellar_objects: list[StellarObject],
    image_shape: tuple[int, int],
    star_position: tuple[float, float] | None = None,
    maximum_offset_px: float = MAXIMUM_STAR_OFFSET_FROM_CENTER_PX,
) -> StellarObject:
    """Choose which detected source is the standard star.

    The brightest source is not always the standard star: a bright star
    near the frame edge can outshine it. The standard star is the one the
    telescope was pointed at, so it is the source closest to the frame
    centre, unless the caller says where it is.

    Parameters
    ----------
    stellar_objects : `list` [`StellarObject`]
        The detected sources.
    image_shape : `tuple` [`int`, `int`]
        The image's ``(height, width)`` in pixels.
    star_position : `tuple` [`float`, `float`], optional
        The star's ``(x, y)`` pixel position. When given, it is used as it
        is, even if the detector did not list a source there (a saturated,
        flat-topped zero order is sometimes rejected as a source).
    maximum_offset_px : `float`, optional
        The farthest a detected source may be from the frame centre and
        still be taken as the star.

    Returns
    -------
    star : `StellarObject`
        The chosen star.

    Raises
    ------
    ProcessingError
        If no source lies within ``maximum_offset_px`` of the frame centre
        and no position was given.
    """
    if star_position is not None:
        x_position, y_position = float(star_position[0]), float(star_position[1])
        return StellarObject(
            id="manual_standard_star",
            name="Standard star (position given)",
            star_data={"xcentroid": x_position, "ycentroid": y_position},
        )

    height, width = image_shape
    best_star, best_offset = None, float("inf")
    for star in stellar_objects:
        # Detections name the centroid `x_centroid` or `xcentroid`.
        x_position = star.star_data.get("x_centroid", star.star_data.get("xcentroid"))
        y_position = star.star_data.get("y_centroid", star.star_data.get("ycentroid"))
        if x_position is None or y_position is None:
            continue
        offset = float(np.hypot(x_position - width / 2, y_position - height / 2))
        if offset < best_offset:
            best_star, best_offset = star, offset

    if best_star is None or best_offset > maximum_offset_px:
        raise ProcessingError(
            f"No detected source lies within {maximum_offset_px:.0f} px of the frame centre "
            f"(nearest: {best_offset:.0f} px). Pass the star's pixel position with --star-position X Y."
        )
    return best_star
