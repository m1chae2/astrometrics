"""Main interface for the stars the pipelines find in the images.

`StellarCatalog` is the one door to the library's star catalog. `get` returns
one star's full record for code to work with. `query` answers every question
about many stars with a short, capped answer: which stars a target has, which
stars sit in a patch of sky, which have spectra, how many there are of each
spectral class. The other methods change stars: they record a star at a
position, update or delete one, and search a light curve for a period.

The methods check their arguments and hand the work to
`pipelines/shared/star_catalog_queries.py`.
"""

import logging
from typing import Any, Literal

from astrometricslib._internal.drivers.catalog_access import AbstractCatalogAccess
from astrometricslib._internal.renames import MovedNames
from astrometricslib.foundation.arguments import check_choice, check_limit
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.catalog_queries import StarQueryResult
from astrometricslib.models.stellar_source import SpectroscopyResult, StellarObject

__all__ = ["StellarCatalog"]

logger = logging.getLogger(__name__)


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

    def dataset_version(self) -> int:
        """Report how many times the star catalog has been written to.

        A caller that keeps an expensive answer built from the whole catalog
        can remember this number and compare it later. If it is unchanged,
        no star was saved, changed or deleted through this process.

        This is one in-memory counter per running process. It does not see a
        write made by another process, and it starts again at 0 when the
        process restarts. A caller that relies on it for correctness, not
        only to avoid repeated work, should also expire its answer on a
        timer.

        Returns
        -------
        version : `int` or `None`
            The number of star catalog writes counted. It is 0 before the
            first write. `None` when the count cannot be read at the moment.
        """
        return self._catalog_access.get_dataset_version("stellar_catalog")

    def get(self, star_id: str) -> StellarObject | None:
        """Find one star's full record by its id.

        Parameters
        ----------
        star_id : `str`
            The star's id. Spaces, underscores and capital letters may
            differ from the stored id.

        Returns
        -------
        star : `StellarObject` or `None`
            The star, or `None` if there is none.
        """
        from astrometricslib.pipelines.shared.star_catalog_queries import find_star

        return find_star(self._catalog_access, star_id)

    def get_spectrum(self, star_id: str) -> SpectroscopyResult | None:
        """Read a star's spectrum from the star itself.

        The spectrum lives on the star's own record, so this needs only the
        star's id. Older libraries also hold a separate copy of the star
        whose id ends in ``::spectroscopy``. When the star's own record has
        no spectrum, that copy is read instead, so a script does not need
        to know the copy exists. An id that ends in ``::spectroscopy`` is
        accepted and read the same way.

        Parameters
        ----------
        star_id : `str`
            The star's id. Spaces, underscores and capital letters may
            differ from the stored id.

        Returns
        -------
        spectrum : `SpectroscopyResult` or `None`
            The star's spectrum, or `None` if there is no such star or it
            has no spectrum.
        """
        suffix = "::spectroscopy"
        base_id = star_id[: -len(suffix)] if star_id.endswith(suffix) else star_id
        for candidate_id in (base_id, base_id + suffix):
            star = self.get(candidate_id)
            if star is not None and star.has_spectra:
                return star.spectroscopy
        return None

    def query(
        self,
        ids: list[str] | None = None,
        name: str | None = None,
        target_id: str | None = None,
        ra_deg: float | None = None,
        dec_deg: float | None = None,
        radius_deg: float | None = None,
        tolerance_arcsec: float | None = None,
        magnitude_min: float | None = None,
        magnitude_max: float | None = None,
        has_spectra: bool | None = None,
        has_photometry: bool | None = None,
        has_catalog_magnitude: bool | None = None,
        spectral_class: str | None = None,
        search: str | None = None,
        include_unresolved: bool = False,
        image_path: str | None = None,
        detail: Literal[
            "exists",
            "ids",
            "summary",
            "analysis",
            "objects",
            "overlay",
            "class_counts",
            "target_counts",
            "stats",
        ] = "summary",
        order: Literal["id", "useful", "match"] | None = None,
        limit: int | None = 50,
        offset: int = 0,
    ) -> StarQueryResult:
        """Look up stars in the library, with a cap on the answer.

        One door for reading stars. Give at most one selector: ``ids``,
        ``name``, ``target_id``, a region (``ra_deg``, ``dec_deg`` and
        ``radius_deg``) or a position (``ra_deg``, ``dec_deg`` and
        ``tolerance_arcsec``). With none, it browses the whole library in id
        order. It reads only; nothing is changed.

        Parameters
        ----------
        ids : `list` [`str`], optional
            Star ids to look up.
        name : `str`, optional
            A star id or name, matched ignoring case. An exact id match
            comes first.
        target_id : `str`, optional
            Stars that belong to this target.
        ra_deg : `float`, optional
            Right ascension of the centre, in degrees.
        dec_deg : `float`, optional
            Declination of the centre, in degrees.
        radius_deg : `float`, optional
            Radius of a region search, in degrees, up to 5 (no cap when
            ``limit`` is `None`).
        tolerance_arcsec : `float`, optional
            Match radius of a position search, in arcseconds. The nearest
            star within it is returned.
        magnitude_min : `float`, optional
            Keep stars at least this magnitude (numerically). Stars with no
            magnitude are dropped when a magnitude bound is given. The bound
            is applied to `StellarObject.magnitude` as stored, which may be
            a SIMBAD V, a Gaia G or an old instrumental value; add
            ``has_catalog_magnitude=True`` to keep catalog values only.
        magnitude_max : `float`, optional
            Keep stars no fainter than this magnitude.
        has_spectra : `bool`, optional
            Keep only stars that do (or do not) have a recorded spectrum.
        has_photometry : `bool`, optional
            Keep only stars that do (or do not) have a light curve.
        has_catalog_magnitude : `bool`, optional
            Keep only stars whose magnitude is (or is not) a real catalog
            magnitude (SIMBAD V or Gaia G). Zero, a missing value, and an
            instrumental magnitude from photometry (below -2,
            ``BRIGHTEST_CATALOG_MAGNITUDE``) are not catalog magnitudes.
        spectral_class : `str`, optional
            Keep only stars whose catalog spectral type is this class (O, B,
            A, F, G, K, M, C or W; a full type such as ``"G2V"`` uses its
            first letter).
        search : `str`, optional
            Keep only stars whose id or name contains this text, ignoring
            case.
        include_unresolved : `bool`, optional
            Also return single-frame detections: the point sources that
            photometry finds in one frame and saves with ids ending in
            ``":Star_<n>"``. They are working records, not catalog stars, so
            they are hidden by default.
        image_path : `str`, optional
            With ``detail="overlay"`` only: the image to place the stars on
            (for example the file the app is showing). By default, the
            target's current stack.
        detail : `str`, optional
            ``"summary"`` (default): id, name, position, magnitude (and
            whether it is a real catalog magnitude), spectral type, targets
            and data flags. ``"ids"``: only ids. ``"exists"``: which of
            ``ids`` are in the library. ``"analysis"``: what the analysis
            found for each star (the star's own spectral type and how well
            it matched, the absorption features and emission lines, and
            whether the brightness repeats), with no raw arrays.
            ``"objects"``: the full star records. ``"overlay"``: the stars of
            ``target_id`` placed in 1-based pixels (as in FITS and DS9) on
            the target's stacked image (needs ``target_id``; takes no
            filter, ``order`` or ``offset``). ``"class_counts"``: how many
            stars each spectral class has. ``"target_counts"``: each
            target's star count and
            whether its stars have spectra or photometry. ``"stats"``:
            counts and coverage for the whole library, single-frame
            detections included. The last three take no selector and no
            filter.
        order : `str`, optional
            ``"id"``: by id. ``"useful"``: stars with spectra first, then
            named stars, then stars with photometry, then brightest first.
            ``"match"``: stars whose own spectrum best matches a reference
            spectrum first; summary rows then carry
            ``selfDeterminedSpectralTypeRms``. By default a ``name`` search
            keeps the exact id match first, ``"analysis"`` with
            ``spectral_class`` uses ``"match"``, and every other list is in
            id order.
        limit : `int` or `None`, optional
            How many stars to return, from 1 to the cap of the detail: 2000
            ids, 500 summaries, 200 overlay stars, and 10 analysis or full
            records. Defaults to 50, or the cap when it is smaller; any
            other value outside the range is refused. A
            program that needs every match passes `None`, which also lifts
            the cap on the region radius and on the number of ``ids`` for
            ``"exists"``.
        offset : `int`, optional
            How many stars to skip, for paging, at least 0.

        Returns
        -------
        answer : `StarQueryResult`
            The stars for the detail, ``total_matching``, and whether the
            answer was cut by the limit.

        Notes
        -----
        Raises `InvalidArgumentError` if the detail or order is unknown, or
        ``limit`` or ``offset`` is out of range.
        """
        from astrometricslib.pipelines.shared.star_catalog_queries import (
            QUERY_DETAILS,
            QUERY_LIMITS,
            QUERY_ORDERS,
            StarQuery,
            run_star_query,
        )

        check_choice("detail", detail, QUERY_DETAILS)
        # The default of 50 is cut to a smaller cap; a limit the caller
        # chose must lie inside the cap.
        if limit != 50:
            check_limit(limit, 1, QUERY_LIMITS.get(detail))
        check_limit(offset, 0, None, name="offset", allow_none=False)
        magnitude_range = None
        if magnitude_min is not None or magnitude_max is not None:
            magnitude_range = (
                magnitude_min if magnitude_min is not None else -30.0,
                magnitude_max if magnitude_max is not None else 60.0,
            )
        star_query = StarQuery(
            ids=ids,
            name=name,
            target_id=target_id,
            ra_deg=ra_deg,
            dec_deg=dec_deg,
            radius_deg=radius_deg,
            tolerance_arcsec=tolerance_arcsec,
            magnitude_range=magnitude_range,
            has_spectra=has_spectra,
            has_photometry=has_photometry,
            has_catalog_magnitude=has_catalog_magnitude,
            spectral_class=spectral_class,
            search=search,
            include_unresolved=include_unresolved,
            image_path=image_path,
        )
        if order is not None:
            check_choice("order", order, QUERY_ORDERS)
        return run_star_query(self._catalog_access, star_query, detail, limit, offset, order)

    def find_or_create_by_position(
        self,
        ra_deg: float,
        dec_deg: float,
        name: str | None = None,
        spectral_type: str | None = None,
        magnitude: float | None = None,
        target_id: str | None = None,
        tolerance_arcsec: float | None = None,
    ) -> StellarObject:
        """Find the star at a spot on the sky, or make a new one there.

        Looks for an existing star with the given name, then for one within
        ``tolerance_arcsec`` of the position. A saved star that is found gets
        any of the spectral type, magnitude and target that it does not have
        yet. When neither exists, a new star is returned but not saved, since
        the catalog keeps only stars with a light curve or a spectrum.

        Parameters
        ----------
        ra_deg : `float`
            Right ascension, in degrees.
        dec_deg : `float`
            Declination, in degrees.
        name : `str`, optional
            The star's catalog name (for example from SIMBAD). Also becomes
            the id of a new star.
        spectral_type : `str`, optional
            Spectral type to record if the star has none.
        magnitude : `float`, optional
            Magnitude to record if the star has none.
        target_id : `str`, optional
            Target to add to the star's list of targets.
        tolerance_arcsec : `float`, optional
            How close in position counts as the same star, in arcseconds.
            By default the library's registered same-star distance (2
            arcseconds), the one every other star-recording step uses.

        Returns
        -------
        star : `StellarObject`
            The star that was found, or a new one at that spot. A new star
            holds no light curve or spectrum, so it is not saved.
        """
        from astrometricslib.foundation.parameters import resolve
        from astrometricslib.pipelines.shared.catalog_star_identity import SAME_STAR_POSITION_TOLERANCE_ARCSEC
        from astrometricslib.pipelines.shared.star_catalog_queries import find_or_create_star

        if tolerance_arcsec is None:
            tolerance_arcsec = float(resolve(SAME_STAR_POSITION_TOLERANCE_ARCSEC).value)
        return find_or_create_star(
            self._catalog_access,
            self.update,
            self.create,
            self.delete,
            ra_deg,
            dec_deg,
            name,
            spectral_type,
            magnitude,
            target_id,
            tolerance_arcsec,
        )

    def analyze_periodicity(self, star_id: str) -> StellarObject | None:
        """Search a star's light curve for a repeating pattern, and save it.

        Runs the Lomb-Scargle periodogram (needs at least 5 brightness
        measurements) and the box-fitting transit search (needs at least
        8), and saves whichever produced a result on the star's
        photometry. The photometry pipeline already runs these for a
        target's own star and its brightest stars; this is how any other
        star gets its period and transit numbers.

        Parameters
        ----------
        star_id : `str`
            The id of the star to analyze.

        Returns
        -------
        star : `StellarObject` or `None`
            The star with any new analysis saved, or `None` if no such star
            exists. A star with too few measurements is returned unchanged.
        """
        from astrometricslib.pipelines.photometry.processing.variability_analyzer import VariabilityAnalyzer

        star = self.get(star_id)
        if star is None or not star.photometry:
            return star

        analyzer = VariabilityAnalyzer()
        periodogram = analyzer.run_lomb_scargle_periodogram(star)
        transit_candidate = analyzer.run_bls_transit_search(star)
        if periodogram is None and transit_candidate is None:
            return star
        return self.update(star.id, {"photometry": star.photometry})

    def delete(self, star_id: str) -> bool:
        """Delete a star from the catalog by its id.

        Parameters
        ----------
        star_id : `str`
            The id of the star to delete.

        Returns
        -------
        deleted : `bool`
            `True` if a matching star was found and removed.
        """
        existing = self.get(star_id)
        if existing is None:
            return False
        self._catalog_access.delete_by_ids("stellar_catalog", [existing.id])
        return True

    def update(self, star_id: str, updates: dict[str, Any]) -> StellarObject | None:
        """Change some of a star's fields and save it.

        Parameters
        ----------
        star_id : `str`
            The id of the star to update.
        updates : `dict`
            Field name and new value pairs. The names are `StellarObject`
            field names (``spectral_type``, not ``spectralType``).

        Returns
        -------
        star : `StellarObject` or `None`
            The updated star, or `None` if ``star_id`` is not found.

        Raises
        ------
        InvalidArgumentError
            If a name is not a `StellarObject` field. Nothing is changed.
        """
        unknown = sorted(set(updates) - set(StellarObject.model_fields))
        if unknown:
            raise InvalidArgumentError(
                f"Unknown star field(s): {', '.join(unknown)}.", details={"unknown": unknown}
            )
        existing = self.get(star_id)
        if not existing:
            return None

        for key, value in updates.items():
            setattr(existing, key, value)

        def _apply_updates(current: StellarObject | None, updated: StellarObject) -> StellarObject:
            """Write the changed fields onto the stored star.

            Returns
            -------
            star : `StellarObject`
                The stored star (or the new one) with the changes.
            """
            target_obj = current if current is not None else updated
            for key, value in updates.items():
                setattr(target_obj, key, value)
            return target_obj

        self._catalog_access.merge_and_record("stellar_catalog", [existing], _apply_updates)
        return self.get(star_id)

    def create(
        self, star_id: str, ra_deg: float | None = None, dec_deg: float | None = None
    ) -> StellarObject:
        """Record a new star in the catalog, or return the one with that id.

        The catalog keeps only stars with a light curve or a spectrum, so a
        new star made here, which holds neither, is returned but not saved.

        Parameters
        ----------
        star_id : `str`
            The id of the star to create.
        ra_deg : `float`, optional
            Right ascension (ICRS), in degrees, 0 up to 360.
        dec_deg : `float`, optional
            Declination (ICRS), in degrees, -90 to 90.

        Returns
        -------
        star : `StellarObject`
            The existing star, or the new, unsaved one.

        Raises
        ------
        InvalidArgumentError
            If ``star_id`` is empty, or a position is not a number in range.
        """
        if not star_id or not str(star_id).strip():
            raise InvalidArgumentError("star_id cannot be empty.")
        for name, value, low, high in (("ra_deg", ra_deg, 0.0, 360.0), ("dec_deg", dec_deg, -90.0, 90.0)):
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int | float) or not low <= value <= high:
                raise InvalidArgumentError(
                    f"{name} must be a number of degrees from {low:g} to {high:g}.", details={name: value}
                )

        existing = self.get(star_id)
        if existing:
            return existing

        new_star = StellarObject()
        new_star.id = star_id
        new_star.name = star_id
        if ra_deg is not None:
            new_star.right_ascension = float(ra_deg)
        if dec_deg is not None:
            new_star.declination = float(dec_deg)

        self._catalog_access.merge_and_record(
            "stellar_catalog",
            [new_star],
            lambda current, updated: current if current is not None else updated,
        )
        return new_star
