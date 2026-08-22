"""Render extracted spectra and spectral line overlays for display."""

import numpy as np
from matplotlib.widgets import Button

from astrometricslib.visualization.visualization_config import VisualizationConfig


class SpectrumOverlay:
    """Render 1D wavelength spectra and absorption line overlays.

    Parameters
    ----------
    axis : `matplotlib.axes.Axes`
        Axis on which the spectrum is plotted.
    fig : `matplotlib.figure.Figure`
        Figure that owns ``axis``.
    config : `VisualizationConfig`
        Color configuration.
    """

    def __init__(self, axis, fig, config: VisualizationConfig):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.ax = axis
        self.fig = fig
        self.config = config
        self.balmer_lines_visible = False
        self.balmer_line_artists = []
        self.balmer_label_artists = []
        self.balmer_button = None
        self.quantum_efficiency_corrected_view_active = True
        self.quantum_efficiency_toggle_button = None
        self.active_quantum_efficiency_corrected_intensities = None

    def render_spectrum(  # ruff: ignore[missing-return-type-undocumented-public-function]
        self,
        index: int,
        star_name: str,
        spectral_type: str,
        wavelengths,  # ruff: ignore[missing-type-function-argument]
        intensities,  # ruff: ignore[missing-type-function-argument]
        quantum_efficiency_corrected_intensities=None,  # ruff: ignore[missing-type-function-argument]
    ):
        """Plot the 1D extracted spectrum.

        Stores the active star's data so the QE-corrected-view toggle
        and Balmer-line toggle can re-render this same star without
        the caller needing to pass the data again.
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
        self._render_active_spectrum()

    def _render_active_spectrum(self):  # ruff: ignore[missing-return-type-private-function]
        """Draw the active star's spectrum, honoring the QE toggle."""
        self.ax.clear()
        self.balmer_line_artists = []
        self.balmer_label_artists = []

        wavelengths = self.active_wavelengths
        show_quantum_efficiency_corrected = (
            self.quantum_efficiency_corrected_view_active
            and self.active_quantum_efficiency_corrected_intensities is not None
        )
        intensities = (
            self.active_quantum_efficiency_corrected_intensities
            if show_quantum_efficiency_corrected
            else self.active_intensities
        )

        if wavelengths is not None and intensities is not None and wavelengths.size > 0:
            title = f"Extracted Spectrum for Star {self.active_index + 1}"
            if self.active_star_name:
                title += f": {self.active_star_name}"
                if self.active_spectral_type:
                    title += f" ({self.active_spectral_type})"

            self.ax.plot(wavelengths, intensities)
            self.ax.set_xlabel("Wavelength (Å)")
            self.ax.set_ylabel("Normalized Flux")
            self.ax.set_title(title)

            if self.balmer_lines_visible:
                self.draw_spectral_lines(self.active_spectral_type, wavelengths)
        else:
            self.ax.text(
                0.5, 0.5, "No spectrum available", ha="center", va="center", color="red", fontsize=12
            )
            self.ax.set_title("Spectrum Not Available")

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
        """Add the QE-corrected-view toggle button to the plot.

        Positioned directly below the Balmer-line toggle so the two
        buttons don't overlap.
        """
        bbox = self.ax.get_position()
        width, height = 0.25, 0.04
        x0 = bbox.x1 - width - 0.005
        y0 = bbox.y0 - height - 0.055 - height - 0.02

        ax_button = self.fig.add_axes([x0, y0, width, height])
        self.quantum_efficiency_toggle_button = Button(
            ax_button,
            "Show QE-Corrected Intensity",
            color=self.config.button_color,
            hovercolor=self.config.button_hover,
        )
        self.quantum_efficiency_toggle_button.on_clicked(self.toggle_quantum_efficiency_corrected_view)

    def toggle_quantum_efficiency_corrected_view(self, event):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Toggle between raw and QE-corrected intensity views."""
        self.quantum_efficiency_corrected_view_active = not self.quantum_efficiency_corrected_view_active
        if self.quantum_efficiency_toggle_button is not None:
            label = (
                "Show Raw Intensity"
                if self.quantum_efficiency_corrected_view_active
                else "Show QE-Corrected Intensity"
            )
            self.quantum_efficiency_toggle_button.label.set_text(label)

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
        """Draws vertical lines for common absorption features."""
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

        lines = {
            "H-Delta": 4101.7,
            "H-Gamma": 4340.5,
            "H-Beta": 4861.3,
            "H-Alpha": 6562.8,
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

        for name, w in lines.items():
            if min_w <= w <= max_w:
                line_artist = self.ax.axvline(
                    w, color=self.config.balmer_color, alpha=0.8, lw=1.5, linestyle="--", zorder=10
                )
                self.balmer_line_artists.append(line_artist)

                clean_name = name.split("_")[0]
                label_text = greek_label_map.get(clean_name, clean_name)

                t = self.ax.text(
                    w + 15,
                    0.05,
                    label_text,
                    transform=self.ax.get_xaxis_transform(),
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
