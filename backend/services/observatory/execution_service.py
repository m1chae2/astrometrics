"""Backend adapter over wayfindinglib's Observation Execution astrometrics.

`ObservationExecution` is an in-process API: several of its operations take
bundles of injected callables (`SessionRunnerDependencies`,
`MeridianFlipSteps`) that drive hardware, and those cannot cross a JSON-RPC
boundary. This service exposes the subset whose inputs are plain data,
translating between session identifiers on the wire and the
`ObservationSession` objects the high-level interface expects.

The remaining operations -- `advance_session`, `execute_meridian_flip`,
`recover_fault`, `recover_guide_star_loss`, and `create_recorder` -- need a
caller that can supply hardware-driving callables. That belongs with whatever
owns the run loop, not with a request handler; see `target_imaging_executor`,
which currently runs its own queue rather than delegating here.
"""

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


class ExecutionService:
    """Expose the data-only parts of Observation Execution over RPC."""

    def __init__(self, wayfinder: Any, astrometrics: Any, config: Any = None):  # ruff: ignore[missing-return-type-special-method]
        """Initialize the ExecutionService.

        Parameters
        ----------
        wayfinder : `Any`
            The facade for wayfindinglib, providing the Execution API.
        astrometrics : `Any`
            The facade for astrometricslib, used during session reconciliation.
        config : `Any`, optional
            Application configuration instance for locating the datastore.
        """
        self.wayfinder = wayfinder
        self.astrometrics = astrometrics

        # Its own butler rather than the high-level interface's
        # private one. Both resolve to the same wayfinding.db, so reads
        # stay consistent, without this service depending on
        # ObservationExecution's internals.
        from wayfindinglib.drivers.butler import DiskButler

        self._butler = DiskButler(app_config=config) if config else DiskButler()

    @property
    def _execution(self) -> Any:
        """The Observation Execution astrometrics.

        Returns
        -------
        execution : `Any`
            The `ObservationExecution` branch of the Wayfinder
            high-level interface.
        """
        return self.wayfinder.execution

    def _load_session(self, session_id: str) -> Any:
        """Load a recorded observation session by identifier.

        Parameters
        ----------
        session_id : `str`
            Identifier of the session to load.

        Returns
        -------
        session : `Any`
            The hydrated `ObservationSession`.

        Raises
        ------
        ValueError
            Raised if no session exists with that identifier.
        """
        session = self._butler.get("observation_session", {"session_id": session_id})
        if session is None:
            raise ValueError(f"No observation session found with id '{session_id}'")
        return session

    def list_sessions(self) -> list[dict[str, Any]]:
        """Summarize every recorded observation session.

        Returns
        -------
        sessions : `list` of `dict`
            One summary per session, newest first, carrying the fields a
            queue view needs without shipping whole session documents.
        """
        sessions = self._butler.get_all("observation_session")
        summaries = [
            {
                "id": session.id,
                "status": getattr(session.status, "value", str(session.status)),
                "night_date": str(getattr(session, "night_date", "")),
                "entry_count": len(getattr(session, "queue", []) or []),
            }
            for session in sessions
        ]
        summaries.sort(key=lambda entry: entry["night_date"], reverse=True)
        return summaries

    def get_session(self, session_id: str) -> dict[str, Any]:
        """Return one observation session in full.

        Parameters
        ----------
        session_id : `str`
            Identifier of the session to return.

        Returns
        -------
        session : `dict`
            The session serialized for transport.
        """
        return self._load_session(session_id).model_dump(mode="json")

    def abort_session(self, session_id: str, reason: str) -> dict[str, Any]:
        """Abort a session, skipping its remaining pending entries.

        Parameters
        ----------
        session_id : `str`
            Identifier of the session to abort.
        reason : `str`
            Operator-supplied reason, recorded on the session.

        Returns
        -------
        session : `dict`
            The aborted session, serialized for transport.
        """
        session = self._load_session(session_id)
        logger.info(f"Aborting observation session {session_id}: {reason}")
        aborted = self._execution.abort_session(session, reason, datetime.now(UTC))
        return aborted.model_dump(mode="json")

    def reconcile_session(self, session_id: str) -> dict[str, Any]:
        """Run post-session reconciliation and record the results.

        Parameters
        ----------
        session_id : `str`
            Identifier of the session to reconcile.

        Returns
        -------
        session : `dict`
            The reconciled session, serialized for transport.
        """
        session = self._load_session(session_id)
        logger.info(f"Reconciling observation session {session_id}")
        # Reconciliation links the physical images captured by the
        # execution loop back to the logical target data models in
        # astrometricslib.
        reconciled = self._execution.reconcile_session(session, self.astrometrics)
        return reconciled.model_dump(mode="json")

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

        record = self._execution.record_divergence(
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
        return record.model_dump(mode="json")
