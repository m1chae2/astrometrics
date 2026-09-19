"""Tools for adjusting image brightness and contrast for display.

This file only handles the math to convert raw telescope data into
a format that can be drawn on a screen. Saving the actual image
files happens somewhere else.
"""

import logging

import numpy as np

logger = logging.getLogger(__name__)


class ImageScaler:
    """Adjust an astronomy image's brightness and contrast for viewing."""

    @staticmethod
    def scale_to_uint8(
        data: np.ndarray,
        vmin: float | None = None,
        vmax: float | None = None,
        stretch: bool = True,
        percentiles: tuple[float, float] = (1.0, 99.0),
    ) -> tuple[np.ndarray, float, float]:
        """Convert raw image data into standard computer colors (0-255).

        If minimum and maximum brightness values are not provided, it
        will guess them based on the darkest and lightest pixels.

        Returns
        -------
        result : `tuple`
            The new image data, the minimum brightness used, and the
            maximum brightness used.
        """
        arr = np.array(data, dtype=float)

        # Handle multi-channel data (e.g. RGB FITS)
        if arr.ndim == 3:
            if arr.shape[0] in [3, 4]:
                arr = np.transpose(arr, (1, 2, 0))
            elif arr.shape[2] not in [3, 4]:
                arr = arr[0] if arr.shape[0] == 1 else arr[:, :, 0]

        if vmin is None or vmax is None:
            if stretch:
                try:
                    # Sampling for large arrays to improve performance
                    if arr.size > 100000:
                        sample = arr.flat[np.random.randint(0, arr.size, 10000)]
                        calc_vmin, calc_vmax = np.percentile(sample, percentiles)
                    else:
                        calc_vmin, calc_vmax = np.percentile(arr, percentiles)

                    if vmin is None:
                        vmin = calc_vmin
                    if vmax is None:
                        vmax = calc_vmax
                except Exception as e:
                    logger.warning(f"Error calculating percentiles for scaling: {e}")

            # Fallback to absolute min/max if stretch is off or failed
            if vmin is None:
                vmin = float(np.nanmin(arr))
            if vmax is None:
                vmax = float(np.nanmax(arr))

        if not np.isfinite(vmin):
            vmin = 0.0
        if not np.isfinite(vmax) or vmax == vmin:
            vmax = vmin + 1.0

        # Clip and scale
        img = np.clip((arr - vmin) / (vmax - vmin), 0.0, 1.0)
        return (img * 255.0).astype(np.uint8), float(vmin), float(vmax)
