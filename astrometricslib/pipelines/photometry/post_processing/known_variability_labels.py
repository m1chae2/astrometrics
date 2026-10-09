"""Says whether the catalogs already list a variable-star candidate.

A star the photometry flags as variable is only a lead if no catalog lists it.
The answer comes from the saved catalog row (SIMBAD object types, Gaia DR3 and
VSX, whichever have been asked; see `models/known_variability.py`). A
candidate whose row is missing, or whose catalogs were never asked, is labelled
unknown, never "not listed".
"""

import logging
from collections.abc import Iterable
from typing import Any

from astrometricslib.foundation.errors import AstrometricsError
from astrometricslib.models.known_variability import KnownVariability, describe_known_variability
from astrometricslib.models.stellar_source import StellarObject, VariableCandidate
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)


def label_known_variability(candidates: Iterable[VariableCandidate], catalog_access: Any) -> None:
    """Fill in each candidate's known-variability answer, in place.

    Parameters
    ----------
    candidates : `Iterable` [`VariableCandidate`]
        The candidates to label.
    catalog_access : `CatalogAccess` or `None`
        Loads the saved stars. With `None`, or if the lookup fails, every
        candidate is left labelled unknown.
    """
    candidates = list(candidates)
    unknown_note = describe_known_variability(KnownVariability.UNKNOWN, [])
    for candidate in candidates:
        candidate.known_variability = KnownVariability.UNKNOWN.value
        candidate.known_variability_note = unknown_note
    if not candidates or catalog_access is None:
        return
    try:
        saved = {
            star.id: star
            for star in catalog_access.get_by_ids(
                "stellar_catalog", [candidate.id for candidate in candidates]
            )
        }
    except (AstrometricsError, *DATA_ERRORS):
        logger.warning("Could not look up the saved catalog rows to label known variability.", exc_info=True)
        return
    for candidate in candidates:
        star = saved.get(candidate.id)
        if star is None:
            continue
        candidate.known_variability = star.known_variability.value
        candidate.known_variability_note = describe_known_variability(
            star.known_variability, star.known_variability_catalogs
        )


def split_scatter_by_catalog_status(
    stars: Iterable[StellarObject], catalog_access: Any, chunk_size: int = 2000
) -> tuple[list[float], list[float]]:
    """Split the scatter of a run's stars by what the catalogs say about them.

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
    cvs : `tuple` [`list` [`float`], `list` [`float`]]
        Two lists: the scatter (coefficient of variation) of the stars a
        catalog lists as variable, and of the stars that were asked and are
        not listed. Stars
        with no saved row, no scatter, or no catalog answer are in neither.
    """
    scatter = {
        star.id: star.photometry.coefficient_of_variation
        for star in stars
        if star.photometry is not None and star.photometry.coefficient_of_variation is not None
    }
    if catalog_access is None or not scatter:
        return [], []
    known: list[float] = []
    unlisted: list[float] = []
    identifiers = list(scatter)
    try:
        for start in range(0, len(identifiers), chunk_size):
            for saved in catalog_access.get_by_ids(
                "stellar_catalog", identifiers[start : start + chunk_size]
            ):
                status = saved.known_variability
                if status is KnownVariability.KNOWN_VARIABLE:
                    known.append(scatter[saved.id])
                elif status is KnownVariability.NOT_LISTED:
                    unlisted.append(scatter[saved.id])
    except (AstrometricsError, *DATA_ERRORS):
        logger.warning(
            "Could not look up the saved catalog rows to check the variability statistic.", exc_info=True
        )
        return [], []
    return known, unlisted
