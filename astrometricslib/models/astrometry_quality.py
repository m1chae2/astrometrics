"""Per-star catalog-match confidence produced by the astrometry pipeline.

`StarIdentifier` already measures how far a detected star sits from the
catalog entry it was matched to, and already notices when two or more
catalog entries were too close together to tell apart -- but today both
signals evaporate: the separation is only ever summed into one batch-wide
RMS (`AstrometryPipelineQualityMetrics.astrometric_residual_rms_arcsec`),
and an ambiguous match is resolved silently with no record left behind.
`CatalogMatchQuality` keeps both, per star, alongside `StellarObject`.
"""

from pydantic import BaseModel, ConfigDict, Field


class CatalogMatchQuality(BaseModel):
    """How confidently one star was matched to a catalog entry.

    `None` fields mean there was no catalog match to judge at all -- a
    star that only got a position-based ``FIELD_J...`` id was never
    compared against SIMBAD or Gaia in the first place.
    """

    model_config = ConfigDict(populate_by_name=True)

    # "simbad", "gaia", or `None` for a position-only (FIELD_J...) star.
    matched_via: str | None = Field(default=None, alias="matchedVia")
    # This star's own catalog-match separation, in arcseconds -- the
    # same per-match distance that today is only ever summed into the
    # batch-wide astrometric_residual_rms_arcsec.
    separation_arcsec: float | None = Field(default=None, alias="separationArcsec")
    # True when two or more catalog entries sat within
    # UNRESOLVED_COMPANION_RADIUS_ARCSEC of this star and the brightest
    # one was chosen (see star_identifier.brightest_unresolved_entry_index).
    # Always False for a Gaia match: Gaia matching does not run this
    # disambiguation check today, so there is nothing to flag.
    is_ambiguous: bool = Field(default=False, alias="isAmbiguous")
