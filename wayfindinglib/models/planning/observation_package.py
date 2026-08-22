"""Purpose: Observation Package Domain Models.

Description: A reusable imaging request for one target, authored
independent of any specific night (`Wayfinding_Library_Architecture.md`
§2.3). `ExposureRequest.frame_type` covers light and calibration
exposures alike in one list rather than a separate request mechanism,
matching how package authoring is actually used today
(`Wayfinding_Library_Architecture.md` §2.3.2). `filter` is typed
`astrometricslib.utilities.enums.FilterType` -- a deliberate
cross-library type dependency in the direction this library already
depends, per Design Invariant 1's scope (`Wayfinding_Library_Architecture.md`
§2.1): it constrains dependencies within this library's layers, not
against the science library.
"""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from astrometricslib import FilterType


class FrameType(StrEnum):
    """The role of a requested exposure within an observation package."""

    LIGHT = "LIGHT"
    DARK = "DARK"
    FLAT = "FLAT"
    BIAS = "BIAS"


class DitherConfig(BaseModel):
    """A package-level dithering cadence, applied across a package's run."""

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = Field(default=False)
    every_n_frames: int = Field(default=3, gt=0)
    pixels: float = Field(default=3.0, gt=0.0)


class ExposureRequest(BaseModel):
    """One requested exposure -- science or calibration -- within a package."""

    model_config = ConfigDict(populate_by_name=True)

    frame_type: FrameType
    filter: FilterType = Field(default=FilterType.NONE)
    exposure_sec: float = Field(..., gt=0.0)
    count: int = Field(..., gt=0)
    delay_sec: float = Field(default=0.0, ge=0.0)

    def total_exposure_sec(self) -> float:
        """Total wall-clock time this request occupies, exposures plus pacing.

        Per `Wayfinding_Library_Architecture.md` §2.3.2: a package's
        total duration sums every requested exposure's
        `total_exposure_sec` including calibration entries.

        Returns
        -------
        total_sec : `float`
            The total wall-clock time this request occupies.
        """
        return self.count * self.exposure_sec + max(self.count - 1, 0) * self.delay_sec


class ObservationPackage(BaseModel):
    """A reusable imaging request for one target.

    `target_id` references an existing `astrometricslib.models.target.Target`
    by identifier only, never embedded -- resolved live at placement
    time, never cached (`Wayfinding_Library_Architecture.md` §2.3.2,
    the "Live Target Resolution" invariant).
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    target_id: str
    exposure_requests: list[ExposureRequest] = Field(default_factory=list)
    dither_config: DitherConfig | None = Field(default=None)
    minimum_altitude_deg: float | None = Field(default=None, ge=-90.0, le=90.0)
    priority: int = Field(default=0)
    quality_weighting_enabled: bool = Field(default=False)
    notes: str = Field(default="")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def total_duration_sec(self) -> float:
        """Sum of every requested exposure's `total_exposure_sec`.

        Returns
        -------
        total_sec : `float`
            The package's total duration, in seconds.
        """
        return sum(r.total_exposure_sec() for r in self.exposure_requests)
