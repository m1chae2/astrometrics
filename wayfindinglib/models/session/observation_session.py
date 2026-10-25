"""Purpose: Observation Session Domain Models.

Description: One observing night's record: the equipment and site it
was planned against, an ordered queue of scheduled observation packages
and its placement diagnostics (written by Observation Planning), and
accumulated telemetry, per-entry outcomes, divergence records, fault
records, and meridian-flip outcomes (written by Observation Execution,
when present).

Who writes which field is fixed: within one
session, the queue and its placement diagnostics are written only by
Planning; execution status, per-entry outcomes, divergence records,
fault records, and accumulated telemetry are written only by Execution.

`QueuedObservationPackage` freezes a self-contained snapshot of the
package it came from -- `exposure_requests`, `dither_config`,
`minimum_altitude_deg`, and `priority` are all frozen at placement time,
so editing or deleting the template afterward cannot change what a
session recorded (`Wayfinding_Library_Architecture.md` Section 3.3,
"Template and instance separation").
`QueueEntryStatus` has no `INFEASIBLE` value: unplaced packages never
enter the queue, so no entry could ever hold it -- infeasibility is
reported on `unplaced_package_diagnostics`, a separate channel.

The session is also the stored night plan (issue #95, register entry
WAY-50). It carries a checklist of fixes the user means to try, the
framing of the field (center, camera rotation and mosaic panels), and a
revision number. Every plan write adds 1 to the revision, and a write that
names an older revision is refused, so two windows cannot overwrite each
other. A session stored before these fields existed loads with an empty
checklist, no framing and revision 0.
"""

from datetime import UTC, date, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from astrometricslib import CaptureSetting, ConflictError
from wayfindinglib.models.planning.framing import Framing
from wayfindinglib.models.planning.observation_package import DitherConfig, ExposureRequest
from wayfindinglib.models.policy.recovery import FaultRecord
from wayfindinglib.models.session.divergence import DivergenceRecord
from wayfindinglib.models.session.meridian_flip import MeridianFlipOutcome
from wayfindinglib.models.session.telemetry import GuidingSample


class SessionStatus(StrEnum):
    """One observation session's overall lifecycle state.

    `SUSPENDED` means an unsafe verdict halted advancement without ending
    the night, since conditions may clear.
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

    A closed enumeration, not free text, so a client can branch on the reason
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

    observation_package_id: str = Field(alias="observationPackageId")
    reason_code: InfeasibilityReasonCode = Field(alias="reasonCode")
    detail: str = Field(default="", alias="detail")


class WeatherSample(BaseModel):
    """A single weather observation.

    No populator exists yet -- schema-ready, empty until an actual
    weather-station integration is built. Recorded for context in the
    session's telemetry, distinct from the safety monitor's environmental
    verdict (`SafetyAssessment`), which must not be best-effort.
    """

    model_config = ConfigDict(populate_by_name=True)
    time: float = Field(..., ge=0.0)
    ambient_temperature_c: float | None = Field(None, alias="ambientTemperatureC")
    humidity_percent: float | None = Field(None, alias="humidityPercent")
    dew_point_c: float | None = Field(None, alias="dewPointC")


class QueuedObservationPackage(BaseModel):
    """One placed, self-contained package instance within a session's queue."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="id")
    observation_package_id: str = Field(alias="observationPackageId")
    target_id: str = Field(alias="targetId")
    exposure_requests: list[ExposureRequest] = Field(default_factory=list, alias="exposureRequests")
    dither_config: DitherConfig | None = Field(default=None, alias="ditherConfig")
    minimum_altitude_deg: float | None = Field(default=None, alias="minimumAltitudeDeg")
    priority: int = Field(default=0, alias="priority")
    applied_priority_boost: int = Field(default=0, ge=0, alias="appliedPriorityBoost")
    start_time_mode: StartTimeMode = Field(alias="startTimeMode")
    requested_start_time: datetime | None = Field(default=None, alias="requestedStartTime")
    computed_start_time: datetime | None = Field(default=None, alias="computedStartTime")
    computed_end_time: datetime | None = Field(default=None, alias="computedEndTime")
    actual_start_time: datetime | None = Field(default=None, alias="actualStartTime")
    actual_end_time: datetime | None = Field(default=None, alias="actualEndTime")
    frames_captured: int = Field(default=0, ge=0, alias="framesCaptured")
    status: QueueEntryStatus = Field(default=QueueEntryStatus.PENDING, alias="status")
    status_detail: str = Field(default="", alias="statusDetail")


class ChecklistItem(BaseModel):
    """One fix the user means to try, kept with the night plan.

    The rule name and capture setting say what the fix is about. Ticking
    the item records when the user did it. Whether a ticked item comes back
    is never stored: the library works that out from the measurements taken
    since the tick.
    """

    model_config = ConfigDict(populate_by_name=True)

    item_id: str = Field(default_factory=lambda: str(uuid4()), alias="itemId")
    rule: str = Field(alias="rule", description="The name of the quality rule that raised the item.")
    capture_setting: CaptureSetting | None = Field(
        default=None, alias="captureSetting", description="The capture setting the item is about."
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="createdAt")
    ticked_at: datetime | None = Field(
        default=None, alias="tickedAt", description="When the user last ticked the item. None: not ticked."
    )
    tick_history: list[datetime] = Field(
        default_factory=list,
        alias="tickHistory",
        description="Every time the item was ticked, oldest first. The last one equals `ticked_at`.",
    )
    note: str = Field(default="", alias="note")


class ObservationSession(BaseModel):
    """One observing night's full record: plan, placement, and outcome."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="id")
    night_date: date = Field(alias="nightDate")
    status: SessionStatus = Field(default=SessionStatus.PLANNED, alias="status")
    status_detail: str = Field(default="", alias="statusDetail")
    site_profile_id: str = Field(alias="siteProfileId")
    telescope_id: str = Field(alias="telescopeId")
    camera_id: str = Field(alias="cameraId")
    queue: list[QueuedObservationPackage] = Field(default_factory=list, alias="queue")
    unplaced_package_diagnostics: list[InfeasibilityReason] = Field(
        default_factory=list, alias="unplacedPackageDiagnostics"
    )
    divergence_records: list[DivergenceRecord] = Field(default_factory=list, alias="divergenceRecords")
    fault_records: list[FaultRecord] = Field(default_factory=list, alias="faultRecords")
    meridian_flips: list[MeridianFlipOutcome] = Field(default_factory=list, alias="meridianFlips")
    target_session_ids: list[str] = Field(default_factory=list, alias="targetSessionIds")
    guiding_samples: list[GuidingSample] = Field(default_factory=list, alias="guidingSamples")
    weather_samples: list[WeatherSample] = Field(default_factory=list, alias="weatherSamples")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="createdAt")
    closed_at: datetime | None = Field(default=None, alias="closedAt")
    checklist: list[ChecklistItem] = Field(default_factory=list, alias="checklist")
    framing: Framing | None = Field(default=None, alias="framing")
    revision: int = Field(
        default=0, ge=0, alias="revision", description="Counts the plan writes. Starts at 0."
    )


class PlanRevisionConflictError(ConflictError):
    """A plan write named a revision that is no longer the stored one.

    `details["current_revision"]` holds the revision now stored, so the
    caller can read the plan again and redo its edit.
    """


class PlanChangeItem(BaseModel):
    """One thing a plan write changed."""

    model_config = ConfigDict(populate_by_name=True)

    kind: str = Field(alias="kind", description="What changed, such as queue_added or checklist_ticked.")
    detail: str = Field(default="", alias="detail")
    entry_id: str | None = Field(default=None, alias="entryId")


class PlanChange(BaseModel):
    """What a plan write returns: the new revision and what changed.

    The backend sends the plan-changed event from this record.
    """

    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(alias="sessionId")
    previous_revision: int = Field(ge=0, alias="previousRevision")
    revision: int = Field(ge=0, alias="revision", description="The revision now stored.")
    changes: list[PlanChangeItem] = Field(default_factory=list, alias="changes")
    setting_notes: list[str] = Field(
        default_factory=list,
        alias="settingNotes",
        description="Notes on accepted capture settings, such as 'setting not measured'.",
    )
    session: ObservationSession = Field(alias="session", description="The session as now stored.")


class QueueRequest(BaseModel):
    """A request to put one recorded package into a session's queue.

    Used by `ObservationPlanning.edit_queue` to add entries by hand and by
    `ObservationPlanning.create_plan(kind="scheduled_session")` to place
    packages automatically. The package is named by its id, so the request
    is plain data.
    """

    model_config = ConfigDict(populate_by_name=True)

    package_id: str = Field(alias="packageId", description="The recorded observation package to queue.")
    start_time_mode: StartTimeMode = Field(
        default=StartTimeMode.SOONEST, alias="startTimeMode", description="When the entry may start."
    )
    requested_start_time: datetime | None = Field(
        default=None, alias="requestedStartTime", description="The fixed start, for a fixed-time entry."
    )
    computed_start_time: datetime | None = Field(
        default=None,
        alias="computedStartTime",
        description="A start worked out by hand. Manual entries only.",
    )
    computed_end_time: datetime | None = Field(
        default=None, alias="computedEndTime", description="An end worked out by hand. Manual entries only."
    )
    exposure_requests: list[ExposureRequest] | None = Field(
        default=None,
        alias="exposureRequests",
        description="Exposures for this entry instead of the package's own. None: use the package's.",
    )


class ObservationSessionSummary(BaseModel):
    """One line about a recorded observation session, for a list."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="id", description="The session id.")
    status: SessionStatus = Field(alias="status", description="Where the session is in its life.")
    night_date: date = Field(alias="nightDate", description="The local date the observing night began.")
    entry_count: int = Field(alias="entryCount", description="How many entries its queue holds.")
