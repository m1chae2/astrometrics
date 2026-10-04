"""Main interface for plotting graphs and rendering images.

This module provides easy-to-use tools for generating charts and images
so that users do not have to interact with the complex, lower-level
visualization and data conversion code directly.
"""

from dataclasses import dataclass
from typing import Any

from matplotlib.figure import Figure

from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.target import Target

__all__ = ["ViewableImage", "Visualization"]


@dataclass
class ViewableImage:
    """A picture a client should show as an image, not as text.

    Attributes
    ----------
    png_bytes : `bytes`
        The PNG file.
    description : `dict` [`str`, `Any`]
        What the picture shows: the file, the brightness range, the size,
        and the crop. A client reads it beside the picture.
    """

    png_bytes: bytes
    description: dict[str, Any]


class Visualization:
    """Interactive plotting and FITS-to-PNG rendering entry points.

    Astronomical data is stored in FITS files, which contain raw
    scientific data that standard image viewers cannot display
    correctly. This class provides the tools to 'stretch' and convert
    that raw data into standard PNG images, making it possible to
    visually inspect your targets and the results of processing
    pipelines like photometry and astrometry.
    """

    def __init__(self, astrometrics: Any):  # ruff: ignore[missing-return-type-special-method]
        """Initialize with a back-reference to the parent `Astrometrics`.

        Parameters
        ----------
        astrometrics : `astrometricslib.Astrometrics`
            The parent astrometrics, used to resolve target frames
            (`Astrometrics.targets`) and stellar objects
            (`Astrometrics.stars`) for rendering.
        """
        self._astrometrics = astrometrics

    def render_fits(
        self,
        path: str | None = None,
        target: Target | None = None,
        file_name: str | None = None,
        max_dimensions: int = 1200,
        stretch: bool = True,
        center: float | None = None,
        width: float | None = None,
        crop_center_x: int | None = None,
        crop_center_y: int | None = None,
        crop_size: int | None = None,
    ) -> ViewableImage:
        """Draw a FITS frame or stack as a picture a client can look at.

        The result is an image, not text. Use it to judge a frame by eye:
        star trailing, clouds, gradients or a bad stack. Nothing is saved.

        Parameters
        ----------
        path : `str`, optional
            Path to the FITS file. Give this, or ``target`` and
            ``file_name``.
        target : `Target`, optional
            The target whose frame to draw, used with ``file_name``.
        file_name : `str`, optional
            Which of the target's frames: its file name, or the number at
            the end of the name, such as ``"013"``. It must match one frame.
        max_dimensions : `int`, optional
            Longest side of the picture in pixels, from 100 to 2000.
            Defaults to 1200. Smaller pictures cost the client less.
        stretch : `bool`, optional
            Brighten faint detail automatically. Defaults to `True`. A
            stack's ``_processed.fits`` file is already stretched by the
            stacking stage, so it is drawn as it is and this is ignored for
            it. The description's ``stretched`` field says what was done.
        center : `float`, optional
            Middle of a manual brightness range, in pixel values. Needs
            ``width``.
        width : `float`, optional
            Width of a manual brightness range, in pixel values. Needs
            ``center``.
        crop_center_x : `int`, optional
            Column of the centre of a zoomed piece, in full-frame pixels.
            Use it with ``crop_center_y`` and ``crop_size`` to inspect stars.
        crop_center_y : `int`, optional
            Row of the centre of the zoomed piece, in full-frame pixels.
        crop_size : `int`, optional
            Side of the zoomed square in pixels. The piece is not shrunk
            below its own size.

        Returns
        -------
        picture : `ViewableImage`
            The PNG and a description of the brightness range and crop.

        Raises
        ------
        ValueError
            If only one of ``center`` and ``width`` is given, only some of
            the crop values are given, or the frame cannot be found.
        """
        from io import BytesIO

        from PIL import Image

        from astrometricslib.drivers.image import AstrometricsImage
        from astrometricslib.pipelines.shared.image_scaling import ImageScaler
        from astrometricslib.pipelines.shared.stack_preview_path import is_processed_fits_path

        if path is None:
            path = self._find_target_frame_path(target, file_name)
        if stretch and is_processed_fits_path(path):
            stretch = False
        if (center is None) != (width is None):
            raise ValueError("Give both center and width for a manual brightness range, or neither.")
        crop_values = (crop_center_x, crop_center_y, crop_size)
        if any(value is not None for value in crop_values) and any(value is None for value in crop_values):
            raise ValueError("Give crop_center_x, crop_center_y and crop_size together, or none of them.")
        max_dimensions = max(100, min(int(max_dimensions), 2000))

        data = AstrometricsImage(path).data
        full_height, full_width = data.shape[-2:]
        crop = None
        if crop_size is not None:
            half = max(8, int(crop_size)) // 2
            left = max(0, min(int(crop_center_x) - half, full_width - 2 * half))
            top = max(0, min(int(crop_center_y) - half, full_height - 2 * half))
            data = data[..., top : top + 2 * half, left : left + 2 * half]
            crop = {"left": left, "top": top, "width": 2 * half, "height": 2 * half}

        vmin = (center - width / 2.0) if center is not None else None
        vmax = (center + width / 2.0) if center is not None else None
        img8, vmin, vmax = ImageScaler.scale_to_uint8(
            data, vmin=vmin, vmax=vmax, stretch=stretch, sample_sky=True
        )
        picture = Image.fromarray(img8)
        if picture.mode != "L":
            picture = picture.convert("L")
        longest_side = max(picture.size)
        target_side = max_dimensions if crop is None else max_dimensions // 2
        # A big frame is shrunk to fit. A small zoomed piece is enlarged so
        # single stars are easy to see.
        if longest_side > target_side or (crop is not None and longest_side < target_side):
            scale = target_side / longest_side
            picture = picture.resize(
                (round(picture.size[0] * scale), round(picture.size[1] * scale)),
                Image.NEAREST if scale > 1.0 else Image.LANCZOS,
            )
        buffer = BytesIO()
        picture.save(buffer, format="PNG", compress_level=1)
        return ViewableImage(
            png_bytes=buffer.getvalue(),
            description={
                "path": path,
                "full_frame_size": {"width": int(full_width), "height": int(full_height)},
                "crop": crop,
                "picture_size": {"width": picture.size[0], "height": picture.size[1]},
                "brightness_range_shown": {"minimum": float(vmin), "maximum": float(vmax)},
                "stretched": bool(stretch),
            },
        )

    @staticmethod
    def _find_target_frame_path(target: Target | None, file_name: str | None) -> str:
        """Find the one frame of a target that a file name or number names.

        Parameters
        ----------
        target : `Target` or `None`
            The target to search.
        file_name : `str` or `None`
            A file name, or the number at the end of one.

        Returns
        -------
        path : `str`
            The frame's path.

        Raises
        ------
        ValueError
            If a target and file name are missing, or no frame or several
            frames match.
        """
        import os

        from astrometricslib.pipelines.shared.quality.frame_selection import file_in_range

        if target is None or not file_name:
            raise ValueError("Give a path, or a target and a file_name.")
        matches = [
            frame.path
            for frame in target.frames
            if file_in_range(os.path.basename(frame.path), file_name, file_name)
        ]
        if len(matches) != 1:
            raise ValueError(
                f"{file_name!r} matches {len(matches)} frames of {target.id}; give the full file name."
            )
        return matches[0]

    def convert_fits_to_png(
        self, path: str, max_dimensions: int = 2000, stretch: bool = True
    ) -> dict[str, Any] | None:
        """Convert a raw FITS data file into a viewable PNG image.

        Parameters
        ----------
        path : `str`
            Path to the FITS file to convert.
        max_dimensions : `int`, optional
            Maximum output dimension in pixels. Defaults to 2000.
        stretch : `bool`, optional
            Whether to automatically adjust the brightness and contrast
            (stretch) so faint details are visible. Defaults to `True`.

        Returns
        -------
        png_data : `dict[str, Any]` or `None`
            The base64-encoded PNG and scale metadata, or `None` if
            conversion fails.
        """
        from astrometricslib.pipelines.shared import image_conversions

        return image_conversions.convert_fits_to_png(path, max_dimensions, stretch)

    def convert_fits_to_png_with_stats(
        self,
        path: str,
        max_dimensions: int = 2000,
        center: float | None = None,
        width: float | None = None,
        cmap: str = "gray",
        stretch: bool = True,
    ) -> tuple[bytes, float, float]:
        """Convert FITS data to PNG bytes and report the brightness scale used.

        Parameters
        ----------
        path : `str`
            Path to the FITS file to convert.
        max_dimensions : `int`, optional
            Maximum output dimension in pixels. Defaults to 2000.
        center : `float`, optional
            Manually set the midpoint of the brightness stretch.
        width : `float`, optional
            Manually set the width (contrast) of the brightness stretch.
        cmap : `str`, optional
            Matplotlib colormap name. Defaults to ``"gray"``.
        stretch : `bool`, optional
            Whether to automatically adjust the brightness and contrast
            (stretch) so faint details are visible. Defaults to `True`.

        Returns
        -------
        result : `tuple[bytes, float, float]`
            A tuple ``(png_bytes, min_value, max_value)`` of the raw
            PNG bytes and the scale bounds used to render them.
        """
        from astrometricslib.pipelines.shared import image_conversions

        return image_conversions.convert_fits_to_png_with_stats(
            path, max_dimensions, center, width, cmap, stretch
        )

    def get_light_frame_data(
        self, target: Target, iso: str, exposure: str, index: int = 0, stretch: bool = True
    ) -> dict[str, Any]:
        """Find a light frame image and convert it into a viewable PNG.

        Parameters
        ----------
        target : `Target`
            The target whose frames are searched.
        iso : `str`
            The ISO/gain setting to match.
        exposure : `str`
            The exposure length to match.
        index : `int`, optional
            Which matching frame to use, by order. Defaults to 0.
        stretch : `bool`, optional
            Whether to automatically adjust the brightness and contrast
            (stretch) so faint details are visible. Defaults to `True`.

        Returns
        -------
        light_frame_data : `dict[str, Any]`
            The scaled base64 PNG data and associated metadata.
        """
        from astrometricslib.pipelines.shared import image_conversions

        return image_conversions.get_light_frame_data(target, iso, exposure, index, stretch)

    def get_last_captured_image(self, stretch: bool = True) -> dict[str, Any] | None:
        """Find the newest FITS image file and convert it to a viewable PNG.

        Parameters
        ----------
        stretch : `bool`, optional
            Whether to automatically adjust the brightness and contrast
            (stretch) so faint details are visible. Defaults to `True`.

        Returns
        -------
        image_data : `dict[str, Any]` or `None`
            The scaled base64 PNG data for the most recently modified
            FITS file, or `None` if no FITS file is found.
        """
        from astrometricslib.pipelines.shared import image_conversions

        return image_conversions.get_last_captured_image(self._astrometrics.config, stretch)

    def plot_target_dashboard(
        self,
        target: Target,
        limit: int = 15,
        figsize: tuple[int, int] = (16, 9),
        selected_star: StellarObject | None = None,
    ) -> Figure:
        """Render the interactive combined dashboard for a processed target.

        Parameters
        ----------
        target : `Target`
            The target to render.
        limit : `int`, optional
            Maximum number of stars to plot. Defaults to 15.
        figsize : `tuple` [`int`, `int`], optional
            Matplotlib figure size, in inches. Defaults to ``(16, 9)``.
        selected_star : `StellarObject`, optional
            A star to highlight in the light-curve panel.

        Returns
        -------
        fig : `matplotlib.figure.Figure`
            Matplotlib figure instance.
        """
        from astrometricslib.visualization.helpers import plot_target_dashboard

        return plot_target_dashboard(
            target,
            self._astrometrics.stars,
            limit=limit,
            figsize=figsize,
            selected_star=selected_star,
        )

    def plot_star_dashboard(
        self,
        star: StellarObject,
        spectral_star: StellarObject | None = None,
        figsize: tuple[int, int] = (9, 8),
    ) -> Figure:
        """Render a single star's light curve and spectrum stacked.

        Parameters
        ----------
        star : `StellarObject`
            The star to render.
        spectral_star : `StellarObject`, optional
            A separate stellar object holding the spectral observation,
            if different from `star`.
        figsize : `tuple` [`int`, `int`], optional
            Matplotlib figure size, in inches. Defaults to ``(9, 8)``.

        Returns
        -------
        fig : `matplotlib.figure.Figure`
            Matplotlib figure instance.
        """
        from astrometricslib.visualization.helpers import plot_stellar_analysis

        return plot_stellar_analysis(star, spectral_star=spectral_star, figsize=figsize)

    def plot_astrometry(self, target: Target, limit: int = 15, figsize: tuple[int, int] = (10, 10)) -> Figure:
        """Render a target's astrometry-solved star field.

        Parameters
        ----------
        target : `Target`
            The target to render.
        limit : `int`, optional
            Maximum number of stars to plot. Defaults to 15.
        figsize : `tuple` [`int`, `int`], optional
            Matplotlib figure size, in inches. Defaults to ``(10, 10)``.

        Returns
        -------
        fig : `matplotlib.figure.Figure`
            Matplotlib figure instance.
        """
        from astrometricslib.visualization.helpers import plot_astrometry

        return plot_astrometry(target, self._astrometrics.stars, limit=limit, figsize=figsize)

    def plot_photometry(self, target: Target, limit: int = 15, figsize: tuple[int, int] = (16, 9)) -> Figure:
        """Render a target's interactive 2-panel photometry dashboard.

        Parameters
        ----------
        target : `Target`
            The target to render.
        limit : `int`, optional
            Maximum number of stars to plot. Defaults to 15.
        figsize : `tuple` [`int`, `int`], optional
            Matplotlib figure size, in inches. Defaults to ``(16, 9)``.

        Returns
        -------
        fig : `matplotlib.figure.Figure`
            Matplotlib figure instance.
        """
        from astrometricslib.visualization.helpers import plot_target_photometry

        return plot_target_photometry(target, self._astrometrics.stars, limit=limit, figsize=figsize)

    def plot_spectroscopy(
        self, target: Target, limit: int = 15, figsize: tuple[int, int] = (16, 9)
    ) -> Figure:
        """Render a target's interactive 2-panel spectroscopy dashboard.

        Parameters
        ----------
        target : `Target`
            The target to render.
        limit : `int`, optional
            Maximum number of stars to plot. Defaults to 15.
        figsize : `tuple` [`int`, `int`], optional
            Matplotlib figure size, in inches. Defaults to ``(16, 9)``.

        Returns
        -------
        fig : `matplotlib.figure.Figure`
            Matplotlib figure instance.
        """
        from astrometricslib.visualization.helpers import plot_target_spectroscopy

        return plot_target_spectroscopy(target, self._astrometrics.stars, limit=limit, figsize=figsize)

    def plot_focus_vs_temperature(self, target: Target, figsize: tuple[int, int] = (14, 9)) -> Figure:
        """Render a target's focuser position, temperature and focus quality.

        Shows the focuser temperature and position through the night, the
        position against the temperature, and (when frames have one) the
        star-image width against the temperature with its slope in pixels
        per degree, so a focus drift as the telescope cools is easy to see.

        Parameters
        ----------
        target : `Target`
            The target to render. Its light frames must carry a focuser
            temperature.
        figsize : `tuple` [`int`, `int`], optional
            Matplotlib figure size, in inches. Defaults to ``(14, 9)``.

        Returns
        -------
        fig : `matplotlib.figure.Figure`
            Matplotlib figure instance. A target with no focuser
            temperature raises `ValueError`.
        """
        from astrometricslib.visualization.focus_plots import plot_focus_vs_temperature

        return plot_focus_vs_temperature(target, figsize=figsize)

    def plot_asteroid_detection(self, target: Target, figsize: tuple[int, int] = (10, 10)) -> Figure:
        """Render a target's stacked image with detected asteroid tracks.

        Parameters
        ----------
        target : `Target`
            The target to render.
        figsize : `tuple` [`int`, `int`], optional
            Matplotlib figure size, in inches. Defaults to ``(10, 10)``.

        Returns
        -------
        fig : `matplotlib.figure.Figure`
            Matplotlib figure instance.
        """
        from astrometricslib.visualization.helpers import plot_asteroid_detection

        return plot_asteroid_detection(target, figsize=figsize)
