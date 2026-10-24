"""The star finder core that two callers share.

Finding stars is done in two parts. The first part estimates the sky
background and the noise of the picture. The two callers do this part
differently, because one works on a picture with a mask of bad pixels and the
other on a plain raw frame. The second part, in this file, is the same for
both: subtract the background, run DAOStarFinder (a matched-filter star finder
from photutils, which slides a star-shaped template over the picture and
responds where the picture looks like a star), and return the sources as
plain dictionaries, brightest first.

Keeping the second part in one place means a change to the star finder's
settings reaches both callers. The one setting that differs between them is
the roundness range. The source detector keeps only nearly round sources,
which suits matching stars to a catalog. The frame measurement keeps every
roundness, because a trailed star is exactly what it has to measure.
"""

from typing import Any

import numpy as np
from photutils.detection import DAOStarFinder

__all__ = ["STAR_SHARPNESS_RANGE", "find_sources_above_background"]

# DAOStarFinder's sharpness is the ratio of a source's central pixel to its
# surrounding pixels. A hot pixel or cosmic ray is sharper than any star and
# broad glow is flatter, so this window removes both. It is the window the
# source detector has used for the astrometry stage since it was written (see
# AST-16 in the validation register), and the frame measurement uses the same
# one so the two find the same kind of object.
STAR_SHARPNESS_RANGE = (0.2, 1.0)


def find_sources_above_background(
    background_subtracted: np.ndarray,
    *,
    noise: float,
    expected_fwhm_px: float,
    threshold_sigma: float,
    roundness_range: tuple[float, float] | None,
    mask: np.ndarray | None = None,
) -> list[dict[str, Any]]:
    """Run the star finder on a picture whose background is already removed.

    Parameters
    ----------
    background_subtracted : `numpy.ndarray`
        The 2-D picture with the sky background subtracted.
    noise : `float`
        The background noise (standard deviation of the sky pixels), in the
        picture's units.
    expected_fwhm_px : `float`
        How wide a star is expected to be across its middle, in pixels. It
        sets the size of the star-shaped template.
    threshold_sigma : `float`
        How far above the noise a source must stand, as a multiple of
        `noise`.
    roundness_range : `tuple` [`float`, `float`] or `None`
        The lowest and highest DAOStarFinder roundness to keep. `None` keeps
        every roundness, so stretched stars are not dropped.
    mask : `numpy.ndarray`, optional
        Pixels to ignore (`True` marks a bad pixel).

    Returns
    -------
    sources : `list` [`dict`]
        The sources, brightest first. Each dictionary holds the star
        finder's columns, including ``x_centroid``, ``y_centroid`` and
        ``flux``.
    """
    finder = DAOStarFinder(
        fwhm=expected_fwhm_px,
        threshold=threshold_sigma * noise,
        exclude_border=True,
        sharpness_range=STAR_SHARPNESS_RANGE,
        roundness_range=roundness_range,
    )
    found = finder(background_subtracted, mask=mask)
    if found is None:
        return []
    found.sort("flux")
    found.reverse()
    return [dict(zip(found.colnames, row, strict=False)) for row in found]
