"""DispersionOverlay: Renders rotated dispersion extraction boxes.

For slitless grism frames.
"""

from matplotlib.patches import Rectangle

from astrometricslib.visualization.geometry import get_rotated_rectangle_bottom_left


class DispersionOverlay:
    """Renders dispersion extraction boxes for slitless grism frames.

    Parameters
    ----------
    axis : `matplotlib.axes.Axes`
        Matplotlib axis to render dispersion rectangles onto.
    config : `VisualizationConfig`
        Color configuration object.
    """

    def __init__(self, axis, config):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.ax = axis
        self.config = config

    def render(
        self, stellar_objects: list, active_index: int = 0, limit: int | None = None
    ) -> list[Rectangle | None]:
        """Draw dispersion trace rectangles.

        Parameters
        ----------
        stellar_objects : `list`
            List of stellar objects or dicts.
        active_index : `int`, optional
            Index of active star.
        limit : `int` or `None`, optional
            Max stars to process.

        Returns
        -------
        rect_patches : `list`
            List of created Rectangle patches.
        """
        rect_patches = []
        target_list = stellar_objects[:limit] if limit is not None else stellar_objects

        for i, obj in enumerate(target_list):
            is_obj = hasattr(obj, "star_data")
            angle = getattr(obj, "dispersion_angle", 0.0) if is_obj else obj.get("dispersion_angle", 0.0)
            rect_info = getattr(obj, "rectangle", None) if is_obj else obj.get("rectangle")

            if rect_info is not None:
                is_active = i == active_index
                color = self.config.active_color if is_active else self.config.rectangle_color
                lw = 3 if is_active else 2

                cx, cy, w, h = rect_info
                rx, ry = get_rotated_rectangle_bottom_left(cx, cy, w, h, angle)

                rect = Rectangle(
                    (rx, ry),
                    w,
                    h,
                    angle=angle,
                    edgecolor=color,
                    facecolor="none",
                    lw=lw,
                    linestyle="--",
                    picker=True,
                )
                self.ax.add_patch(rect)
                rect_patches.append(rect)
            else:
                rect_patches.append(None)

        return rect_patches
