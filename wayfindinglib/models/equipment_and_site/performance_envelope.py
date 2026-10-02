"""Purpose: Performance Envelope Models.

Description: The limits that separate normal from abnormal for the
equipment in use now: how large a guiding error is acceptable, how low a
guide-star signal is suspicious, how fast a mount speed can be before it
must be a bad calibration, and so on.

None of these numbers is stored. They are worked out from the equipment
each time they are asked for, so when the equipment changes, every limit
changes with it and nothing stale is left behind. Each limit records how it
was obtained (`ThresholdTier`), the numbers it was worked out from, and, if
it could not be worked out, why not. A limit with not enough data behind it
says so (`ThresholdStatus.INSUFFICIENT_DATA`) and carries no value; it is
never replaced by a guess.

`Wayfinding_Library_Architecture.md` §2.2 treats equipment as configured
Foundation state. The envelope is computed Foundation state: derived on
demand from that configuration and from measurements, never persisted.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ThresholdTier(StrEnum):
    """How a limit was obtained, from most to least direct.

    `GEOMETRY` limits follow from the equipment's dimensions or from
    physics alone. `SENSOR_PROFILE` limits come from the camera's stored
    profile. `PHYSICAL_BUDGET` limits are the amount of an error the
    equipment's own measured image quality can absorb. `OWN_BASELINE` limits
    come from how this same equipment has behaved in the past.
    """

    GEOMETRY = "geometry"
    SENSOR_PROFILE = "sensor_profile"
    PHYSICAL_BUDGET = "physical_budget"
    OWN_BASELINE = "own_baseline"


class ThresholdStatus(StrEnum):
    """Whether a limit could be worked out."""

    DERIVED = "derived"
    INSUFFICIENT_DATA = "insufficient_data"


class PerformanceThreshold(BaseModel):
    """One limit, with the record of how it was obtained.

    Attributes
    ----------
    name : `str`
        The limit's name.
    value : `float` or `None`
        The limit. `None` when `status` is `INSUFFICIENT_DATA`.
    unit : `str`
        Unit of `value`, for example ``"arcsec"``.
    tier : `ThresholdTier`
        How the limit was obtained.
    status : `ThresholdStatus`
        Whether it could be worked out.
    derivation : `str`
        Plain-language statement of how the value follows from the inputs,
        or, for a limit with insufficient data, what is missing.
    inputs : `dict` [`str`, `float` or `str` or `None`]
        The numbers the value was worked out from, so anyone can redo it.
    sample_count : `int` or `None`
        How many measurements a measured input rests on, if any.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str
    value: float | None = None
    unit: str
    tier: ThresholdTier
    status: ThresholdStatus
    derivation: str
    inputs: dict[str, float | str | None] = Field(default_factory=dict)
    sample_count: int | None = Field(default=None, alias="sampleCount")


class PerformanceEnvelope(BaseModel):
    """Every limit for the equipment in use now.

    Attributes
    ----------
    equipment_fingerprint : `str`
        Identifies the equipment the limits were worked out for (see
        `build_equipment_fingerprint`).
    blur_tolerance_fraction : `float`
        The one policy choice behind the physical-budget limits: the most
        that guiding error and trailing may widen a star image, as a
        fraction of its width.
    thresholds : `dict` [`str`, `PerformanceThreshold`]
        Every limit, by name.
    """

    model_config = ConfigDict(populate_by_name=True)

    equipment_fingerprint: str = Field(alias="equipmentFingerprint")
    blur_tolerance_fraction: float = Field(alias="blurToleranceFraction")
    thresholds: dict[str, PerformanceThreshold] = Field(default_factory=dict)

    def value(self, name: str) -> float | None:
        """Return one limit's value, or `None` if it could not be derived.

        Parameters
        ----------
        name : `str`
            The limit's name.

        Returns
        -------
        value : `float` or `None`
            The value, or `None` if the limit is missing or has
            insufficient data.
        """
        threshold = self.thresholds.get(name)
        return threshold.value if threshold is not None else None
