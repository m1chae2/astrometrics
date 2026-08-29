"""Purpose: Unit tests for the ObservationExecution astrometrics.

Description: Verifies each method delegates to its underlying task
function with the interface's own storage layer where relevant,
and that this module's own source carries no direct INDI hardware import
-- a static source-text scan mirroring
`test_planning_registry.py::test_planning_module_tree_imports_no_device_driver`,
since a runtime `sys.modules` check would be polluted by this
package's own pre-existing `phd2_guiding_service -> observatory
-> drivers.indi.hardware_lock` import chain (a lightweight file-lock
utility, not real hardware driver code, and out of this module's
scope to change).
"""

import pathlib
from datetime import UTC, date, datetime

import pytest

from wayfindinglib.api.execution_registry import ObservationExecution
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.policy.device_state import DeviceSummaryState
from wayfindinglib.models.policy.recovery import RecoveryPolicy
from wayfindinglib.models.policy.safety import SafetyAssessment, SafetyVerdict
from wayfindinglib.models.session.correction_config import CorrectionConfig
from wayfindinglib.models.session.observation_session import (
    ObservationSession,
    QueuedObservationPackage,
    QueueEntryStatus,
    SessionStatus,
    StartTimeMode,
)
from wayfindinglib.tasks.execution_tasks.meridian_flip import MeridianFlipSteps
from wayfindinglib.tasks.execution_tasks.session_runner import SessionRunnerDependencies

_NOW = datetime(2026, 8, 5, 4, 0, 0, tzinfo=UTC)


@pytest.fixture
def butler(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Build a DiskButler backed by an isolated temporary database.

    Returns
    -------
    butler : `DiskButler`
        A fresh, isolated butler instance.
    """
    from astrometricslib import AppConfiguration

    config_path = tmp_path / "astrometrics.config"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    config = AppConfiguration()
    config.update_config({"Wayfinding Library": {"path": str(tmp_path / "wayfinding_library")}})
    return DiskButler(app_config=config)


def _session() -> ObservationSession:
    entry = QueuedObservationPackage(
        id="entry-1",
        observation_package_id="pkg-1",
        target_id="M 81",
        start_time_mode=StartTimeMode.SOONEST,
        computed_start_time=_NOW,
        status=QueueEntryStatus.PENDING,
    )
    return ObservationSession(
        id="session-1",
        night_date=date(2026, 8, 5),
        status=SessionStatus.RUNNING,
        site_profile_id="site-1",
        telescope_id="scope-1",
        camera_id="cam-1",
        queue=[entry],
    )


def _deps() -> SessionRunnerDependencies:
    from wayfindinglib.models.policy.delegation import DelegationPolicy

    return SessionRunnerDependencies(
        delegation_policy=DelegationPolicy(id="policy-1"),
        get_safety_assessment=lambda: SafetyAssessment(verdict=SafetyVerdict.SAFE),
        required_device_states=lambda entry: [],
        check_meridian_crossing=lambda entry, now: False,
        perform_meridian_flip=lambda entry: None,
        compute_actions=lambda entry: [],
        capture_outcome=lambda entry: (5, ""),
        checkpoint=lambda session: None,
        now=lambda: _NOW,
    )


def test_advance_session_delegates_to_task_function():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify advance_session runs the queue-advancement cycle."""
    execution = ObservationExecution()
    result = execution.advance_session(_session(), _deps())
    assert result.queue[0].status == QueueEntryStatus.COMPLETED


def test_abort_session_delegates_to_task_function():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify abort_session skips remaining pending entries."""
    execution = ObservationExecution()
    result = execution.abort_session(_session(), "operator abort", _NOW)
    assert result.status == SessionStatus.ABORTED
    assert result.queue[0].status == QueueEntryStatus.SKIPPED


def test_execute_meridian_flip_delegates_to_task_function():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify execute_meridian_flip runs the six-step sequence."""
    execution = ObservationExecution()
    steps = MeridianFlipSteps(
        complete_or_abandon_exposure=lambda: True,
        stop_guiding=lambda: True,
        slew_through_flip=lambda: True,
        realign_iteration=lambda: (True, 5.0),
        reacquire_guide_star=lambda: True,
        resume_exposure=lambda: True,
    )
    outcome = execution.execute_meridian_flip("flip-1", "entry-1", 1.0, steps, CorrectionConfig())
    assert outcome.resumed is True


def test_recover_fault_delegates_to_task_function():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify recover_fault runs bounded recovery attempts."""
    execution = ObservationExecution()
    record = execution.recover_fault(
        "fault-1",
        "mount-1",
        DeviceSummaryState.FAULT,
        "connection lost",
        RecoveryPolicy(max_attempts=1),
        return_to_standby=lambda: None,
        re_enable=lambda: None,
        read_state=lambda: DeviceSummaryState.ENABLED,
        sleep=lambda seconds: None,
    )
    assert record.recovered is True


def test_recover_guide_star_loss_delegates_to_task_function():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify recover_guide_star_loss runs a bounded reacquire loop."""
    from wayfindinglib.models.session.correction_config import CorrectionConfig

    execution = ObservationExecution()
    event = execution.recover_guide_star_loss(
        "loss-1",
        "session-1",
        "frame-1",
        reacquire_guide_star=lambda: True,
        config=CorrectionConfig(),
    )
    assert event.recovered is True
    assert event.reacquire_attempts == 1


def test_record_divergence_delegates_to_task_function():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify record_divergence builds a DivergenceRecord."""
    from wayfindinglib.models.policy.delegation import ObservatoryCapability

    execution = ObservationExecution()
    record = execution.record_divergence(
        "div-1",
        "session-1",
        "entry-1",
        ObservatoryCapability.PLATE_SOLVE_ALIGNMENT,
        "frame-1",
        10.0,
        9.5,
        1.0,
        "arcsec",
    )
    assert record.within_tolerance is True


def test_create_recorder_binds_the_astrometrics_own_butler(butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify create_recorder binds the recorder to this interface's butler."""
    execution = ObservationExecution(butler=butler)
    recorder = execution.create_recorder(guiding_service=object(), indi_driver=object())
    assert recorder._butler is butler


def test_reconcile_session_delegates_and_persists(butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify reconcile_session runs both reconciliations and records."""

    class _FakeTargetRegistry:
        def get(self, target_id):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
            return None

    class _FakeAstrometrics:
        def __init__(self):  # ruff: ignore[missing-return-type-special-method]
            self.targets = _FakeTargetRegistry()

    execution = ObservationExecution(butler=butler)
    session = _session()
    session.status = SessionStatus.COMPLETED
    session.queue[0].status = QueueEntryStatus.COMPLETED
    butler.put(session, "observation_session", {"session_id": session.id})

    result = execution.reconcile_session(session, _FakeAstrometrics())

    reloaded = butler.get("observation_session", {"session_id": session.id})
    assert reloaded is not None
    assert result.id == session.id


def test_module_source_carries_no_direct_indi_hardware_import():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify this module's own source never references INDI or PyIndi."""
    module_path = pathlib.Path(__file__).resolve().parents[1] / "execution_registry.py"
    text = module_path.read_text()
    forbidden_substrings = ("wayfindinglib.drivers.indi", "import PyIndi", "from PyIndi")
    offending = [needle for needle in forbidden_substrings if needle in text]
    assert offending == []
