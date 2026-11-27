"""Main interface for the stars the pipelines find in the images.

`StellarCatalog` is `Astrometrics.stars`, the one door to the library's star
catalog. `get` reads one star and `query` lists the stars that match some
filters; `count_by_class`, `count_by_target` and `get_statistics` count
them. `create`, `update` and `delete` change the catalog, and
`measure_periodicity` and `record_periodicity` search a light curve for a
period. A star's light curve comes only with ``include=["light_curves"]``;
its spectrum comes from `get_spectrum`.

Each method is a thin wrapper of one function in
`astrometricslib.pipelines.shared.star_catalog_queries` or
`astrometricslib.pipelines.photometry.processing.star_periodicity`.
"""

from collections.abc import Iterable
from typing import Any

from astrometricslib._internal.drivers.catalog_access import AbstractCatalogAccess
from astrometricslib._internal.renames import MovedNames
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.models.catalog_queries import TargetStarCount
from astrometricslib.models.page import Page
from astrometricslib.models.star_catalog import PeriodicitySearch, SpectralClassCount, StarCatalogStatistics
from astrometricslib.models.stellar_source import SpectroscopyResult, StellarObject
from astrometricslib.models.target import Target

__all__ = ["StellarCatalog"]


class StellarCatalog(MovedNames):
    """The library's catalog of stars and what the analyses found about them.

    While the target catalog deals with whole pictures, this catalog tracks
    single stars over time: their brightness (photometry) and their light
    split by wavelength (spectroscopy).

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    storage : `AbstractCatalogAccess`
        The database the star catalog is read from and saved to.
    """

    def __init__(self, config: AppConfiguration, storage: AbstractCatalogAccess) -> None:
        self._config = config
        self._catalog_access = storage

    def get(
        self, star_id: str, *, include: Iterable[str] | None = None, required: bool = False
    ) -> StellarObject | None:
        """Read one star by its id.

        Owner: astrometricslib.pipelines.shared.star_catalog_queries.get_star

        Parameters
        ----------
        star_id : `str`
            The star's id. Spaces, underscores and capital letters may
            differ from the stored id.
        include : `Iterable` [`str`] or `None`, optional
            Heavy parts to read too: ``"light_curves"`` adds the points of
            the star's light curve to `StellarObject.photometry`. Without
            it the point lists are empty, though the period results and the
            per-night summaries are there.
        required : `bool`, optional
            Raise `NotFoundError` instead of returning `None` when no star
            has the id. Defaults to `False`.

        Returns
        -------
        star : `StellarObject` or `None`
            The star, or `None` if there is none and ``required`` is
            `False`.

        Notes
        -----
        Raises `InvalidArgumentError` if ``star_id`` is empty or an include
        part is unknown.

        Examples
        --------
        >>> star = astrometrics.stars.get("V0335 Cyg")  # doctest: +SKIP
        """
        from astrometricslib.pipelines.shared.star_catalog_queries import get_star

        return get_star(self._catalog_access, star_id, include=include, required=required)

    def get_spectrum(self, star: StellarObject) -> SpectroscopyResult | None:
        """Read a star's spectrum.

        Older libraries also hold a separate copy of the star whose id ends
        in ``::spectroscopy``. When the star's own record has no spectrum,
        that copy is read instead.

        Owner:

            astrometricslib.pipelines.shared.star_catalog_queries.get_star_spectrum

        Parameters
        ----------
        star : `StellarObject`
            The star, as `get` or `query` gave it.

        Returns
        -------
        spectrum : `SpectroscopyResult` or `None`
            The star's spectrum, or `None` if it has none.
        """
        from astrometricslib.pipelines.shared.star_catalog_queries import get_star_spectrum

        return get_star_spectrum(self._catalog_access, star)

    def query(
        self,
        *,
        target: Target | None = None,
        spectral_class: str | None = None,
        variable: bool | None = None,
        text: str | None = None,
        ra_deg: float | None = None,
        dec_deg: float | None = None,
        radius_deg: float | None = None,
        magnitude_min: float | None = None,
        magnitude_max: float | None = None,
        has_photometry: bool | None = None,
        has_spectra: bool | None = None,
        has_catalog_magnitude: bool | None = None,
        include_unresolved: bool = False,
        order_by: str | None = None,
        include: Iterable[str] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> Page[StellarObject]:
        """List the stars that match some filters.

        Every filter is optional; with none, every star is listed. It reads
        only; nothing is changed.

        Owner:

            astrometricslib.pipelines.shared.star_catalog_queries.query_stars

        Parameters
        ----------
        target : `Target`, optional
            Only the stars that belong to this target.
        spectral_class : `str`, optional
            Only stars whose catalog spectral type is this class (O, B, A,
            F, G, K, M, C or W; a full type such as ``"G2V"`` uses its
            first letter).
        variable : `bool`, optional
            `True` keeps the stars that SIMBAD, Gaia DR3 or VSX list as
            variable or suspected variable (`StellarObject.known_variability`);
            `False` keeps the rest.
        text : `str`, optional
            Only stars whose id or name contains this text, ignoring case.
        ra_deg, dec_deg, radius_deg : `float`, optional
            Only stars inside this circle on the sky (ICRS), in degrees.
            Give all three.
        magnitude_min, magnitude_max : `float`, optional
            Only stars with a magnitude in this range. Stars with no
            magnitude are dropped when a bound is given. The bound applies
            to `StellarObject.magnitude` as stored, which may be a SIMBAD V,
            a Gaia G or an old instrumental value; add
            ``has_catalog_magnitude=True`` to keep catalog values only.
        has_photometry, has_spectra : `bool`, optional
            Only stars that do (or do not) have a light curve, or a
            spectrum.
        has_catalog_magnitude : `bool`, optional
            Only stars whose magnitude is (or is not) a real catalog
            magnitude (SIMBAD V or Gaia G).
        include_unresolved : `bool`, optional
            Also list single-frame detections: the point sources that
            photometry finds in one frame and saves with ids ending in
            ``":Star_<n>"``. They are hidden by default.
        order_by : `str`, optional
            ``"id"`` (the default), ``"useful"`` (stars with spectra first,
            then named stars, then stars with photometry, then brightest
            first) or ``"match"`` (stars whose own spectrum best matches a
            reference spectrum first).
        include : `Iterable` [`str`] or `None`, optional
            Heavy parts to read too: ``"light_curves"`` (see `get`).
        limit : `int` or `None`, optional
            Most stars to return, at least 1. `None` (the default) returns
            every match.
        offset : `int`, optional
            How many matches to skip, for paging. Defaults to 0.

        Returns
        -------
        page : `Page` [`StellarObject`]
            The matching stars (``items``, ``total``, ``next_offset``).

        Notes
        -----
        Raises `InvalidArgumentError` if a circle is given in part, the
        spectral class or order is unknown, or ``limit`` or ``offset`` is
        out of range.

        Examples
        --------
        >>> page = astrometrics.stars.query(text="Cyg")  # doctest: +SKIP
        """
        from astrometricslib.pipelines.shared.star_catalog_queries import (
            StarFilters,
            query_stars,
            target_filter_id,
        )

        magnitude_range = None
        if magnitude_min is not None or magnitude_max is not None:
            magnitude_range = (
                magnitude_min if magnitude_min is not None else -30.0,
                magnitude_max if magnitude_max is not None else 60.0,
            )
        filters = StarFilters(
            target_id=target_filter_id(target),
            ra_deg=ra_deg,
            dec_deg=dec_deg,
            radius_deg=radius_deg,
            magnitude_range=magnitude_range,
            has_spectra=has_spectra,
            has_photometry=has_photometry,
            has_catalog_magnitude=has_catalog_magnitude,
            spectral_class=spectral_class,
            variable=variable,
            text=text,
            include_unresolved=include_unresolved,
        )
        return query_stars(
            self._catalog_access, filters, order_by=order_by, include=include, limit=limit, offset=offset
        )

    def count_by_class(
        self, *, target: Target | None = None, include_unresolved: bool = False
    ) -> Page[SpectralClassCount]:
        """Count the stars by catalog spectral class.

        Owner:

            astrometricslib.pipelines.shared.star_catalog_queries.count_stars_by_class

        Parameters
        ----------
        target : `Target`, optional
            Count only the stars of this target.
        include_unresolved : `bool`, optional
            Also count single-frame detections. Defaults to `False`.

        Returns
        -------
        classes : `Page` [`SpectralClassCount`]
            One entry per class present, in class order. Stars with no
            catalog type are not counted.
        """
        from astrometricslib.pipelines.shared.star_catalog_queries import (
            count_stars_by_class,
            target_filter_id,
        )

        return count_stars_by_class(
            self._catalog_access, target_id=target_filter_id(target), include_unresolved=include_unresolved
        )

    def count_by_target(self, *, include_unresolved: bool = False) -> Page[TargetStarCount]:
        """Count each target's stars and say what data they have.

        Owner:

            astrometricslib.pipelines.shared.star_catalog_queries.count_stars_by_target

        Parameters
        ----------
        include_unresolved : `bool`, optional
            Also count single-frame detections. Defaults to `False`.

        Returns
        -------
        counts : `Page` [`TargetStarCount`]
            One entry per target id that at least one star belongs to.
        """
        from astrometricslib.pipelines.shared.star_catalog_queries import count_stars_by_target

        return count_stars_by_target(self._catalog_access, include_unresolved=include_unresolved)

    def get_statistics(self) -> StarCatalogStatistics:
        """Count how much the catalog holds, single-frame detections included.

        Owner:

            astrometricslib.pipelines.shared.star_catalog_queries.get_star_catalog_statistics

        Returns
        -------
        statistics : `StarCatalogStatistics`
            The counts and how much of the catalog has a name, a spectral
            type and a magnitude.
        """
        from astrometricslib.pipelines.shared.star_catalog_queries import get_star_catalog_statistics

        return get_star_catalog_statistics(self._catalog_access)

    def create(
        self, name: str, ra_deg: float | None = None, dec_deg: float | None = None, **fields: Any
    ) -> StellarObject:
        """Record a new star in the catalog.

        The catalog keeps only stars with a light curve or a spectrum, so a
        new star given neither is returned but not saved.

        Owner:

            astrometricslib.pipelines.shared.star_catalog_queries.create_star

        Parameters
        ----------
        name : `str`
            The star's name, which is also its id.
        ra_deg : `float`, optional
            Right ascension (ICRS), in degrees, 0 up to 360.
        dec_deg : `float`, optional
            Declination (ICRS), in degrees, -90 to 90.
        **fields
            Other `StellarObject` fields, by field name, such as
            ``spectral_type`` or ``photometry``.

        Returns
        -------
        star : `StellarObject`
            The new star.

        Notes
        -----
        Raises `InvalidArgumentError` if ``name`` is empty, a position is out
        of range or a field is unknown, and `ConflictError` if a star with
        that id is already stored.
        """
        from astrometricslib.pipelines.shared.star_catalog_queries import create_star

        return create_star(self._catalog_access, name, ra_deg, dec_deg, **fields)

    def update(self, star: StellarObject, **changes: Any) -> StellarObject:
        """Change some of a stored star's fields and save it.

        The stored record, light curve included, is what changes, so a star
        read without its light curve can be updated safely.

        Owner:

            astrometricslib.pipelines.shared.star_catalog_queries.update_star

        Parameters
        ----------
        star : `StellarObject`
            The star, as `get` or `query` gave it. It is not changed.
        **changes
            New values, by `StellarObject` field name (``spectral_type``,
            not ``spectralType``). The id cannot change.

        Returns
        -------
        star : `StellarObject`
            The saved star, with its light curve.

        Notes
        -----
        Raises `InvalidArgumentError` if a name is not a star field (nothing
        is changed), and `NotFoundError` if the star is not stored.
        """
        from astrometricslib.pipelines.shared.star_catalog_queries import update_star

        return update_star(self._catalog_access, star, **changes)

    def delete(self, star: StellarObject) -> None:
        """Delete a star from the catalog.

        Owner:

            astrometricslib.pipelines.shared.star_catalog_queries.delete_star

        Parameters
        ----------
        star : `StellarObject`
            The star to delete.

        Notes
        -----
        Raises `NotFoundError` if the star is not stored.
        """
        from astrometricslib.pipelines.shared.star_catalog_queries import delete_star

        delete_star(self._catalog_access, star)

    def measure_periodicity(self, star: StellarObject) -> PeriodicitySearch:
        """Search a star's light curve for a repeating pattern, saving nothing.

        Runs the Lomb-Scargle periodogram (needs at least 5 brightness
        measurements) and the box-fitting transit search (needs at least
        8). The photometry pipeline already runs these for a target's own
        star and its brightest stars.

        Owner:

            astrometricslib.pipelines.photometry.processing.star_periodicity.measure_star_periodicity

        Parameters
        ----------
        star : `StellarObject`
            The star, with or without its light curve.

        Returns
        -------
        search : `PeriodicitySearch`
            Both results. A search that could not run is `None`, with a
            warning saying why.
        """
        from astrometricslib.pipelines.photometry.processing.star_periodicity import measure_star_periodicity

        return measure_star_periodicity(self._catalog_access, star)

    def record_periodicity(self, star: StellarObject) -> StellarObject:
        """Search a star's light curve for a repeating pattern, and save it.

        Owner:

            astrometricslib.pipelines.photometry.processing.star_periodicity.record_star_periodicity

        Parameters
        ----------
        star : `StellarObject`
            The star, with or without its light curve.

        Returns
        -------
        star : `StellarObject`
            The saved star with its new period and transit results. A star
            with too few measurements is returned as stored.

        Notes
        -----
        Raises `NotFoundError` if the star is not stored.
        """
        from astrometricslib.pipelines.photometry.processing.star_periodicity import record_star_periodicity

        return record_star_periodicity(self._catalog_access, star)
