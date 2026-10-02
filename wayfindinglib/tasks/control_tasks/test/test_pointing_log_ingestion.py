"""Purpose: Unit tests for the pointing-model ingestion pipeline (M7a).

Description: Verifies `pointing_log_ingestion.compute_pointing_model`
reads local alignment attempts (session-scoped or all-history) and
fits them with the active rig's observer latitude, using fake
`ObservatoryControl`/`LoggerInterface` stand-ins -- no remote fetch, no
`RemoteTransferDriver` dependency (see module docstring).
"""

from typing import Any

from wayfindinglib.models.session.telemetry import MountPointingModel
from wayfindinglib.tasks.control_tasks import pointing_log_ingestion

_ATTEMPTS = [
    {"ra": 10.0, "dec": 20.0, "delta_ra_arcsec": 5.0, "delta_dec_arcsec": -3.0},
    {"ra": 11.0, "dec": 21.0, "delta_ra_arcsec": 4.5, "delta_dec_arcsec": -2.5},
    {"ra": 12.0, "dec": 22.0, "delta_ra_arcsec": 5.5, "delta_dec_arcsec": -3.5},
]


class _FakeLoggerInterface:
    """Serves fixed all-history/session-scoped alignment attempt lists."""

    def __init__(
        self, all_attempts: list[dict[str, Any]], session_attempts: list[dict[str, Any]] | None = None
    ) -> None:
        """Initialize with fixed responses for both query paths."""
        self._all_attempts = all_attempts
        self._session_attempts = session_attempts if session_attempts is not None else []
        self.last_limit: int | None = None
        self.last_session_id: str | None = None

    def get_alignment_logs(self, target_name: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Return the fixed all-history attempts.

        Returns
        -------
        attempts : `list` [`dict`]
            The fixed all-history response.
        """
        self.last_limit = limit
        return self._all_attempts

    def get_session_alignment_attempts(
        self, session_id: str | None = None, limit: int = 10000
    ) -> list[dict[str, Any]]:
        """Return the fixed session-scoped attempts.

        Returns
        -------
        attempts : `list` [`dict`]
            The fixed session-scoped response.
        """
        self.last_session_id = session_id
        return self._session_attempts


class _FakeObservatory:
    """A stand-in `ObservatoryControl` reporting a fixed observer location."""

    def __init__(self, observer_location: dict[str, float] | None) -> None:
        """Initialize with a fixed `get_observer_location` response."""
        self._observer_location = observer_location

    def get_observer_location(self) -> dict[str, float] | None:
        """Return the fixed observer location.

        Returns
        -------
        location : `dict` | `None`
            The fixed response passed at construction.
        """
        return self._observer_location


def test_compute_pointing_model_uses_all_history_without_session_id():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify no session_id reads all-history, not session-scoped attempts."""
    logger_interface = _FakeLoggerInterface(all_attempts=_ATTEMPTS)
    observatory = _FakeObservatory(observer_location={"latitude": 39.7, "longitude": -105.0})

    model = pointing_log_ingestion.compute_pointing_model(observatory, logger_interface)

    assert isinstance(model, MountPointingModel)
    assert model.sample_count == len(_ATTEMPTS)
    assert logger_interface.last_session_id is None


def test_compute_pointing_model_uses_session_scoped_attempts_when_given():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a session_id reads session-scoped attempts, not all-history."""
    session_attempts = _ATTEMPTS[:2]
    logger_interface = _FakeLoggerInterface(all_attempts=_ATTEMPTS, session_attempts=session_attempts)
    observatory = _FakeObservatory(observer_location={"latitude": 39.7, "longitude": -105.0})

    model = pointing_log_ingestion.compute_pointing_model(observatory, logger_interface, session_id="s1")

    assert model.sample_count == len(session_attempts)
    assert logger_interface.last_session_id == "s1"


def test_compute_pointing_model_falls_back_to_default_latitude_when_unconfigured():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a missing observer location doesn't raise, uses the default."""
    logger_interface = _FakeLoggerInterface(all_attempts=_ATTEMPTS)
    observatory = _FakeObservatory(observer_location=None)

    model = pointing_log_ingestion.compute_pointing_model(observatory, logger_interface)

    assert isinstance(model, MountPointingModel)


def test_compute_pointing_model_never_persists_anything():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the session-scoped model carries no id/persistence fields.

    Regression guard for the plan's explicit invariant: `MountPointingModel`
    must never gain a persisted-standing-state shape the way
    `GuidingSpectrumAnalysis` did in M7a, since its terms describe
    tonight's setup, not the mount.
    """
    logger_interface = _FakeLoggerInterface(all_attempts=_ATTEMPTS)
    observatory = _FakeObservatory(observer_location={"latitude": 39.7, "longitude": -105.0})

    model = pointing_log_ingestion.compute_pointing_model(observatory, logger_interface)

    assert not hasattr(model, "id")
    assert not hasattr(model, "schema_version")
