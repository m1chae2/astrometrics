"""Purpose: The ranking of candidate targets for tonight.

Description: `get_advisory(kind="tonight", at=...)` ranks the targets the user
could image at a chosen time by how much one hour of real clock time would
help each one (`QUALITY_METRICS_PLAN.md` Section 4.2, use cases UC6 and UC12).
The ranking is recomputed on each request and never stored.

A target with no data yet is marked "new" and is not ranked, because it has no
noise history to rank from. A target whose noise has stopped falling is ranked
at zero, with the reason. Photometry and spectroscopy targets are ranked in
lists of their own, because their gains are not comparable with imaging.

The sky the ranking assumes (the Moon and the target's altitude) comes from a
model fitted to the user's own frames. The ranking says so, and gives the
model's confidence (`TonightSkyModel`).
"""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from astrometricslib import CaptureSetting, MeasurementReference
from wayfindinglib.models.quality.statements import Confidence


class RankedUse(StrEnum):
    """What a target is observed for. Each use has its own ranked list."""

    IMAGING = "imaging"
    PHOTOMETRY = "photometry"
    SPECTROSCOPY = "spectroscopy"


class CandidateStatus(StrEnum):
    """What the ranking could do with a target.

    `RANKED` has a gain and a rank. `STOPPED` is ranked at zero because its
    noise has stopped falling. `NEW` has no data yet and is not ranked.
    `NO_FORECAST` has data, but something needed for the forecast is missing,
    and the reason says what.
    """

    RANKED = "ranked"
    STOPPED = "stopped"
    NEW = "new"
    NO_FORECAST = "no_forecast"


class SkySettingSummary(BaseModel):
    """How well the sky model is known for one equipment setting.

    Parameters
    ----------
    setting_key : `str`
        The equipment the model was fitted for (camera, telescope, filter,
        gain setting, offset and binning).
    nights_used : `int`
        Nights of the user's own frames behind the fit.
    frames_used : `int`
        Frames behind the fit.
    held_out_relative_error : `float`
        The error to expect on a new night, as a fraction of the sky rate.
    moon_fitted : `bool`
        Whether enough moonlit nights existed to fit the Moon.
    """

    model_config = ConfigDict(populate_by_name=True)

    setting_key: str = Field(alias="settingKey")
    nights_used: int = Field(alias="nightsUsed")
    frames_used: int = Field(alias="framesUsed")
    held_out_relative_error: float = Field(alias="heldOutRelativeError")
    moon_fitted: bool = Field(alias="moonFitted")


class TonightSkyModel(BaseModel):
    """The sky the ranking assumed, labeled as a model.

    Parameters
    ----------
    at : `datetime`
        The time the sky is for.
    moon_altitude_deg : `float` or `None`
        The Moon's altitude at the site, in degrees (negative is below the
        horizon). ``None`` when it could not be worked out.
    moon_illumination_fraction : `float` or `None`
        The lit fraction of the Moon's disc, from 0 to 1.
    settings : `list` [`SkySettingSummary`]
        The models that were fitted, one for each equipment setting.
    unavailable : `list` [`str`]
        One sentence for each equipment setting that has no model, with the
        reason. A target of such a setting is ranked from its own frames' sky
        and says so.
    model_derived : `bool`
        Always true: the values come from a model, not a measurement.
    extinction_note : `str`
        Says that atmospheric extinction is not modeled separately.
    confidence : `Confidence`
        Always ``design_estimate`` (shown as tentative).
    register_entry : `str`
        The validation register entry behind the model.
    """

    model_config = ConfigDict(populate_by_name=True)

    at: datetime
    moon_altitude_deg: float | None = Field(default=None, alias="moonAltitudeDeg")
    moon_illumination_fraction: float | None = Field(default=None, alias="moonIlluminationFraction")
    settings: list[SkySettingSummary] = Field(default_factory=list)
    unavailable: list[str] = Field(default_factory=list)
    model_derived: bool = Field(default=True, alias="modelDerived")
    extinction_note: str = Field(
        default=(
            "Extinction is not modeled separately. It is folded into the airmass term of the sky "
            "model, and no photometric night has checked it."
        ),
        alias="extinctionNote",
    )
    confidence: Confidence = Confidence.DESIGN_ESTIMATE
    register_entry: str = Field(default="WAY-71", alias="registerEntry")


class RankedCandidate(BaseModel):
    """One target in a ranked list.

    Parameters
    ----------
    target_id : `str`
        The target.
    use : `RankedUse`
        The list it belongs to.
    status : `CandidateStatus`
        Ranked, stopped, new or no forecast.
    rank : `int` or `None`
        Its place in the list, starting at 1. ``None`` for a new target or one
        with no forecast, because those are not ranked.
    gain_per_hour_fraction : `float` or `None`
        How much one clock hour of exposures cuts the stack's noise, as a
        fraction (0.18 is a fall of 18%). 0 for a stopped target. ``None``
        when it could not be found.
    altitude_deg : `float` or `None`
        The target's altitude at the planning time, in degrees.
    capture_setting : `CaptureSetting` or `None`
        The setting the forecast assumes (the one most used so far).
    frames_in_hour : `int` or `None`
        How many frames of that setting fit in one clock hour.
    exposure_seconds : `float` or `None`
        The exposure of one of those frames, in seconds.
    overhead_fraction : `float` or `None`
        Downloads, dithers and autofocus over exposure time (0.2 means the
        hour holds 20% less exposure than the clock says).
    effective_hours_so_far : `float` or `None`
        Exposure time of the frames already taken, in hours.
    sky_rate_e_per_s_per_px : `float` or `None`
        The sky rate the forecast assumed for tonight.
    sky_source : `str`
        ``"model"`` when the rate came from the sky model, ``"history"`` when
        it was the target's own past frames, or ``"none"``.
    thin_dataset : `bool`
        True when the target has little data, so a large gain per hour is
        partly just a short history.
    reason : `str`
        Why a target is stopped, new or without a forecast, or a remark on a
        ranked one. Empty when there is nothing to add.
    confidence : `Confidence`
        Always ``design_estimate`` today.
    register_entry : `str`
        The validation register entry behind the ranking.
    evidence : `list` [`MeasurementReference`]
        The stored rows the forecast rests on.
    """

    model_config = ConfigDict(populate_by_name=True)

    target_id: str = Field(alias="targetId")
    use: RankedUse
    status: CandidateStatus
    rank: int | None = Field(default=None, ge=1)
    gain_per_hour_fraction: float | None = Field(default=None, ge=0.0, alias="gainPerHourFraction")
    altitude_deg: float | None = Field(default=None, alias="altitudeDeg")
    capture_setting: CaptureSetting | None = Field(default=None, alias="captureSetting")
    frames_in_hour: int | None = Field(default=None, ge=0, alias="framesInHour")
    exposure_seconds: float | None = Field(default=None, gt=0.0, alias="exposureSeconds")
    overhead_fraction: float | None = Field(default=None, ge=0.0, alias="overheadFraction")
    effective_hours_so_far: float | None = Field(default=None, ge=0.0, alias="effectiveHoursSoFar")
    sky_rate_e_per_s_per_px: float | None = Field(default=None, ge=0.0, alias="skyRateEPerSPerPx")
    sky_source: str = Field(default="none", alias="skySource")
    thin_dataset: bool = Field(default=False, alias="thinDataset")
    reason: str = ""
    confidence: Confidence = Confidence.DESIGN_ESTIMATE
    register_entry: str = Field(default="WAY-72", alias="registerEntry")
    evidence: list[MeasurementReference] = Field(default_factory=list)


class RankedList(BaseModel):
    """The ranked candidates of one use.

    Parameters
    ----------
    use : `RankedUse`
        The use.
    gain_description : `str`
        What the gain means for this list, in one sentence.
    candidates : `list` [`RankedCandidate`]
        Ranked targets first (best first), then stopped ones at zero, then new
        targets and those with no forecast, which are not ranked.
    """

    model_config = ConfigDict(populate_by_name=True)

    use: RankedUse
    gain_description: str = Field(alias="gainDescription")
    candidates: list[RankedCandidate] = Field(default_factory=list)


class TonightRanking(BaseModel):
    """What to image at a chosen time, ranked by the gain from one clock hour.

    Parameters
    ----------
    at : `datetime`
        The planning time the ranking is for.
    generated_at : `datetime`
        When the ranking was computed.
    lists : `list` [`RankedList`]
        One list for each use that has candidates.
    sky : `TonightSkyModel`
        The sky model the gains assume.
    notes : `list` [`str`]
        Remarks for the whole ranking, such as a thin dataset leading a list,
        targets that were not visible, or kinds of target that are never
        ranked.
    """

    model_config = ConfigDict(populate_by_name=True)

    at: datetime
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="generatedAt")
    lists: list[RankedList] = Field(default_factory=list)
    sky: TonightSkyModel
    notes: list[str] = Field(default_factory=list)
