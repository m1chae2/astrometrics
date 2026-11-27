"""Purpose: Search one catalog star's light curve for a repeating pattern.

Description: The photometry pipeline runs the period and transit searches
for a target's own star and its brightest stars. These two functions run
them for any other star of the catalog:

* `measure_star_periodicity` runs the Lomb-Scargle period search (at least
  5 brightness measurements) and the box-fitting transit search (at least
  8) and returns what they found, saving nothing.
* `record_star_periodicity` runs the same searches and saves the results on
  the star's photometry.

`StellarCatalog.measure_periodicity` and `record_periodicity` are their thin
public wrappers.
"""

from typing import Any

from astrometricslib.foundation.errors import NotFoundError
from astrometricslib.models.star_catalog import PeriodicitySearch
from astrometricslib.models.stellar_source import StellarObject

__all__ = ["measure_star_periodicity", "record_star_periodicity"]


def _whole_star(storage: Any, star: StellarObject) -> StellarObject:
    """Give a copy of the star that holds its light curve.

    Returns
    -------
    star : `StellarObject`
        A deep copy of the stored record, or of the given star when it is
        not stored but holds its light curve.

    Raises
    ------
    NotFoundError
        If the star is not stored and the given copy has no light curve.
    """
    from astrometricslib.pipelines.shared.star_catalog_queries import read_stars

    stored = read_stars(storage, [star.id], light_curves=True)
    if stored:
        return stored[0].model_copy(deep=True)
    if star.light_curves_included:
        return star.model_copy(deep=True)
    raise NotFoundError(f"No star with id {star.id!r} in the catalog.", details={"star_id": star.id})


def _search(star: StellarObject) -> PeriodicitySearch:
    """Run both searches on a star, writing their results onto its photometry.

    Returns
    -------
    search : `PeriodicitySearch`
        What the searches found, with a warning for each that did not run.
    """
    from astrometricslib.pipelines.photometry.processing.variability_analyzer import VariabilityAnalyzer

    if not star.has_photometry:
        return PeriodicitySearch(star_id=star.id, warnings=("The star has no light curve.",))
    analyzer = VariabilityAnalyzer()
    periodogram = analyzer.run_lomb_scargle_periodogram(star)
    transit_candidate = analyzer.run_bls_transit_search(star)
    warnings = []
    if periodogram is None:
        warnings.append("Too few brightness measurements for the period search (it needs 5).")
    if transit_candidate is None:
        warnings.append(
            "No transit search: it needs 8 brightness measurements with a positive mean brightness."
        )
    return PeriodicitySearch(
        star_id=star.id,
        periodogram=periodogram,
        transit_candidate=transit_candidate,
        warnings=tuple(warnings),
    )


def measure_star_periodicity(storage: Any, star: StellarObject) -> PeriodicitySearch:
    """Search a star's light curve for a repeating pattern, saving nothing.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage. The star's light curve is read from it.
    star : `StellarObject`
        The star, with or without its light curve.

    Returns
    -------
    search : `PeriodicitySearch`
        The period search and the transit search results. A search that
        could not run is `None`, with a warning saying why.

    Notes
    -----
    Raises `NotFoundError` if the star is not stored and holds no light
    curve.
    """
    return _search(_whole_star(storage, star))


def record_star_periodicity(storage: Any, star: StellarObject) -> StellarObject:
    """Search a star's light curve for a repeating pattern, and save it.

    Parameters
    ----------
    storage : `AbstractCatalogAccess`
        The catalog storage.
    star : `StellarObject`
        The star, with or without its light curve.

    Returns
    -------
    star : `StellarObject`
        The saved star with its new period and transit results, and its
        light curve. A star with too few measurements is returned as stored.

    Raises
    ------
    NotFoundError
        If the star is not in the catalog.
    """
    from astrometricslib.pipelines.shared.star_catalog_queries import read_stars, update_star

    stored = read_stars(storage, [star.id], light_curves=True)
    if not stored:
        raise NotFoundError(f"No star with id {star.id!r} in the catalog.", details={"star_id": star.id})
    whole = stored[0].model_copy(deep=True)
    search = _search(whole)
    if search.periodogram is None and search.transit_candidate is None:
        return stored[0]
    return update_star(storage, whole, photometry=whole.photometry)
