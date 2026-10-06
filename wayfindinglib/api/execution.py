"""Purpose: `ObservationExecution`, the entry point for running a session.

Description: `Wayfinder.execution` is an `ObservationExecution`. It
advances a session's queue, runs the meridian flip, recovers from device
faults and lost guide stars, records divergences and telemetry, and
reconciles a session after the night. Callers never import
`wayfindinglib.tasks.execution_tasks` directly.

Execution is the one part of the library that uses both of the others:
it runs what planning wrote, through the hardware `control` drives. The
caller builds the bundles of steps that touch hardware
(`SessionRunnerDependencies`, `MeridianFlipSteps`) and hands them in, the
same way `control.safety.execute_safe_state` takes its steps. That
wiring depends on what each queue entry needs, so this class does not
build it.
"""

from datetime import UTC, datetime
from typing import Any

from astrometricslib import AppConfiguration, Astrometrics, NotFoundError
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
    """Run and recover an observing session.

    Parameters
    ----------
    config : `AppConfiguration`, optional
        The application configuration. Taken from `butler`, or loaded,
        when omitted.
    butler : `DiskButler`, optional
        Stores sessions and their records. Built over `config` when
        omitted.
    astrometrics : `Astrometrics`, optional
        The science library handle shared with the rest of the
        `Wayfinder`. Built over `config` on first use when omitted.
    """

    def __init__(
        self,
        config: AppConfiguration | None = None,
        butler: DiskButler | None = None,
        *,
        astrometrics: Astrometrics | None = None,
    ) -> None:
        """Store the configuration, storage and shared science handle."""
        if config is None:
            if butler is not None:
                config = butler.config
            else:
                from astrometricslib import get_configuration

                config = get_configuration()
        self._config = config
        self._butler = butler or DiskButler(app_config=config)
        self._shared_astrometrics = astrometrics

    @property
    def _astrometrics(self) -> Astrometrics:
        """The shared `Astrometrics` handle, built on first use if not given.

        Returns
        -------
        astrometrics : `astrometricslib.Astrometrics`
            The science library handle.
        """
        if self._shared_astrometrics is None:
            self._shared_astrometrics = Astrometrics(self._config)
        return self._shared_astrometrics

    def _session(self, session: str | ObservationSession) -> ObservationSession:
        """Turn a session id into the recorded session it names.

        Parameters
        ----------
        session : `str` or `ObservationSession`
            A session id, or a session, which is returned as it is.

        Returns
        -------
        session : `ObservationSession`
            The session.

        Raises
        ------
        NotFoundError
            If no recorded session has that id.
        """
        if isinstance(session, ObservationSession):
            return session
        found = self._butler.get("observation_session", {"session_id": session})
        if found is None:
            raise NotFoundError(f"No observation session {session!r}.", details={"session_id": session})
        return found

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

    def abort_session(
        self, session: str | ObservationSession, reason: str, now: datetime | None = None
    ) -> ObservationSession:
        """Abort a session: skip its pending entries, close it and record it.

        Parameters
        ----------
        session : `str` or `ObservationSession`
            The session, by id or as a session.
        reason : `str`
            Why it was aborted, recorded on the session and on each
            skipped entry.
        now : `datetime`, optional
            When it was closed. Defaults to now.

        Returns
        -------
        session : `ObservationSession`
            The aborted, recorded session.
        """
        aborted = abort_session(self._session(session), reason, now or datetime.now(UTC))
        self._butler.put(aborted, "observation_session", {"session_id": aborted.id})
        return aborted

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

    def reconcile_session(self, session: str | ObservationSession) -> ObservationSession:
        """Run both post-session reconciliations, recording the results.

        Parameters
        ----------
        session : `str` or `ObservationSession`
            The session, by id or as a session.

        Returns
        -------
        session : `ObservationSession`
            The session after reconciliation, with results recorded.
        """
        return reconcile_session(self._butler, self._session(session), self._astrometrics)
