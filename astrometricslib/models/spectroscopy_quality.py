"""Data structures for judging one star's spectrum, not the whole pipeline run.

`quality_summary.py` records how a whole processing run went (how many
stars were found, how many frames were skipped). This module is about a
single star's own spectrum instead: how good the raw data behind it was,
what it looks like next to the star's catalog entry, and how much the
resulting classification should be trusted. These are attached directly
to `SpectroscopyResult` on `StellarObject`, one set per star, so a reader
can judge a single result without re-deriving any of this themselves.
"""

from pydantic import BaseModel, ConfigDict, Field


class CatalogComparison(BaseModel):
    """How a star's self-determined spectrum compares with its catalog entry.

    The catalog (SIMBAD/Gaia) already has a spectral type and a B-V colour
    for most stars, measured a different way. This is not used to help the
    classification along -- it is a check done afterward, to catch a
    spectrum that probably is not this star's at all (a bright neighbour's
    light, glare from a nearby bright star, or a name given to the wrong
    object).
    """

    model_config = ConfigDict(populate_by_name=True)

    # Whether the self-determined type is close enough to the catalog type
    # to call it agreement. `None` when there was nothing to compare (no
    # catalog type, or no self-determined type).
    spectral_type_agrees: bool | None = Field(default=None, alias="spectralTypeAgrees")
    # Why `spectral_type_agrees` is `False`, or an empty string otherwise.
    spectral_type_note: str = Field(default="", alias="spectralTypeNote")
    # True when the catalog calls this star a giant or supergiant. The
    # classifier only ever compares against main-sequence (dwarf)
    # references, so a giant's self-determined type is always "the dwarf
    # that looks most alike", not a contradiction to be flagged.
    is_luminosity_class_uncertain: bool = Field(default=False, alias="isLuminosityClassUncertain")
    # Explains the dwarf-vs-giant mismatch above, or an empty string when
    # `is_luminosity_class_uncertain` is `False`.
    luminosity_class_note: str = Field(default="", alias="luminosityClassNote")
    # The giant or supergiant reference that looks closest to this
    # spectrum, named alongside the ordinary (dwarf) self-determined type
    # when the catalog calls the star a giant. `None` when not looked up.
    closest_giant_type: str | None = Field(default=None, alias="closestGiantType")
    closest_giant_rms: float | None = Field(default=None, alias="closestGiantRms")
    # Whether the spectrum's own colour (see `synthetic_b_minus_v`) is
    # close enough to the catalog's B-V to call it agreement. `None` when
    # there was nothing to compare.
    colour_agrees: bool | None = Field(default=None, alias="colourAgrees")
    # Why `colour_agrees` is `False`, or an empty string otherwise.
    colour_note: str = Field(default="", alias="colourNote")

    def joined_note(self) -> str:
        """Join this comparison's notes into one line of text.

        Kept for callers that only want a short summary rather than the
        structured fields above -- for example a UI showing one line
        under a star's classification. The order (colour, then type, then
        luminosity class) matches how the note used to be built before
        this class existed, so it reads the same way it always has.

        Returns
        -------
        note : `str`
            The non-empty notes among colour, type and luminosity class,
            joined with "; ", or an empty string when none fired.
        """
        return "; ".join(
            note for note in (self.colour_note, self.spectral_type_note, self.luminosity_class_note) if note
        )


class InputQualityAssessment(BaseModel):
    """How good the raw data behind a spectrum was, before anything was found.

    Answers "was this spectrum even worth analyzing?" using only signals
    that come from the extraction itself -- the instrument's resolution,
    how much of the frame was saturated, how much of the requested
    spectrum actually landed on the image, and how far the signal stood
    out from noise. None of this depends on what the classifier or the
    feature tests concluded.
    """

    model_config = ConfigDict(populate_by_name=True)

    # How much the instrument blurred this spectrum, in Angstroms (see
    # `spectral_resolution`). A finer resolution means the classification
    # and feature tests can trust smaller differences between spectral
    # types; a coarser one means two nearby types are hard to tell apart
    # no matter how good the rest of the data is.
    resolution_element_angstrom: float = Field(alias="resolutionElementAngstrom")
    # `True` when the resolution above came from this spectrum's own
    # measured trail width, `False` when a fixed fallback was used instead
    # (a weaker basis for judging how sharp the data is).
    is_resolution_measured: bool = Field(alias="isResolutionMeasured")
    # The fraction of pixels at the star's zero-order position that were
    # saturated (pinned at the sensor's maximum), 0 to 1. `None` when not
    # measured. A high fraction means the brightest part of the trail may
    # be unreliable.
    zero_order_saturated_pixel_fraction: float | None = Field(
        default=None, alias="zeroOrderSaturatedPixelFraction"
    )
    # The fraction (0 to 1) of the requested spectrum that actually landed
    # on the image and inside the camera's sensitive range. Below 1.0,
    # part of the trail ran off the edge of the picture.
    valid_fraction: float | None = Field(default=None, alias="validFraction")
    # How strongly the spectrum stands out from its own noise (see
    # `estimate_spectrum_signal_to_noise`). `None` when it could not be
    # judged (for example too few usable samples).
    signal_to_noise: float | None = Field(default=None, alias="signalToNoise")


class OutputQualityAssessment(BaseModel):
    """How much to trust a spectrum's classification, given everything found.

    Combines the classifier's own match statistics (is the winning type a
    weak match, or nearly tied with the runner-up) with the catalog
    comparison above, into one overall verdict. Unlike
    `InputQualityAssessment`, this depends on what classifying the
    spectrum actually produced.
    """

    model_config = ConfigDict(populate_by_name=True)

    # The winning type's match score was weak (see
    # `is_classification_low_confidence`). `False` for an unclassified
    # star -- that is a separate "nothing to compare" case, not a shaky
    # match.
    is_low_confidence: bool = Field(alias="isLowConfidence")
    # The top two candidate types were too close to call apart (see
    # `is_classification_ambiguous`).
    is_ambiguous: bool = Field(alias="isAmbiguous")
    # Whether the classifier's own top two candidates are closer together
    # than the instrument's resolution can actually distinguish. `None`
    # until the Angstrom-per-subtype-step conversion this needs has been
    # calibrated (see `post_processing.assess_output_quality`) -- not
    # `False`, since "not computed" and "computed and found precise
    # enough" are different claims.
    is_subtype_finer_than_resolution: bool | None = Field(default=None, alias="isSubtypeFinerThanResolution")
    # `CatalogComparison.spectral_type_agrees`, repeated here so a reader
    # can see the overall trust verdict without also fetching the
    # comparison object. `None` when there was nothing to compare.
    catalog_agrees: bool | None = Field(default=None, alias="catalogAgrees")
    # The overall verdict: `False` when any of the above says this result
    # should be treated with caution, `True` when none of them do.
    is_trustworthy: bool = Field(alias="isTrustworthy")
