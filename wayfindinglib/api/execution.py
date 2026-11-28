"""Purpose: `ObservationExecution`, the entry point for running a session.

Description: `Wayfinder.execution` is an `ObservationExecution`. It
runs a session's queue (one cycle, or until it is done), aborts a
session, reconciles it after the night, and builds the recorder that
saves a night's telemetry. The meridian flip, the recovery from device
faults and lost guide stars are private steps of a run.
Callers never import `wayfindinglib.tasks.execution_tasks` directly.

Execution is the one part of the library that uses both of the others:
it runs what planning wrote, through the hardware `control` drives. The
steps of an imaging queue are built on the work behind `control`
(`tasks.execution_tasks.imaging_queue`).
"""

import threading
from typing import TYPE_CHECKING, Any

from astrometricslib import AppConfiguration, Astrometrics
from astrometricslib.exceptions import ConfigurationError
from wayfindinglib._internal.renames import MovedNames
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.policy.device_state import DeviceSummaryState
from wayfindinglib.models.policy.recovery import FaultRecord, RecoveryPolicy
from wayfindinglib.models.session.guide_star_loss import GuideStarLossEvent
from wayfindinglib.models.session.meridian_flip import MeridianFlipOutcome
from wayfindinglib.models.session.observation_session import ObservationSession
from wayfindinglib.tasks.execution_tasks.fault_recovery import recover_fault
from wayfindinglib.tasks.execution_tasks.guide_star_loss_recovery import attempt_guide_star_recovery
from wayfindinglib.tasks.execution_tasks.meridian_flip import MeridianFlipSteps, execute_meridian_flip
from wayfindinglib.tasks.execution_tasks.session_recorder import ObservationSessionRecorder

if TYPE_CHECKING:
    from wayfindinglib.api.control import ObservatoryControl
    from wayfindinglib.api.control.context import ControlContext

# Declares this module's own public surface. Without it, sphinx-automodapi
# documents every imported name too, which is what produced the
# "stub file not found" warnings for re-exports and typing helpers.
__all__ = [
    "ObservationExecution",
]


class ObservationExecution(MovedNames):
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
    control : `ObservatoryControl`, optional
        The `Wayfinder`'s hardware sub-API. `run` and `create_recorder`
        use its context.
    """

    def __init__(
        self,
        config: AppConfiguration | None = None,
        butler: DiskButler | None = None,
        *,
        astrometrics: Astrometrics | None = None,
        control: ObservatoryControl | None = None,
    ) -> None:
        """Store the configuration, storage, science handle and control."""
        if config is None:
            if butler is not None:
                config = butler.config
            else:
                from astrometricslib import get_configuration

                config = get_configuration()
        self._config = config
        self._butler = butler or DiskButler(app_config=config)
        self._shared_astrometrics = astrometrics
        self._control = control

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

    def _control_context(self) -> ControlContext:
        """Return the shared control context, or refuse without `control`.

        Returns
        -------
        context : `ControlContext`
            The context of the `Wayfinder`'s `control`.

        Raises
        ------
        ConfigurationError
            If this object was built without `control`.
        """
        if self._control is None:
            raise ConfigurationError("Execution needs the Wayfinder's control, which drives the hardware.")
        return self._control._context

    # -- Queue advancement -------------------------------------------------

    def run(
        self, session: ObservationSession, stop: threading.Event | None = None
    ) -> ObservationSession | None:
        """Run a session's queue: one cycle, or cycle after cycle.

        One cycle checks safety and the devices, picks the next entry whose
        start time has come, sends (or only records, per the delegation
        policy) its slew, captures its frames, and saves the session.

        Owner:
            wayfindinglib.tasks.execution_tasks.session_execution.run_session

        Parameters
        ----------
        session : `ObservationSession`
            The session.
        stop : `threading.Event`, optional
            Given, keep running cycles until the queue has nothing left to
            run, the session ends or is suspended, or `stop` is set. This
            blocks, so run it on its own thread.

        Returns
        -------
        session : `ObservationSession` or `None`
            The session after the last cycle (`None` if `stop` was already
            set).

        Notes
        -----
        Raises `ConfigurationError` if this object was built without
        `control`.
        """
        from wayfindinglib.tasks.execution_tasks.session_execution import run_session

        return run_session(self._control_context(), self._butler, session, stop)

    def abort(self, session: ObservationSession, reason: str = "") -> ObservationSession:
        """Abort a session: skip its pending entries, close it and save it.

        Owner:
            wayfindinglib.tasks.execution_tasks.session_execution.abort_and_save

        Parameters
        ----------
        session : `ObservationSession`
            The session.
        reason : `str`, optional
            Why it was aborted, recorded on the session and on each
            skipped entry.

        Returns
        -------
        session : `ObservationSession`
            The aborted, saved session.
        """
        from wayfindinglib.tasks.execution_tasks.session_execution import abort_and_save

        return abort_and_save(self._butler, session, reason)

    # -- Meridian flip -------------------------------------------------------

    def _execute_meridian_flip(
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

    def _recover_fault(
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

    def _recover_guide_star_loss(
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

    # -- Telemetry recording -------------------------------------------------

    def create_recorder(
        self,
        phd2_host: str | None = None,
        phd2_port: int | None = None,
        snapshot_interval_s: int = 300,
    ) -> ObservationSessionRecorder:
        """Build the recorder that saves guiding and weather telemetry.

        The recorder reads the INDI session of the `Wayfinder`'s `control`
        and opens its own connection to the PHD2 event server (the guiding
        program's data feed).

        Owner:
            wayfindinglib.tasks.execution_tasks.session_execution.build_recorder

        Parameters
        ----------
        phd2_host : `str`, optional
            The computer PHD2 runs on. The configured PHD2 location when
            omitted.
        phd2_port : `int`, optional
            The PHD2 event-server port. PHD2's standard port, 4400, if not
            given.
        snapshot_interval_s : `int`, optional
            How often a snapshot is saved, in seconds.

        Returns
        -------
        recorder : `ObservationSessionRecorder`
            The recorder, not yet running.

        Notes
        -----
        Raises `ConfigurationError` if this object was built without
        `control`.
        """
        from wayfindinglib.tasks.execution_tasks.session_execution import build_recorder

        return build_recorder(
            self._control_context(),
            self._butler,
            phd2_host=phd2_host,
            phd2_port=phd2_port,
            snapshot_interval_s=snapshot_interval_s,
        )

    # -- Post-session reconciliation -----------------------------------------

    def reconcile(self, session: ObservationSession) -> ObservationSession:
        """Run both post-session reconciliations, recording the results.

        Owner:
            wayfindinglib.tasks.execution_tasks.post_session_reconciliation.reconcile_session

        Parameters
        ----------
        session : `ObservationSession`
            The session, which must have ended.

        Returns
        -------
        session : `ObservationSession`
            The session after reconciliation, with results recorded.
        """
        from wayfindinglib.tasks.execution_tasks.post_session_reconciliation import reconcile_session

        return reconcile_session(self._butler, session, self._astrometrics)
