"""Purpose: The shapes of efficiency and the loss account.

Description: Efficiency (written eta) is the effective exposure time of a
set of frames divided by the time a reference frame would need for the same
result, from 0 to 1. It is always stated for one science goal, in one noise
regime, against one named and versioned reference. Eta is the product of
factors, one for each cause of loss (seeing, sky brightness, read noise,
and so on). The loss account splits the lost time among those causes.

A cause the library cannot measure is listed as "not included" with the
reason; no value is invented for it. A frame better than the reference is
capped at a factor of 1, and the factor says so.

Two different "effective times" exist and must not be mixed:
`weight_effective_seconds` is the weighted time the stacker reports, and
`efficiency_effective_seconds` is eta times the captured time.
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EfficiencyReferenceKind(StrEnum):
    """Where a reference gets its reference values."""

    PHYSICAL = "physical"
    BASELINE = "baseline"


class ScienceGoal(StrEnum):
    """What the data is for. Efficiency depends on the goal."""

    FAINT_POINT_SOURCE = "faint_point_source"
    ASTROMETRIC_PRECISION = "astrometric_precision"
    EXTENDED_OBJECT = "extended_object"
    BRIGHT_STAR_PHOTOMETRY = "bright_star_photometry"
    SLITLESS_SPECTRAL_RESOLUTION = "slitless_spectral_resolution"
    PERIOD_DETECTION = "period_detection"


class NoiseRegime(StrEnum):
    """Which noise dominates a frame."""

    SKY_LIMITED = "sky_limited"
    READ_NOISE_LIMITED = "read_noise_limited"
    SOURCE_LIMITED = "source_limited"
    UNKNOWN = "unknown"


class EfficiencyReference(BaseModel):
    """A named, versioned reference that efficiency is measured against.

    Parameters
    ----------
    name : `str`
        The reference's name, such as ``"physical"``.
    version : `int`
        Bumped whenever the way the reference is built changes.
    kind : `EfficiencyReferenceKind`
        ``physical`` (set by the optics and the site) or ``baseline`` (the
        equipment's own good nights).
    factors : `dict` [`str`, `float`]
        The reference value of each factor that is included, by factor name.
    not_included : `dict` [`str`, `str`]
        Each factor left out, with the reason.
    register_entry : `str`
        The validation register entry or design-estimate row behind it.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str
    version: int = Field(ge=1)
    kind: EfficiencyReferenceKind
    factors: dict[str, float] = Field(default_factory=dict)
    not_included: dict[str, str] = Field(default_factory=dict, alias="notIncluded")
    register_entry: str = Field(min_length=1, alias="registerEntry")


class EfficiencyFactor(BaseModel):
    """One cause of lost efficiency.

    Parameters
    ----------
    cause : `str`
        The cause, such as ``"seeing"`` or ``"sky_brightness"``.
    value : `float` or `None`
        The factor, above 0 and at most 1. ``None`` when it cannot be found.
    included : `bool`
        Whether the factor is part of eta.
    not_included_reason : `str` or `None`
        Why it is not part of eta. Required when `included` is false.
    capped_reason : `str` or `None`
        Set when the frame was better than the reference and the factor was
        capped at 1.
    recoverable_with_time : `bool`
        Whether more exposure time repairs this loss.
    model_derived : `bool`
        Whether the factor comes from a model instead of a direct
        measurement.
    """

    model_config = ConfigDict(populate_by_name=True)

    cause: str
    value: float | None = Field(default=None, gt=0.0, le=1.0)
    included: bool
    not_included_reason: str | None = Field(default=None, alias="notIncludedReason")
    capped_reason: str | None = Field(default=None, alias="cappedReason")
    recoverable_with_time: bool = Field(alias="recoverableWithTime")
    model_derived: bool = Field(alias="modelDerived")

    @model_validator(mode="after")
    def _check_included_fits_value_and_reason(self) -> Self:
        """Check that included factors have values and others a reason.

        Returns
        -------
        factor : `EfficiencyFactor`
            This factor, unchanged.

        Raises
        ------
        ValueError
            If an included factor has no value, or a factor that is not
            included gives no reason.
        """
        if self.included and self.value is None:
            raise ValueError("An included efficiency factor must have a value.")
        if not self.included and not (self.not_included_reason or "").strip():
            raise ValueError("A factor that is not included must give not_included_reason.")
        return self


class EfficiencyResult(BaseModel):
    """Efficiency of a set of frames, for one goal and one reference.

    Parameters
    ----------
    goal : `ScienceGoal`
        The science goal the efficiency is stated for.
    regime : `NoiseRegime`
        The noise regime.
    reference : `EfficiencyReference`
        The reference it is measured against.
    eta : `float` or `None`
        Efficiency, above 0 and at most 1. ``None`` when it cannot be found.
    factors : `list` [`EfficiencyFactor`]
        One factor for each cause, included or not.
    captured_seconds : `float`
        Exposure time captured, in seconds.
    efficiency_effective_seconds : `float` or `None`
        Eta times the captured time, in seconds. ``None`` when eta is.
    """

    model_config = ConfigDict(populate_by_name=True)

    goal: ScienceGoal
    regime: NoiseRegime
    reference: EfficiencyReference
    eta: float | None = Field(default=None, gt=0.0, le=1.0)
    factors: list[EfficiencyFactor] = Field(default_factory=list)
    captured_seconds: float = Field(ge=0.0, alias="capturedSeconds")
    efficiency_effective_seconds: float | None = Field(
        default=None, ge=0.0, alias="efficiencyEffectiveSeconds"
    )

    @model_validator(mode="after")
    def _check_effective_time_follows_eta(self) -> Self:
        """Check that the effective time exists exactly when eta does.

        Returns
        -------
        result : `EfficiencyResult`
            This result, unchanged.

        Raises
        ------
        ValueError
            If only one of `eta` and `efficiency_effective_seconds` is set.
        """
        if (self.eta is None) != (self.efficiency_effective_seconds is None):
            raise ValueError("eta and efficiency_effective_seconds must both be set or both be None.")
        return self


class LossShare(BaseModel):
    """The time lost to one cause.

    Parameters
    ----------
    cause : `str`
        The cause, or for a frame set aside, the reason it was set aside.
    seconds : `float`
        Time lost, in seconds.
    recoverable_with_time : `bool`
        Whether more exposure time repairs this loss.
    model_derived : `bool`
        Whether the share comes from a model instead of a measurement.
    """

    model_config = ConfigDict(populate_by_name=True)

    cause: str
    seconds: float = Field(ge=0.0)
    recoverable_with_time: bool = Field(alias="recoverableWithTime")
    model_derived: bool = Field(alias="modelDerived")


class LossAccount(BaseModel):
    """Where the time of a night or a target went.

    Two parts are kept apart and never added into one number without saying
    so: the frames set aside, and the efficiency deficit of the frames kept.

    Parameters
    ----------
    goal : `ScienceGoal`
        The science goal the account is stated for.
    reference_name : `str`
        The name of the reference efficiency is measured against.
    set_aside_seconds : `float`
        Total exposure time of the frames set aside, in seconds.
    set_aside : `list` [`LossShare`]
        The same time split by the reason frames were set aside.
    kept_frame_deficit : `list` [`LossShare`]
        Captured minus effective time of the kept frames, split by cause.
    cause_unknown_seconds : `float` or `None`
        The part of the deficit that no listed cause explains. ``None``
        means "not measured": it exists only where efficiency is measured
        independently of the causes.
    cause_unknown_reason : `str`
        Why the part is unknown, or why it was not measured.
    transparency_included : `bool`
        Whether cloud and haze are part of the account.
    transparency_reason : `str`
        Why transparency is or is not included.
    """

    model_config = ConfigDict(populate_by_name=True)

    goal: ScienceGoal
    reference_name: str = Field(alias="referenceName")
    set_aside_seconds: float = Field(ge=0.0, alias="setAsideSeconds")
    set_aside: list[LossShare] = Field(default_factory=list, alias="setAside")
    kept_frame_deficit: list[LossShare] = Field(default_factory=list, alias="keptFrameDeficit")
    cause_unknown_seconds: float | None = Field(default=None, ge=0.0, alias="causeUnknownSeconds")
    cause_unknown_reason: str = Field(min_length=1, alias="causeUnknownReason")
    transparency_included: bool = Field(alias="transparencyIncluded")
    transparency_reason: str = Field(min_length=1, alias="transparencyReason")
