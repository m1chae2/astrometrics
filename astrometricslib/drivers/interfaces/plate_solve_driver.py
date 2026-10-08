"""Purpose: The interface to a plate solver.

Description: Plate solving works out exactly which part of the sky an
image shows, from the pattern of its stars. The result is a FITS header
with a World Coordinate System (WCS), the mapping from pixel positions to
sky coordinates. `PlateSolveDriver` is the abstract base class every
plate solver's driver implements. Astrometry.net is the solver in use
(`AstrometryNetPlateSolveDriver` in `drivers/astrometry_net_driver.py`).
"""

from abc import ABC, abstractmethod
from typing import Any

from astropy.io import fits


class PlateSolveDriver(ABC):
    """What the astrometry pipeline needs from a plate solver."""

    @abstractmethod
    def solve(
        self,
        image_path: str | None = None,
        sources: list[dict[str, Any]] | None = None,
        image_width: int = 1000,
        image_height: int = 1000,
        **hints: Any,
    ) -> fits.Header | None:
        """Work out which part of the sky an image shows.

        Parameters
        ----------
        image_path : `str`, optional
            The image file.
        sources : `list` [`dict`], optional
            Stars already found in the image, each with pixel ``x`` and
            ``y`` and a ``flux``.
        image_width, image_height : `int`, optional
            The image size in pixels, needed when solving from `sources`.
        **hints
            Optional hints that make solving faster, such as the expected
            centre (``center_ra``, ``center_dec``), the search ``radius``
            and the pixel scale range (``scale_units``, ``scale_lower``,
            ``scale_upper``).

        Returns
        -------
        header : `astropy.io.fits.Header` or `None`
            A header holding the WCS, or `None` if the image could not be
            solved.
        """
