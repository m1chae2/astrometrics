"""Component for managing interactive mouse events and state."""

import numpy as np
from matplotlib.widgets import Button

from .visualization_config import VisualizationConfig


class InteractionHandler:
    """Manage mouse interaction state for the star field visualization.

    Tracks star dragging, line-measurement mode, and crosshair
    synchronization, and dispatches to orchestrator-supplied
    callbacks (``on_star_select``, ``on_star_drag``,
    ``on_crosshair_sync``, ``on_find_star``) so this class stays
    decoupled from the star-field data it operates on.
    """

    def __init__(self, fig, ax_image, ax_spectrum, config: VisualizationConfig):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        """Initialize interaction state on the given figure and axes."""
        self.fig = fig
        self.ax_image = ax_image
        self.ax_spectrum = ax_spectrum
        self.config = config

        # Interaction State
        self.dragging = False
        self.dragged_star_index = None
        self.active_star_index = 0
        self.line_mode = False
        self.line_points = []
        self.line_points_axes = []

        # Artists
        self.line_artist = None
        self.annotation_artist = None
        self.image_cross_artist = None
        self.spectrum_cross_artist = None

        # Callbacks (to be set by orchestrator)
        self.on_star_select = None
        self.on_star_drag = None
        self.on_crosshair_sync = None

    def add_measurement_toggle(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Add the measurement mode toggle button."""
        bbox = self.ax_image.get_position()
        width, height = 0.25, 0.04
        x0 = bbox.x1 - width - 0.005
        y0 = bbox.y0 - height - 0.03

        ax_button = self.fig.add_axes([x0, y0, width, height])
        self.btn_measurement = Button(
            ax_button,
            "Measurement Mode: OFF",
            color=self.config.button_color,
            hovercolor=self.config.button_hover,
        )
        self.btn_measurement.on_clicked(self.toggle_line_mode)

    def toggle_line_mode(self, event):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Toggle line-measurement mode and reset any in-progress line."""
        self.line_mode = not self.line_mode
        self.line_points = []
        self.line_points_axes = []
        self.clear_measurement_artists()
        self.btn_measurement.label.set_text(f"Measurement Mode: {'ON' if self.line_mode else 'OFF'}")
        self.fig.canvas.draw_idle()

    def clear_measurement_artists(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Remove the measurement line and annotation artists, if present."""
        if self.line_artist:
            self.line_artist.remove()
            self.line_artist = None
        if self.annotation_artist:
            self.annotation_artist.remove()
            self.annotation_artist = None

    def connect_events(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Connect mouse press, motion, and release events to handlers."""
        self.fig.canvas.mpl_connect("button_press_event", self.on_press)
        self.fig.canvas.mpl_connect("motion_notify_event", self.on_motion)
        self.fig.canvas.mpl_connect("button_release_event", self.on_release)

    def on_press(self, event):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Route a mouse button press to click, drag-start, or line handling.

        Parameters
        ----------
        event : `matplotlib.backend_bases.MouseEvent`
            The mouse press event, used for its axes and data
            coordinates.
        """
        if event.inaxes == self.ax_spectrum:
            self.handle_spectrum_click(event)
            return

        if event.inaxes != self.ax_image:
            return

        if self.line_mode:
            self.handle_measurement_click(event)
            return

        # Check for star selection/drag
        star_idx = self.find_star_at(event.xdata, event.ydata)
        if star_idx is not None:
            self.dragging = True
            self.dragged_star_index = star_idx
            if self.on_star_select:
                self.on_star_select(star_idx)
        else:
            self.handle_image_click(event)

    def on_motion(self, event):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Forward mouse motion to the drag callback while dragging a star.

        Parameters
        ----------
        event : `matplotlib.backend_bases.MouseEvent`
            The mouse motion event, used for its axes and data
            coordinates.
        """
        if self.dragging and event.inaxes == self.ax_image:
            if self.on_star_drag:
                self.on_star_drag(self.dragged_star_index, event.xdata, event.ydata)

    def on_release(self, event):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Clear drag state on mouse button release."""
        self.dragging = False
        self.dragged_star_index = None

    def find_star_at(self, x, y):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Find the index of the star under the given data coordinates.

        Parameters
        ----------
        x : `float`
            X data coordinate to hit-test.
        y : `float`
            Y data coordinate to hit-test.

        Returns
        -------
        star_index : `int` or `None`
            The index of the star patch at this position, or `None`
            if no ``on_find_star`` callback is set or no star is hit.
        """
        # This will be delegated to the orchestrator which owns the
        # star patches
        return self.on_find_star(x, y) if hasattr(self, "on_find_star") else None

    def handle_measurement_click(self, event):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Record a measurement click, drawing the line after two clicks.

        Parameters
        ----------
        event : `matplotlib.backend_bases.MouseEvent`
            The mouse click event, used for its data coordinates.
        """
        self.line_points.append((event.xdata, event.ydata))
        self.line_points_axes.append(event.inaxes)
        if len(self.line_points) == 2:
            self.draw_measurement_line()
            self.line_points = []
            self.line_points_axes = []

    def draw_measurement_line(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Draw the two-point measurement line with a length/angle label."""
        (x0, y0), (x1, y1) = self.line_points
        self.clear_measurement_artists()

        self.line_artist = self.ax_image.plot([x0, x1], [y0, y1], color=self.config.balmer_color, lw=2)[0]
        dx, dy = x1 - x0, y1 - y0
        dist = np.hypot(dx, dy)
        angle = np.degrees(np.arctan2(dy, dx))

        text = f"Length: {dist:.1f} px\nAngle: {angle:.1f}°"
        self.annotation_artist = self.ax_image.annotate(
            text,
            ((x0 + x1) / 2, (y0 + y1) / 2),
            color=self.config.balmer_color,
            fontsize=12,
            bbox={"boxstyle": "round,pad=0.3", "fc": "black", "alpha": 0.5},
        )
        self.fig.canvas.draw_idle()

    def handle_image_click(self, event):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Sync crosshairs from an image-panel click, if callback is set."""
        if self.on_crosshair_sync:
            self.on_crosshair_sync("image", event.xdata, event.ydata)

    def handle_spectrum_click(self, event):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Sync crosshairs from a spectrum-panel click, if callback is set."""
        if self.on_crosshair_sync:
            self.on_crosshair_sync("spectrum", event.xdata, event.ydata)

    def clear_crosshairs(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Remove the image and spectrum crosshair artists, if present."""
        if self.image_cross_artist:
            self.image_cross_artist.remove()
            self.image_cross_artist = None
        if self.spectrum_cross_artist:
            self.spectrum_cross_artist.remove()
            self.spectrum_cross_artist = None
