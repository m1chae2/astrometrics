"""Purpose: High-level API entry points for visualization tools.

Description: Contains functions for rendering star fields, light curves,
spectra, and multi-panel target dashboards.
"""

from typing import Any

import matplotlib.pyplot as plt
import numpy as np

from astrometricslib.utilities.image import AstrometricsImage

from .layers import ImageOverlay, PhotometryOverlay, SpectrumOverlay, StarOverlay
from .star_field_visualization import _AnalysisView
from .visualization_config import VisualizationConfig


def plot_fits_star_field(  # ruff: ignore[missing-return-type-undocumented-public-function]
    image_data: np.ndarray | None = None,
    stellar_objects: list | None = None,
    ax: plt.Axes | None = None,
    active_index: int = 0,
    percentile: float = 99.5,
    title: str = "FITS Image with Detected Stars",
    draw_dispersion_rectangles: bool = True,
    limit: int | None = None,
    fits_path: str | None = None,
    target: Any | None = None,
):
    """Render a 2D FITS image with star selection circles and text labels.

    Parameters
    ----------
    image_data : `np.ndarray`, optional
        2D pixel array of the FITS image.
    stellar_objects : `list`, optional
        List of stellar objects or coordinate dicts.
    ax : `plt.Axes`, optional
        Matplotlib axis to render into. Creates figure if `None`.
    active_index : `int`, optional
        Active star index, default `0`.
    percentile : `float`, optional
        Percentile intensity clipping for black/white image scaling,
        default `99.5`.
    title : `str`, optional
        Axis title.
    draw_dispersion_rectangles : `bool`, optional
        Whether to render green dispersion trace boxes, default `True`.
    limit : `int` or `None`, optional
        Maximum number of stars to display overlays for. If `None`,
        displays all.
    fits_path : `str`, optional
        Path to FITS file to load image data from if `image_data` is `None`.
    target : `Target`, optional
        Target instance to load `stacked_image` FITS file from if
        `image_data` is `None`.

    Returns
    -------
    ax : `plt.Axes`
        Matplotlib axis rendering the FITS star field.
    """
    if stellar_objects is None:
        stellar_objects = []

    if image_data is None and target is not None:
        fits_path = getattr(target, "stacked_image", None) or fits_path

    if image_data is None and fits_path:
        from astropy.io import fits

        with fits.open(fits_path, memmap=False) as hdul:
            image_data = hdul[0].data

    if ax is None:
        _fig, ax = plt.subplots(figsize=(8, 8))
        plt.style.use("dark_background")

    config = VisualizationConfig()
    layer_image = ImageOverlay(ax, config)
    layer_stars = StarOverlay(ax, config)

    layer_image.render(image_data, percentile, title=title)
    layer_stars.render(stellar_objects, active_index=active_index, limit=limit)

    if draw_dispersion_rectangles:
        from .layers import DispersionOverlay

        layer_dispersion = DispersionOverlay(ax, config)
        layer_dispersion.render(stellar_objects, active_index=active_index, limit=limit)

    plt.show()
    return ax


def plot_spectroscopy_analysis(
    spectroscopy_analysis_results: dict | None = None,
    figsize: tuple[int, int] = (9, 11),
    hspace: float = 0.45,
    show_balmer_lines: bool = True,
    limit: int | None = None,
    **kwargs,  # ruff: ignore[missing-type-kwargs]
) -> _AnalysisView:
    """Create interactive 2-panel composite plot (2D Star Field + 1D Spectrum).

    Parameters
    ----------
    spectroscopy_analysis_results : `dict`, optional
        Result dictionary returned from `analyze_target` with
        `type="spectroscopy"`.
    figsize : `tuple[int, int]`, optional
        Figure dimensions, default `(9, 11)`.
    hspace : `float`, optional
        Subplot vertical spacing, default `0.45`.
    show_balmer_lines : `bool`, optional
        Whether Balmer line overlays are visible by default, default `True`.
    limit : `int` or `None`, optional
        Maximum number of stars to display overlays for. If `None`,
        displays all.
    **kwargs
        Flexible keyword arguments for context, stellar_object, image_data,
        or fits_path.

    Returns
    -------
    visualization : `_AnalysisView`
        Interactive visualization controller instance.
    """
    if spectroscopy_analysis_results is None:
        spectroscopy_analysis_results = {}

    context = kwargs.get("context") or spectroscopy_analysis_results.get("context")
    stellar_objects = kwargs.get("stellar_objects") or spectroscopy_analysis_results.get(
        "stellar_objects", []
    )
    if not stellar_objects and "stellar_object" in kwargs:
        stellar_objects = [kwargs["stellar_object"]]

    if context is None and kwargs.get("fits_path"):
        from astrometricslib.analysis_context import AnalysisContext

        context = AnalysisContext(image=AstrometricsImage(kwargs["fits_path"]))

    plt.close("all")
    plt.style.use("dark_background")

    fig, (ax_image, ax_spectrum) = plt.subplots(
        2, 1, figsize=figsize, gridspec_kw={"height_ratios": [2, 1], "hspace": hspace}
    )

    visualization = _AnalysisView(
        context=context,
        enriched_objects=stellar_objects,
        fig=fig,
        ax_image=ax_image,
        ax_spectrum=ax_spectrum,
        mode="spectroscopy",
    )
    if show_balmer_lines and hasattr(visualization, "renderer_spectrum"):
        visualization.renderer_spectrum.balmer_lines_visible = True

    visualization.plot(block=False, add_buttons=True, limit=limit)
    plt.show()

    return visualization


def plot_photometry_analysis(
    photometry_analysis_results: dict | None = None,
    figsize: tuple[int, int] = (9, 11),
    hspace: float = 0.45,
    limit: int | None = None,
    **kwargs,  # ruff: ignore[missing-type-kwargs]
) -> _AnalysisView:
    """Create interactive 2-panel plot (2D Star Field + 1D Light Curve).

    Parameters
    ----------
    photometry_analysis_results : `dict`, optional
        Result dictionary returned from `analyze_target` with
        `type="photometry"`.
    figsize : `tuple[int, int]`, optional
        Figure dimensions, default `(9, 11)`.
    hspace : `float`, optional
        Subplot vertical spacing, default `0.45`.
    limit : `int` or `None`, optional
        Maximum number of stars to display overlays for. If `None`,
        displays all.
    **kwargs
        Flexible keyword arguments for context or stellar_objects.

    Returns
    -------
    visualization : `_AnalysisView`
        Interactive visualization controller instance.
    """
    if photometry_analysis_results is None:
        photometry_analysis_results = {}

    context = kwargs.get("context") or photometry_analysis_results.get("context")
    raw_objects = (
        kwargs.get("stellar_objects")
        or photometry_analysis_results.get("variable_candidates")
        or photometry_analysis_results.get("variableCandidates")
        or photometry_analysis_results.get("stellar_objects")
        or photometry_analysis_results.get("stellarObjects")
        or (context.stellar_objects if context and getattr(context, "stellar_objects", None) else [])
    )
    # Prioritize stars that have populated light curve timestamps
    lc_objects = [
        obj
        for obj in raw_objects
        if getattr(obj, "light_curve", None) is not None
        and getattr(obj.light_curve, "timestamps", None)
        and len(obj.light_curve.timestamps) > 0
    ]
    stellar_objects = lc_objects if lc_objects else raw_objects

    plt.close("all")
    plt.style.use("dark_background")

    fig, (ax_image, ax_spectrum) = plt.subplots(
        2, 1, figsize=figsize, gridspec_kw={"height_ratios": [2, 1], "hspace": hspace}
    )

    visualization = _AnalysisView(
        context=context,
        enriched_objects=stellar_objects,
        fig=fig,
        ax_image=ax_image,
        ax_spectrum=ax_spectrum,
        mode="photometry",
    )

    visualization.plot(block=False, add_buttons=False, limit=limit)
    plt.show()

    return visualization


def plot_stellar_photometry(
    star: Any,
    ax: plt.Axes | None = None,
    figsize: tuple[int, int] = (9, 5),
) -> plt.Figure:
    """Render a single star's differential light curve.

    Parameters
    ----------
    star : `Any`
        Stellar object containing a `.light_curve` attribute.
    ax : `plt.Axes`, optional
        Existing Matplotlib axis to render into. Creates figure if `None`.
    figsize : `tuple[int, int]`, optional
        Figure dimensions if creating a new figure, default `(9, 5)`.

    Returns
    -------
    fig : `plt.Figure`
        Matplotlib figure object containing the plot.

    Raises
    ------
    ValueError
        If the star has no light_curve attribute or data.
    """
    light_curve = getattr(star, "light_curve", None)
    if light_curve is None:
        raise ValueError("Provided stellar object has no light_curve attribute or data.")

    if ax is None:
        plt.style.use("dark_background")
        fig, ax = plt.subplots(figsize=figsize)
    else:
        fig = ax.figure

    config = VisualizationConfig()
    photometry_layer = PhotometryOverlay(ax, fig, config)

    timestamps = getattr(light_curve, "timestamps", None)
    fluxes = getattr(light_curve, "fluxes_detrended", None) or getattr(light_curve, "fluxes_normalized", None)
    star_name = getattr(star, "name", "Star")
    is_var = getattr(star, "is_variable_candidate", False)

    photometry_layer.render_light_curve(
        0,
        star_name,
        timestamps,
        fluxes,
        is_variable_candidate=is_var,
    )
    return fig


def plot_stellar_spectroscopy(
    star: Any,
    ax: plt.Axes | None = None,
    figsize: tuple[int, int] = (9, 5),
) -> plt.Figure:
    """Render a single star's 1D wavelength spectrum.

    Parameters
    ----------
    star : `Any`
        Stellar object containing spectrum data.
    ax : `plt.Axes`, optional
        Existing Matplotlib axis to render into. Creates figure if `None`.
    figsize : `tuple[int, int]`, optional
        Figure dimensions if creating a new figure, default `(9, 5)`.

    Returns
    -------
    fig : `plt.Figure`
        Matplotlib figure object containing the plot.

    Raises
    ------
    ValueError
        If the star has no processed spectrum data.
    """
    spectrum_data = getattr(star, "spectrum_data_processed", None) or (star if isinstance(star, dict) else {})
    wavelengths = spectrum_data.get("wavelengths_angstrom") if isinstance(spectrum_data, dict) else None
    intensities = spectrum_data.get("intensities") if isinstance(spectrum_data, dict) else None

    if wavelengths is None or intensities is None:
        raise ValueError("Provided stellar object has no processed spectrum data.")

    if ax is None:
        plt.style.use("dark_background")
        fig, ax = plt.subplots(figsize=figsize)
    else:
        fig = ax.figure

    config = VisualizationConfig()
    spectrum_layer = SpectrumOverlay(ax, fig, config)

    star_name = getattr(star, "name", "Star")
    spectral_type = getattr(star, "stellar_spectral_type", "")
    qe_intensities = spectrum_data.get("quantum_efficiency_corrected_intensities")

    spectrum_layer.render_spectrum(
        0,
        star_name,
        spectral_type,
        wavelengths,
        intensities,
        quantum_efficiency_corrected_intensities=qe_intensities,
    )
    return fig


def plot_stellar_analysis(
    star: Any,
    spectral_star: Any | None = None,
    figsize: tuple[int, int] = (9, 8),
) -> plt.Figure:
    """Render a single star's differential light curve and spectrum stacked.

    Parameters
    ----------
    star : `Any`
        Stellar object containing light curve and/or spectrum data.
    spectral_star : `Any`, optional
        Separate spectroscopy star object if spectroscopy is stored separately.
    figsize : `tuple[int, int]`, optional
        Figure dimensions, default `(9, 8)`.

    Returns
    -------
    fig : `plt.Figure`
        Matplotlib figure object containing the plot(s).

    Raises
    ------
    ValueError
        If neither light curve nor spectrum data is available.
    """
    has_photo = getattr(star, "light_curve", None) is not None

    target_spec_star = spectral_star or star
    spec_data = getattr(target_spec_star, "spectrum_data_processed", None) or (
        target_spec_star if isinstance(target_spec_star, dict) else {}
    )
    has_spec = (
        isinstance(spec_data, dict)
        and spec_data.get("wavelengths_angstrom") is not None
        and spec_data.get("intensities") is not None
    )

    if not has_photo and not has_spec:
        raise ValueError("Provided stellar object has neither light_curve nor spectrum data.")

    if has_photo and has_spec:
        plt.style.use("dark_background")
        fig, (ax_photo, ax_spec) = plt.subplots(2, 1, figsize=figsize)

        plot_stellar_photometry(star, ax=ax_photo)
        plot_stellar_spectroscopy(target_spec_star, ax=ax_spec)
        fig.tight_layout()
        return fig

    if has_photo:
        return plot_stellar_photometry(star, figsize=figsize)

    return plot_stellar_spectroscopy(target_spec_star, figsize=figsize)


# Convenient alias for plural usage
plot_stellar_analyses = plot_stellar_analysis


def _magnitude_sort_key(obj: Any) -> float:
    """Sort key for visually brightest star first, robust to bad magnitudes.

    Parameters
    ----------
    obj : `Any`
        Stellar object.

    Returns
    -------
    key : `float`
        Magnitude value or float('inf').
    """
    magnitude = getattr(obj, "magnitude", None)
    if magnitude in (None, "") or float(magnitude) <= 0:
        return float("inf")
    return float(magnitude)


def _load_target_stars(target: Any, stars: Any, limit: int) -> tuple[list, list, dict]:
    """Fetch astrometry-identified and spectroscopy-extracted stars for target.

    Parameters
    ----------
    target : `Any`
        Target object.
    stars : `astrometricslib.api.stars.StellarCatalog`
        Stellar catalog used to look up identified objects.
    limit : `int`
        Maximum number of astrometry stars to retrieve.

    Returns
    -------
    astrometry_stars : `list`
        Astrometry catalog stars.
    spectral_stars : `list`
        Spectroscopy extracted stars.
    spectral_by_id : `dict`
        Mapping from star ID to spectroscopy star object.

    Raises
    ------
    ValueError
        If stacked image or catalog stars are missing.
    """
    if not getattr(target, "stacked_image", None):
        raise ValueError(f"Target {getattr(target, 'id', 'unknown')!r} has no stacked_image.")

    all_objects = stars.list_objects()
    target_id = getattr(target, "id", "")

    astrometry_stars = sorted(
        (
            obj
            for obj in all_objects
            if target_id in getattr(obj, "target_ids", [])
            and getattr(obj, "dispersion_angle", None) is None
            and not getattr(obj, "id", "").startswith("Star_")
        ),
        key=_magnitude_sort_key,
    )[:limit]

    if not astrometry_stars:
        raise ValueError(f"No catalog-identified stars found for target {target_id!r}.")

    spectral_stars = [
        obj
        for obj in all_objects
        if target_id in getattr(obj, "target_ids", []) and getattr(obj, "dispersion_angle", None) is not None
    ]
    spectral_by_id = {getattr(star, "id", ""): star for star in spectral_stars}

    return astrometry_stars, spectral_stars, spectral_by_id


def plot_astrometry(
    target: Any,
    stars: Any,
    limit: int = 15,
    figsize: tuple[int, int] = (10, 10),
) -> plt.Figure:
    """Render a target's astrometry-solved star field, with no side panel.

    Parameters
    ----------
    target : `Target`
        Target object.
    stars : `astrometricslib.api.stars.StellarCatalog`
        Astrometrics library instance.
    limit : `int`, optional
        Maximum number of catalog stars to overlay, default `15`.
    figsize : `tuple[int, int]`, optional
        Figure dimensions, default `(10, 10)`.

    Returns
    -------
    fig : `plt.Figure`
        Matplotlib figure instance.
    """
    astrometry_stars, _, _ = _load_target_stars(target, stars, limit)

    config = VisualizationConfig()
    plt.style.use("dark_background")

    fig, ax_astrometry = plt.subplots(figsize=figsize)
    image_layer = ImageOverlay(ax_astrometry, config)
    star_layer = StarOverlay(ax_astrometry, config)

    image_layer.render(
        AstrometricsImage(target.stacked_image).data,
        config.default_percentile,
        title=f"{target.id} - Astrometry Solved Star Field",
    )
    star_layer.render(astrometry_stars, active_index=0)

    return fig


def plot_target_photometry(
    target: Any,
    stars: Any,
    limit: int = 15,
    figsize: tuple[int, int] = (16, 9),
) -> plt.Figure:
    """Create interactive 2-panel photometry dashboard.

    Parameters
    ----------
    target : `Target`
        Target object.
    stars : `astrometricslib.api.stars.StellarCatalog`
        Astrometrics library instance.
    limit : `int`, optional
        Maximum number of stars to highlight, default `15`.
    figsize : `tuple[int, int]`, optional
        Figure dimensions, default `(16, 9)`.

    Returns
    -------
    fig : `plt.Figure`
        Matplotlib figure instance.
    """
    astrometry_stars, _, _ = _load_target_stars(target, stars, limit)

    config = VisualizationConfig()
    plt.style.use("dark_background")

    fig = plt.figure(figsize=figsize)
    gs = fig.add_gridspec(1, 2, width_ratios=[1.4, 1.0], wspace=0.28)

    ax_astrometry = fig.add_subplot(gs[0, 0])
    ax_photometry = fig.add_subplot(gs[0, 1])

    image_layer = ImageOverlay(ax_astrometry, config)
    star_layer = StarOverlay(ax_astrometry, config)
    photometry_layer = PhotometryOverlay(ax_photometry, fig, config)

    image_layer.render(
        AstrometricsImage(target.stacked_image).data,
        config.default_percentile,
        title=f"{target.id} - Astrometry Solved Star Field",
    )
    star_patches = star_layer.render(astrometry_stars, active_index=0)

    def render_photometry_panel(index: int) -> None:
        """Render photometry panel for star at given index."""
        star = astrometry_stars[index]
        light_curve = getattr(star, "light_curve", None)
        timestamps = light_curve.timestamps if light_curve else None
        flux = (
            (light_curve.fluxes_detrended if light_curve.fluxes_detrended else light_curve.fluxes_normalized)
            if light_curve
            else None
        )
        photometry_layer.render_light_curve(
            index,
            getattr(star, "name", ""),
            timestamps,
            flux,
            is_variable_candidate=getattr(star, "is_variable_candidate", False),
        )

    def update_selection(index: int) -> None:
        """Update selected star highlight and re-render panel."""
        for i, patch in enumerate(star_patches):
            if patch is None:
                continue
            is_active = i == index
            patch.set_edgecolor(config.active_color if is_active else config.inactive_color)
            patch.set_linewidth(3 if is_active else 2)
        render_photometry_panel(index)
        fig.canvas.draw_idle()

    def find_star_at(x: float, y: float) -> int | None:
        """Locate star index matching click coordinates.

        Returns
        -------
        int | None
            Index of star or None.
        """
        for i, obj in enumerate(astrometry_stars):
            star_data = getattr(obj, "star_data", {})
            star_x = star_data.get("xcentroid", star_data.get("x_centroid"))
            star_y = star_data.get("ycentroid", star_data.get("y_centroid"))
            if star_x is None or star_y is None:
                continue
            if (x - star_x) ** 2 + (y - star_y) ** 2 <= config.fixed_radius**2:
                return i
        return None

    def on_click(event: Any) -> None:
        """Handle click event on star field."""
        if event.inaxes is not ax_astrometry or event.xdata is None or event.ydata is None:
            return
        idx = find_star_at(event.xdata, event.ydata)
        if idx is not None:
            update_selection(idx)

    fig.canvas.mpl_connect("button_press_event", on_click)
    render_photometry_panel(0)
    return fig


def plot_target_spectroscopy(
    target: Any,
    stars: Any,
    limit: int = 15,
    figsize: tuple[int, int] = (16, 9),
) -> plt.Figure:
    """Create interactive 2-panel spectroscopy dashboard.

    Parameters
    ----------
    target : `Target`
        Target object.
    stars : `astrometricslib.api.stars.StellarCatalog`
        Astrometrics library instance.
    limit : `int`, optional
        Maximum number of stars to highlight, default `15`.
    figsize : `tuple[int, int]`, optional
        Figure dimensions, default `(16, 9)`.

    Returns
    -------
    fig : `plt.Figure`
        Matplotlib figure instance.
    """
    astrometry_stars, _, spectral_by_id = _load_target_stars(target, stars, limit)

    config = VisualizationConfig()
    plt.style.use("dark_background")

    fig = plt.figure(figsize=figsize)
    gs = fig.add_gridspec(1, 2, width_ratios=[1.4, 1.0], wspace=0.28)

    ax_astrometry = fig.add_subplot(gs[0, 0])
    ax_spectrum = fig.add_subplot(gs[0, 1])

    image_layer = ImageOverlay(ax_astrometry, config)
    star_layer = StarOverlay(ax_astrometry, config)
    spectrum_layer = SpectrumOverlay(ax_spectrum, fig, config)

    image_layer.render(
        AstrometricsImage(target.stacked_image).data,
        config.default_percentile,
        title=f"{target.id} - Astrometry Solved Star Field",
    )
    star_patches = star_layer.render(astrometry_stars, active_index=0)

    def render_spectrum_panel(index: int) -> None:
        """Render spectrum panel for star at given index."""
        star = astrometry_stars[index]
        spectral_star = spectral_by_id.get(f"{getattr(star, 'id', '')}::spectroscopy")
        if spectral_star is not None:
            data = getattr(spectral_star, "spectrum_data_processed", None) or {}
            spectrum_layer.render_spectrum(
                index,
                getattr(spectral_star, "name", ""),
                getattr(spectral_star, "stellar_spectral_type", ""),
                data.get("wavelengths_angstrom"),
                data.get("intensities"),
                quantum_efficiency_corrected_intensities=data.get("quantum_efficiency_corrected_intensities"),
            )
        else:
            ax_spectrum.clear()
            ax_spectrum.text(
                0.5, 0.5, "No spectrum available", ha="center", va="center", color="red", fontsize=12
            )
            ax_spectrum.set_title("Spectrum Not Available")

    def update_selection(index: int) -> None:
        """Update selected star highlight and re-render panel."""
        for i, patch in enumerate(star_patches):
            if patch is None:
                continue
            is_active = i == index
            patch.set_edgecolor(config.active_color if is_active else config.inactive_color)
            patch.set_linewidth(3 if is_active else 2)
        render_spectrum_panel(index)
        fig.canvas.draw_idle()

    def find_star_at(x: float, y: float) -> int | None:
        """Locate star index matching click coordinates.

        Returns
        -------
        int | None
            Index of star or None.
        """
        for i, obj in enumerate(astrometry_stars):
            star_data = getattr(obj, "star_data", {})
            star_x = star_data.get("xcentroid", star_data.get("x_centroid"))
            star_y = star_data.get("ycentroid", star_data.get("y_centroid"))
            if star_x is None or star_y is None:
                continue
            if (x - star_x) ** 2 + (y - star_y) ** 2 <= config.fixed_radius**2:
                return i
        return None

    def on_click(event: Any) -> None:
        """Handle click event on star field."""
        if event.inaxes is not ax_astrometry or event.xdata is None or event.ydata is None:
            return
        idx = find_star_at(event.xdata, event.ydata)
        if idx is not None:
            update_selection(idx)

    fig.canvas.mpl_connect("button_press_event", on_click)
    render_spectrum_panel(0)
    return fig


def plot_target_dashboard(
    target: Any,
    stars: Any,
    limit: int = 15,
    figsize: tuple[int, int] = (16, 9),
    selected_star: Any | None = None,
) -> plt.Figure:
    """Create interactive combined dashboard for a fully-processed target.

    Parameters
    ----------
    target : `Target`
        Target object.
    stars : `astrometricslib.api.stars.StellarCatalog`
        Astrometrics library instance.
    limit : `int`, optional
        Maximum number of catalog stars to retrieve, default `15`.
    figsize : `tuple[int, int]`, optional
        Figure dimensions, default `(16, 9)`.
    selected_star : `StellarObject` or `str` or `None`, optional
        Specific star object or star ID/name to set as active selection.

    Returns
    -------
    fig : `plt.Figure`
        Matplotlib figure instance.
    """
    astrometry_stars, spectral_stars, spectral_by_id = _load_target_stars(target, stars, limit)

    has_photometry = any(getattr(s, "light_curve", None) is not None for s in astrometry_stars)
    has_spectroscopy = len(spectral_stars) > 0

    active_index = 0
    if selected_star is not None:
        target_str = getattr(selected_star, "id", str(selected_star)).lower()
        target_name = getattr(selected_star, "name", str(selected_star)).lower()
        for idx, s in enumerate(astrometry_stars):
            sid = getattr(s, "id", "").lower()
            sname = getattr(s, "name", "").lower()
            if (
                target_str in (sid, sname)
                or target_name in (sid, sname)
                or sid.startswith(target_str)
                or target_str.startswith(sid)
            ):
                active_index = idx
                break

    config = VisualizationConfig()
    plt.style.use("dark_background")

    fig = plt.figure(figsize=figsize)

    if has_photometry and has_spectroscopy:
        gs = fig.add_gridspec(2, 2, width_ratios=[1.4, 1.0], height_ratios=[1, 1], wspace=0.28, hspace=0.4)
        ax_astrometry = fig.add_subplot(gs[:, 0])
        ax_photometry = fig.add_subplot(gs[0, 1])
        ax_spectrum = fig.add_subplot(gs[1, 1])
    elif has_photometry:
        gs = fig.add_gridspec(1, 2, width_ratios=[1.4, 1.0], wspace=0.28)
        ax_astrometry = fig.add_subplot(gs[0, 0])
        ax_photometry = fig.add_subplot(gs[0, 1])
        ax_spectrum = None
    elif has_spectroscopy:
        gs = fig.add_gridspec(1, 2, width_ratios=[1.4, 1.0], wspace=0.28)
        ax_astrometry = fig.add_subplot(gs[0, 0])
        ax_photometry = None
        ax_spectrum = fig.add_subplot(gs[0, 1])
    else:
        ax_astrometry = fig.add_subplot(1, 1, 1)
        ax_photometry = None
        ax_spectrum = None

    image_layer = ImageOverlay(ax_astrometry, config)
    star_layer = StarOverlay(ax_astrometry, config)
    photometry_layer = PhotometryOverlay(ax_photometry, fig, config) if ax_photometry is not None else None
    spectrum_layer = SpectrumOverlay(ax_spectrum, fig, config) if ax_spectrum is not None else None

    image_layer.render(
        AstrometricsImage(target.stacked_image).data,
        config.default_percentile,
        title=f"{target.id} - Astrometry Solved Star Field",
    )
    star_patches = star_layer.render(astrometry_stars, active_index=active_index)

    def render_side_panels(index: int) -> None:
        """Render active side panels for star at given index."""
        star = astrometry_stars[index]

        if photometry_layer is not None:
            light_curve = getattr(star, "light_curve", None)
            timestamps = light_curve.timestamps if light_curve else None
            flux = None
            if light_curve:
                flux = (
                    light_curve.fluxes_detrended
                    if light_curve.fluxes_detrended
                    else light_curve.fluxes_normalized
                )
            photometry_layer.render_light_curve(
                index,
                getattr(star, "name", ""),
                timestamps,
                flux,
                is_variable_candidate=getattr(star, "is_variable_candidate", False),
            )

        if spectrum_layer is not None:
            spectral_star = spectral_by_id.get(f"{getattr(star, 'id', '')}::spectroscopy")
            if spectral_star is not None:
                data = getattr(spectral_star, "spectrum_data_processed", None) or {}
                spectrum_layer.render_spectrum(
                    index,
                    getattr(spectral_star, "name", ""),
                    getattr(spectral_star, "stellar_spectral_type", ""),
                    data.get("wavelengths_angstrom"),
                    data.get("intensities"),
                    quantum_efficiency_corrected_intensities=data.get(
                        "quantum_efficiency_corrected_intensities"
                    ),
                )
            else:
                ax_spectrum.clear()
                ax_spectrum.text(
                    0.5, 0.5, "No spectrum available", ha="center", va="center", color="red", fontsize=12
                )
                ax_spectrum.set_title("Spectrum Not Available")

    def update_selection(index: int) -> None:
        """Update selected star highlight and re-render side panels."""
        for i, patch in enumerate(star_patches):
            if patch is None:
                continue
            is_active = i == index
            patch.set_edgecolor(config.active_color if is_active else config.inactive_color)
            patch.set_linewidth(3 if is_active else 2)
        render_side_panels(index)
        fig.canvas.draw_idle()

    def find_star_at(x: float, y: float) -> int | None:
        """Locate star index matching click coordinates.

        Returns
        -------
        int | None
            Index of star or None.
        """
        for i, obj in enumerate(astrometry_stars):
            star_data = getattr(obj, "star_data", {})
            star_x = star_data.get("xcentroid", star_data.get("x_centroid"))
            star_y = star_data.get("ycentroid", star_data.get("y_centroid"))
            if star_x is None or star_y is None:
                continue
            if (x - star_x) ** 2 + (y - star_y) ** 2 <= config.fixed_radius**2:
                return i
        return None

    def on_click(event: Any) -> None:
        """Handle click event on star field."""
        if event.inaxes is not ax_astrometry or event.xdata is None or event.ydata is None:
            return
        idx = find_star_at(event.xdata, event.ydata)
        if idx is not None:
            update_selection(idx)

    fig.canvas.mpl_connect("button_press_event", on_click)
    render_side_panels(active_index)
    return fig
