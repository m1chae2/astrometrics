"""Purpose: Observation Session Domain Models.

Description: One observing night's record: the equipment and site it
was planned against, an ordered queue of scheduled observation packages
and its placement diagnostics (written by Observation Planning), and
accumulated telemetry, per-entry outcomes, divergence records, fault
records, and meridian-flip outcomes (written by Observation Execution,
when present) -- `Wayfinding_Library_Architecture.md` §2.2.2, §2.4.

Session field ownership is a Design Invariant (§2.2.3): within one
session, the queue and its placement diagnostics are written only by
Planning; execution status, per-entry outcomes, divergence records,
fault records, and accumulated telemetry are written only by Execution.

`QueuedObservationPackage` freezes a self-contained snapshot of the
package it came from -- `exposure_requests`, `dither_config`,
`minimum_altitude_deg`, and `priority` are all frozen at placement time,
so editing or deleting the template afterward cannot change what a
session recorded (the "Template/Instance Separation" invariant).
`QueueEntryStatus` has no `INFEASIBLE` value: unplaced packages never
enter the queue, so no entry could ever hold it -- infeasibility is
reported on `unplaced_package_diagnostics`, a separate channel.
"""

from datetime import UTC, date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from wayfindinglib.models.planning.observation_package import DitherConfig, ExposureRequest
from wayfindinglib.models.policy.recovery import FaultRecord
from wayfindinglib.models.session.divergence import DivergenceRecord
from wayfindinglib.models.session.meridian_flip import MeridianFlipOutcome
from wayfindinglib.models.session.telemetry import GuidingSample


class SessionStatus(StrEnum):
    """One observation session's overall lifecycle state.

    `SUSPENDED` did not exist before this revision: an unsafe verdict
    halts advancement without ending the night, since conditions may
    clear (`Wayfinding_Library_Architecture.md` §2.4.2).
    """

    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    SUSPENDED = "SUSPENDED"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"


class QueueEntryStatus(StrEnum):
    """One queue entry's execution status.

    No `INFEASIBLE` value -- unplaced packages never enter the queue.
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class StartTimeMode(StrEnum):
    """A package placement request's starting disposition."""

    SOONEST = "SOONEST"
    FIXED = "FIXED"


class InfeasibilityReasonCode(StrEnum):
    """A closed, named reason an observation package could not be placed.

    Per `Wayfinding_Library_Architecture.md` §2.3.2: a closed
    enumeration, not free text, so a client can branch on the reason
    and a test can assert one specific diagnosis rather than "some
    failure."
    """

    NEVER_CLEARS_ALTITUDE = "NEVER_CLEARS_ALTITUDE"
    BLOCKED_BY_AVOIDANCE_ZONE = "BLOCKED_BY_AVOIDANCE_ZONE"
    WINDOW_TOO_SHORT = "WINDOW_TOO_SHORT"
    NIGHT_FULLY_COMMITTED = "NIGHT_FULLY_COMMITTED"
    FIXED_TIME_CONFLICT = "FIXED_TIME_CONFLICT"


class InfeasibilityReason(BaseModel):
    """One specific, non-boolean reason a package could not be placed."""

    model_config = ConfigDict(populate_by_name=True)

    observation_package_id: str
    reason_code: InfeasibilityReasonCode
    detail: str = Field(default="")


class WeatherSample(BaseModel):
    """A single weather observation.

    No populator exists yet -- schema-ready, empty until an actual
    weather-station integration is built. Recorded for context in the
    session's telemetry, distinct from the safety monitor's environmental
    verdict (`SafetyAssessment`), which must not be best-effort
    (`Wayfinding_Library_Architecture.md` §2.4.7).
    """

    model_config = ConfigDict(populate_by_name=True)
    time: float = Field(..., ge=0.0)
    ambient_temperature_c: float | None = Field(None, alias="ambientTemperatureC")
    humidity_percent: float | None = Field(None, alias="humidityPercent")
    dew_point_c: float | None = Field(None, alias="dewPointC")


class QueuedObservationPackage(BaseModel):
    """One placed, self-contained package instance within a session's queue."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    observation_package_id: str
    target_id: str
    exposure_requests: list[ExposureRequest] = Field(default_factory=list)
    dither_config: DitherConfig | None = Field(default=None)
    minimum_altitude_deg: float | None = Field(default=None)
    priority: int = Field(default=0)
    applied_priority_boost: int = Field(default=0, ge=0)
    start_time_mode: StartTimeMode
    requested_start_time: datetime | None = Field(default=None)
    computed_start_time: datetime | None = Field(default=None)
    computed_end_time: datetime | None = Field(default=None)
    actual_start_time: datetime | None = Field(default=None)
    actual_end_time: datetime | None = Field(default=None)
    frames_captured: int = Field(default=0, ge=0)
    status: QueueEntryStatus = Field(default=QueueEntryStatus.PENDING)
    status_detail: str = Field(default="")


class ObservationSession(BaseModel):
    """One observing night's full record: plan, placement, and outcome."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    night_date: date
    status: SessionStatus = Field(default=SessionStatus.PLANNED)
    status_detail: str = Field(default="")
    site_profile_id: str
    telescope_id: str
    camera_id: str
    queue: list[QueuedObservationPackage] = Field(default_factory=list)
    unplaced_package_diagnostics: list[InfeasibilityReason] = Field(default_factory=list)
    divergence_records: list[DivergenceRecord] = Field(default_factory=list)
    fault_records: list[FaultRecord] = Field(default_factory=list)
    meridian_flips: list[MeridianFlipOutcome] = Field(default_factory=list)
    target_session_ids: list[str] = Field(default_factory=list)
    guiding_samples: list[GuidingSample] = Field(default_factory=list)
    weather_samples: list[WeatherSample] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    closed_at: datetime | None = Field(default=None)
