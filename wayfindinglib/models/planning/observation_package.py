"""Purpose: Observation Package Domain Models.

Description: A reusable imaging request for one target, authored
independent of any specific night (`Wayfinding_Library_Architecture.md`
Section 4.2).
`ExposureRequest.frame_type` covers light and calibration
exposures alike in one list rather than a separate request mechanism,
matching how package authoring is actually used today
(`Wayfinding_Library_Architecture.md` Section 4.2). `filter` is typed
`astrometricslib.foundation.enums.FilterType` -- a deliberate
cross-library type dependency in the direction this library already
depends, per the scope of rule 1 in
`Wayfinding_Library_Architecture.md` Section 2.1: it constrains
dependencies within this library's layers, not
against the science library.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from astrometricslib import FilterType
from wayfindinglib.models.planning.framing import Framing


class FrameType(StrEnum):
    """The role of a requested exposure within an observation package."""

    LIGHT = "LIGHT"
    DARK = "DARK"
    FLAT = "FLAT"
    BIAS = "BIAS"


class DitherConfig(BaseModel):
    """A package-level dithering cadence, applied across a package's run."""

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = Field(default=False, alias="enabled")
    every_n_frames: int = Field(default=3, gt=0, alias="everyNFrames")
    pixels: float = Field(default=3.0, gt=0.0, alias="pixels")


class ExposureRequest(BaseModel):
    """One requested exposure -- science or calibration -- within a package."""

    model_config = ConfigDict(populate_by_name=True)

    frame_type: FrameType = Field(alias="frameType")
    filter: FilterType = Field(default=FilterType.NONE, alias="filter")
    exposure_sec: float = Field(..., gt=0.0, alias="exposureSec")
    count: int = Field(..., gt=0, alias="count")
    delay_sec: float = Field(default=0.0, ge=0.0, alias="delaySec")
    gain_setting: int | None = Field(
        default=None,
        ge=0,
        alias="gainSetting",
        description="The camera's gain setting (a unitless index, not electrons per ADU). None: not set.",
    )
    offset: int | None = Field(
        default=None,
        ge=0,
        alias="offset",
        description="The constant the camera adds to every pixel. None: not set.",
    )
    binning: int | None = Field(
        default=None,
        ge=1,
        alias="binning",
        description="The binning factor (1 means no binning). None: not set.",
    )

    def total_exposure_sec(self) -> float:
        """Total wall-clock time this request occupies, exposures plus pacing.

        Per `Wayfinding_Library_Architecture.md` Section 4.2: a package's
        total duration sums every requested exposure's
        `total_exposure_sec` including calibration entries.

        Returns
        -------
        total_sec : `float`
            The total wall-clock time this request occupies.
        """
        return self.count * self.exposure_sec + max(self.count - 1, 0) * self.delay_sec


class PackageStatus(StrEnum):
    """Where an observation package stands in the package library."""

    PLANNED = "PLANNED"
    SUGGESTED = "SUGGESTED"
    COMPLETE = "COMPLETE"


class PackageSuggestion(BaseModel):
    """Why the library suggested a package, kept with the package.

    Fields only. A later step fills them in when it makes or updates a
    suggested package.
    """

    model_config = ConfigDict(populate_by_name=True)

    source_kind: Literal["rule", "ranking"] = Field(
        alias="sourceKind",
        description='"rule": a quality rule asked for more frames. "ranking": tonight\'s ranking picked it.',
    )
    rule_name: str = Field(
        default="", alias="ruleName", description="The rule that made the suggestion, if any."
    )
    source_key: str = Field(
        default="",
        alias="sourceKey",
        description="Names what the suggestion came from, so a repeat is not added twice.",
    )
    reason: str = Field(default="", alias="reason", description="The reason, in plain words.")
    confidence: str = Field(
        default="",
        alias="confidence",
        description="How sure the library is, for example measured or design_estimate.",
    )
    evidence_fingerprint: str = Field(
        default="",
        alias="evidenceFingerprint",
        description="A short code for the evidence. A dismissal lasts until it changes.",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="createdAt")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="updatedAt")


class ObservationPackage(BaseModel):
    """A reusable imaging request for one target.

    `target_id` references an existing `astrometricslib.models.target.Target`
    by identifier only, never embedded -- resolved live at placement
    time, never cached (`Wayfinding_Library_Architecture.md` Section 3.3,
    "Live target resolution").
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
    status: PackageStatus = Field(
        default=PackageStatus.PLANNED, description="Planned, suggested by the library, or complete."
    )
    revision: int = Field(
        default=0, ge=0, description="Counts the package writes. Starts at 0. A stale edit is refused."
    )
    suggestion: PackageSuggestion | None = Field(
        default=None, description="Why the library suggested this package. None for one made by hand."
    )
    framing: Framing | None = Field(
        default=None, description="Where the camera points for this package, with any mosaic panels."
    )

    def total_duration_sec(self) -> float:
        """Sum of every requested exposure's `total_exposure_sec`.

        Returns
        -------
        total_sec : `float`
            The package's total duration, in seconds.
        """
        return sum(r.total_exposure_sec() for r in self.exposure_requests)
