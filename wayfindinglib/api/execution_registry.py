"""Purpose: Observation Execution High-Level Interface.

Description: `ObservationExecution` is the single entry point external
callers should use for queue advancement, meridian-flip sequencing,
fault recovery, divergence recording, telemetry recording, and
post-session reconciliation (`Wayfinding_Library_Architecture.md`
§2.4.1). Callers should never import `tasks.execution_tasks` directly.

Per Design Invariant 3 (`Wayfinding_Library_Architecture.md` §2.1.2),
Execution is the one function permitted to depend on the other two --
this high-level interface is where that dependency actually appears, since
`advance_session`'s `SessionRunnerDependencies` bundle is where
computed actions get issued through `ObservatoryControl`. The bundle
itself is built by the caller (mirroring `ObservatoryControl
.execute_safe_state`'s `SafeStateSteps` parameter): this high-level interface
delegates to the state machine rather than constructing the
hardware/astrometrics wiring on its own, since that wiring is specific
to what a given queue entry actually requires.
"""

from datetime import datetime
from typing import Any

from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.policy.device_state import DeviceSummaryState
from wayfindinglib.models.policy.recovery import FaultRecord, RecoveryPolicy
from wayfindinglib.models.session.divergence import DivergenceRecord
from wayfindinglib.models.session.guide_star_loss import GuideStarLossEvent
from wayfindinglib.models.session.meridian_flip import MeridianFlipOutcome
from wayfindinglib.models.session.observation_session import ObservationSession
from wayfindinglib.tasks.execution_tasks.divergence_recording import record_divergence
from wayfindinglib.tasks.execution_tasks.fault_recovery import recover_fault
from wayfindinglib.tasks.execution_tasks.guide_star_loss_recovery import attempt_guide_star_recovery
from wayfindinglib.tasks.execution_tasks.meridian_flip import MeridianFlipSteps, execute_meridian_flip
from wayfindinglib.tasks.execution_tasks.post_session_reconciliation import reconcile_session
from wayfindinglib.tasks.execution_tasks.session_recorder import ObservationSessionRecorder
from wayfindinglib.tasks.execution_tasks.session_runner import (
    SessionRunnerDependencies,
    abort_session,
    advance_session,
)

# Declares this module's own public surface. Without it, sphinx-automodapi
# documents every imported name too, which is what produced the
# "stub file not found" warnings for re-exports and typing helpers.
__all__ = [
    "ObservationExecution",
]


class ObservationExecution:
    """Synchronous observation-execution API: queue advancement and support."""

    def __init__(self, butler: DiskButler | None = None):  # ruff: ignore[missing-return-type-special-method]
        """Initialize the high-level interface with a storage layer."""
        self._butler = butler or DiskButler()

    # -- Queue advancement -------------------------------------------------

    def advance_session(
        self, session: ObservationSession, deps: SessionRunnerDependencies
    ) -> ObservationSession:
        """Run one queue-advancement cycle against `session`.

        Returns
        -------
        session : `ObservationSession`
            The session after one queue-advancement cycle.
        """
        return advance_session(session, deps)

    def abort_session(self, session: ObservationSession, reason: str, now: datetime) -> ObservationSession:
        """Abort a session: skip remaining pending entries, close it out.

        Returns
        -------
        session : `ObservationSession`
            The session with remaining pending entries skipped and
            closed out.
        """
        return abort_session(session, reason, now)

    # -- Meridian flip -------------------------------------------------------

    def execute_meridian_flip(
        self,
        outcome_id: str,
        queued_observation_package_id: str,
        hour_angle_at_trigger_deg: float,
        steps: MeridianFlipSteps,
        config: Any,
    ) -> MeridianFlipOutcome:
        """Run the six-step meridian-flip sequence and record the outcome.

        Returns
        -------
        outcome : `MeridianFlipOutcome`
            The recorded outcome of the meridian-flip sequence.
        """
        return execute_meridian_flip(
            outcome_id, queued_observation_package_id, hour_angle_at_trigger_deg, steps, config
        )

    # -- Fault recovery --------------------------------------------------

    def recover_fault(
        self,
        fault_record_id: str,
        device_id: str,
        faulted_state: DeviceSummaryState,
        fault_detail: str,
        policy: RecoveryPolicy,
        return_to_standby: Any,
        re_enable: Any,
        read_state: Any,
        sleep: Any,
        escalate: Any = None,
    ) -> FaultRecord:
        """Run bounded recovery attempts against a faulted device.

        Returns
        -------
        record : `FaultRecord`
            The recorded fault and its bounded recovery attempts.
        """
        return recover_fault(
            fault_record_id,
            device_id,
            faulted_state,
            fault_detail,
            policy,
            return_to_standby,
            re_enable,
            read_state,
            sleep,
            escalate,
        )

    # -- Guide-star loss recovery --------------------------------------------

    def recover_guide_star_loss(
        self,
        event_id: str,
        observation_session_id: str,
        comparison_input_id: str,
        reacquire_guide_star: Any,
        config: Any,
    ) -> GuideStarLossEvent:
        """Run a bounded reacquire loop against a lost guide star.

        Returns
        -------
        event : `GuideStarLossEvent`
            The recorded loss event and its reacquire attempts.
        """
        return attempt_guide_star_recovery(
            event_id, observation_session_id, comparison_input_id, reacquire_guide_star, config
        )

    # -- Divergence recording ----------------------------------------------

    def record_divergence(
        self,
        record_id: str,
        observation_session_id: str,
        queued_observation_package_id: str | None,
        capability: Any,
        comparison_input_id: str,
        intended_value: float,
        observed_value: float,
        tolerance: float,
        divergence_unit: str,
        converged: bool | None = None,
        detail: str = "",
    ) -> DivergenceRecord:
        """Build one `DivergenceRecord` from a computed/observed value pair.

        Returns
        -------
        record : `DivergenceRecord`
            The constructed divergence record.
        """
        return record_divergence(
            record_id=record_id,
            observation_session_id=observation_session_id,
            queued_observation_package_id=queued_observation_package_id,
            capability=capability,
            comparison_input_id=comparison_input_id,
            intended_value=intended_value,
            observed_value=observed_value,
            tolerance=tolerance,
            divergence_unit=divergence_unit,
            converged=converged,
            detail=detail,
        )

    # -- Telemetry recording -------------------------------------------------

    def create_recorder(
        self, guiding_service: Any, indi_driver: Any, snapshot_interval_seconds: int = 300
    ) -> ObservationSessionRecorder:
        """Build an `ObservationSessionRecorder` bound to this DB.

        Returns
        -------
        recorder : `ObservationSessionRecorder`
            The newly constructed recorder.
        """
        return ObservationSessionRecorder(
            guiding_service, indi_driver, self._butler, snapshot_interval_seconds
        )

    # -- Post-session reconciliation -----------------------------------------

    def reconcile_session(self, session: ObservationSession, astrometrics: Any) -> ObservationSession:
        """Run both post-session reconciliations, recording the results.

        Returns
        -------
        session : `ObservationSession`
            The session after reconciliation, with results recorded.
        """
        return reconcile_session(self._butler, session, astrometrics)
