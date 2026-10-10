"""Purpose: Serve the observing-session RPC methods.

Description: The UI lists observing sessions, opens one, aborts one, or
reconciles one after the night. Reading goes through
`Wayfinder.planning.get_plan`; abort and reconcile go through
`Wayfinder.execution`, which take the session id and record the result.

Several `ObservationExecution` operations take bundles of steps that
drive hardware (`SessionRunnerDependencies`, `MeridianFlipSteps`), which
cannot cross a JSON-RPC call. `execute_meridian_flip`, `recover_fault`,
`recover_guide_star_loss` and `create_recorder` therefore belong with
whatever owns a run loop, not with a request handler. The sequencer's
run loop is `advance_session`, started by `target_imaging_executor`.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ExecutionService:
    """Expose the data-only parts of Observation Execution over RPC."""

    def __init__(self, wayfinder: Any) -> None:
        """Keep the Wayfinder whose planning and execution APIs do the work.

        Parameters
        ----------
        wayfinder : `wayfindinglib.Wayfinder`
            The shared Wayfinder.
        """
        self.wayfinder = wayfinder

    def list_sessions(self) -> list[Any]:
        """Summarize every recorded observation session, newest night first.

        Returns
        -------
        sessions : `list` [`ObservationSessionSummary`]
            One line per session.
        """
        return self.wayfinder.planning.get_plan()

    def get_session(self, session_id: str) -> Any:
        """Return one observation session in full.

        Parameters
        ----------
        session_id : `str`
            Identifier of the session to return.

        Returns
        -------
        session : `ObservationSession`
            The session.
        """
        return self.wayfinder.planning.get_plan(session_id)

    def abort_session(self, session_id: str, reason: str) -> Any:
        """Abort a session, skipping its remaining pending entries.

        Parameters
        ----------
        session_id : `str`
            Identifier of the session to abort.
        reason : `str`
            Operator-supplied reason, recorded on the session.

        Returns
        -------
        session : `ObservationSession`
            The aborted, recorded session.
        """
        logger.info("Aborting observation session %s: %s", session_id, reason)
        return self.wayfinder.execution.abort_session(session_id, reason)

    def reconcile_session(self, session_id: str) -> Any:
        """Run post-session reconciliation and record the results.

        Parameters
        ----------
        session_id : `str`
            Identifier of the session to reconcile.

        Returns
        -------
        session : `ObservationSession`
            The reconciled session.
        """
        logger.info("Reconciling observation session %s", session_id)
        return self.wayfinder.execution.reconcile_session(session_id)

    def record_divergence(
        self,
        record_id: str,
        observation_session_id: str,
        capability: str,
        comparison_input_id: str,
        intended_value: float,
        observed_value: float,
        tolerance: float,
        divergence_unit: str,
        queued_observation_package_id: str | None = None,
        converged: bool | None = None,
        detail: str = "",
    ) -> dict[str, Any]:
        """Record one computed-versus-observed divergence.

        Parameters
        ----------
        record_id : `str`
            Identifier for the new divergence record.
        observation_session_id : `str`
            Session the divergence was observed during.
        capability : `str`
            Name of the `ObservatoryCapability` the divergence concerns.
        comparison_input_id : `str`
            Identifier of the input the comparison was drawn from.
        intended_value, observed_value, tolerance : `float`
            The computed value, what was actually measured, and the
            allowed difference between them.
        divergence_unit : `str`
            Unit the three values are expressed in.
        queued_observation_package_id : `str`, optional
            Queue entry the divergence belongs to, if any.
        converged : `bool`, optional
            Whether a corrective loop converged.
        detail : `str`, optional
            Free-text note.

        Returns
        -------
        record : `dict`
            The divergence record, serialized for transport.
        """
        from wayfindinglib.models.policy.delegation import ObservatoryCapability

        record = self.wayfinder.execution.record_divergence(
            record_id,
            observation_session_id,
            queued_observation_package_id,
            ObservatoryCapability(capability),
            comparison_input_id,
            intended_value,
            observed_value,
            tolerance,
            divergence_unit,
            converged=converged,
            detail=detail,
        )
        return record.model_dump(mode="json", by_alias=True)
