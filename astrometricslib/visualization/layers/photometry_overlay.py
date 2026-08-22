"""PhotometryOverlay: Renders 1D time-series differential light curves.

Also renders Lomb-Scargle periodograms.
"""

import numpy as np


class PhotometryOverlay:
    """Renders 1D differential light curves and periodograms.

    Parameters
    ----------
    axis : `matplotlib.axes.Axes`
        Axis on which the light curve or periodogram is plotted.
    fig : `matplotlib.figure.Figure`
        Figure that owns the axis.
    config : `VisualizationConfig`
        Color and styling configuration.
    """

    def __init__(self, axis, fig, config):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.ax = axis
        self.fig = fig
        self.config = config
        self.active_star_name = ""
        self.active_timestamps = None
        self.active_light_curve = None

    def render_light_curve(  # ruff: ignore[missing-return-type-undocumented-public-function]
        self,
        index: int,
        star_name: str,
        timestamps: np.ndarray | list | None,
        normalized_flux: np.ndarray | list | None,
        is_variable_candidate: bool = False,
    ):
        """Draw 1D time-series differential light curve."""
        self.ax.clear()
        self.active_star_name = star_name

        if timestamps is not None and normalized_flux is not None and len(timestamps) > 0:
            title = f"Light Curve for Star {index + 1}"
            if star_name:
                title += f": {star_name}"
            if is_variable_candidate:
                title += " (Variable Candidate)"

            color = self.config.active_color if is_variable_candidate else "#00bfff"
            self.ax.plot(timestamps, normalized_flux, "o", color=color, alpha=0.8, ms=4)
            self.ax.set_xlabel("Observation Time (JD / Epoch)")
            self.ax.set_ylabel("Normalized Differential Flux")
            self.ax.set_title(title)
            self.ax.grid(True, linestyle=":", alpha=0.4)
        else:
            self.ax.text(
                0.5, 0.5, "No light curve available", ha="center", va="center", color="red", fontsize=12
            )
            self.ax.set_title("Light Curve Not Available")

        self.fig.canvas.draw_idle()
