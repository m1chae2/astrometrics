"""Purpose: Event-driven pointing-model ingestion pipeline.

Description: The analyze -> expose chain for plate-solve alignment
attempts, per `Wayfinding_Library_Architecture.md` §2.5.1a's §6a
extension. Unlike guiding (`guiding_log_ingestion.py`), this half needs
no remote fetch and no `RemoteTransferDriver` (M7) dependency:
`AlignmentService.compute_pointing_model` already reads plate-solve
attempts from the *local* database as they happen -- there is no file
on a remote host to sync, unlike PHD2's guide log.

Deliberately session-scoped, never persisted: `MountPointingModel`'s
polar-misalignment/index-error terms (`ME`/`MA`/`IH`/`ID`) describe
tonight's specific setup, not the mount itself, and reset every time
polar alignment is redone. Persisting or feeding them forward across
sessions would apply a stale alignment's correction to a setup that no
longer has that error -- unlike `guiding_log_ingestion.py`'s spectrum
analysis, which *is* mount-mechanical and correctly persists cumulatively.

Rewiring `AlignmentService.compute_pointing_model` to call this instead
of importing `fit_pointing_model` directly is
`Wayfinding_Library_Architecture.md` M9's backend-cleanup scope, paired
with this milestone but not part of it.
"""

from typing import Any

from wayfindinglib.analytics.pointing_model import fit_pointing_model
from wayfindinglib.models.session.telemetry import MountPointingModel

_DEFAULT_LATITUDE_DEG = 45.0
"""Matches `fit_pointing_model`'s own default, used when no observer
location is configured on the active rig."""


def compute_pointing_model(
    observatory,  # ruff: ignore[missing-type-function-argument]
    logger_interface: Any,
    session_id: str | None = None,
    limit: int = 5000,
) -> MountPointingModel:
    """Fit the geometric pointing model from locally recorded plate solves.

    Parameters
    ----------
    observatory : `wayfindinglib.api.control_registry.ObservatoryControl`
        Provides `get_observer_location` for the fit's latitude input.
    logger_interface : `astrometricslib.LoggerInterface`
        Source of recorded alignment attempts.
    session_id : `str` | `None`, optional
        Target session to fit, or `None` to fit all recorded historical
        solves.
    limit : `int`, optional
        Maximum number of historical attempts to read when `session_id`
        is `None`, default 5000.

    Returns
    -------
    model : `MountPointingModel`
        The freshly-fit model -- not persisted or fed forward across
        sessions (see module docstring).
    """
    if session_id:
        attempts = logger_interface.get_session_alignment_attempts(session_id)
    else:
        attempts = logger_interface.get_alignment_logs(limit=limit)

    observer_location = observatory.get_observer_location()
    latitude_deg = observer_location["latitude"] if observer_location else _DEFAULT_LATITUDE_DEG

    return fit_pointing_model(attempts, latitude_deg=latitude_deg)
