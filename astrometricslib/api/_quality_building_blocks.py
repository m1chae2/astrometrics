"""Purpose: `QualityDetections` and `QualityBlocks`, measurements of one image.

Description: The Command Console preloads only the `astrometrics` and
`wayfinder` objects, so a script reaches the library through its public
methods. These two objects give a script the measurements the pipelines make
on one image:

* `QualityDetections` (``astrometrics.quality.detections``): one star's
  radial profile (`measure_profile`), the stars the astrometry run's star
  finder detects (`measure_detections`), and the star width of each color
  channel (`measure_widths`).
* `QualityBlocks` (``astrometrics.quality.blocks``): the small measurements a
  script combines: `measure_sky`, `measure_stars`, `measure_sky_gradient`,
  `measure_aperture`, `measure_sky_corners`, `measure_robust_sigma` and
  `measure_clipped_values`.

An image argument is a FITS path, a frame record or a pixel array. Every
position is in 1-based pixels (as in FITS and DS9). Nothing is stored. Each
method is a thin wrapper of one owner function, which its docstring names;
most are in `astrometricslib.pipelines.stacking.building_blocks`.
"""

import os
from collections.abc import Sequence
from typing import Literal

import numpy as np

from astrometricslib._internal.renames import MovedNames
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.models.quality.detections import DetectionsMeasurement
from astrometricslib.models.quality.image_measurements import (
    ApertureFluxes,
    ChannelStarWidths,
    FoundStars,
    SkyBackground,
    SkyCorners,
    SkyGradient,
)
from astrometricslib.models.quality.star_profile import StarProfileMeasurement
from astrometricslib.models.target import FrameRecord
from astrometricslib.pipelines.stacking.building_blocks import FINDERS, ImageInput

__all__ = ["FINDERS", "ImageInput", "QualityBlocks", "QualityDetections"]


class QualityDetections(MovedNames):
    """One star's profile, the stars a finder detects, and widths by color.

    Reached as ``astrometrics.quality.detections``. Nothing is stored.
    """

    def measure_profile(
        self,
        path: str,
        position: Sequence[float],
        *,
        radius_px: float | None = None,
        cut_angle_deg: float = 0.0,
        cut_half_length_px: float | None = None,
        search_radius_px: float | None = None,
    ) -> StarProfileMeasurement:
        """Measure one star's radial profile and a straight cut through it.

        Owner:
            astrometricslib.pipelines.photometry.star_inspection.measure_star_profile

        Nothing is stored. The clicked position is refined to the
        brightness-weighted centroid in a small box. The sky is the
        sigma-clipped median of a ring just outside the radius. The radial
        profile lists every pixel within the radius with its distance from
        the center and its value above the sky, and fits a round Moffat curve
        and a round Gaussian curve. The cut reads values along a straight
        line through the center (bilinear interpolation), which shows a
        one-sided flare or a close pair. A color image is averaged over its
        channels, and the result says so.

        Parameters
        ----------
        path : `str`
            A FITS image.
        position : `Sequence` [`float`]
            The star's ``(x, y)`` in 1-based pixels (as in FITS and DS9):
            the center of the first pixel is (1, 1).
        radius_px : `float`, optional
            The radius of the profile, at least 3 pixels. Taken from the
            star width when left out.
        cut_angle_deg : `float`, optional
            The cut's direction, measured from +y (increasing row) toward
            +x (increasing column), in image pixel axes. 0 runs along a
            column, 90 along a row. Defaults to 0.
        cut_half_length_px : `float`, optional
            How far the cut reaches each way from the center. Taken from
            the star width when left out.
        search_radius_px : `float`, optional
            When given, the position first moves to the brightest peak of
            the lightly smoothed image within this many pixels (capped at
            50), so a click that lands beside a star still finds it. Left
            out, only the small centroid box refines the click.

        Returns
        -------
        profile : `StarProfileMeasurement`
            Fields: ``clicked_x``, ``clicked_y``, ``center_x``, ``center_y``,
            ``sky``, ``sky_noise``, ``profile_radius_px`` and
            ``profile_values``, ``peak``, ``clipped`` with ``clipped_reason``,
            ``moffat`` and ``gaussian`` (each with ``fwhm_px``, its
            ``estimator`` and ``fwhm_on_scale_px``, ``fwhm_arcsec``,
            ``peak``, ``beta``, curve points and ``r_squared``),
            ``cut_distance_px`` and ``cut_values``, then ``cutout`` (with
            ``cutout_origin_x`` and ``cutout_origin_y``), ``snapped``,
            ``snap_distance_px``, ``star_found`` and ``not_a_star_reason``.
            The ring around the star is in the ``ring_*`` fields: its radii,
            the 5 degree direction bins, the Fourier terms
            ``ring_harmonics`` (orders 1 to 8), the neighbor stars left out
            and ``ring_note``. Every angle runs from +y toward +x, like the
            cut. ``peak_over_sky_noise``, ``edge_distance_px``,
            ``coverage_fraction``, the photometry circle and sky ring radii
            and the neighbor counts say whether the star is measurable. The
            core shape is ``star_elongation`` (from the same second-moment
            estimator as the frame's ``frame.star_elongation``),
            ``star_elongation_angle_deg``,
            ``star_elongation_position_angle_deg`` (on the sky, when the
            image is plate solved), ``star_shape_judgeable`` with
            ``star_shape_note``, and ``trailing_elongation_limit`` (the
            stacking limit for a trailed frame).

        Notes
        -----
        A position outside the image, a radius under 3 pixels or a number
        that is not finite raises `InvalidArgumentError`; a missing file
        raises `NotFoundError`.
        """
        from astrometricslib.pipelines.photometry.star_inspection import measure_star_profile

        return measure_star_profile(
            path,
            position,
            radius_px=radius_px,
            cut_angle_deg=cut_angle_deg,
            cut_half_length_px=cut_half_length_px,
            search_radius_px=search_radius_px,
        )

    def measure_detections(self, path: str) -> DetectionsMeasurement:
        """List the stars the astrometry run's star finder detects in an image.

        Owner:
            astrometricslib.pipelines.astrometry.processing.detections.measure_detections_in_file

        A saved list is reused while it still matches the file's size and
        modified time: first the ``<stack>_detections.json`` the astrometry
        run saves beside a stack, then a copy in the cache folder. Otherwise
        the image is searched now, with the same function the astrometry run
        uses (the star-width-scaled kernel, the merge of a star reported
        twice, and the averaged copy of a color image), so a marker drawn from
        the result is a star the run finds, and the result is saved to the
        cache folder for next time. Nothing is written to the database or the
        image.

        Parameters
        ----------
        path : `str`
            A FITS image.

        Returns
        -------
        detections : `DetectionsMeasurement`
            Fields: ``path``, ``width`` and ``height``, ``detections`` (each
            with ``x`` and ``y`` in 1-based pixels, ``flux`` and ``merged``,
            `True` when two reports of one star were merged),
            ``detection_count``, ``merged_count``, ``kernel_fwhm_px``,
            ``binned_on_color``, ``star_width_px`` (this image's star width,
            which sizes the circle) and ``aperture_radius_px``, the radius of
            the circle photometry measures each star in.

        Notes
        -----
        A missing file raises `NotFoundError`; an image with no pixels raises
        `InvalidArgumentError`.
        """
        from astrometricslib.pipelines.astrometry.processing.detections import measure_detections_in_file

        return measure_detections_in_file(path)

    def measure_widths(
        self,
        image: ImageInput,
        positions: Sequence[tuple[float, float]] | None = None,
        *,
        debayer: bool = False,
    ) -> ChannelStarWidths:
        """Measure the star width of each color channel, as the preview does.

        Owner:
            astrometricslib.pipelines.stacking.building_blocks.measure_channel_star_widths

        This is the width the color preview matches between its channels.
        Each star's width is the spread of its light around its center (the
        second moment), given as the full width at half maximum (FWHM) of a
        Gaussian star of that spread. A channel's width is the median over
        the stars. The widths are for comparing the color channels of one
        image with each other (the preview's channel matching), not one image
        with another; `quality.detections.measure_profile` gives widths on
        the library's one star-width scale. Nothing is stored.

        Parameters
        ----------
        image : `str` or `numpy.ndarray`
            A FITS path, or a linear pixel array (2-D, or color channels
            first).
        positions : `list` [`tuple` [`float`, `float`]], optional
            The stars to measure, as ``(x, y)`` in 1-based pixels, such as
            the ``stars`` of `measure_stars`. By default the preview's own
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
        from astrometricslib.pipelines.stacking.building_blocks import measure_channel_star_widths

        return measure_channel_star_widths(
            image, None if positions is None else list(positions), debayer=debayer
        )


class QualityBlocks(MovedNames):
    """The library's small measurements, for a script to combine.

    Reached as ``astrometrics.quality.blocks``. Each method takes a FITS
    path, a frame record or a pixel array, gives plain numbers, and stores
    nothing. Positions are 1-based pixels. A raw color mosaic is refused
    unless ``debayer=True`` (see the module text). A path that names no file
    raises `NotFoundError`; an image with no pixels, or one that is not 2-D
    or 3-D, raises `InvalidArgumentError`.

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

        Owner:
            astrometricslib.pipelines.stacking.building_blocks.measure_image_sky

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

        Notes
        -----
        Raises `InvalidArgumentError` if only one of ``bias_adu`` and
        ``gain`` is given, or the gain is not above zero.
        """
        from astrometricslib.pipelines.stacking.building_blocks import measure_image_sky

        return measure_image_sky(image, bias_adu=bias_adu, gain=gain, debayer=debayer)

    def measure_stars(
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

        Owner:
            astrometricslib.pipelines.stacking.building_blocks.find_image_stars

        ``"astrometry"`` (the default) is the finder the astrometry run and
        `quality.detections.measure_detections` use: it subtracts a sky map
        that follows the image, merges a star reported twice, searches a
        color image on an averaged smaller copy, and sizes its template from
        the image's own star width unless ``fwhm_px`` is given. Its
        roundness limits leave out stretched shapes such as nebula
        filaments. ``"daofind"`` is the plain shared finder (DAOStarFinder)
        the frame measurement uses: one sky level as `measure_sky` measures
        it, every roundness kept (so a trailed star is found), a color image
        searched on the mean of its channels.
        `quality.detections.measure_detections` is the ``"astrometry"``
        search of one file at the run's own settings, saved for reuse and
        with the photometry circle size. Nothing is stored.

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

        Notes
        -----
        Raises `InvalidArgumentError` if the finder is unknown, or a
        width, threshold or isolation distance is not above zero.
        """
        from astrometricslib.pipelines.stacking.building_blocks import find_image_stars

        return find_image_stars(
            image,
            finder=finder,
            fwhm_px=fwhm_px,
            threshold_sigma=threshold_sigma,
            isolation_px=isolation_px,
            debayer=debayer,
        )

    def measure_sky_gradient(self, image: ImageInput, *, debayer: bool = False) -> SkyGradient:
        """Measure how much the sky tilts across a frame, as the preview does.

        Owner:
            astrometricslib.pipelines.stacking.building_blocks.measure_image_sky_gradient

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
        from astrometricslib.pipelines.stacking.building_blocks import measure_image_sky_gradient

        return measure_image_sky_gradient(image, debayer=debayer)

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

        Owner:
            astrometricslib.pipelines.stacking.building_blocks.measure_image_aperture

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
            ``stars`` of `measure_stars`.
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

        Notes
        -----
        Raises `InvalidArgumentError` if a color image is given with no
        channel, a channel is given for an image without it, or a
        radius, gain or read noise is out of range.
        """
        from astrometricslib.pipelines.stacking.building_blocks import measure_image_aperture

        return measure_image_aperture(
            image,
            positions,
            radius_px,
            sky_inner_px=sky_inner_px,
            sky_outer_px=sky_outer_px,
            gain=gain,
            read_noise_e=read_noise_e,
            channel=channel,
            config=self._config,
        )

    def measure_sky_corners(self, path: str | os.PathLike[str] | FrameRecord) -> SkyCorners | None:
        """Give the sky positions of a plate-solved image's four corners.

        Owner:
            astrometricslib.pipelines.stacking.building_blocks.measure_image_sky_corners

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

        Notes
        -----
        Raises `NotFoundError` if the file does not exist.
        """
        from astrometricslib.pipelines.stacking.building_blocks import measure_image_sky_corners

        return measure_image_sky_corners(path)

    def measure_robust_sigma(self, values: Sequence[float] | np.ndarray) -> float:
        """Measure a spread of numbers that a few stray values cannot move.

        Owner:
            astrometricslib.pipelines.stacking.building_blocks.robust_spread

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
        from astrometricslib.pipelines.stacking.building_blocks import robust_spread

        return robust_spread(values)

    def measure_clipped_values(
        self,
        values: Sequence[float] | np.ndarray,
        *,
        clip_sigma: float = 3.0,
        maximum_passes: int = 5,
        minimum_count: int = 3,
    ) -> list[float] | None:
        """Drop outlying numbers, again and again, until none is dropped.

        Owner:
            astrometricslib.pipelines.stacking.building_blocks.clipped_values

        Each pass drops every value farther than ``clip_sigma`` robust
        spreads (`robust_spread`) from the median of the values kept so far.
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
        from astrometricslib.pipelines.stacking.building_blocks import clipped_values

        return clipped_values(
            values, clip_sigma=clip_sigma, maximum_passes=maximum_passes, minimum_count=minimum_count
        )
