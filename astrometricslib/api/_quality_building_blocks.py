"""Small measurements a script can combine: `astrometrics.quality.blocks`.

The Command Console preloads only the `astrometrics` and `wayfinder`
objects, so a script reaches the library through its public methods.
`QualityBlocks`, bound as ``QualityRecords.blocks``, gives a script the
building blocks the pipelines use, each from its one home in the library:

* `measure_sky`: the empty sky's level and noise (`image_scaling`).
* `find_stars`: the astrometry run's star finder (`star_detection`) or the
  plain shared finder (`star_finding`).
* `star_widths`: the star width of each color channel (`star_width_match`).
* `measure_sky_gradient`: the sky's tilt, measured as the preview does
  (`sky_plane`, built on `binned_sky_plane`).
* `measure_aperture`: aperture photometry with uncertainties
  (`frame_photometry`, `detector_noise`).
* `sky_corners`: a plate-solved image's corners on the sky (`header_wcs`).
* `solved_stars`: the stars astrometry named on a stack (`solved_star_list`).
* `robust_sigma` and `sigma_clip`: the library's robust spread and clipping
  loop (`robust_statistics`).

An image argument is a FITS path or a pixel array. Every position is in
1-based pixels (as in FITS and DS9). A raw color mosaic (a one-plane frame
whose header names a Bayer pattern, ``BAYERPAT``) is refused unless the
caller passes ``debayer=True``; it is then measured as a color image of 2 by
2 super-pixels (red, the mean of the two greens, blue), and positions and
widths are given back in the raw frame's pixels. An array carries no header,
so it is taken as it is. Nothing is stored.
"""

import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from astrometricslib._internal.drivers.fits_access import (
    collapse_to_2d,
    header_is_plate_solved,
    read_header,
    read_header_and_data,
)
from astrometricslib._internal.renames import MovedNames
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.models.quality.detections import StarDetection
from astrometricslib.models.quality.image_measurements import (
    ApertureFlux,
    ApertureFluxes,
    ChannelSky,
    ChannelStarWidths,
    FoundStars,
    SkyBackground,
    SkyCorners,
    SkyGradient,
)
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.shared.quality.star_profile_fitting import BAYER_CELL_SIZE_PX
from astrometricslib.utilities import robust_statistics

__all__ = ["FINDERS", "ImageInput", "QualityBlocks"]

ImageInput = str | os.PathLike[str] | np.ndarray
"""A FITS path or a pixel array: 2-D, or color channels first."""

FINDERS = ("astrometry", "daofind")
"""The star finders `QualityBlocks.find_stars` offers."""

_CHANNEL_NAMES = ("red", "green", "blue")


@dataclass(frozen=True)
class _Image:
    """An image argument read into floating-point pixels.

    Attributes
    ----------
    pixels : `numpy.ndarray`
        2-D, or color channels first.
    header : `Any`
        The FITS header, or `None` for an array.
    cell : `int`
        1, or 2 when a raw mosaic was turned into super-pixels.
    """

    pixels: np.ndarray
    header: Any
    cell: int = 1

    def to_image_pixel(self, value: float) -> float:
        """Move a 1-based position on the measured plane onto the image given.

        Returns
        -------
        position : `float`
            The 1-based position on the image as stored.
        """
        return (value - 0.5) * self.cell + 0.5

    def to_measured_pixel(self, value: float) -> float:
        """Move a 1-based position on the image given onto the measured plane.

        Returns
        -------
        position : `float`
            The 1-based position on the measured plane.
        """
        return (value - 0.5) / self.cell + 0.5


def _superpixel_channels(plane: np.ndarray, pattern: str) -> np.ndarray:
    """Turn a raw color mosaic into red, green and blue 2 by 2 super-pixels.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The raw mosaic, 2-D.
    pattern : `str`
        The Bayer layout of the top-left 2 by 2 block in row order, such as
        ``"RGGB"``.

    Returns
    -------
    cube : `numpy.ndarray`
        Shape (3, height // 2, width // 2): red, the mean of the two greens,
        and blue.

    Raises
    ------
    InvalidArgumentError
        If the pattern is not one red, two green and one blue letter.
    """
    pattern = pattern.strip().upper()
    if sorted(pattern) != ["B", "G", "G", "R"]:
        raise InvalidArgumentError(f"BAYERPAT {pattern!r} is not a red, green and blue Bayer layout.")
    rows = plane.shape[0] - plane.shape[0] % BAYER_CELL_SIZE_PX
    columns = plane.shape[1] - plane.shape[1] % BAYER_CELL_SIZE_PX
    trimmed = plane[:rows, :columns]
    blocks: dict[str, list[np.ndarray]] = {letter: [] for letter in "RGB"}
    for position, letter in enumerate(pattern):
        blocks[letter].append(trimmed[position // 2 :: 2, position % 2 :: 2])
    return np.stack([np.mean(blocks[letter], axis=0) for letter in "RGB"])


def _read(image: ImageInput, debayer: bool) -> _Image:
    """Read an image argument; refuse a raw mosaic unless asked to debayer.

    Parameters
    ----------
    image : `str`, `os.PathLike` or `numpy.ndarray`
        A FITS path or a pixel array.
    debayer : `bool`
        Turn a raw color mosaic into 2 by 2 super-pixels instead of
        refusing it.

    Returns
    -------
    image : `_Image`
        The pixels as 64-bit floats, the header and the super-pixel size.

    Raises
    ------
    NotFoundError
        If a path names no file.
    InvalidArgumentError
        If the file holds no pixels, the array is not 2-D or 3-D, a raw
        color mosaic is given without ``debayer=True``, or ``debayer=True``
        is given for an image that is not a raw mosaic.
    """
    header = None
    if isinstance(image, np.ndarray):
        if debayer:
            raise InvalidArgumentError("debayer=True needs a FITS path: an array has no BAYERPAT card.")
        pixels = image
    else:
        path = os.fspath(image)
        if not os.path.isfile(path):
            raise NotFoundError(f"No image at {path}.")
        header, pixels = read_header_and_data(path)
        if pixels is None:
            raise InvalidArgumentError(f"{path} holds no pixels.")
    pixels = np.asarray(pixels, dtype=np.float64)
    if pixels.ndim not in (2, 3):
        raise InvalidArgumentError(f"An image must be 2-D or 3-D, not {pixels.ndim}-D.")
    pattern = str(header.get("BAYERPAT", "")).strip() if header is not None else ""
    mosaic = bool(pattern) and pixels.ndim == 2
    if mosaic and not debayer:
        raise InvalidArgumentError(
            f"{os.fspath(image)} is a raw color mosaic (BAYERPAT={pattern}). Its pixels sit behind "
            "red, green and blue filters, so they cannot be measured as one image. Pass debayer=True "
            "to measure it as 2 by 2 color super-pixels."
        )
    if debayer and not mosaic:
        raise InvalidArgumentError("debayer=True applies only to a raw color mosaic (a BAYERPAT card).")
    if mosaic:
        return _Image(_superpixel_channels(pixels, pattern), header, BAYER_CELL_SIZE_PX)
    return _Image(pixels, header)


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


def _channel_name(index: int, count: int) -> str:
    """Name a color channel.

    Returns
    -------
    name : `str`
        ``"red"``, ``"green"`` or ``"blue"`` for a three-channel image, else
        ``"channel <n>"`` counted from 1.
    """
    return _CHANNEL_NAMES[index] if count == 3 else f"channel {index + 1}"


def _source_positions(sources: list[dict[str, Any]]) -> np.ndarray:
    """Give the 0-based ``(x, y)`` of finder sources, in either key spelling.

    Returns
    -------
    positions : `numpy.ndarray`
        Shape (n, 2).
    """
    return np.array(
        [
            (
                float(source.get("x_centroid", source.get("xcentroid"))),
                float(source.get("y_centroid", source.get("ycentroid"))),
            )
            for source in sources
        ],
        dtype=float,
    ).reshape(-1, 2)


class QualityBlocks(MovedNames):
    """The library's small measurements, for a script to combine.

    Reached as ``astrometrics.quality.blocks``. Each method takes a FITS path
    or a pixel array, gives plain numbers, and stores nothing. Positions are
    1-based pixels. A raw color mosaic is refused unless ``debayer=True``
    (see the module text). A path that names no file raises
    `NotFoundError`; an image with no pixels, or one that is not 2-D or 3-D,
    raises `InvalidArgumentError`.

    Parameters
    ----------
    config : `AppConfiguration`, optional
        The settings, which give the camera profiles `measure_aperture`
        reads the gain and read noise from. The process-wide settings when
        left out.
    """

    def __init__(self, config: AppConfiguration | None = None) -> None:
        self._config = config

    def measure_sky(
        self,
        image: ImageInput,
        *,
        bias_adu: float | None = None,
        gain: float | None = None,
        debayer: bool = False,
    ) -> SkyBackground | None:
        """Measure the empty sky's level and noise in an image.

        This is the library's one sky measurement. It samples the image on a
        grid and clips out everything that stands above the sky, so a large
        galaxy or nebula does not raise the level. The noise comes from
        differences between pixels a few (by default four) columns apart,
        which assumes the noise of two such pixels is independent; in a
        debayered, resampled or stacked image it reads low. The numbers are
        raw ADU (the stored pixel values), with the camera's bias offset
        still in the level. Give both ``bias_adu`` and ``gain`` for
        electrons. A color image is measured on the mean of its channels,
        and each channel is also given on its own. Nothing is stored.

        Parameters
        ----------
        image : `str` or `numpy.ndarray`
            A FITS path, or a pixel array (2-D, or color channels first).
        bias_adu : `float`, optional
            The bias level in ADU, taken off the levels for electrons.
        gain : `float`, optional
            The gain in electrons per ADU.
        debayer : `bool`, optional
            Measure a raw color mosaic as 2 by 2 super-pixels. The green
            super-pixel is the mean of two pixels, so its noise is about
            0.7 of one pixel's.

        Returns
        -------
        sky : `SkyBackground` or `None`
            Fields: ``level``, ``noise`` (one standard deviation),
            ``unit``, ``bias_adu``, ``gain_e_per_adu`` and ``channels``.
            `None` when the sky cannot be measured (too few usable pixels,
            or no spread).

        Raises
        ------
        InvalidArgumentError
            If only one of ``bias_adu`` and ``gain`` is given, or the gain
            is not above zero.
        """
        from astrometricslib.pipelines.shared.image_scaling import measure_sky_background

        if (bias_adu is None) != (gain is None):
            raise InvalidArgumentError("Give both bias_adu and gain for electrons, or neither for ADU.")
        if gain is not None and not gain > 0.0:
            raise InvalidArgumentError("gain must be above zero.")
        read = _read(image, debayer)
        measured = measure_sky_background(collapse_to_2d(read.pixels))
        if measured is None:
            return None

        def convert(level: float, noise: float) -> tuple[float, float]:
            """Turn a level and noise into the result's unit.

            Returns
            -------
            level, noise : `float`
                In electrons when a gain is given, else in ADU.
            """
            if gain is None or bias_adu is None:
                return float(level), float(noise)
            return float((level - bias_adu) * gain), float(noise * gain)

        channels = []
        cube = _channels_first(read.pixels)
        if cube.shape[0] > 1:
            for index, plane in enumerate(cube):
                plane_sky = measure_sky_background(plane)
                if plane_sky is not None:
                    level, noise = convert(*plane_sky)
                    channels.append(
                        ChannelSky(channel=_channel_name(index, cube.shape[0]), level=level, noise=noise)
                    )
        level, noise = convert(*measured)
        return SkyBackground(
            level=level,
            noise=noise,
            unit="ADU" if gain is None else "electrons",
            bias_adu=None if bias_adu is None else float(bias_adu),
            gain_e_per_adu=None if gain is None else float(gain),
            channels=channels,
        )

    def find_stars(
        self,
        image: ImageInput,
        *,
        finder: Literal["astrometry", "daofind"] = "astrometry",
        fwhm_px: float | None = None,
        threshold_sigma: float | None = None,
        isolation_px: float | None = None,
        debayer: bool = False,
    ) -> FoundStars | None:
        """Find the stars in an image with one of the library's star finders.

        ``"astrometry"`` (the default) is the finder the astrometry run and
        `QualityRecords.measure_detections` use: it subtracts a sky map that
        follows the image, merges a star reported twice, searches a color
        image on an averaged smaller copy, and sizes its template from the
        image's own star width unless ``fwhm_px`` is given. Its roundness
        limits leave out stretched shapes such as nebula filaments.
        ``"daofind"`` is the plain shared finder (DAOStarFinder) the frame
        measurement uses: one sky level as `measure_sky` measures it, every
        roundness kept (so a trailed star is found), a color image searched
        on the mean of its channels. `QualityRecords.measure_detections` is
        the ``"astrometry"`` search of one file at the run's own settings,
        saved for reuse and with the photometry circle size. Nothing is
        stored.

        Parameters
        ----------
        image : `str` or `numpy.ndarray`
            A FITS path, or a pixel array (2-D, or color channels first).
        finder : {``"astrometry"``, ``"daofind"``}, optional
            The finder.
        fwhm_px : `float`, optional
            The expected star width across its middle, in pixels. It sizes
            the template. The finder's own width when left out (for
            ``"astrometry"`` on a one-channel image, the image's measured
            star width).
        threshold_sigma : `float`, optional
            How many noise levels above the sky a star must stand. The
            finder's registered threshold (5) when left out.
        isolation_px : `float`, optional
            When given, a star with another found star within this many
            pixels is left out, so its light is not mixed with a neighbor's.
        debayer : `bool`, optional
            Search a raw color mosaic as 2 by 2 color super-pixels.

        Returns
        -------
        stars : `FoundStars` or `None`
            Fields: ``finder``, ``sky_level``, ``noise``,
            ``kernel_fwhm_px``, ``threshold_sigma``, ``binned_on_color``,
            ``debayered``, ``stars`` (brightest first, each with ``x`` and
            ``y`` in 1-based pixels, ``flux`` and ``merged``) and
            ``star_count``. The model says what ``flux`` is. `None` when a
            ``"daofind"`` search cannot measure the sky.

        Raises
        ------
        InvalidArgumentError
            If the finder is unknown, or a width, threshold or isolation
            distance is not above zero.
        """
        from astrometricslib.foundation.arguments import check_choice

        check_choice("finder", finder, FINDERS)
        for name, value in (
            ("fwhm_px", fwhm_px),
            ("threshold_sigma", threshold_sigma),
            ("isolation_px", isolation_px),
        ):
            if value is not None and not float(value) > 0.0:
                raise InvalidArgumentError(f"{name} must be above zero.")
        read = _read(image, debayer)
        if finder == "astrometry":
            found = self._find_with_astrometry_finder(read, fwhm_px, threshold_sigma)
        else:
            found = self._find_with_daofind(read, fwhm_px, threshold_sigma)
        if found is None:
            return None
        sources, settings = found
        positions = _source_positions(sources)
        if isolation_px is not None and len(sources):
            from astrometricslib.pipelines.shared.star_finding import isolated_sources

            keep = isolated_sources(positions, float(isolation_px) / read.cell)
            sources = [source for source, kept in zip(sources, keep, strict=True) if kept]
            positions = positions[keep]
        stars = [
            StarDetection(
                x=read.to_image_pixel(float(x) + 1.0),
                y=read.to_image_pixel(float(y) + 1.0),
                flux=float(source.get("flux", 0.0)),
                merged=int(source.get("merged_detection_count", 1)) > 1,
            )
            for (x, y), source in zip(positions, sources, strict=True)
        ]
        return FoundStars(
            finder=finder, stars=stars, star_count=len(stars), debayered=read.cell > 1, **settings
        )

    @staticmethod
    def _find_with_astrometry_finder(
        read: _Image, fwhm_px: float | None, threshold_sigma: float | None
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Run the astrometry run's star finder.

        Returns
        -------
        sources : `list` [`dict`]
            The merged sources, 0-based positions on the measured plane.
        settings : `dict`
            The `FoundStars` fields that describe the search.
        """
        from astrometricslib.pipelines.astrometry.pre_processing.source_detection import SourceDetector
        from astrometricslib.pipelines.astrometry.processing.star_detection import find_unique_stars

        color = read.pixels.ndim == 3
        detector = SourceDetector(
            fwhm=None if fwhm_px is None else float(fwhm_px) / read.cell,
            threshold_sigma=None if threshold_sigma is None else float(threshold_sigma),
            keep_close_pairs=True,
        )
        _, unique = find_unique_stars(
            detector,
            collapse_to_2d(read.pixels),
            is_color_frame=color,
            with_radius=False,
            size_kernel_from_image=fwhm_px is None,
        )
        return unique, {
            "sky_level": None,
            "noise": float(detector.last_background_noise or 0.0),
            "kernel_fwhm_px": float(detector.fwhm),
            "threshold_sigma": float(detector.threshold_sigma),
            "binned_on_color": color,
        }

    @staticmethod
    def _find_with_daofind(
        read: _Image, fwhm_px: float | None, threshold_sigma: float | None
    ) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
        """Run the plain shared finder on one sky level.

        Returns
        -------
        found : `tuple` or `None`
            The sources (0-based positions on the measured plane) and the
            `FoundStars` fields that describe the search, or `None` when the
            sky cannot be measured.
        """
        from astrometricslib.foundation.parameters import resolve
        from astrometricslib.pipelines.astrometry.pre_processing.source_detection import (
            SOURCE_DETECTOR_KERNEL_FWHM_PX,
        )
        from astrometricslib.pipelines.shared.image_scaling import measure_sky_background
        from astrometricslib.pipelines.shared.quality.star_profile_fitting import (
            STAR_FINDER_THRESHOLD_SIGMA,
        )
        from astrometricslib.pipelines.shared.star_finding import find_sources_above_background

        width = (
            float(resolve(SOURCE_DETECTOR_KERNEL_FWHM_PX).value)
            if fwhm_px is None
            else float(fwhm_px) / read.cell
        )
        threshold = (
            float(resolve(STAR_FINDER_THRESHOLD_SIGMA).value)
            if threshold_sigma is None
            else float(threshold_sigma)
        )
        plane = collapse_to_2d(read.pixels)
        measured = measure_sky_background(plane)
        if measured is None or not measured[1] > 0.0:
            return None
        sky, noise = measured
        sources = find_sources_above_background(
            np.nan_to_num(plane - sky),
            noise=noise,
            expected_fwhm_px=width,
            threshold_sigma=threshold,
            roundness_range=None,
        )
        return sources, {
            "sky_level": float(sky),
            "noise": float(noise),
            "kernel_fwhm_px": width,
            "threshold_sigma": threshold,
            "binned_on_color": False,
        }

    def star_widths(
        self,
        image: ImageInput,
        positions: list[tuple[float, float]] | None = None,
        *,
        debayer: bool = False,
    ) -> ChannelStarWidths:
        """Measure the star width of each color channel, as the preview does.

        This is the width the color preview matches between its channels.
        Each star's width is the spread of its light around its center (the
        second moment), given as the full width at half maximum (FWHM) of a
        Gaussian star of that spread. A channel's width is the median over
        the stars. The widths are for comparing the color channels of one
        image with each other (the preview's channel matching), not one image
        with another; `QualityRecords.measure_star_profile` gives widths on
        the library's one star-width scale. Nothing is stored.

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
        debayer : `bool`, optional
            Measure a raw color mosaic as 2 by 2 color super-pixels. The
            widths are given in the raw frame's pixels.

        Returns
        -------
        widths : `ChannelStarWidths`
            Fields: ``widths_px`` (one FWHM per channel, red, green and blue
            for a color image; empty with no stars; NaN for a channel where
            no star could be measured), ``estimator`` and ``star_count``.
        """
        from astrometricslib.pipelines.stacking.post_processing.star_width_match import (
            find_measurable_stars,
            measure_channel_widths,
        )

        read = _read(image, debayer)
        cube = _channels_first(read.pixels)
        if positions is None:
            three = cube if cube.shape[0] == 3 else np.repeat(cube[:1], 3, axis=0)
            stars = find_measurable_stars(three)
        else:
            stars = np.array(
                [
                    (read.to_measured_pixel(float(x)) - 1.0, read.to_measured_pixel(float(y)) - 1.0)
                    for x, y in positions
                ],
                dtype=np.float64,
            ).reshape(-1, 2)
        widths = [float(width) * read.cell for width in measure_channel_widths(cube, stars)]
        return ChannelStarWidths(widths_px=widths, star_count=len(stars))

    def measure_sky_gradient(self, image: ImageInput, *, debayer: bool = False) -> SkyGradient:
        """Measure how much the sky tilts across a frame, as the preview does.

        The image is cut into 64 by 64 bins, the bins of empty sky are kept,
        and a plane is fitted to their medians. The preview subtracts that
        plane when the tilt is below its limit, and runs GraXpert otherwise.
        The fit also says whether a vignetting term (darker corners) is
        used. Nothing is changed or stored.

        Parameters
        ----------
        image : `str` or `numpy.ndarray`
            A FITS path, or a linear pixel array (2-D, or color channels
            first).
        debayer : `bool`, optional
            Measure a raw color mosaic as 2 by 2 color super-pixels.

        Returns
        -------
        gradient : `SkyGradient`
            Fields: ``gradient_fraction``, ``limit_fraction``,
            ``plane_used``, ``sky_bin_count``, ``plane_coefficients``,
            ``radial_coefficients``, ``radial_fraction`` and ``reason``.
        """
        from astrometricslib.pipelines.stacking.post_processing.sky_plane import choose_background_method

        pixels = _read(image, debayer).pixels
        decision = choose_background_method(pixels if pixels.ndim == 2 else _channels_first(pixels))
        return SkyGradient(
            gradient_fraction=None
            if decision.gradient_fraction is None
            else float(decision.gradient_fraction),
            limit_fraction=float(decision.limit_fraction),
            plane_used=decision.method == "plane",
            sky_bin_count=int(decision.sky_bin_count),
            plane_coefficients=[(float(a), float(b)) for a, b in decision.plane_coefficients],
            radial_coefficients=[(float(a), float(b)) for a, b in decision.radial_coefficients],
            radial_fraction=None if decision.radial_fraction is None else float(decision.radial_fraction),
            reason=decision.reason,
        )

    def measure_aperture(
        self,
        image: ImageInput,
        positions: Sequence[tuple[float, float]],
        radius_px: float | None = None,
        *,
        sky_inner_px: float | None = None,
        sky_outer_px: float | None = None,
        gain: float | None = None,
        read_noise_e: float | None = None,
        channel: int | None = None,
    ) -> ApertureFluxes:
        """Measure the light of stars inside circles, with uncertainties.

        This is the library's one aperture photometry (the photometry
        pipeline's). For each position, the pixels inside the circle are
        summed (boundary pixels by the share inside), and the sky, the
        clipped median of a ring around it, is taken off. The uncertainty is
        the CCD noise equation: the star's own photon noise, the sky, the
        read noise and the noise of the sky level. That needs the gain and
        read noise: given here, else from the camera's profile for the
        frame's setting (or the header's ``EGAIN`` and ``RDNOISE``), else
        assumed as 1 electron per ADU and no read noise, and the result says
        which. The equation assumes the bias is taken off (a calibrated
        frame); on a raw frame the sky term reads high. A raw color mosaic
        is refused; a color image needs ``channel``. Nothing is stored.

        Parameters
        ----------
        image : `str` or `numpy.ndarray`
            A FITS path, or a pixel array.
        positions : `Sequence` [`tuple` [`float`, `float`]]
            The circle centers, as ``(x, y)`` in 1-based pixels, such as the
            ``stars`` of `find_stars`.
        radius_px : `float`, optional
            The circle's radius in pixels. The photometry pipeline's radius
            when left out.
        sky_inner_px, sky_outer_px : `float`, optional
            The sky ring's radii in pixels. The pipeline's ring when left out.
        gain : `float`, optional
            The gain in electrons per ADU.
        read_noise_e : `float`, optional
            The read noise in electrons.
        channel : `int`, optional
            For a color image, the channel to measure, counted from 0 (0 is
            red).

        Returns
        -------
        photometry : `ApertureFluxes`
            Fields: ``radius_px``, ``sky_inner_px``, ``sky_outer_px``,
            ``gain_e_per_adu``, ``read_noise_e``, ``noise_source`` and
            ``stars`` (each with ``x``, ``y``, ``flux`` and ``flux_error`` in
            ADU, ``signal_to_noise``, ``peak_adu`` and ``saturated``).

        Raises
        ------
        InvalidArgumentError
            If a color image is given with no channel, a channel is given
            for an image without it, or a radius, gain or read noise is out
            of range.
        """
        from astrometricslib._internal.drivers.camera_profile_store import resolve_camera_profile
        from astrometricslib.foundation.parameters import resolve
        from astrometricslib.pipelines.photometry.pre_processing.detector_noise import (
            DetectorNoise,
            resolve_detector_noise,
        )
        from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import (
            APERTURE_RADIUS_PX,
            SKY_ANNULUS_INNER_RADIUS_PX,
            SKY_ANNULUS_OUTER_RADIUS_PX,
            measure_aperture_photometry,
        )

        read = _read(image, debayer=False)
        plane = read.pixels
        if channel is not None:
            cube = _channels_first(plane)
            if plane.ndim != 3 or not 0 <= int(channel) < cube.shape[0]:
                raise InvalidArgumentError(f"channel {channel} is not a channel of this image.")
            plane = cube[int(channel)]
        elif plane.ndim != 2:
            raise InvalidArgumentError("A color image needs channel=, the channel to measure.")
        radius = float(resolve(APERTURE_RADIUS_PX).value) if radius_px is None else float(radius_px)
        inner = (
            float(resolve(SKY_ANNULUS_INNER_RADIUS_PX).value) if sky_inner_px is None else float(sky_inner_px)
        )
        outer = (
            float(resolve(SKY_ANNULUS_OUTER_RADIUS_PX).value) if sky_outer_px is None else float(sky_outer_px)
        )
        if not 0.0 < radius <= inner < outer:
            raise InvalidArgumentError("The radii must satisfy 0 < radius_px <= sky_inner_px < sky_outer_px.")
        if gain is not None and not gain > 0.0:
            raise InvalidArgumentError("gain must be above zero.")
        if read_noise_e is not None and not read_noise_e >= 0.0:
            raise InvalidArgumentError("read_noise_e cannot be negative.")
        camera = read.header.get("INSTRUME") if read.header is not None else None
        profile = resolve_camera_profile(str(camera), self._config) if camera else None
        found = resolve_detector_noise(profile, read.header) if read.header is not None else DetectorNoise()
        noise = DetectorNoise(
            gain_e_per_adu=found.gain_e_per_adu if gain is None else float(gain),
            read_noise_e=found.read_noise_e if read_noise_e is None else float(read_noise_e),
            gain_is_assumed=found.gain_is_assumed and gain is None,
            read_noise_is_assumed=found.read_noise_is_assumed and read_noise_e is None,
        )
        if gain is not None and read_noise_e is not None:
            source = "given"
        elif noise.gain_is_assumed or noise.read_noise_is_assumed:
            source = "assumed"
        else:
            source = "camera profile or header"
        saturation = float(profile.pixel_saturation_level_adu.value) if profile is not None else float("inf")
        stars = []
        for x, y in positions:
            measured = measure_aperture_photometry(
                plane,
                float(x) - 1.0,
                float(y) - 1.0,
                radius,
                inner,
                outer,
                saturation_threshold_adu=saturation,
                noise=noise,
            )
            on_image = measured.flux_error_adu > 0.0
            flux = float(measured.net_flux_adu) if on_image else None
            error = float(measured.flux_error_adu) if on_image else None
            stars.append(
                ApertureFlux(
                    x=float(x),
                    y=float(y),
                    flux=flux,
                    flux_error=error,
                    signal_to_noise=None if flux is None or not error else flux / error,
                    peak_adu=float(measured.peak_adu) if np.isfinite(measured.peak_adu) else None,
                    saturated=bool(measured.is_saturated),
                )
            )
        return ApertureFluxes(
            radius_px=radius,
            sky_inner_px=inner,
            sky_outer_px=outer,
            gain_e_per_adu=float(noise.gain_e_per_adu),
            read_noise_e=float(noise.read_noise_e),
            noise_source=source,
            stars=stars,
        )

    def sky_corners(self, path: str) -> SkyCorners | None:
        """Give the sky positions of a plate-solved image's four corners.

        The positions come from the plate solution (WCS) in the header,
        including the lens distortion terms the solver adds, at the outer
        edges of the corner pixels. Only a plate solution counts: the
        pointing cards a capture program writes are not one (see
        `astrometricslib.drivers.header_is_plate_solved`). The result names
        the sky frame (``RADESYS``) and ``EQUINOX`` the positions are in.
        Nothing is stored.

        Parameters
        ----------
        path : `str`
            A FITS image.

        Returns
        -------
        corners : `SkyCorners` or `None`
            Fields: ``corners`` (``(ra, dec)`` in degrees, in order around
            the edge), ``radesys`` and ``equinox``. `None` when the image is
            not plate-solved or has no usable sky map.

        Raises
        ------
        NotFoundError
            If the file does not exist.
        """
        from astrometricslib.pipelines.shared.header_wcs import celestial_wcs, image_corners_on_sky

        if not os.path.isfile(path):
            raise NotFoundError(f"No image at {path}.")
        header = read_header(path)
        if not header_is_plate_solved(header):
            return None
        wcs = celestial_wcs(header)
        width, height = header.get("NAXIS1"), header.get("NAXIS2")
        if wcs is None or not width or not height:
            return None
        radesys = header.get("RADESYS", header.get("RADECSYS"))
        equinox = header.get("EQUINOX", header.get("EPOCH"))
        try:
            equinox_value = None if equinox is None else float(equinox)
        except TypeError, ValueError:
            equinox_value = None
        return SkyCorners(
            corners=image_corners_on_sky(wcs, int(width), int(height)),
            radesys=None if radesys is None else str(radesys).strip(),
            equinox=equinox_value,
        )

    def solved_stars(self, stack_path: str) -> list[StellarObject]:
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
        from astrometricslib.pipelines.shared.solved_star_list import read_solved_stars

        return read_solved_stars(stack_path)

    def robust_sigma(self, values: Sequence[float] | np.ndarray) -> float:
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
        self,
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
