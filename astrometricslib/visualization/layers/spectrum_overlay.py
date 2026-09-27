"""Render extracted spectra and spectral line overlays for display."""

import numpy as np
from matplotlib.axes import Axes
from matplotlib.widgets import Button

from astrometricslib.pipelines.spectroscopy.optics_physics import BALMER_SERIES_NM
from astrometricslib.visualization.visualization_config import VisualizationConfig


class SpectrumOverlay:
    """Render 1D wavelength spectra and absorption line overlays.

    Parameters
    ----------
    axis : `matplotlib.axes.Axes`
        Axis the raw, uncalibrated spectrum is plotted on.
    fig : `matplotlib.figure.Figure`
        Figure that owns ``axis``.
    config : `VisualizationConfig`
        Color configuration.
    corrected_axis : `matplotlib.axes.Axes`, optional
        A second axis for the calibrated spectrum (the best available
        correction -- see `_render_corrected_spectrum`), shown side by
        side with the raw one rather than behind a toggle. When omitted,
        only `axis` is drawn, and a toggle button switches it between
        raw and corrected (see `add_quantum_efficiency_correction_toggle`).
    """

    def __init__(self, axis, fig, config: VisualizationConfig, corrected_axis=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.ax = axis
        self.ax_corrected = corrected_axis
        self.fig = fig
        self.config = config
        self.balmer_lines_visible = False
        self.balmer_line_artists = []
        self.balmer_label_artists = []
        self.balmer_button = None
        # Only meaningful when there is no separate `ax_corrected`: with
        # one axis, a toggle switches it between raw and corrected;
        # with two, both are always shown and this is unused.
        self.quantum_efficiency_corrected_view_active = True
        self.quantum_efficiency_toggle_button = None
        self.active_quantum_efficiency_corrected_intensities = None
        self.active_response_corrected_intensities = None

    def render_spectrum(  # ruff: ignore[missing-return-type-undocumented-public-function]
        self,
        index: int,
        star_name: str,
        spectral_type: str,
        wavelengths,  # ruff: ignore[missing-type-function-argument]
        intensities,  # ruff: ignore[missing-type-function-argument]
        quantum_efficiency_corrected_intensities=None,  # ruff: ignore[missing-type-function-argument]
        response_corrected_intensities=None,  # ruff: ignore[missing-type-function-argument]
    ):
        """Plot the 1D extracted spectrum.

        Stores the active star's data so the corrected-view toggle and
        Balmer-line toggle can re-render this same star without the
        caller needing to pass the data again.

        Parameters
        ----------
        response_corrected_intensities : optional
            The spectrum with the full instrument response (grating,
            optics and atmosphere, not just the sensor's QE) removed --
            see `instrument_response.apply_instrument_response`. This is
            the physically meaningful normalized flux, and it is
            preferred over `quantum_efficiency_corrected_intensities`
            when both are given, since QE alone still leaves the
            grating/optics/atmosphere tilt in the spectrum.
        """
        self.active_index = index
        self.active_star_name = star_name
        self.active_spectral_type = spectral_type
        self.active_wavelengths = np.array(wavelengths) if wavelengths is not None else None
        self.active_intensities = np.array(intensities) if intensities is not None else None
        self.active_quantum_efficiency_corrected_intensities = (
            np.array(quantum_efficiency_corrected_intensities)
            if quantum_efficiency_corrected_intensities is not None
            else None
        )
        self.active_response_corrected_intensities = (
            np.array(response_corrected_intensities) if response_corrected_intensities is not None else None
        )
        self._render_active_spectrum()

    def _active_axes(self) -> list:
        """List the axes currently showing this star's spectrum.

        Returns
        -------
        axes : `list`
            `[self.ax]`, plus `self.ax_corrected` when a second panel
            is in use.
        """
        return [self.ax] if self.ax_corrected is None else [self.ax, self.ax_corrected]

    def _title(self, prefix: str) -> str:
        """Build a panel's title from the active star's name and type.

        Returns
        -------
        title : `str`
            ``"<prefix> for Star N: name (type)"``, trimmed to whatever
            of name and type are known.
        """
        title = f"{prefix} for Star {self.active_index + 1}"
        if self.active_star_name:
            title += f": {self.active_star_name}"
            if self.active_spectral_type:
                title += f" ({self.active_spectral_type})"
        return title

    def _plot_or_placeholder(
        self,
        axis: Axes,
        wavelengths: np.ndarray | None,
        intensities: np.ndarray | None,
        title: str,
        y_label: str,
    ) -> None:
        """Draw one spectrum panel, or say why there is nothing to draw.

        Samples where `intensities` is `NaN` (a correction's way of
        marking a wavelength it does not cover) are left out of the
        drawn line rather than breaking it or drawing a false value.
        """
        axis.clear()
        if wavelengths is not None and intensities is not None and wavelengths.size > 0:
            finite = np.isfinite(intensities)
            axis.plot(wavelengths[finite], intensities[finite])
            axis.set_xlabel("Wavelength (Å)")
            axis.set_ylabel(y_label)
            axis.set_title(title)
        else:
            axis.text(0.5, 0.5, "No spectrum available", ha="center", va="center", color="red", fontsize=12)
            axis.set_title("Spectrum Not Available")

    def _best_available_correction(self) -> tuple:
        """Give the best calibrated view of the active star's spectrum.

        Prefers the full instrument-response correction over a bare
        QE correction, since QE alone still leaves the grating, optics
        and atmosphere's tilt in the spectrum (see `render_spectrum`).

        Returns
        -------
        intensities : `np.ndarray` or `None`
            The best available corrected intensities, or `None` when
            neither correction is available for this star.
        y_label : `str`
            What the returned intensities are, for the axis label.
        """
        if self.active_response_corrected_intensities is not None:
            return self.active_response_corrected_intensities, "Normalized Flux"
        if self.active_quantum_efficiency_corrected_intensities is not None:
            return (
                self.active_quantum_efficiency_corrected_intensities,
                "QE-Corrected Counts (grating/optics/atmosphere tilt not removed)",
            )
        return None, "No Correction Available"

    def _render_active_spectrum(self):  # ruff: ignore[missing-return-type-private-function]
        """Draw the active star's spectrum on whichever axes are in use."""
        self.balmer_line_artists = []
        self.balmer_label_artists = []
        wavelengths = self.active_wavelengths

        if self.ax_corrected is not None:
            self._plot_or_placeholder(
                self.ax, wavelengths, self.active_intensities, self._title("Raw Spectrum"), "Raw Counts"
            )
            corrected_intensities, y_label = self._best_available_correction()
            corrected_title = self._title("Calibrated Spectrum")
            self._plot_or_placeholder(
                self.ax_corrected, wavelengths, corrected_intensities, corrected_title, y_label
            )
        else:
            show_corrected = self.quantum_efficiency_corrected_view_active
            if show_corrected:
                intensities, y_label = self._best_available_correction()
                if intensities is None:
                    intensities, y_label = self.active_intensities, "Raw Counts"
            else:
                intensities, y_label = self.active_intensities, "Raw Counts"
            extracted_title = self._title("Extracted Spectrum")
            self._plot_or_placeholder(self.ax, wavelengths, intensities, extracted_title, y_label)

        if self.balmer_lines_visible and wavelengths is not None:
            self.draw_spectral_lines(self.active_spectral_type, wavelengths)

    def add_balmer_toggle(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Add the Balmer line toggle button to the plot."""
        bbox = self.ax.get_position()
        width, height = 0.25, 0.04
        x0 = bbox.x1 - width - 0.005
        y0 = bbox.y0 - height - 0.055

        ax_button = self.fig.add_axes([x0, y0, width, height])
        self.balmer_button = Button(
            ax_button,
            "Show Balmer Lines",
            color=self.config.button_color,
            hovercolor=self.config.button_hover,
        )
        self.balmer_button.on_clicked(self.toggle_balmer_lines)

    def add_quantum_efficiency_correction_toggle(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Add the raw/corrected toggle button to the plot.

        Only useful in single-axis mode (`self.ax_corrected is None`):
        with two axes, raw and corrected are always shown side by side
        and there is nothing for this toggle to switch.

        Positioned directly below the Balmer-line toggle so the two
        buttons don't overlap.
        """
        if self.ax_corrected is not None:
            return
        bbox = self.ax.get_position()
        width, height = 0.25, 0.04
        x0 = bbox.x1 - width - 0.005
        y0 = bbox.y0 - height - 0.055 - height - 0.02

        ax_button = self.fig.add_axes([x0, y0, width, height])
        self.quantum_efficiency_toggle_button = Button(
            ax_button,
            self._corrected_view_button_label(),
            color=self.config.button_color,
            hovercolor=self.config.button_hover,
        )
        self.quantum_efficiency_toggle_button.on_clicked(self.toggle_quantum_efficiency_corrected_view)

    def _corrected_view_button_label(self) -> str:
        """Give the toggle button's label for the view it would switch to.

        Names whichever correction is actually available for the active
        star, since a response correction is only derived for some
        cameras and a fainter/redder star can fall outside its valid
        wavelength range even when one exists.

        Returns
        -------
        label : `str`
            The button's next label.
        """
        if self.quantum_efficiency_corrected_view_active:
            return "Show Raw Intensity"
        if self.active_response_corrected_intensities is not None:
            return "Show Normalized Flux"
        return "Show QE-Corrected Intensity"

    def toggle_quantum_efficiency_corrected_view(self, event):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Toggle between raw counts and the best available correction."""
        self.quantum_efficiency_corrected_view_active = not self.quantum_efficiency_corrected_view_active
        if self.quantum_efficiency_toggle_button is not None:
            self.quantum_efficiency_toggle_button.label.set_text(self._corrected_view_button_label())

        self._render_active_spectrum()
        self.fig.canvas.draw_idle()

    def toggle_balmer_lines(self, event):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Toggle Balmer line visibility.

        Draws or clears vertical Balmer line overlays on the plot.
        """
        self.balmer_lines_visible = not self.balmer_lines_visible
        if not self.balmer_lines_visible:
            self.clear_spectral_lines()
            if self.balmer_button is not None:
                self.balmer_button.label.set_text("Show Balmer Lines")
        else:
            if self.balmer_button is not None:
                self.balmer_button.label.set_text("Hide Balmer Lines")
            if hasattr(self, "active_wavelengths") and self.active_wavelengths is not None:
                self.draw_spectral_lines(self.active_spectral_type, self.active_wavelengths)

        self.fig.canvas.draw_idle()

    def clear_spectral_lines(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Remove spectral line artists from the plot."""
        for artist in self.balmer_line_artists + self.balmer_label_artists:
            try:
                if artist.axes is not None:
                    artist.remove()
            except NotImplementedError, ValueError:
                pass
        self.balmer_line_artists = []
        self.balmer_label_artists = []

    def draw_spectral_lines(self, spectral_type: str, wavelengths: np.ndarray):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Draws vertical lines for common absorption features.

        Drawn on every axis currently showing a spectrum (both the raw
        and the calibrated panel, when there are two), so the same
        features are easy to compare between them.
        """
        self.clear_spectral_lines()

        spectral_lines_map = {
            "O": {"He II": 4541, "He II_2": 4686, "H-Beta": 4861, "H-Alpha": 6563},
            "B": {"He I": 4026, "He I_2": 4471, "H-Gamma": 4340, "H-Beta": 4861, "H-Alpha": 6563},
            "A": {"H-Delta": 4102, "H-Gamma": 4340, "H-Beta": 4861, "H-Alpha": 6563},
            "F": {"Ca K": 3934, "Ca H": 3968, "H-Gamma": 4340, "H-Beta": 4861, "H-Alpha": 6563},
            "G": {
                "Ca K": 3934,
                "Ca H": 3968,
                "G-Band": 4300,
                "H-Beta": 4861,
                "Mg I": 5175,
                "Na D": 5890,
                "H-Alpha": 6563,
            },
            "K": {"Ca K": 3934, "Ca H": 3968, "G-Band": 4300, "Mg I": 5175, "Na D": 5890},
            "M": {"Ca I": 4227, "TiO": 4761, "TiO_2": 4954, "Mg I": 5175, "Na D": 5890, "TiO_3": 7050},
        }

        # BALMER_SERIES_NM is in nanometers; this plot's x-axis is
        # angstroms (1 nm = 10 Å).
        lines = {
            "H-Delta": round(BALMER_SERIES_NM["H-delta"] * 10, 1),
            "H-Gamma": round(BALMER_SERIES_NM["H-gamma"] * 10, 1),
            "H-Beta": round(BALMER_SERIES_NM["H-beta"] * 10, 1),
            "H-Alpha": round(BALMER_SERIES_NM["H-alpha"] * 10, 1),
        }
        if spectral_type and spectral_type[0].upper() in spectral_lines_map:
            lines = spectral_lines_map[spectral_type[0].upper()]

        min_w, max_w = wavelengths.min(), wavelengths.max()
        greek_label_map = {
            "H-Alpha": "Hα",
            "H-Beta": "Hβ",
            "H-Gamma": "Hγ",
            "H-Delta": "Hδ",
        }

        for axis in self._active_axes():
            for name, w in lines.items():
                if min_w <= w <= max_w:
                    line_artist = axis.axvline(
                        w, color=self.config.balmer_color, alpha=0.8, lw=1.5, linestyle="--", zorder=10
                    )
                    self.balmer_line_artists.append(line_artist)

                    clean_name = name.split("_")[0]
                    label_text = greek_label_map.get(clean_name, clean_name)

                    t = axis.text(
                        w + 15,
                        0.05,
                        label_text,
                        transform=axis.get_xaxis_transform(),
                        color=self.config.balmer_color,
                        fontsize=10,
                        fontweight="normal",
                        rotation=0,
                        ha="left",
                        va="bottom",
                        zorder=11,
                        bbox={"boxstyle": "round,pad=0.15", "fc": "black", "ec": "none", "alpha": 0.5},
                    )
                    self.balmer_label_artists.append(t)
