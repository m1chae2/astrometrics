"""Main interface for drawing pictures of images and charts of results.

Astronomical data is stored in FITS files, which hold raw measurements that
an ordinary image viewer cannot show well. `Visualization` turns them into
pictures: `render` draws one frame, stack or array, stretched so faint detail
shows, as the bytes of a PNG or JPEG file; `plot_image` draws one as a
Matplotlib figure, optionally with the detected and the named stars marked
on it; and the ``plot_<noun>`` methods draw the charts of a target's or a
star's results. Each method is a thin wrapper of one owner function, named in
its docstring; the work happens in `pipelines/shared/image_conversions.py`
and in the `visualization/` package.
"""

from collections.abc import Sequence

import numpy as np
from matplotlib.figure import Figure

from astrometricslib._internal.drivers.catalog_access import AbstractCatalogAccess
from astrometricslib._internal.renames import MovedNames
from astrometricslib.foundation.arguments import check_limit
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.models.star_catalog import StarOverlay
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.target import FrameRecord, RenderedImage, Target
from astrometricslib.visualization.sky_placement import SkyPicturePlacement

__all__ = ["Visualization"]

CHART_STAR_LIMIT = 15
"""Most stars a target chart marks and lists, unless the caller asks."""


class Visualization(MovedNames):
    """Draw FITS images as pictures, and plot the results of the pipelines.

    An image is given as a path, a frame record (from
    ``targets.frames.query``) or an array of pixels. A file that cannot be
    drawn raises `ProcessingError`. A chart of a target that lacks what the
    chart needs (a stack, catalog stars, a focuser temperature) raises
    `InvalidArgumentError`.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings. They give the frames folder.
    storage : `AbstractCatalogAccess`
        The database the library reads and writes. It holds the stars the
        charts and the overlays draw.
    """

    def __init__(self, config: AppConfiguration, storage: AbstractCatalogAccess) -> None:
        self._config = config
        self._storage = storage

    def render(
        self,
        image: str | FrameRecord | np.ndarray,
        *,
        stretch: bool = True,
        brightness: tuple[float, float] | None = None,
        crop: tuple[float, float, int] | None = None,
        max_dimensions: int | None = None,
    ) -> RenderedImage:
        """Draw a frame, a stack or an array as a picture. Nothing is saved.

        Use it to judge an image by eye: star trailing, clouds, gradients or
        a bad stack. A stretched view of a whole stack is the picture the
        enhancement stage saved beside it (a JPEG), when that picture is
        current; every other view is drawn from the pixels as a PNG. A
        Jupyter notebook shows the result as a picture.

        Owner: astrometricslib.pipelines.shared.image_conversions.render_image

        Parameters
        ----------
        image : `str`, `FrameRecord` or `numpy.ndarray`
            The FITS file's path, a frame record, or a 2-D or color array.
            A frame that stacking moved into an ``_excluded`` folder is
            found there.
        stretch : `bool`, optional
            Brighten faint detail automatically. Defaults to `True`.
            `False` draws a straight line from the darkest to the
            brightest pixel.
        brightness : `tuple` [`float`, `float`], optional
            The pixel values to draw black and white, with a straight line
            in between. It replaces ``stretch``.
        crop : `tuple` [`float`, `float`, `int`], optional
            Zoom in on one place: the column and row of its center, counted
            from 1, and the side of the square, all in full-image pixels.
            The piece is enlarged to half of ``max_dimensions``.
        max_dimensions : `int`, optional
            Longest side of the picture, from 100 to 2000 pixels. Defaults
            to 2000.

        Returns
        -------
        picture : `RenderedImage`
            The picture's bytes, its type, the brightness range shown, its
            size and the crop.

        Notes
        -----
        Raises `ProcessingError` if the file cannot be drawn. An image of
        another type, a brightness range whose black value is not below its
        white value, or a crop that is not three numbers raises
        `InvalidArgumentError`.

        Examples
        --------
        >>> target = astrometrics.targets.get("M 13")  # doctest: +SKIP
        >>> picture = astrometrics.visualization.render(
        ...     target.stacking.stacked_image
        ... )  # doctest: +SKIP
        """
        from astrometricslib.pipelines.shared.image_conversions import render_image

        return render_image(
            image, stretch=stretch, brightness=brightness, crop=crop, max_dimensions=max_dimensions
        )

    def render_newest(
        self, *, stretch: bool = True, max_dimensions: int | None = None
    ) -> RenderedImage | None:
        """Draw the newest FITS file anywhere in the frames folder.

        The frames folder holds every target's frames and the calibration
        frames, so this is the frame the camera took last, whatever it was.

        Owner:
            astrometricslib.pipelines.shared.image_conversions.render_newest_capture

        Parameters
        ----------
        stretch : `bool`, optional
            Brighten faint detail automatically. Defaults to `True`.
        max_dimensions : `int`, optional
            Longest side of the picture, from 100 to 2000 pixels. Defaults
            to 2000.

        Returns
        -------
        picture : `RenderedImage` or `None`
            The picture, or `None` if there is no FITS file or it cannot be
            drawn.
        """
        from astrometricslib.pipelines.shared.image_conversions import render_newest_capture

        return render_newest_capture(self._config, stretch=stretch, max_dimensions=max_dimensions)

    def plot_image(
        self,
        image: str | FrameRecord | np.ndarray,
        *,
        overlays: Sequence[str] | str | None = None,
        crop: tuple[float, float, int] | None = None,
        stretch: bool = True,
        brightness: tuple[float, float] | None = None,
    ) -> Figure:
        """Draw a frame, a stack or an array as a figure, at full resolution.

        The first row of pixels is at the top, as in the app's viewer, and
        the axes count full-image pixels from 1, even for a crop. A notebook
        shows the figure in the page; the app's Command Console opens it in
        a figure window. With ``overlays`` it marks what Image Processing
        marks.

        Owner: astrometricslib.visualization.image_display.plot_image

        Parameters
        ----------
        image : `str`, `FrameRecord` or `numpy.ndarray`
            The FITS file's path, a frame record, or a 2-D or color array.
        overlays : `Sequence` [`str`], `str` or `None`, optional
            What to mark on a file. None by default. ``"detections"``: the
            stars the astrometry run's star finder detects (small cyan
            circles), from the stack's ``<stack>_detections.json`` file
            when it is still valid. ``"stars"``: the stack's named stars
            that have photometry (yellow circles with names), from its
            ``<stack>_identification.json`` file. The title counts what
            each overlay drew, or says why it drew nothing. An array has no
            saved detections or names, so it takes none.
        crop : `tuple` [`float`, `float`, `int`], optional
            Zoom in on one place: the column and row of its center, counted
            from 1, and the side of the square, in full-image pixels.
        stretch : `bool`, optional
            Brighten faint detail automatically. Defaults to `True`. A
            stack's ``_processed.fits`` file is already stretched, so it is
            drawn as it is.
        brightness : `tuple` [`float`, `float`], optional
            The pixel values to draw black and white. It replaces
            ``stretch``.

        Returns
        -------
        figure : `matplotlib.figure.Figure`
            The figure.

        Notes
        -----
        Raises `InvalidArgumentError` if an overlay is unknown, overlays are
        asked for on an array, or the brightness range or the crop is not
        valid.

        Examples
        --------
        >>> figure = astrometrics.visualization.plot_image(
        ...     target.stacking.stacked_image, overlays=["detections", "stars"]
        ... )  # doctest: +SKIP
        """
        from astrometricslib.visualization.image_display import plot_image

        return plot_image(
            image, storage=self._storage, overlays=overlays, crop=crop, stretch=stretch, brightness=brightness
        )

    def plot_target(
        self, target: Target, *, star: StellarObject | None = None, limit: int = CHART_STAR_LIMIT
    ) -> Figure:
        """Draw the combined view of a processed target.

        The stack with its catalog stars beside the light curve and the
        spectrum of the chosen star. Clicking a star in a figure window
        shows its panels.

        Owner: astrometricslib.visualization.helpers.plot_target_dashboard

        Parameters
        ----------
        target : `Target`
            The target.
        star : `StellarObject`, optional
            The star to show first. Defaults to the brightest.
        limit : `int`, optional
            Most catalog stars to mark, at least 1. Defaults to 15.

        Returns
        -------
        figure : `matplotlib.figure.Figure`
            The chart.

        Notes
        -----
        Raises `InvalidArgumentError` if the target has no stack or no catalog
        stars.
        """
        from astrometricslib.visualization.helpers import plot_target_dashboard

        check_limit(limit, 1, None, allow_none=False)
        return plot_target_dashboard(target, self._storage, limit=limit, selected_star=star)

    def plot_star(self, star: StellarObject) -> Figure:
        """Draw one star's light curve above its spectrum.

        Owner: astrometricslib.visualization.helpers.plot_stellar_analysis

        Parameters
        ----------
        star : `StellarObject`
            The star. Read it with ``stars.get(star_id,
            include=["light_curves"])`` to draw its light curve.

        Returns
        -------
        figure : `matplotlib.figure.Figure`
            The chart: one panel when the star has only a light curve or
            only a spectrum.

        Notes
        -----
        Raises `InvalidArgumentError` if the star has neither a light curve nor
        a spectrum.
        """
        from astrometricslib.visualization.helpers import plot_stellar_analysis

        return plot_stellar_analysis(star)

    def plot_astrometry(self, target: Target, *, limit: int = CHART_STAR_LIMIT) -> Figure:
        """Draw a target's plate-solved star field with its catalog stars.

        Owner: astrometricslib.visualization.helpers.plot_astrometry

        Parameters
        ----------
        target : `Target`
            The target.
        limit : `int`, optional
            Most catalog stars to mark, at least 1. Defaults to 15.

        Returns
        -------
        figure : `matplotlib.figure.Figure`
            The chart.

        Notes
        -----
        Raises `InvalidArgumentError` if the target has no stack or no catalog
        stars.
        """
        from astrometricslib.visualization.helpers import plot_astrometry

        check_limit(limit, 1, None, allow_none=False)
        return plot_astrometry(target, self._storage, limit=limit)

    def plot_photometry(
        self, target: Target, *, star: StellarObject | None = None, limit: int = CHART_STAR_LIMIT
    ) -> Figure:
        """Draw a target's stars beside the light curve of one of them.

        Owner: astrometricslib.visualization.helpers.plot_target_photometry

        Parameters
        ----------
        target : `Target`
            The target.
        star : `StellarObject`, optional
            The star whose light curve to show first. Defaults to the
            brightest.
        limit : `int`, optional
            Most catalog stars to mark, at least 1. Defaults to 15.

        Returns
        -------
        figure : `matplotlib.figure.Figure`
            The chart.

        Notes
        -----
        Raises `InvalidArgumentError` if the target has no stack or no catalog
        stars.
        """
        from astrometricslib.visualization.helpers import plot_target_photometry

        check_limit(limit, 1, None, allow_none=False)
        return plot_target_photometry(target, self._storage, limit=limit, selected_star=star)

    def plot_spectroscopy(
        self, target: Target, *, star: StellarObject | None = None, limit: int = CHART_STAR_LIMIT
    ) -> Figure:
        """Draw a target's spectral stack beside the spectrum of one star.

        Owner: astrometricslib.visualization.helpers.plot_target_spectroscopy

        Parameters
        ----------
        target : `Target`
            The target.
        star : `StellarObject`, optional
            The star whose spectrum to show first. Defaults to the first
            star with an extraction box.
        limit : `int`, optional
            Most catalog stars to read, at least 1. Defaults to 15.

        Returns
        -------
        figure : `matplotlib.figure.Figure`
            The chart.

        Notes
        -----
        Raises `InvalidArgumentError` if the target has no spectral stack or no
        extracted spectrum with an extraction box.
        """
        from astrometricslib.visualization.helpers import plot_target_spectroscopy

        check_limit(limit, 1, None, allow_none=False)
        return plot_target_spectroscopy(target, self._storage, limit=limit, selected_star=star)

    def plot_focus(self, target: Target) -> Figure:
        """Draw how the focus followed the temperature through the nights.

        The focuser position and the temperature through the night, and the
        star width against the temperature with its slope in pixels per
        degree, so a focus drift as the telescope cools is easy to see.

        Owner:
            astrometricslib.visualization.focus_plots.plot_focus_vs_temperature

        Parameters
        ----------
        target : `Target`
            The target whose frames to read.

        Returns
        -------
        figure : `matplotlib.figure.Figure`
            The chart.

        Notes
        -----
        Raises `InvalidArgumentError` if no frame of the target has a focuser
        temperature.
        """
        from astrometricslib.visualization.focus_plots import plot_focus_vs_temperature

        return plot_focus_vs_temperature(target)

    def plot_asteroids(self, target: Target) -> Figure:
        """Draw a target's stack with the detected asteroid tracks.

        Owner: astrometricslib.visualization.helpers.plot_asteroid_detection

        Parameters
        ----------
        target : `Target`
            The target.

        Returns
        -------
        figure : `matplotlib.figure.Figure`
            The chart.

        Notes
        -----
        Raises `InvalidArgumentError` if the target has no stack.
        """
        from astrometricslib.visualization.helpers import plot_asteroid_detection

        return plot_asteroid_detection(target)

    def get_star_overlay(
        self, target: Target, *, image: str | None = None, limit: int | None = None
    ) -> StarOverlay:
        """Place a target's catalog stars on an image, for drawing labels.

        Each star's pixel position comes from the image's plate solution,
        or from the pixel center saved when the star was detected on the
        target's only stack. Positions count from 1, as in FITS and DS9.

        Owner:
            astrometricslib.pipelines.shared.star_overlay.place_target_stars

        Parameters
        ----------
        target : `Target`
            The target whose stars to place.
        image : `str`, optional
            The image to place them on, such as the file the app shows. By
            default, the target's current stack.
        limit : `int` or `None`, optional
            Most stars to place, at least 1, the most useful first. `None`
            (the default) places every star found.

        Returns
        -------
        overlay : `StarOverlay`
            The placed stars, and why there are none when the image has no
            sky solution.
        """
        from astrometricslib.pipelines.shared.star_overlay import place_target_stars

        return place_target_stars(self._storage, target, image_path=image, limit=limit)

    def get_sky_picture(self, target: Target) -> SkyPicturePlacement | None:
        """Say where a target's stack picture lies on the sky.

        A sky chart lays the picture over the sky. The picture is the color
        picture when the target has one, else the stack's preview. Where it
        lies comes from the stretched stack's plate solution, distortion
        terms included: a grid of points across the picture (17 along each
        side, edges included) is mapped to the sky. The answer is kept and
        worked out again only when either file changes.

        Owner: astrometricslib.visualization.sky_placement.get_sky_picture

        Parameters
        ----------
        target : `Target`
            The target.

        Returns
        -------
        placement : `SkyPicturePlacement` or `None`
            ``picture_path``, ``width``, ``height``, ``grid_size`` and
            ``grid`` (``[ra, dec]`` pairs in degrees, ICRS, row by row from
            the picture's top-left corner). `None` when the target has no
            stretched stack, no picture or no real plate solution (only the
            mount's pointing does not count); that is a status, not an
            error.
        """
        from astrometricslib.visualization.sky_placement import get_sky_picture

        return get_sky_picture(target)
