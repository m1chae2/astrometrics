"""Purpose: Internal orchestrator for interactive 2D star field visualization.

Description: Combines 2D star fields and 1D analysis visualization layers
into interactive 2-panel views.
"""

import matplotlib.pyplot as plt

from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.shared.analysis_context import AnalysisContext

from .geometry import hit_test_rectangle, map_point_to_relative_x, rotate_point
from .interaction_handler import InteractionHandler
from .layers import (
    DispersionOverlay,
    ImageOverlay,
    PhotometryOverlay,
    SpectrumOverlay,
    StarOverlay,
    StarSelectionOverlay,
    resolve_star_radius,
)
from .spectroscopy_field_access import get_spectroscopy_field as _get_spectroscopy_field
from .visualization_config import VisualizationConfig


class _AnalysisView:
    """Orchestrates layer renderers and user interactions across star fields.

    Parameters
    ----------
    context : `AnalysisContext`
        Context containing FITS image data.
    enriched_objects : `list[StellarObject]`
        List of stellar objects or coordinate objects.
    fig : `Figure`, optional
        Matplotlib figure.
    ax_image : `Axes`, optional
        2D FITS image axis.
    ax_spectrum : `Axes`, optional
        1D analysis axis (spectrum or light curve).
    mode : `str`, optional
        Analysis mode: `"spectroscopy"` or `"photometry"`, default
        `"spectroscopy"`.
    """

    def __init__(  # ruff: ignore[missing-return-type-special-method]
        self,
        context: AnalysisContext,
        enriched_objects: list[StellarObject],
        fig=None,  # ruff: ignore[missing-type-function-argument]
        ax_image=None,  # ruff: ignore[missing-type-function-argument]
        ax_spectrum=None,  # ruff: ignore[missing-type-function-argument]
        mode: str = "spectroscopy",
    ):
        self.context = context
        self.stellar_objects = enriched_objects
        self.config = VisualizationConfig()
        self.mode = mode
        self.active_star_index = 0

        # Initialize Figure. Spectroscopy mode gets a raw and a calibrated
        # spectrum panel side by side, rather than one panel toggled
        # between the two views, so both are visible at once; photometry
        # mode's light curve keeps a single panel.
        self.ax_spectrum_corrected = None
        if fig is not None and ax_image is not None and ax_spectrum is not None:
            self.fig = fig
            self.ax_image = ax_image
            self.ax_spectrum = ax_spectrum
        elif mode == "spectroscopy":
            self.fig = plt.figure(figsize=(9, 13))
            gs = self.fig.add_gridspec(3, 1, height_ratios=[2, 1, 1], hspace=0.5)
            self.ax_image = self.fig.add_subplot(gs[0, 0])
            self.ax_spectrum = self.fig.add_subplot(gs[1, 0])
            self.ax_spectrum_corrected = self.fig.add_subplot(gs[2, 0])
        else:
            self.fig, (self.ax_image, self.ax_spectrum) = plt.subplots(
                2, 1, figsize=(9, 11), gridspec_kw={"height_ratios": [2, 1], "hspace": 0.45}
            )

        # Initialize Layers
        self.layer_image = ImageOverlay(self.ax_image, self.config)
        self.layer_stars = StarOverlay(self.ax_image, self.config)
        self.layer_selection = StarSelectionOverlay(self.ax_image, self.config)
        self.layer_dispersion = DispersionOverlay(self.ax_image, self.config)
        self.renderer_spectrum = SpectrumOverlay(
            self.ax_spectrum, self.fig, self.config, corrected_axis=self.ax_spectrum_corrected
        )
        self.layer_photometry = PhotometryOverlay(self.ax_spectrum, self.fig, self.config)

        self.interaction = InteractionHandler(self.fig, self.ax_image, self.ax_spectrum, self.config)

        # Wire Callbacks
        self.interaction.on_star_select = self._update_selection
        self.interaction.on_star_drag = self._handle_star_drag
        self.interaction.on_find_star = self._find_star_at
        self.interaction.on_crosshair_sync = self._sync_crosshairs

        self.star_patches = []
        self.rectangle_patches = []

    def plot(self, block: bool = True, add_buttons: bool = True, limit: int | None = None):  # ruff: ignore[missing-return-type-private-function]
        """Launch the interactive visualization window."""
        if not self.stellar_objects:
            print("No stars to display.")
            return

        # 1. Render Image Layer
        title = (
            "Slitless Grism Spectrum FITS Image" if self.mode == "spectroscopy" else "Target Field FITS Image"
        )
        self.layer_image.render(self.context.image.data, self.config.default_percentile, title=title)

        # 2. Render Star & Dispersion Overlays
        self.star_patches = self.layer_stars.render(self.stellar_objects, self.active_star_index, limit=limit)

        if self.mode == "spectroscopy":
            self.rectangle_patches = self.layer_dispersion.render(
                self.stellar_objects, self.active_star_index, limit=limit
            )

        # 3. Render 1D Analysis Panel
        self._plot_active_analysis()

        # 4. Add Controls & Connect Events
        if add_buttons and self.mode == "spectroscopy":
            self.renderer_spectrum.add_balmer_toggle()
            # With a separate calibrated-view panel (ax_spectrum_corrected),
            # both the raw and corrected spectra are always visible, so
            # there is nothing left for a raw/corrected toggle to switch.

        self.interaction.connect_events()

        if block:
            plt.show()

    def setup_multi_field_controls(self):  # ruff: ignore[missing-return-type-private-function]
        """Configure interactive controls across multiple target fields."""
        pass

    def _plot_active_analysis(self):  # ruff: ignore[missing-return-type-private-function]
        """Render active star 1D analysis profile (spectrum or light curve)."""
        if not self.stellar_objects:
            return
        obj = self.stellar_objects[self.active_star_index]
        if self.mode == "spectroscopy":
            self.renderer_spectrum.render_spectrum(
                self.active_star_index,
                getattr(obj, "name", ""),
                getattr(obj, "stellar_spectral_type", ""),
                _get_spectroscopy_field(obj, "wavelengths_angstrom"),
                _get_spectroscopy_field(obj, "intensities"),
                quantum_efficiency_corrected_intensities=_get_spectroscopy_field(
                    obj, "quantum_efficiency_corrected_intensities"
                ),
                response_corrected_intensities=_get_spectroscopy_field(obj, "response_corrected_intensities"),
            )
        else:
            photometry = getattr(obj, "photometry", None)
            timestamps = photometry.timestamps if photometry else None
            flux = (
                (photometry.fluxes_detrended if photometry.fluxes_detrended else photometry.fluxes_normalized)
                if photometry
                else None
            )
            is_var = getattr(obj, "is_variable_candidate", False)
            self.layer_photometry.render_light_curve(
                self.active_star_index,
                getattr(obj, "name", f"Star {self.active_star_index + 1}"),
                timestamps,
                flux,
                is_variable_candidate=is_var,
            )

    def _update_selection(self, index: int):  # ruff: ignore[missing-return-type-private-function]
        """Handle selection of a new star."""
        self.active_star_index = index
        for i, cp in enumerate(self.star_patches):
            if cp is not None:
                is_active = i == index
                color = self.config.active_color if is_active else self.config.inactive_color
                cp.set_edgecolor(color)
                cp.set_linewidth(3 if is_active else 2)

        for i, rp in enumerate(self.rectangle_patches):
            if rp is not None:
                is_active = i == index
                color = self.config.active_color if is_active else self.config.rectangle_color
                rp.set_edgecolor(color)
                rp.set_linewidth(3 if is_active else 2)

        self._plot_active_analysis()
        self.fig.canvas.draw_idle()

    def _handle_star_drag(self, index: int, new_x: float, new_y: float):  # ruff: ignore[missing-return-type-private-function]
        """Handle dragging of a star's centroid.

        Parameters
        ----------
        index : `int`
            Index of the star being dragged (`InteractionHandler`'s
            ``dragged_star_index``).
        new_x : `float`
            New X data coordinate for the star's centroid.
        new_y : `float`
            New Y data coordinate for the star's centroid.
        """
        if not self.stellar_objects or index is None:
            return
        obj = self.stellar_objects[index]
        if hasattr(obj, "star_data"):
            obj.star_data["xcentroid"] = new_x
            obj.star_data["ycentroid"] = new_y
        else:
            obj["xcentroid"] = new_x
            obj["ycentroid"] = new_y

        cp = self.star_patches[index]
        if cp is not None:
            cp.center = (new_x, new_y)

        self._plot_active_analysis()
        self.fig.canvas.draw_idle()

    def _find_star_at(self, event_x: float, event_y: float) -> int | None:
        """Locate star under event coordinates.

        Returns
        -------
        int | None
            Index of the star under event coordinates, or None.
        """
        for i, obj in enumerate(self.stellar_objects):
            is_obj = hasattr(obj, "star_data")
            x = (
                obj.star_data.get("xcentroid", obj.star_data.get("x_centroid"))
                if is_obj
                else obj.get("xcentroid", obj.get("x_centroid", 0.0))
            )
            y = (
                obj.star_data.get("ycentroid", obj.star_data.get("y_centroid"))
                if is_obj
                else obj.get("ycentroid", obj.get("y_centroid", 0.0))
            )

            radius = resolve_star_radius(obj, self.config.fixed_radius)
            dx = event_x - x
            dy = event_y - y
            if (dx * dx + dy * dy) <= radius * radius:
                return i

            if self.mode == "spectroscopy":
                rect = _get_spectroscopy_field(obj, "rectangle")
                angle = _get_spectroscopy_field(obj, "dispersion_angle", 0.0)
                if rect is not None and hit_test_rectangle(event_x, event_y, *rect, angle):
                    return i
        return None

    def _sync_crosshairs(self, panel: str, x: float, y: float):  # ruff: ignore[missing-return-type-private-function]
        """Synchronize crosshairs between panels.

        Parameters
        ----------
        panel : `str`
            Which panel the click came from: ``"image"`` or
            ``"spectrum"``.
        x : `float`
            Data-space X coordinate of the click, in the source
            panel's own axis.
        y : `float`
            Data-space Y coordinate of the click, in the source
            panel's own axis.
        """
        if self.active_star_index >= len(self.stellar_objects):
            return
        obj = self.stellar_objects[self.active_star_index]
        if self.mode != "spectroscopy":
            return
        rect = _get_spectroscopy_field(obj, "rectangle")
        angle = _get_spectroscopy_field(obj, "dispersion_angle", 0.0)
        if rect is None:
            return
        rect_center_x, rect_center_y, _rect_width, _rect_height = rect

        if panel == "image":
            pt_x, pt_y = x, y
        else:
            x0 = (
                obj.star_data.get("xcentroid", obj.star_data.get("x_centroid"))
                if hasattr(obj, "star_data")
                else obj.get("xcentroid", obj.get("x_centroid", 0.0))
            )
            y0 = (
                obj.star_data.get("ycentroid", obj.star_data.get("y_centroid"))
                if hasattr(obj, "star_data")
                else obj.get("ycentroid", obj.get("y_centroid", 0.0))
            )
            xr, yr = rotate_point(x, 0.0, angle)
            pt_x = x0 + xr
            pt_y = y0 + yr

        wavelengths = _get_spectroscopy_field(obj, "wavelengths_angstrom")
        if wavelengths is not None and len(wavelengths) > 0:
            rel_x = map_point_to_relative_x(pt_x, pt_y, rect_center_x, rect_center_y, angle)
            idx = max(0, min(int(rel_x), len(wavelengths) - 1))
            wavelength_val = wavelengths[idx]
            self.interaction.update_spectrum_crosshair(wavelength_val)
            self.interaction.update_image_crosshairs(pt_x, pt_y)
