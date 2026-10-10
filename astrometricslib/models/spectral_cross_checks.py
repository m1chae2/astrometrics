"""Data structures for two checks that sit beside a spectral type.

The template classifier compares the overall shape of a spectrum with
reference spectra. At low resolution that shape is mostly the continuum
slope, which mixes a star's temperature with the reddening from dust and with
small errors in the instrument response. Two records describe the checks that
reduce that dependence:

* `ReddeningRecord` says what colour excess (E(B-V)) was used to remove the
  dust's reddening before the classification, where that number came from,
  and how much the best type moved because of it.
* `LineIndexClassification` says what type the strengths of a few spectral
  lines point to. A line strength is measured against the continuum right
  next to the line, so it does not depend on the slope.

Both attach to `SpectroscopyResult` (see `stellar_source`). Neither replaces
the template classifier's decision.
"""

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["LineIndexClassification", "ReddeningRecord"]


class ReddeningRecord(BaseModel):
    """The reddening correction applied before a spectrum was classified.

    Dust between us and a star dims blue light more than red light and tilts
    the spectrum toward the red. The colour excess E(B-V) measures how much,
    in magnitudes. When a catalog gives E(B-V) for a star, the pipeline
    removes the tilt (see `interstellar_extinction`) and classifies both the
    observed and the dereddened spectrum. This record keeps both results.
    """

    model_config = ConfigDict(populate_by_name=True)

    # The colour excess E(B-V) used, in magnitudes. Zero means no dust.
    ebv: float = Field(alias="ebv")
    # Where the number came from, for example "Gaia DR3 GSP-Phot E(BP-RP)
    # divided by 1.339". Names the catalog and the conversion.
    ebv_source: str = Field(alias="ebvSource")
    # The Gaia DR3 source id the number was looked up with, as text (the id
    # is longer than a JavaScript number can hold exactly). Empty when the
    # source was not Gaia.
    gaia_source_id: str = Field(default="", alias="gaiaSourceId")
    # The best template type for the spectrum as observed, before any
    # dereddening. Empty when the observed spectrum could not be classified.
    observed_best_type: str = Field(default="", alias="observedBestType")
    # The best template type after dereddening. Empty when the dereddened
    # spectrum could not be classified.
    dereddened_best_type: str = Field(default="", alias="dereddenedBestType")
    # Dereddened position minus observed position on the O-to-M ladder, in
    # subtype steps (ten per letter class). Negative means dereddening moved
    # the type toward hotter. `None` when either type is missing or not on
    # the ladder.
    type_shift_steps: float | None = Field(default=None, alias="typeShiftSteps")


class LineIndexClassification(BaseModel):
    """A spectral type estimated from line strengths alone.

    Each index is the depth of one spectral line or band below the continuum
    next to it, as a fraction of that continuum (0.1 means 10% dimmer). The
    same indices are measured on every bundled reference spectrum after
    blurring it to the instrument's resolution. The best type is the
    reference whose indices are closest to the star's. It is a cross-check on
    the template classifier, not a replacement for it.
    """

    model_config = ConfigDict(populate_by_name=True)

    # The reference type whose indices are closest to this spectrum's.
    best_type: str = Field(alias="bestType")
    # How far the best reference is in index space: the root-mean-square of
    # the index differences, each divided by that index's spread across the
    # references. Unitless; 0 is an exact match.
    distance: float = Field(alias="distance")
    # The measured depths by index name, for the indices that could be
    # measured (see `spectral_line_indices.LINE_INDICES`).
    indices: dict[str, float] = Field(default_factory=dict, alias="indices")
    # The template-fit type this was compared with (the type the pipeline
    # reports). Empty when there was none.
    template_fit_type: str = Field(default="", alias="templateFitType")
    # Distance on the O-to-M ladder between `best_type` and
    # `template_fit_type`, in subtype steps (ten per letter class). `None`
    # when either type is missing or not on the ladder.
    steps_from_template_fit: float | None = Field(default=None, alias="stepsFromTemplateFit")
