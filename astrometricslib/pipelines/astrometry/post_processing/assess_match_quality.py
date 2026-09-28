"""Build a star's `CatalogMatchQuality` from its own catalog match.

The logic here does nothing SIMBAD/Gaia matching didn't already compute
-- it just wraps that computation's own separation/ambiguity signal in a
real, typed object instead of letting it evaporate into a batch average
or a silent tie-break. See `CatalogMatchQuality`'s own docstring.
"""

from astrometricslib.models.astrometry_quality import CatalogMatchQuality


def assess_match_quality(
    matched_via: str, separation_arcsec: float, is_ambiguous: bool = False
) -> CatalogMatchQuality:
    """Wrap one star's catalog-match separation and ambiguity into a record.

    Parameters
    ----------
    matched_via : `str`
        Which catalog resolved this star: ``"simbad"`` or ``"gaia"``.
    separation_arcsec : `float`
        How far this star's detected position sits from the catalog
        entry it was matched to, in arcseconds.
    is_ambiguous : `bool`, optional
        Whether two or more catalog entries were too close together to
        tell apart, and the brightest was chosen. Default `False`.

    Returns
    -------
    match_quality : `CatalogMatchQuality`
        The per-star record to store on `StellarObject.catalog_match_quality`.
    """
    return CatalogMatchQuality(
        matched_via=matched_via,
        separation_arcsec=separation_arcsec,
        is_ambiguous=is_ambiguous,
    )
