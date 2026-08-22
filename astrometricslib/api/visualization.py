"""Layer-1 domain high-level interface for plotting and image rendering.

Thin wrappers around `astrometricslib.visualization.helpers` and
`astrometricslib.data_access.image_conversions`, exposed on the
client so callers render/plot through the high-level interface
rather than importing those modules directly.
"""

from typing import Any

from matplotlib.figure import Figure

from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.target import Target

__all__ = ["Visualization"]


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

    def convert_fits_to_png(
        self, path: str, max_dimensions: int = 2000, stretch: bool = True
    ) -> dict[str, Any] | None:
        """Convert a FITS file to a base64 PNG with min/max scale values.

        Parameters
        ----------
        path : `str`
            Path to the FITS file to convert.
        max_dimensions : `int`, optional
            Maximum output dimension in pixels. Defaults to 2000.
        stretch : `bool`, optional
            Whether to apply the stretch/normalization before
            rendering. Defaults to `True`.

        Returns
        -------
        png_data : `dict[str, Any]` or `None`
            The base64-encoded PNG and scale metadata, or `None` if
            conversion fails.
        """
        from astrometricslib.data_access import image_conversions

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
        """Perform standard FITS scaling and return raw PNG bytes and stats.

        Parameters
        ----------
        path : `str`
            Path to the FITS file to convert.
        max_dimensions : `int`, optional
            Maximum output dimension in pixels. Defaults to 2000.
        center : `float`, optional
            Stretch center override.
        width : `float`, optional
            Stretch width override.
        cmap : `str`, optional
            Matplotlib colormap name. Defaults to ``"gray"``.
        stretch : `bool`, optional
            Whether to apply the stretch/normalization before
            rendering. Defaults to `True`.

        Returns
        -------
        result : `tuple[bytes, float, float]`
            A tuple ``(png_bytes, min_value, max_value)`` of the raw
            PNG bytes and the scale bounds used to render them.
        """
        from astrometricslib.data_access import image_conversions

        return image_conversions.convert_fits_to_png_with_stats(
            path, max_dimensions, center, width, cmap, stretch
        )

    def get_light_frame_data(
        self, target: Target, iso: str, exposure: str, index: int = 0, stretch: bool = True
    ) -> dict[str, Any]:
        """Find a light frame record and scale it to base64 PNG data.

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
            Whether to apply the stretch/normalization before
            rendering. Defaults to `True`.

        Returns
        -------
        light_frame_data : `dict[str, Any]`
            The scaled base64 PNG data and associated metadata.
        """
        from astrometricslib.data_access import image_conversions

        return image_conversions.get_light_frame_data(target, iso, exposure, index, stretch)

    def get_last_captured_image(self, stretch: bool = True) -> dict[str, Any] | None:
        """Locate and return the most recently modified FITS image file.

        Parameters
        ----------
        stretch : `bool`, optional
            Whether to apply the stretch/normalization before
            rendering. Defaults to `True`.

        Returns
        -------
        image_data : `dict[str, Any]` or `None`
            The scaled base64 PNG data for the most recently modified
            FITS file, or `None` if no FITS file is found.
        """
        from astrometricslib.data_access import image_conversions

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
