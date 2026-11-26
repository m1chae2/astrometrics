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
settings reaches every caller. The one setting that differs between them is
the roundness range. The source detector keeps only nearly round sources,
which suits matching stars to a catalog. The frame measurement keeps every
roundness, because a trailed star is exactly what it has to measure.

Two filters pick the stars whose shape can be measured. `isolated_sources`
drops a source that has another source near it, whose light would mix with
its own. `unsaturated_boxes` drops a star whose box reaches a ceiling (a
saturated core is cut flat, so its width reads wrong), runs off the picture
or holds pixels with no data. The star-width measurements of the color
preview use the finder with both filters.
"""

import warnings
from typing import Any

import numpy as np
from photutils.detection import DAOStarFinder
from photutils.utils import NoDetectionsWarning
from scipy.spatial import cKDTree

from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)

__all__ = [
    "STAR_SHARPNESS_RANGE",
    "find_sources_above_background",
    "isolated_sources",
    "source_positions",
    "unsaturated_boxes",
]

# DAOStarFinder's sharpness is the ratio of a source's central pixel to its
# surrounding pixels. A hot pixel or cosmic ray is sharper than any star and
# broad glow is flatter, so this window removes both. It is the window the
# source detector has used for the astrometry stage since it was written (see
# AST-16 in the validation register), and the frame measurement uses the same
# one so the two find the same kind of object.
STAR_SHARPNESS_RANGE = TunedParameter(
    name="shared.star_sharpness_range",
    default=(0.2, 1.0),
    unit="ratio",
    description="Window of star sharpness that rejects hot pixels and broad glow.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="AST-D01",
)


def find_sources_above_background(
    background_subtracted: np.ndarray,
    *,
    noise: float,
    expected_fwhm_px: float,
    threshold_sigma: float,
    roundness_range: tuple[float, float] | None,
    mask: np.ndarray | None = None,
    min_separation: float | None = None,
    isolation_px: float | None = None,
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
    min_separation : `float`, optional
        The closest two sources may lie, in pixels. `None` keeps the finder's
        own value, 2.5 star widths, so two stars closer than that merge into
        one source. A caller that needs close pairs, such as the photometry
        neighbor list, passes a smaller value.
    isolation_px : `float`, optional
        When given, a source with another found source within this many
        pixels is left out (see `isolated_sources`).

    Returns
    -------
    sources : `list` [`dict`]
        The sources, brightest first. Each dictionary holds the star
        finder's columns, including ``x_centroid``, ``y_centroid`` and
        ``flux``.
    """
    separation_option = {} if min_separation is None else {"min_separation": min_separation}
    finder = DAOStarFinder(
        fwhm=expected_fwhm_px,
        threshold=threshold_sigma * noise,
        exclude_border=True,
        sharpness_range=resolve(STAR_SHARPNESS_RANGE).value,
        roundness_range=roundness_range,
        **separation_option,
    )
    with warnings.catch_warnings():
        # A picture with no stars is an ordinary case: the answer is empty.
        warnings.simplefilter("ignore", NoDetectionsWarning)
        found = finder(background_subtracted, mask=mask)
    if found is None:
        return []
    found.sort("flux")
    found.reverse()
    sources = [dict(zip(found.colnames, row, strict=False)) for row in found]
    if isolation_px is not None and sources:
        alone = isolated_sources(source_positions(sources), isolation_px)
        sources = [source for source, keep in zip(sources, alone, strict=True) if keep]
    return sources


def source_positions(sources: list[dict[str, Any]]) -> np.ndarray:
    """Give the x and y positions of the finder's sources.

    Parameters
    ----------
    sources : `list` [`dict`]
        What `find_sources_above_background` returned.

    Returns
    -------
    positions : `numpy.ndarray`
        Shape (n, 2): the x (column) and y (row) of each source, in the
        order given.
    """
    return np.array(
        [[float(source["x_centroid"]), float(source["y_centroid"])] for source in sources], dtype=float
    ).reshape(-1, 2)


def isolated_sources(positions: np.ndarray, distance_px: float) -> np.ndarray:
    """Tell which sources have no other source near them.

    Parameters
    ----------
    positions : `numpy.ndarray`
        Shape (n, 2): the sources' positions, in pixels.
    distance_px : `float`
        A source with another one closer than or as close as this is not
        isolated.

    Returns
    -------
    isolated : `numpy.ndarray`
        One `True` or `False` per source.
    """
    if len(positions) < 2:
        return np.ones(len(positions), dtype=bool)
    groups = cKDTree(positions).query_ball_point(positions, distance_px)
    return np.array([len(group) == 1 for group in groups], dtype=bool)


def unsaturated_boxes(
    planes: np.ndarray,
    positions: np.ndarray,
    half_box: int,
    ceiling: float,
    floor: float | None = None,
) -> np.ndarray:
    """Tell which stars have a whole, unsaturated box in every plane.

    Parameters
    ----------
    planes : `numpy.ndarray`
        The picture, shape (height, width) or (planes, height, width).
    positions : `numpy.ndarray`
        Shape (n, 2): the stars' x and y positions, in pixels.
    half_box : `int`
        The box runs this many pixels each way from the star's nearest pixel.
    ceiling : `float`
        A box whose brightest pixel, in any plane, reaches this is saturated.
    floor : `float`, optional
        When given, a box with a pixel at or below this (no data, such as the
        empty border of a moved picture) is not used either.

    Returns
    -------
    usable : `numpy.ndarray`
        One `True` or `False` per star: `True` when the box lies inside the
        picture, holds only finite values, stays under `ceiling` and, with
        `floor`, above it.
    """
    cube = planes if planes.ndim == 3 else planes[np.newaxis]
    height, width = cube.shape[1:]
    usable = np.zeros(len(positions), dtype=bool)
    for index, (x, y) in enumerate(positions):
        column, row = round(float(x)), round(float(y))
        if not (half_box <= row < height - half_box and half_box <= column < width - half_box):
            continue
        box = cube[:, row - half_box : row + half_box + 1, column - half_box : column + half_box + 1]
        if not np.all(np.isfinite(box)) or float(box.max()) >= ceiling:
            continue
        usable[index] = floor is None or float(box.min()) > floor
    return usable
