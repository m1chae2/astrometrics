"""ImageOverlay: Renders 2D FITS image arrays.

Uses percentile-based intensity clipping.
"""

import numpy as np

from astrometricslib.catalog_services.utilities.image_scaling import ImageScaler


class ImageOverlay:
    """Renders 2D FITS image pixel arrays onto a Matplotlib axis.

    Parameters
    ----------
    axis : `matplotlib.axes.Axes`
        The axis to display the FITS image onto.
    config : `VisualizationConfig`
        Styling configuration object.
    """

    def __init__(self, axis, config):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.ax = axis
        self.config = config

    def render(  # ruff: ignore[missing-return-type-undocumented-public-function]
        self, data: np.ndarray | None, percentile: float = 99.5, title: str = "FITS Image with Detected Stars"
    ):
        """Display the 2D FITS pixel array with percentile clipping."""
        if data is None:
            self.ax.set_title(title)
            self.ax.set_xlabel("X Pixel")
            self.ax.set_ylabel("Y Pixel")
            return

        image_data = np.asarray(data)
        if image_data.size == 0 or (image_data.dtype == object and image_data.item() is None):
            self.ax.set_title(title)
            self.ax.set_xlabel("X Pixel")
            self.ax.set_ylabel("Y Pixel")
            return

        if image_data.ndim == 3:
            if image_data.shape[0] in (1, 3, 4):
                image_data = np.mean(image_data, axis=0)
            elif image_data.shape[2] in (1, 3, 4):
                image_data = np.mean(image_data, axis=2)
            else:
                image_data = image_data[0]

        # A hard threshold mask (zeroing everything below `percentile`
        # and letting imshow auto-scale the rest) crushes any star
        # just above the cut to near-black next to a bright cluster
        # core, since the remaining linear range still spans
        # background-to-peak. Use the same percentile-clip stretch as
        # the UI's Image Processor Display (`ImageScaler.scale_to_uint8`)
        # instead, so stars actually stand out at display brightness.
        # `percentile` is caller-supplied (directly from
        # VisualizationConfig in some callers) and isn't guaranteed to
        # sit above the 1.0 lower clip -- clamp it so vmax can never
        # come out <= vmin, which imshow's Normalize rejects outright.
        upper_percentile = min(max(percentile, 1.0 + 1e-3), 100.0)
        _, vmin, vmax = ImageScaler.scale_to_uint8(image_data, percentiles=(1.0, upper_percentile))
        self.ax.imshow(image_data, origin="lower", cmap="gray", vmin=vmin, vmax=vmax)
        self.ax.set_aspect("auto")
        self.ax.set_title(title)
        self.ax.set_xlabel("X Pixel")
        self.ax.set_ylabel("Y Pixel")
