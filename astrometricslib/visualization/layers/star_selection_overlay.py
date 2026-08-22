"""StarSelectionOverlay: Highlights active star selection.

Includes crosshair markers.
"""


class StarSelectionOverlay:
    """Renders dynamic selection crosshairs and active star highlight rings.

    Parameters
    ----------
    axis : `matplotlib.axes.Axes`
        The axis to render selection crosshairs onto.
    config : `VisualizationConfig`
        Color configuration.
    """

    def __init__(self, axis, config):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.ax = axis
        self.config = config
        self.crosshair_v = None
        self.crosshair_h = None

    def update_selection(self, x: float, y: float):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Update active selection crosshairs at position (x, y)."""
        if self.crosshair_v is not None:
            self.crosshair_v.remove()
        if self.crosshair_h is not None:
            self.crosshair_h.remove()

        self.crosshair_v = self.ax.axvline(x, color=self.config.active_color, linestyle=":", alpha=0.6)
        self.crosshair_h = self.ax.axhline(y, color=self.config.active_color, linestyle=":", alpha=0.6)
