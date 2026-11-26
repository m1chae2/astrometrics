"""Says whether the catalogs already list a variable-star candidate.

A star the photometry flags as variable is only a lead if no catalog lists it.
The answer comes from the saved catalog row (SIMBAD object types, Gaia DR3 and
VSX, whichever have been asked; see `models/known_variability.py`), or from the
run's own star when the star has no saved row yet (only measured stars are
saved). A candidate with neither, or whose catalogs were never asked, is
labelled unknown, never "not listed".
"""

import logging
from collections.abc import Iterable
from typing import Any

from astrometricslib.foundation.errors import AstrometricsError
from astrometricslib.models.known_variability import KnownVariability, describe_known_variability
from astrometricslib.models.stellar_source import StellarObject, VariableCandidate
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)


def label_known_variability(
    candidates: Iterable[VariableCandidate],
    catalog_access: Any,
    run_stars: Iterable[StellarObject] = (),
) -> None:
    """Fill in each candidate's known-variability answer, in place.

    Parameters
    ----------
    candidates : `Iterable` [`VariableCandidate`]
        The candidates to label.
    catalog_access : `CatalogAccess` or `None`
        Loads the saved stars. With `None`, or if the lookup fails, only the
        run's stars are used.
    run_stars : `Iterable` [`StellarObject`], optional
        The run's own stars, with the catalog answers filled in memory. A
        candidate with no saved row takes its answer from the run's star of
        the same id.
    """
    candidates = list(candidates)
    unknown_note = describe_known_variability(KnownVariability.UNKNOWN, [])
    for candidate in candidates:
        candidate.known_variability = KnownVariability.UNKNOWN.value
        candidate.known_variability_note = unknown_note
    if not candidates:
        return
    saved: dict[str, StellarObject] = {star.id: star for star in run_stars}
    if catalog_access is not None:
        try:
            saved.update({
                star.id: star
                for star in catalog_access.get_by_ids(
                    "stellar_catalog", [candidate.id for candidate in candidates]
                )
            })
        except (AstrometricsError, *DATA_ERRORS):
            logger.warning(
                "Could not look up the saved catalog rows to label known variability.", exc_info=True
            )
    for candidate in candidates:
        star = saved.get(candidate.id)
        if star is None:
            continue
        candidate.known_variability = star.known_variability.value
        candidate.known_variability_note = describe_known_variability(
            star.known_variability, star.known_variability_catalogs
        )


def is_listed_as_variable(star: StellarObject) -> bool:
    """Say whether a catalog lists, or suspects, a star as variable.

    The comparison-star choice uses this to keep such stars out of the
    comparison set: a star a catalog calls variable cannot be assumed
    constant. The answer is read from the catalog columns the star already
    carries, so it needs no lookup. A star no catalog was asked about is not
    listed, because the answer is unknown and the constancy check of the
    comparison set still tests it.

    Parameters
    ----------
    star : `StellarObject`
        The star to check.

    Returns
    -------
    is_listed : `bool`
        `True` when the combined SIMBAD, Gaia DR3 and VSX answer is known
        variable or suspected variable.
    """
    return star.known_variability in (KnownVariability.KNOWN_VARIABLE, KnownVariability.SUSPECTED_VARIABLE)


def split_scores_by_catalog_status(
    stars: Iterable[StellarObject], catalog_access: Any, chunk_size: int = 2000
) -> tuple[list[float], list[float]]:
    """Split the variability scores of a run's stars by what the catalogs say.

    Parameters
    ----------
    stars : `Iterable` [`StellarObject`]
        The run's stars.
    catalog_access : `CatalogAccess` or `None`
        Loads the saved stars, whose catalog answers are read. With `None`,
        or if the lookup fails, both lists are empty.
    chunk_size : `int`, optional
        How many stars are loaded in one request.

    Returns
    -------
    scores : `tuple` [`list` [`float`], `list` [`float`]]
        Two lists: the variability score (see `variability_indices`) of the
        stars a catalog lists as variable, and of the stars that were asked
        and are not listed. Stars with no saved row, no score (a field too
        small for a noise model), or no catalog answer are in neither.
    """
    scores = {
        star.id: star.photometry.variability_score
        for star in stars
        if star.photometry is not None and star.photometry.variability_score is not None
    }
    if catalog_access is None or not scores:
        return [], []
    known: list[float] = []
    unlisted: list[float] = []
    identifiers = list(scores)
    try:
        for start in range(0, len(identifiers), chunk_size):
            for saved in catalog_access.get_by_ids(
                "stellar_catalog", identifiers[start : start + chunk_size]
            ):
                status = saved.known_variability
                if status is KnownVariability.KNOWN_VARIABLE:
                    known.append(scores[saved.id])
                elif status is KnownVariability.NOT_LISTED:
                    unlisted.append(scores[saved.id])
    except (AstrometricsError, *DATA_ERRORS):
        logger.warning(
            "Could not look up the saved catalog rows to check the variability score.", exc_info=True
        )
        return [], []
    return known, unlisted
