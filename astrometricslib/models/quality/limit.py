"""The limit a quality measurement is compared with, and where it came from.

A number such as "star width above 3 arcsec" means little unless a reader can
see how the limit was found. `QualityLimit` carries the value, its unit, how
trustworthy its source is (the `LimitTier`), the derivation in words, and,
for a limit checked against real data, the validation register entry that
holds the evidence.

This model merges the limit fields of `GateResult` with the threshold model
that `wayfindinglib` uses. `gate_result.py` stays in place until the later
phase that migrates its users.
"""

import math
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from astrometricslib.foundation.parameters import ResolvedValue
from astrometricslib.models.tuned_parameter import ParameterSource, TunedParameter


class LimitTier(StrEnum):
    """How a limit was obtained, from most fixed to most data-driven."""

    GEOMETRY = "geometry"
    SENSOR_PROFILE = "sensor_profile"
    PHYSICAL_BUDGET = "physical_budget"
    DESIGN_ESTIMATE = "design_estimate"
    OWN_BASELINE = "own_baseline"
    VALIDATED_DATASET = "validated_dataset"
    PROCESSING_SETTING = "processing_setting"
    TUNED_PARAMETER = "tuned_parameter"


def limit_tier_for(source: ParameterSource, status: str | None) -> LimitTier:
    """Choose the limit tier that matches where a parameter value came from.

    Parameters
    ----------
    source : `ParameterSource`
        Where the value comes from.
    status : `str` or `None`
        The validation status of the register entry, such as ``"Measured"``,
        ``"Partly measured"`` or ``"Design estimate"``. Only a hardcoded
        value uses it.

    Returns
    -------
    tier : `LimitTier`
        The tier. A hardcoded value is ``validated_dataset`` only when its
        status is ``Measured``; any other status, or none, gives
        ``design_estimate``.
    """
    if source is ParameterSource.EQUIPMENT_PROFILE:
        return LimitTier.SENSOR_PROFILE
    if source is ParameterSource.TUNED_PARAMETER:
        return LimitTier.TUNED_PARAMETER
    if source is ParameterSource.PROCESSING_SETTING:
        return LimitTier.PROCESSING_SETTING
    if source is ParameterSource.DETERMINED_EACH_RUN:
        return LimitTier.PHYSICAL_BUDGET
    if status == "Measured":
        return LimitTier.VALIDATED_DATASET
    return LimitTier.DESIGN_ESTIMATE


class QualityLimit(BaseModel):
    """One limit that a measurement can be compared with.

    Parameters
    ----------
    value : `float` or `None`
        The limit itself. ``None`` when the limit could not be computed.
    unit : `str`
        The unit of `value`, such as ``"arcsec"``.
    tier : `LimitTier`
        How the limit was obtained.
    derivation : `str`
        A plain-language statement of how the value was found.
    sample_count : `int` or `None`
        The number of samples behind the limit, when it comes from data.
    register_entry : `str` or `None`
        The validation register entry that holds the evidence, such as
        ``"STK-07"``. Required when `tier` is ``validated_dataset``.
    """

    model_config = ConfigDict(populate_by_name=True)

    value: float | None
    unit: str
    tier: LimitTier
    derivation: str
    sample_count: int | None = Field(default=None, alias="sampleCount")
    register_entry: str | None = Field(default=None, alias="registerEntry")

    @classmethod
    def from_parameter(
        cls,
        parameter: TunedParameter,
        resolved: ResolvedValue,
        *,
        status: str | None = None,
        derivation: str | None = None,
    ) -> Self:
        """Build a limit from a declared parameter and its resolved value.

        Parameters
        ----------
        parameter : `TunedParameter`
            The declaration, which gives the unit, the register entry and
            the default derivation text.
        resolved : `ResolvedValue`
            The value the resolver chose. When its origin is ``default``
            (a value was missing), the tier is the one for ``hardcoded``.
        status : `str`, optional
            The validation status of the register entry, used for a
            hardcoded value.
        derivation : `str`, optional
            How the value was found. Defaults to the parameter description.

        Returns
        -------
        limit : `QualityLimit`
            The limit.

        Raises
        ------
        ValueError
            If the value is a tuple, or the tier is ``validated_dataset``
            and the parameter has no register entry.
        """
        if isinstance(resolved.value, tuple):
            raise ValueError(f"{parameter.name}: a limit needs a single number, not a tuple")
        origin = ParameterSource.HARDCODED if resolved.origin == "default" else resolved.origin
        return cls(
            value=float(resolved.value),
            unit=parameter.unit,
            tier=limit_tier_for(origin, status),
            derivation=derivation if derivation is not None else parameter.description,
            registerEntry=parameter.register_entry,
        )

    @field_validator("value")
    @classmethod
    def _require_finite_value(cls, limit_value: float | None) -> float | None:
        """Refuse NaN and infinity as a limit.

        Parameters
        ----------
        limit_value : `float` or `None`
            The limit being set.

        Returns
        -------
        limit_value : `float` or `None`
            The same value.

        Raises
        ------
        ValueError
            If the limit is NaN or infinite.
        """
        if limit_value is not None and not math.isfinite(limit_value):
            raise ValueError("A limit value must be a finite number, not NaN or infinity.")
        return limit_value

    @model_validator(mode="after")
    def _require_register_entry_for_validated_dataset(self) -> QualityLimit:
        """Require a register entry when the limit was validated on a data set.

        Returns
        -------
        limit : `QualityLimit`
            This limit, unchanged.

        Raises
        ------
        ValueError
            If the tier is ``validated_dataset`` and has no register entry.
        """
        if self.tier == LimitTier.VALIDATED_DATASET and not self.register_entry:
            raise ValueError("A validated_dataset limit must name its register_entry.")
        return self
