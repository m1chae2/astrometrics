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

from astrometricslib.drivers.catalog_access import AbstractCatalogAccess
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.catalog_queries import StarQueryResult
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.shared.api_arguments import check_choice

__all__ = ["StellarCatalog"]

logger = logging.getLogger(__name__)


class StellarCatalog:
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
        self.catalog_access = storage

    def get(self, object_id: str) -> StellarObject | None:
        """Find one star's full record by its id.

        Parameters
        ----------
        object_id : `str`
            The star's id. Spaces, underscores and capital letters may
            differ from the stored id.

        Returns
        -------
        star : `StellarObject` or `None`
            The star, or `None` if there is none.
        """
        from astrometricslib.pipelines.shared.star_catalog_queries import find_star

        return find_star(self.catalog_access, object_id)

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
            magnitude are dropped when a magnitude bound is given.
        magnitude_max : `float`, optional
            Keep stars no fainter than this magnitude.
        has_spectra : `bool`, optional
            Keep only stars that do (or do not) have a recorded spectrum.
        has_photometry : `bool`, optional
            Keep only stars that do (or do not) have a light curve.
        has_catalog_magnitude : `bool`, optional
            Keep only stars whose magnitude is (or is not) a real catalog
            magnitude. Zero, a missing value, and an instrumental magnitude
            from photometry (below -2) are not catalog magnitudes.
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
        detail : `str`, optional
            ``"summary"`` (default): id, name, position, magnitude (and
            whether it is a real catalog magnitude), spectral type, targets
            and data flags. ``"ids"``: only ids. ``"exists"``: which of
            ``ids`` are in the library. ``"analysis"``: what the analysis
            found for each star (the star's own spectral type and how well
            it matched, the absorption features and emission lines, and
            whether the brightness repeats), with no raw arrays.
            ``"objects"``: the full star records. ``"overlay"``: the stars of
            ``target_id`` placed in pixels on the target's stacked image
            (needs ``target_id``; takes no filter, ``order`` or
            ``offset``). ``"class_counts"``: how many stars each spectral
            class has. ``"target_counts"``: each target's star count and
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
            How many stars to return. At most 2000 ids, 500 summaries, 200
            overlay stars, and 10 analysis or full records. Defaults to 50.
            A program that needs every match passes `None`, which also lifts
            the cap on the region radius and on the number of ``ids`` for
            ``"exists"``.
        offset : `int`, optional
            How many stars to skip, for paging.

        Returns
        -------
        answer : `StarQueryResult`
            The stars for the detail, ``total_matching``, and whether the
            answer was cut by the limit.
        """
        from astrometricslib.pipelines.shared.star_catalog_queries import (
            QUERY_DETAILS,
            QUERY_ORDERS,
            StarQuery,
            run_star_query,
        )

        check_choice("detail", detail, QUERY_DETAILS)
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
        )
        if order is not None:
            check_choice("order", order, QUERY_ORDERS)
        return run_star_query(self.catalog_access, star_query, detail, limit, offset, order)

    def find_or_create_by_position(
        self,
        ra_deg: float,
        dec_deg: float,
        name: str | None = None,
        spectral_type: str | None = None,
        magnitude: float | None = None,
        target_id: str | None = None,
        tolerance_arcsec: float = 5.0,
    ) -> StellarObject:
        """Find the star at a spot on the sky, or record a new one there.

        Looks for an existing star with the given name, then for one within
        ``tolerance_arcsec`` of the position, and only records a new star if
        neither exists. Whichever star is used gets any of the spectral type,
        magnitude and target that it does not have yet.

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

        Returns
        -------
        star : `StellarObject`
            The star that was found or created.
        """
        from astrometricslib.pipelines.shared.star_catalog_queries import find_or_create_star

        return find_or_create_star(
            self.catalog_access,
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

    def analyze_periodicity(self, object_id: str) -> StellarObject | None:
        """Search a star's light curve for a repeating pattern, and save it.

        Runs the Lomb-Scargle periodogram (needs at least 5 brightness
        measurements) and the box-fitting transit search (needs at least
        8), and saves whichever produced a result on the star's
        photometry. The photometry pipeline already runs these for a
        target's own star and its brightest stars; this is how any other
        star gets its period and transit numbers.

        Parameters
        ----------
        object_id : `str`
            The id of the star to analyze.

        Returns
        -------
        star : `StellarObject` or `None`
            The star with any new analysis saved, or `None` if no such star
            exists. A star with too few measurements is returned unchanged.
        """
        from astrometricslib.pipelines.photometry.processing.variability_analyzer import VariabilityAnalyzer

        star = self.get(object_id)
        if star is None or not star.photometry:
            return star

        analyzer = VariabilityAnalyzer()
        periodogram = analyzer.run_lomb_scargle_periodogram(star)
        transit_candidate = analyzer.run_bls_transit_search(star)
        if periodogram is None and transit_candidate is None:
            return star
        return self.update(star.id, {"photometry": star.photometry})

    def tune_spectroscopy_calibration(
        self,
        image_path: str,
        camera_id: str | None = None,
        star_x: float | None = None,
        star_y: float | None = None,
    ) -> dict[str, Any]:
        """Fit the spectroscope's physical model to one spectrum image.

        Parameters
        ----------
        image_path : `str`
            Path to the spectroscopy FITS image to calibrate against.
        camera_id : `str`, optional
            The camera, used to look up its quantum-efficiency curve.
        star_x : `float`, optional
            Zero-order star's x pixel position, if already known.
        star_y : `float`, optional
            Zero-order star's y pixel position, if already known.

        Returns
        -------
        calibration_result : `dict`
            The fitted calibration values and how well they fit.
        """
        from astrometricslib.pipelines.spectroscopy.utilities.calibration_tuner import (
            SpectroscopyCalibrationTuner,
        )

        tuner = SpectroscopyCalibrationTuner(config=self._config)
        star_position = (star_x, star_y) if (star_x is not None and star_y is not None) else None
        return tuner.tune_calibration(image_path, camera_name=camera_id, star_pos=star_position)

    def delete(self, object_id: str) -> bool:
        """Delete a star from the catalog by its id.

        Parameters
        ----------
        object_id : `str`
            The id of the star to delete.

        Returns
        -------
        deleted : `bool`
            `True` if a matching star was found and removed.
        """
        existing = self.get(object_id)
        if existing is None:
            return False
        self.catalog_access.delete_by_ids("stellar_catalog", [existing.id])
        return True

    def update(self, object_id: str, updates: dict[str, Any]) -> StellarObject | None:
        """Change some of a star's fields and save it.

        Parameters
        ----------
        object_id : `str`
            The id of the star to update.
        updates : `dict`
            Field name and new value pairs. Unknown names are ignored.

        Returns
        -------
        star : `StellarObject` or `None`
            The updated star, or `None` if ``object_id`` is not found.
        """
        existing = self.get(object_id)
        if not existing:
            return None

        for key, value in updates.items():
            if hasattr(existing, key):
                setattr(existing, key, value)

        def _apply_updates(current: StellarObject | None, updated: StellarObject) -> StellarObject:
            target_obj = current if current is not None else updated
            for key, value in updates.items():
                if hasattr(target_obj, key):
                    setattr(target_obj, key, value)
            return target_obj

        self.catalog_access.merge_and_record("stellar_catalog", [existing], _apply_updates)
        return self.get(object_id)

    def create(self, object_id: str, ra: str | None = None, dec: str | None = None) -> StellarObject:
        """Record a new star in the catalog, or return the one with that id.

        Parameters
        ----------
        object_id : `str`
            The id of the star to create.
        ra : `str`, optional
            Right ascension, in sexagesimal or degrees.
        dec : `str`, optional
            Declination, in sexagesimal or degrees.

        Returns
        -------
        star : `StellarObject`
            The existing or newly created star.

        Raises
        ------
        InvalidArgumentError
            If ``object_id`` is empty.
        """
        if not object_id or not str(object_id).strip():
            raise InvalidArgumentError("object_id cannot be empty.")

        existing = self.get(object_id)
        if existing:
            return existing

        new_star = StellarObject()
        new_star.id = object_id
        new_star.name = object_id
        if ra:
            new_star.right_ascension = ra
        if dec:
            new_star.declination = dec

        self.catalog_access.merge_and_record(
            "stellar_catalog",
            [new_star],
            lambda current, updated: current if current is not None else updated,
        )
        return new_star

    def save_all(self, objects: list[StellarObject], allow_empty: bool = False) -> str:
        """Save a complete list of stars, replacing the whole catalog.

        Warning: this deletes every star that is not in the new list. To
        change a few stars, use `update` instead.

        Parameters
        ----------
        objects : `list` [`StellarObject`]
            The full set of stars to record.
        allow_empty : `bool`, optional
            Saving an empty list is refused unless this is `True`, so the
            whole catalog is never deleted by accident.

        Returns
        -------
        result : `str`
            A short note saying the catalog was saved.

        Raises
        ------
        InvalidArgumentError
            If ``objects`` is empty and ``allow_empty`` is `False`.
        """
        if not objects and not allow_empty:
            raise InvalidArgumentError(
                "save_all() received an empty list, which would delete every star. "
                "Pass allow_empty=True to clear the catalog on purpose."
            )
        self.catalog_access.put(objects, "stellar_catalog", {})
        return "stellar catalog saved"

    def detect_point_sources(
        self,
        image_data: Any,
        threshold_sigma: float = 5.0,
        fwhm: float = 4.0,
    ) -> list[dict[str, Any]]:
        """Find stars (point sources) in an image's pixel data.

        Parameters
        ----------
        image_data : `numpy.ndarray`
            2D pixel array to search.
        threshold_sigma : `float`, optional
            Detection threshold, in noise standard deviations above the
            background. Defaults to 5.0.
        fwhm : `float`, optional
            Expected star width (FWHM), in pixels. Defaults to 4.0.

        Returns
        -------
        sources : `list` [`dict`]
            The stars found, brightest first.
        """
        from astrometricslib.pipelines.astrometry.pre_processing.source_detection import SourceDetector

        return SourceDetector(threshold_sigma=threshold_sigma, fwhm=fwhm).detect(image_data)
