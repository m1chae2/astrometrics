"""StarOverlay: Draws star centroids and selection circles.

Includes catalog labels with limit filtering.
"""

from matplotlib.patches import Circle


class StarOverlay:
    """Draws star selection circles and text labels onto a 2D FITS image axis.

    Parameters
    ----------
    axis : `matplotlib.axes.Axes`
        The Matplotlib axis to render star overlays onto.
    config : `VisualizationConfig`
        Color and size configuration.
    """

    def __init__(self, axis, config):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.ax = axis
        self.config = config

    def render(
        self,
        stellar_objects: list,
        active_index: int = 0,
        limit: int | None = None,
    ) -> list[Circle]:
        """Draw star selection circles and text labels.

        Parameters
        ----------
        stellar_objects : `list`
            List of stellar objects or coordinate dicts.
        active_index : `int`, optional
            Active star index to highlight, default `0`.
        limit : `int` or `None`, optional
            Maximum number of stars to display overlays for. If `None`,
            displays all.

        Returns
        -------
        star_patches : `list[Circle]`
            List of drawn circle patches.
        """
        star_patches = []
        target_list = stellar_objects[:limit] if limit is not None else stellar_objects

        for i, obj in enumerate(target_list):
            is_obj = hasattr(obj, "star_data") or not isinstance(obj, dict)
            if is_obj:
                star_data = getattr(obj, "star_data", {}) or {}
                if isinstance(star_data, dict):
                    x = star_data.get("xcentroid", star_data.get("x_centroid"))
                    y = star_data.get("ycentroid", star_data.get("y_centroid"))
                else:
                    x, y = None, None

                if x is None:
                    x = getattr(obj, "x", getattr(obj, "xcentroid", getattr(obj, "x_centroid", 0.0)))
                if y is None:
                    y = getattr(obj, "y", getattr(obj, "ycentroid", getattr(obj, "y_centroid", 0.0)))
            else:
                star_data = obj.get("star_data", {}) if isinstance(obj.get("star_data"), dict) else {}
                x = obj.get(
                    "xcentroid",
                    obj.get(
                        "x_centroid",
                        obj.get("x", star_data.get("xcentroid", star_data.get("x_centroid", 0.0))),
                    ),
                )
                y = obj.get(
                    "ycentroid",
                    obj.get(
                        "y_centroid",
                        obj.get("y", star_data.get("ycentroid", star_data.get("y_centroid", 0.0))),
                    ),
                )

            name = getattr(obj, "name", "") if is_obj else obj.get("name", "")
            spectral_type = (
                getattr(obj, "stellar_spectral_type", "") if is_obj else obj.get("stellar_spectral_type", "")
            )

            is_active = i == active_index
            color = self.config.active_color if is_active else self.config.inactive_color
            lw = 3 if is_active else 2

            circle = Circle(
                (x, y),
                self.config.fixed_radius,
                edgecolor=color,
                facecolor="none",
                lw=lw,
                picker=True,
                clip_on=True,
            )
            self.ax.add_patch(circle)
            star_patches.append(circle)

            if name:
                label = name
                if spectral_type:
                    label += f" ({spectral_type})"
                self.ax.text(
                    x + self.config.fixed_radius + 5,
                    y,
                    label,
                    color=color,
                    fontsize=10,
                    verticalalignment="center",
                    clip_on=True,
                )

        return star_patches
