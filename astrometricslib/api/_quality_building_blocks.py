"""Small measurements a script can combine, part of `QualityRecords`.

The Command Console preloads only the `astrometrics` and `wayfinder`
objects, so a script reaches the library through its public methods. These
methods give a script the building blocks the pipelines use, each from its
one home in the library:

* `measure_sky`: the empty sky's level and noise (`image_scaling`).
* `find_stars`: the shared star finder (`star_finding`).
* `star_widths`: the star width of each color channel (`star_width_match`).
* `measure_sky_gradient`: the sky's tilt, measured as the preview does
  (`sky_plane`, built on `binned_sky_plane`).
* `sky_corners`: an image's corners on the sky (`header_wcs`).
* `solved_stars`: the stars astrometry named on a stack (`solved_star_list`).
* `robust_sigma` and `sigma_clip`: the library's robust spread and clipping
  loop (`robust_statistics`).

An image argument is a FITS path or a pixel array. Nothing is stored. Each
is written as a function that takes the `QualityRecords` as ``self``;
`QualityRecords` (in `quality.py`) binds them as its own methods.
"""

import os
from collections.abc import Sequence

import numpy as np

from astrometricslib._internal.drivers.fits_access import collapse_to_2d, read_data, read_header

# `QualityRecords` binds the functions below as its methods, so this
# module and `quality.py` import each other. An annotation is read
# only when asked for, after both modules have loaded.
from astrometricslib.api import quality as _quality
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.models.quality.detections import StarDetection
from astrometricslib.models.quality.image_measurements import FoundStars, SkyBackground, SkyGradient
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.shared.header_wcs import celestial_wcs, image_corners_on_sky
from astrometricslib.pipelines.shared.image_scaling import measure_sky_background
from astrometricslib.pipelines.shared.solved_star_list import read_solved_stars
from astrometricslib.pipelines.shared.star_finding import find_sources_above_background, source_positions
from astrometricslib.pipelines.stacking.post_processing.sky_plane import choose_background_method
from astrometricslib.pipelines.stacking.post_processing.star_width_match import (
    find_measurable_stars,
    measure_channel_widths,
)
from astrometricslib.utilities import robust_statistics

ImageInput = str | os.PathLike[str] | np.ndarray
"""A FITS path or a pixel array: 2-D, or color channels first."""


def _pixels(image: ImageInput) -> np.ndarray:
    """Read an image argument into a floating-point array.

    Parameters
    ----------
    image : `str`, `os.PathLike` or `numpy.ndarray`
        A FITS path or a pixel array.

    Returns
    -------
    pixels : `numpy.ndarray`
        The pixels as 64-bit floats, in their own shape.

    Raises
    ------
    NotFoundError
        If a path names no file.
    InvalidArgumentError
        If the file holds no pixels, or the array is not 2-D or 3-D.
    """
    if isinstance(image, np.ndarray):
        pixels = image
    else:
        path = os.fspath(image)
        if not os.path.isfile(path):
            raise NotFoundError(f"No image at {path}.")
        pixels = read_data(path)
        if pixels is None:
            raise InvalidArgumentError(f"{path} holds no pixels.")
    pixels = np.asarray(pixels, dtype=np.float64)
    if pixels.ndim not in (2, 3):
        raise InvalidArgumentError(f"An image must be 2-D or 3-D, not {pixels.ndim}-D.")
    return pixels


def _channels_first(pixels: np.ndarray) -> np.ndarray:
    """Give an image as channels first, shape (channels, height, width).

    Parameters
    ----------
    pixels : `numpy.ndarray`
        A 2-D image, or a color image with the channels first or last.

    Returns
    -------
    cube : `numpy.ndarray`
        The image with the channels first; a 2-D image gets one channel.
    """
    if pixels.ndim == 2:
        return pixels[np.newaxis]
    if pixels.shape[0] in (1, 3, 4):
        return pixels
    return np.moveaxis(pixels, -1, 0)


def measure_sky(self: _quality.QualityRecords, image: ImageInput) -> SkyBackground | None:
    """Measure the empty sky's level and noise in an image.

    This is the library's one sky measurement. It samples the image on a
    grid and clips out everything that stands above the sky, so a large
    galaxy or nebula does not raise the level. The noise comes from
    differences between pixels a few columns apart. A color image is
    measured on the mean of its channels. Nothing is stored. A path that
    names no file raises `NotFoundError`; an image with no pixels, or one
    that is not 2-D or 3-D, raises `InvalidArgumentError`.

    Parameters
    ----------
    image : `str` or `numpy.ndarray`
        A FITS path, or a pixel array (2-D, or color channels first).

    Returns
    -------
    sky : `SkyBackground` or `None`
        Fields: ``level`` and ``noise`` (one standard deviation), in
        image units. `None` when the sky cannot be measured (too few
        usable pixels, or no spread).
    """
    measured = measure_sky_background(collapse_to_2d(_pixels(image)))
    if measured is None:
        return None
    return SkyBackground(level=float(measured[0]), noise=float(measured[1]))


def find_stars(
    self: _quality.QualityRecords,
    image: ImageInput,
    *,
    fwhm_px: float,
    threshold_sigma: float,
    isolation_px: float | None = None,
) -> FoundStars | None:
    """Find the stars in an image with the library's shared star finder.

    The sky is measured as `measure_sky` does and subtracted, then the
    star finder (DAOStarFinder, which slides a star-shaped template over
    the image) keeps the sources that stand ``threshold_sigma`` noise
    levels above the sky. Every roundness is kept, so a trailed star is
    found too. A color image is searched on the mean of its channels.
    Nothing is stored. A path that names no file raises
    `NotFoundError`; an image with no pixels, or one that is not 2-D or
    3-D, raises `InvalidArgumentError`.

    Parameters
    ----------
    image : `str` or `numpy.ndarray`
        A FITS path, or a pixel array (2-D, or color channels first).
    fwhm_px : `float`
        The expected star width across its middle, in pixels. It sizes
        the template.
    threshold_sigma : `float`
        How many noise levels above the sky a star must stand.
    isolation_px : `float`, optional
        When given, a star with another star within this many pixels is
        left out, so its light is not mixed with a neighbor's.

    Returns
    -------
    stars : `FoundStars` or `None`
        Fields: ``sky_level``, ``noise``, ``fwhm_px``,
        ``threshold_sigma``, ``stars`` (brightest first, each with ``x``
        and ``y`` in 1-based pixels and ``flux``) and ``star_count``.
        `None` when the sky cannot be measured.

    Raises
    ------
    InvalidArgumentError
        If a width or threshold is not above zero.
    """
    if not fwhm_px > 0.0 or not threshold_sigma > 0.0:
        raise InvalidArgumentError("fwhm_px and threshold_sigma must be above zero.")
    plane = collapse_to_2d(_pixels(image))
    measured = measure_sky_background(plane)
    if measured is None or not measured[1] > 0.0:
        return None
    sky, noise = measured
    sources = find_sources_above_background(
        np.nan_to_num(plane - sky),
        noise=noise,
        expected_fwhm_px=float(fwhm_px),
        threshold_sigma=float(threshold_sigma),
        roundness_range=None,
        isolation_px=None if isolation_px is None else float(isolation_px),
    )
    positions = source_positions(sources)
    stars = [
        StarDetection(x=float(x) + 1.0, y=float(y) + 1.0, flux=float(source["flux"]))
        for (x, y), source in zip(positions, sources, strict=True)
    ]
    return FoundStars(
        sky_level=float(sky),
        noise=float(noise),
        fwhm_px=float(fwhm_px),
        threshold_sigma=float(threshold_sigma),
        stars=stars,
        star_count=len(stars),
    )


def star_widths(
    self: _quality.QualityRecords, image: ImageInput, positions: list[tuple[float, float]] | None = None
) -> list[float]:
    """Measure the star width of each color channel of an image.

    Each star's width is the spread of its light around its center,
    given as the full width at half maximum (FWHM) of a Gaussian star of
    that spread. A channel's width is the median over the stars. This is
    the measure the preview uses to match the channels' star widths.
    Nothing is stored. A path that names no file raises
    `NotFoundError`; an image with no pixels, or one that is not 2-D or
    3-D, raises `InvalidArgumentError`.

    Parameters
    ----------
    image : `str` or `numpy.ndarray`
        A FITS path, or a linear pixel array (2-D, or color channels
        first).
    positions : `list` [`tuple` [`float`, `float`]], optional
        The stars to measure, as ``(x, y)`` in 1-based pixels, such as
        the ``stars`` of `find_stars`. By default the preview's own
        choice is used: bright, unsaturated, isolated stars found on the
        green channel (the only channel of a 2-D image).

    Returns
    -------
    widths : `list` [`float`]
        One FWHM in pixels per channel (red, green and blue for a color
        image), or empty when there are no stars. A channel in which no
        star could be measured gives NaN.
    """
    cube = _channels_first(_pixels(image))
    if positions is None:
        three = cube if cube.shape[0] == 3 else np.repeat(cube[:1], 3, axis=0)
        stars = find_measurable_stars(three)
    else:
        stars = np.array([(float(x) - 1.0, float(y) - 1.0) for x, y in positions], dtype=np.float64)
        stars = stars.reshape(-1, 2)
    return [float(width) for width in measure_channel_widths(cube, stars)]


def measure_sky_gradient(self: _quality.QualityRecords, image: ImageInput) -> SkyGradient:
    """Measure how much the sky tilts across a frame, as the preview does.

    The image is cut into 64 by 64 bins, the bins of empty sky are kept,
    and a plane is fitted to their medians. The preview subtracts that
    plane when the tilt is below its limit, and runs GraXpert otherwise.
    The fit also says whether a vignetting term (darker corners) is
    used. Nothing is changed or stored. A path that names no file raises
    `NotFoundError`; an image with no pixels, or one that is not 2-D or
    3-D, raises `InvalidArgumentError`.

    Parameters
    ----------
    image : `str` or `numpy.ndarray`
        A FITS path, or a linear pixel array (2-D, or color channels
        first).

    Returns
    -------
    gradient : `SkyGradient`
        Fields: ``gradient_fraction``, ``limit_fraction``,
        ``plane_used``, ``sky_bin_count``, ``plane_coefficients``,
        ``radial_coefficients``, ``radial_fraction`` and ``reason``.
    """
    pixels = _pixels(image)
    decision = choose_background_method(pixels if pixels.ndim == 2 else _channels_first(pixels))
    return SkyGradient(
        gradient_fraction=None if decision.gradient_fraction is None else float(decision.gradient_fraction),
        limit_fraction=float(decision.limit_fraction),
        plane_used=decision.method == "plane",
        sky_bin_count=int(decision.sky_bin_count),
        plane_coefficients=[(float(a), float(b)) for a, b in decision.plane_coefficients],
        radial_coefficients=[(float(a), float(b)) for a, b in decision.radial_coefficients],
        radial_fraction=None if decision.radial_fraction is None else float(decision.radial_fraction),
        reason=decision.reason,
    )


def sky_corners(self: _quality.QualityRecords, path: str) -> list[tuple[float, float]] | None:
    """Give the sky positions of a plate-solved image's four corners.

    The positions come from the image's sky map (WCS) in its header,
    including the lens distortion terms a solver adds, at the outer
    edges of the corner pixels. Nothing is stored.

    Parameters
    ----------
    path : `str`
        A FITS image.

    Returns
    -------
    corners : `list` [`tuple` [`float`, `float`]] or `None`
        ``(ra, dec)`` in degrees, in order around the edge. `None` when
        the image has no usable sky map (it is not plate-solved).

    Raises
    ------
    NotFoundError
        If the file does not exist.
    """
    if not os.path.isfile(path):
        raise NotFoundError(f"No image at {path}.")
    header = read_header(path)
    wcs = celestial_wcs(header)
    width, height = header.get("NAXIS1"), header.get("NAXIS2")
    if wcs is None or not width or not height:
        return None
    return image_corners_on_sky(wcs, int(width), int(height))


def solved_stars(self: _quality.QualityRecords, stack_path: str) -> list[StellarObject]:
    """Read the stars the astrometry run named on a stack.

    The astrometry run saves the stars it named beside the stack. The
    list is read only while the stack is unchanged since then. Nothing
    is stored.

    Parameters
    ----------
    stack_path : `str`
        The stack.

    Returns
    -------
    stars : `list` [`StellarObject`]
        The named stars, with their names, sky and pixel positions and
        catalog details. Empty when the stack has no saved list, or the
        stack changed after the list was saved.
    """
    return read_solved_stars(stack_path)


def robust_sigma(self: _quality.QualityRecords, values: Sequence[float] | np.ndarray) -> float:
    """Measure a spread of numbers that a few stray values cannot move.

    The spread is 1.4826 times the median absolute deviation from the
    median, which equals the standard deviation for normally spread
    numbers. Values that are not numbers are left out. This is the
    library's one robust spread.

    Parameters
    ----------
    values : `list` [`float`] or `numpy.ndarray`
        The numbers.

    Returns
    -------
    sigma : `float`
        The spread. NaN when there is no number to measure.
    """
    return float(robust_statistics.robust_sigma(np.asarray(values, dtype=np.float64).ravel()))


def sigma_clip(
    self: _quality.QualityRecords,
    values: Sequence[float] | np.ndarray,
    *,
    clip_sigma: float,
    maximum_passes: int,
    minimum_count: int,
) -> list[float] | None:
    """Drop outlying numbers, again and again, until none is dropped.

    Each pass drops every value farther than ``clip_sigma`` robust
    spreads (`robust_sigma`) from the median of the values kept so far.
    The passes stop when nothing more is dropped, when a pass would
    keep fewer than ``minimum_count`` values, or after
    ``maximum_passes`` passes. This is the library's one clipping loop.

    Parameters
    ----------
    values : `list` [`float`] or `numpy.ndarray`
        The numbers. Values that are not finite are left out.
    clip_sigma : `float`
        How many robust spreads from the median a value may lie.
    maximum_passes : `int`
        The most clipping passes.
    minimum_count : `int`
        The fewest finite values that give a result, and the fewest a
        pass may keep.

    Returns
    -------
    kept : `list` [`float`] or `None`
        The kept values. `None` when fewer than ``minimum_count`` finite
        values are given.
    """
    kept = robust_statistics.sigma_clipped_values(
        values,
        minimum_count=int(minimum_count),
        maximum_passes=int(maximum_passes),
        clip_sigma=clip_sigma,
    )
    return None if kept is None else kept.tolist()
