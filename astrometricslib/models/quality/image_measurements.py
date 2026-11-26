"""What the measurement building blocks (`astrometrics.quality.blocks`) return.

The blocks are small measurements that a script can combine: the empty sky's
level and noise, the stars in an image, their widths, the sky's tilt, aperture
photometry and an image's corners on the sky. They take a FITS path or a pixel
array and store nothing. Every number is a plain Python float, int, bool or
list. Pixel positions are 1-based, as in FITS and DS9.
"""

from typing import Literal

from pydantic import BaseModel

from astrometricslib.models.quality.detections import StarDetection

__all__ = [
    "ApertureFlux",
    "ApertureFluxes",
    "ChannelSky",
    "ChannelStarWidths",
    "FoundStars",
    "SkyBackground",
    "SkyCorners",
    "SkyGradient",
]


class ChannelSky(BaseModel):
    """The empty sky's level and noise in one color channel.

    Parameters
    ----------
    channel : `str`
        ``"red"``, ``"green"`` or ``"blue"`` (or ``"channel <n>"`` for an
        image with other than three channels).
    level : `float`
        The channel's sky level, in the result's ``unit``.
    noise : `float`
        The channel's sky noise, one standard deviation of a pixel, in the
        result's ``unit``.
    """

    channel: str
    level: float
    noise: float


class SkyBackground(BaseModel):
    """The empty sky's level and noise in one image.

    Parameters
    ----------
    level : `float`
        The sky level. In ADU (the stored pixel values) it still holds the
        camera's bias offset; in electrons the given bias is taken off.
        Large bright objects are clipped out, so a galaxy that fills much of
        the frame does not raise it. A color image is measured on the mean
        of its channels.
    noise : `float`
        The sky noise, one standard deviation of a pixel. It comes from
        differences between pixels a few (by default four) columns apart,
        divided by the square root of two, so smooth light does not widen
        it. That assumes the noise of two pixels so far apart is
        independent: in a debayered, resampled or stacked image, where
        neighbors share noise, it reads low.
    unit : {``"ADU"``, ``"electrons"``}
        The unit of the levels and noises.
    bias_adu : `float` or `None`
        The bias taken off the levels before turning them into electrons,
        or `None` in ADU.
    gain_e_per_adu : `float` or `None`
        The gain used to turn ADU into electrons, or `None` in ADU.
    channels : `list` [`ChannelSky`]
        One entry per color channel; empty for a one-channel image.
    """

    level: float
    noise: float
    unit: Literal["ADU", "electrons"] = "ADU"
    bias_adu: float | None = None
    gain_e_per_adu: float | None = None
    channels: list[ChannelSky] = []


class FoundStars(BaseModel):
    """The stars one of the library's star finders found in one image.

    Parameters
    ----------
    finder : {``"astrometry"``, ``"daofind"``}
        Which finder searched: the astrometry run's finder (a sky map that
        follows the image, merged double reports, a color image searched on
        a smaller averaged copy) or the plain DAOStarFinder on one sky level.
    sky_level : `float` or `None`
        The one sky level subtracted before a ``"daofind"`` search, in image
        units. `None` for ``"astrometry"``, which subtracts a sky map.
    noise : `float`
        The sky noise the threshold was set from, in image units, on the
        plane that was searched.
    kernel_fwhm_px : `float`
        The star width, in pixels of the searched plane, that sized the
        finder's star-shaped template. It is a setting of the search, not a
        measured star width (`QualityBlocks.star_widths` measures one).
    threshold_sigma : `float`
        How many noise levels above the sky a star had to stand.
    binned_on_color : `bool`
        `True` when the astrometry finder searched an averaged, smaller copy
        of a color image. Positions are given back in full image pixels.
    debayered : `bool`
        `True` when a raw color mosaic was first turned into a color image
        of 2 by 2 super-pixels. Positions are given back in the raw frame's
        pixels.
    stars : `list` [`StarDetection`]
        The stars, brightest first. ``x`` and ``y`` are 1-based pixels (as
        in FITS and DS9), as in `QualityRecords.measure_detections`.
        ``flux`` is the finder's own brightness: the sum of the
        sky-subtracted pixel values inside the template's footprint (a
        circle about 1.5 template widths across), in image units, on the
        searched plane. It is not the star's total light (use
        `QualityBlocks.measure_aperture` for that). For a merged star it is
        the sum of the merged reports, and ``merged`` is `True`.
    star_count : `int`
        The number of stars (the length of ``stars``).
    """

    finder: Literal["astrometry", "daofind"]
    sky_level: float | None
    noise: float
    kernel_fwhm_px: float
    threshold_sigma: float
    binned_on_color: bool = False
    debayered: bool = False
    stars: list[StarDetection]
    star_count: int


class ChannelStarWidths(BaseModel):
    """The star width of each color channel, as the color preview measures it.

    This is the width the color preview uses to match its channels' star
    widths: the spread of each star's light around its center (its second
    moment), given as the full width at half maximum (FWHM) of a Gaussian
    star of that spread, and the median over the stars.

    Parameters
    ----------
    widths_px : `list` [`float`]
        One FWHM in pixels per channel (red, green and blue for a color
        image), or empty when there are no stars. A channel in which no star
        could be measured gives NaN.
    estimator : `str`
        The estimator's name, ``"preview channel second moment"``. It is not
        one of the `StarWidthEstimator` members.
    widths_on_scale_px : `list` [`float` or `None`]
        The widths on the library's one star-width scale. `None` for each
        channel: no ratio of this estimator to the scale has been measured
        yet, so compare these widths only with each other.
    star_count : `int`
        How many stars were measured.
    """

    widths_px: list[float]
    estimator: str = "preview channel second moment"
    widths_on_scale_px: list[float | None]
    star_count: int


class SkyGradient(BaseModel):
    """How much the sky tilts across a frame, measured as the preview does.

    Parameters
    ----------
    gradient_fraction : `float` or `None`
        The sky tilt across the frame as a fraction of the sky level.
        `None` when the image could not be measured.
    limit_fraction : `float`
        The tilt below which the preview subtracts a fitted plane instead
        of running GraXpert.
    plane_used : `bool`
        `True` when the preview would subtract the fitted plane.
    sky_bin_count : `int`
        How many of the 64 by 64 bins were taken as sky.
    plane_coefficients : `list` [`tuple` [`float`, `float`]]
        For each color plane, the change of the fitted plane from the center
        to the right and to the bottom edge, in image units. Empty when the
        plane is not used.
    radial_coefficients : `list` [`tuple` [`float`, `float`]]
        For each color plane, the terms ``(d2, d4)`` of the vignetting part
        ``d2 * r^2 + d4 * r^4``, in image units. Empty when it is not used.
    radial_fraction : `float` or `None`
        The vignetting at the corners as a fraction of the sky level, or
        `None` when it was not fitted.
    reason : `str`
        A plain sentence saying what was found.
    """

    gradient_fraction: float | None
    limit_fraction: float
    plane_used: bool
    sky_bin_count: int
    plane_coefficients: list[tuple[float, float]]
    radial_coefficients: list[tuple[float, float]]
    radial_fraction: float | None
    reason: str


class SkyCorners(BaseModel):
    """A plate-solved image's four corners on the sky.

    Parameters
    ----------
    corners : `list` [`tuple` [`float`, `float`]]
        ``(ra, dec)`` in degrees, in order around the edge, at the outer
        edges of the corner pixels, with the solver's distortion terms.
    radesys : `str` or `None`
        The sky frame the positions are in, from the header's ``RADESYS``
        card (such as ``"ICRS"`` or ``"FK5"``), or `None` when the header
        does not say (the FITS standard then means ICRS, or FK5 when an
        equinox is given).
    equinox : `float` or `None`
        The header's ``EQUINOX`` (such as 2000.0), or `None` when it does not
        say.
    """

    corners: list[tuple[float, float]]
    radesys: str | None
    equinox: float | None


class ApertureFlux(BaseModel):
    """The light of one star inside a circle, with its uncertainty.

    Parameters
    ----------
    x, y : `float`
        The circle's center, 1-based pixels, as given.
    flux : `float` or `None`
        The sky-subtracted sum inside the circle, in ADU. It can be below
        zero for a faint star. `None` when the star is too near the edge for
        the sky ring to fit.
    flux_error : `float` or `None`
        Its 1-sigma uncertainty in ADU, from the CCD noise equation (the
        star's own photon noise, the sky, the read noise, and the noise of
        the sky level itself).
    signal_to_noise : `float` or `None`
        ``flux`` divided by ``flux_error``.
    peak_adu : `float` or `None`
        The largest raw value inside the circle, sky included.
    saturated : `bool`
        Whether the circle holds a significant number of saturated pixels.
    """

    x: float
    y: float
    flux: float | None
    flux_error: float | None
    signal_to_noise: float | None
    peak_adu: float | None
    saturated: bool


class ApertureFluxes(BaseModel):
    """Aperture photometry of some stars in one image.

    Parameters
    ----------
    radius_px, sky_inner_px, sky_outer_px : `float`
        The circle's radius and the sky ring's radii, in image pixels.
    gain_e_per_adu : `float`
        The gain the uncertainties used.
    read_noise_e : `float`
        The read noise the uncertainties used, in electrons.
    noise_source : `str`
        Where the gain and read noise came from: ``"given"``, ``"camera
        profile or header"``, or ``"assumed"`` (1 electron per ADU and no
        read noise, so the uncertainties are rough).
    stars : `list` [`ApertureFlux`]
        One entry per position, in the order given.
    """

    radius_px: float
    sky_inner_px: float
    sky_outer_px: float
    gain_e_per_adu: float
    read_noise_e: float
    noise_source: str
    stars: list[ApertureFlux]
