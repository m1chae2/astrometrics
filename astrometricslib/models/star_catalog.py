"""Purpose: The answers of the star catalog's counts, statistics and searches.

Description: `StellarCatalog.query` returns a `Page` of `StellarObject`
records. Its other read methods return these small, frozen models:

- `SpectralClassCount` from `StellarCatalog.count_by_class`: how many stars
  have one catalog spectral class.
- `StarCatalogStatistics` from `StellarCatalog.get_statistics`: how much the
  whole catalog holds and how much of it has a name, a type or a
  magnitude.
- `PeriodicitySearch` from `StellarCatalog.measure_periodicity`: what the
  period and transit searches found in one star's light curve.
- `StarOverlay` from `Visualization.get_star_overlay`: a target's stars
  placed on an image (`OverlayStar` rows, from `catalog_queries`).

The per-target counts of `StellarCatalog.count_by_target` are
`TargetStarCount` rows, also from `catalog_queries`.
"""

from pydantic import BaseModel, ConfigDict, Field

from astrometricslib.models.catalog_queries import OverlayStar
from astrometricslib.models.stellar_source import PeriodogramResult, TransitCandidate

__all__ = ["PeriodicitySearch", "SpectralClassCount", "StarCatalogStatistics", "StarOverlay"]


class SpectralClassCount(BaseModel):
    """How many stars of the catalog have one spectral class.

    Attributes
    ----------
    spectral_class : `str`
        The class letter: O, B, A, F, G, K, M, C or W.
    label : `str`
        A short description of the class, such as ``"hot blue"``.
    count : `int`
        How many stars have that class as the first letter of their catalog
        spectral type.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    spectral_class: str = Field(alias="spectralClass")
    label: str = ""
    count: int = 0


class StarCatalogStatistics(BaseModel):
    """How much the star catalog holds, single-frame detections included.

    Attributes
    ----------
    total_objects : `int`
        Every star record.
    identified_objects : `int`
        Stars with a name that is not a ``Star_<n>`` working id.
    with_spectral_type : `int`
        Stars with a known catalog spectral type.
    with_magnitude : `int`
        Stars with a magnitude.
    spectral_coverage_pct : `float`
        ``with_spectral_type`` as a percentage of ``total_objects``.
    photometric_coverage_pct : `float`
        ``with_magnitude`` as a percentage of ``total_objects``.
    """

    model_config = ConfigDict(frozen=True)

    total_objects: int = 0
    identified_objects: int = 0
    with_spectral_type: int = 0
    with_magnitude: int = 0
    spectral_coverage_pct: float = 0.0
    photometric_coverage_pct: float = 0.0


class PeriodicitySearch(BaseModel):
    """What the period and transit searches found in one star's light curve.

    Attributes
    ----------
    star_id : `str`
        The star that was searched.
    periodogram : `PeriodogramResult` or `None`
        The Lomb-Scargle period search, or `None` when the light curve has
        fewer than 5 points.
    transit_candidate : `TransitCandidate` or `None`
        The box-shaped dip search, or `None` when the light curve has fewer
        than 8 points or no positive brightness.
    warnings : `tuple` [`str`, ...]
        Why a search did not run, such as too few points.
    """

    model_config = ConfigDict(frozen=True)

    star_id: str
    periodogram: PeriodogramResult | None = None
    transit_candidate: TransitCandidate | None = None
    warnings: tuple[str, ...] = ()


class StarOverlay(BaseModel):
    """A target's catalog stars placed on an image.

    Attributes
    ----------
    stars : `tuple` [`OverlayStar`, ...]
        The placed stars, most useful first, in 1-based pixels.
    reason : `str` or `None`
        Why there are no stars when the image has no sky solution (WCS) and
        no saved pixel center fits it. Otherwise `None`.
    """

    model_config = ConfigDict(frozen=True)

    stars: tuple[OverlayStar, ...] = ()
    reason: str | None = None
