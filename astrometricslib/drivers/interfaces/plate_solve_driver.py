"""Purpose: The interface to a plate solver.

Description: Plate solving works out exactly which part of the sky an
image shows, from the pattern of its stars. The result is a FITS header
with a World Coordinate System (WCS), the mapping from pixel positions to
sky coordinates. `PlateSolveDriver` is the abstract base class every
plate solver's driver implements. Astrometry.net is the solver in use
(`AstrometryNetPlateSolveDriver` in `drivers/astrometry_net_driver.py`).

A solver may also report how well its fit matched the stars. It does so by
returning a `PlateSolveHeader`, a FITS header that carries two optional
numbers: the fit residual (how far, on average, the fitted sky positions of
the matched stars sit from the reference stars) and the count of matched
stars. A solver that cannot report them returns a plain header, and the
numbers read as unknown.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any

from astropy.io import fits


class PlateSolveHeader(fits.Header):
    """A solved FITS header that also carries the solver's fit statistics.

    It behaves as an ordinary `astropy.io.fits.Header`, so code that only
    wants the World Coordinate System (WCS) can use it unchanged. The two
    extra numbers describe how well the solver's own fit matched the field
    stars to its reference catalog.

    Parameters
    ----------
    cards : iterable, optional
        The header cards, as for `astropy.io.fits.Header`.
    copy : `bool`, optional
        Whether to copy the cards, as for `astropy.io.fits.Header`.
    fit_residual_rms_arcsec : `float`, optional
        The root mean square (RMS) of the angular distance, in arcseconds,
        between each matched field star's fitted sky position and its
        reference-catalog position. `None` when the solver gave no
        per-star match table.
    matched_star_count : `int`, optional
        How many field stars the solver matched to reference stars.
        `None` when unknown.
    """

    def __init__(
        self,
        cards: Iterable[Any] = (),
        copy: bool = False,
        *,
        fit_residual_rms_arcsec: float | None = None,
        matched_star_count: int | None = None,
    ) -> None:
        """Build the header and store the fit statistics next to it."""
        super().__init__(cards, copy)
        self.fit_residual_rms_arcsec = fit_residual_rms_arcsec
        self.matched_star_count = matched_star_count

    def copy(self, strip: bool = False) -> PlateSolveHeader:
        """Copy the header, keeping the fit statistics.

        Parameters
        ----------
        strip : `bool`, optional
            Remove cards specific to one kind of HDU (header and data
            unit), as for `astropy.io.fits.Header.copy`.

        Returns
        -------
        header : `PlateSolveHeader`
            A new header with the same cards and the same statistics.
        """
        duplicate = super().copy(strip=strip)
        duplicate.fit_residual_rms_arcsec = self.fit_residual_rms_arcsec
        duplicate.matched_star_count = self.matched_star_count
        return duplicate

    @classmethod
    def from_header(
        cls,
        header: fits.Header,
        *,
        fit_residual_rms_arcsec: float | None = None,
        matched_star_count: int | None = None,
    ) -> PlateSolveHeader:
        """Wrap an ordinary header together with fit statistics.

        Parameters
        ----------
        header : `astropy.io.fits.Header`
            The solved header to copy.
        fit_residual_rms_arcsec : `float`, optional
            See the class description.
        matched_star_count : `int`, optional
            See the class description.

        Returns
        -------
        solved : `PlateSolveHeader`
            A copy of `header` with the statistics attached.
        """
        return cls(
            header.cards,
            copy=True,
            fit_residual_rms_arcsec=fit_residual_rms_arcsec,
            matched_star_count=matched_star_count,
        )


def read_fit_statistics(header: object) -> tuple[float | None, int | None]:
    """Read the fit statistics a solver attached to its result, if any.

    Parameters
    ----------
    header : `object`
        Whatever the solver returned. Only a `PlateSolveHeader` carries
        statistics.

    Returns
    -------
    fit_residual_rms_arcsec : `float` or `None`
        The fit residual in arcseconds.
    matched_star_count : `int` or `None`
        The matched-star count. Both are `None` for a plain header, so a
        caller can tell "the solver did not say" from a measured value.
    """
    if isinstance(header, PlateSolveHeader):
        return header.fit_residual_rms_arcsec, header.matched_star_count
    return None, None


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
            solved. A solver that measures its own fit returns a
            `PlateSolveHeader`, which adds the fit residual and the
            matched-star count. A plain header is also valid.
        """
