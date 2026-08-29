"""Purpose: Unit tests for the ObservatoryControl astrometrics.

Description: Verifies equipment activation/resolution round-trips,
correction methods delegate with resolved calibration (raising when
none exists for the active pairing), the safety monitor's hysteresis
state is carried across calls made through the same astrometrics instance,
safe-state and capability-promotion delegation work end to end, and
that constructing the high-level interface and calling its non-hardware methods
never imports the INDI driver layer.
"""

from datetime import UTC, datetime, timedelta

import pytest

from wayfindinglib.api.control_registry import ObservatoryControl
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.equipment_and_site.enclosure import Enclosure, EnclosureType
from wayfindinglib.models.equipment_and_site.guider_calibration import GuiderCalibration
from wayfindinglib.models.policy.delegation import DelegationState, ObservatoryCapability
from wayfindinglib.models.policy.safety import SafetyRule, SafetyRuleSet, SafetyVerdict
from wayfindinglib.tasks.control_tasks.safe_state import SafeStateSteps

_NOW = datetime(2026, 8, 5, 4, 0, 0, tzinfo=UTC)


@pytest.fixture
def app_config(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Build a real, isolated AppConfiguration and matching DiskButler.

    Returns
    -------
    config : `AppConfiguration`
        A fresh, isolated configuration instance.
    """
    from astrometricslib import AppConfiguration

    config_path = tmp_path / "astrometrics.config"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    config = AppConfiguration()
    config.update_config({"Wayfinding Library": {"path": str(tmp_path / "wayfinding_library")}})
    return config


@pytest.fixture
def control(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Build an ObservatoryControl backed by the isolated app_config/butler.

    Returns
    -------
    control : `ObservatoryControl`
        The constructed astrometrics.
    """
    butler = DiskButler(app_config=app_config)
    return ObservatoryControl(config=app_config, butler=butler)


def _configure_active_rig(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    app_config.update_config({
        "Observatory.Telescope": {"models": "Rig A", "active_telescope": "Rig A"},
        "Observatory.Telescope.Rig A": {"focal_length_mm": "450.0", "focal_ratio": "6.0"},
        "Observatory.Camera": {"models": "CamA", "default_primary_camera": "CamA"},
        "Observatory.Camera.CamA": {
            "pixel_size_μm": "3.76",
            "sensor_width_px": "6248",
            "sensor_height_px": "4176",
        },
    })


def test_set_active_telescope_and_camera_round_trip(control, app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify equipment activation records and resolves via active_*()."""
    _configure_active_rig(app_config)
    assert control.set_active_telescope("Rig A") is True
    assert control.set_active_camera("CamA") is True
    assert control.active_telescope().id == "Rig A"
    assert control.active_camera().id == "CamA"


def test_compute_pointing_correction_delegates_with_astrometrics_config(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify compute_pointing_correction forwards to the task function."""
    correction = control.compute_pointing_correction("frame-1", 180.0, 0.0, 180.0, 0.0, iteration=1)
    assert correction.converged is True


def test_compute_guiding_correction_raises_without_calibration(control, app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify guiding correction raises with no calibration for the pairing."""
    _configure_active_rig(app_config)
    control.set_active_telescope("Rig A")
    control.set_active_camera("CamA")

    with pytest.raises(ValueError, match="No GuiderCalibration"):
        control.compute_guiding_correction("frame-1", 5.0, 0.0)


def test_compute_guiding_correction_succeeds_with_saved_calibration(control, app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify guiding correction resolves a saved calibration."""
    _configure_active_rig(app_config)
    control.set_active_telescope("Rig A")
    control.set_active_camera("CamA")

    calibration = GuiderCalibration(
        id="cal-1",
        camera_id="CamA",
        telescope_id="Rig A",
        arcsec_per_pixel=2.0,
        camera_angle_deg=0.0,
        ra_rate_arcsec_per_sec=10.0,
        dec_rate_arcsec_per_sec=10.0,
    )
    control.save_guider_calibration(calibration)

    correction = control.compute_guiding_correction("frame-1", 5.0, 0.0)
    assert correction.pulse_ra_ms > 0


def test_assess_safety_hysteresis_persists_across_calls_on_same_astrometrics(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the interface's SafetyMonitor carries hysteresis across calls."""
    rule_set = SafetyRuleSet(
        id="default",
        rules=[
            SafetyRule(
                id="wind", measurement="wind_speed_kph", comparison="greater_than", unsafe_threshold=40.0
            )
        ],
        settling_period_sec=900,
    )
    control._butler.put(rule_set, "safety_rule_set", {"id": "default"})

    unsafe = control.assess_safety({"wind_speed_kph": (50.0, _NOW)}, now=_NOW)
    assert unsafe.verdict == SafetyVerdict.UNSAFE

    just_after = _NOW + timedelta(seconds=1)
    still_settling = control.assess_safety({"wind_speed_kph": (5.0, just_after)}, now=just_after)
    assert still_settling.verdict == SafetyVerdict.UNSAFE

    settled = _NOW + timedelta(seconds=900)
    cleared = control.assess_safety({"wind_speed_kph": (5.0, settled)}, now=settled)
    assert cleared.verdict == SafetyVerdict.SAFE


def test_execute_safe_state_delegates_to_task_function(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify execute_safe_state runs the ordered sequence, returns outcome."""
    enclosure = Enclosure(
        id="enc-1",
        enclosure_type=EnclosureType.ROLL_OFF_ROOF,
        park_azimuth_deg=180.0,
        park_altitude_deg=0.0,
    )
    steps = SafeStateSteps(
        abandon_exposure=lambda: True,
        stop_guiding=lambda: True,
        park_mount=lambda: True,
        get_mount_position=lambda: (0.0, 180.0),
        close_enclosure=lambda: True,
        warm_sensor=lambda: True,
        close_session=lambda: True,
        enclosure=enclosure,
    )
    outcome = control.execute_safe_state("unsafe_verdict", steps)
    assert outcome.failed_step is None
    assert outcome.enclosure_closed is True


def test_apply_promotion_decision_and_summarize_divergence_evidence(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify promotion delegation records and evidence summarizes."""
    policy = control.apply_promotion_decision(
        ObservatoryCapability.PLATE_SOLVE_ALIGNMENT, DelegationState.SHADOWED
    )
    assert policy.state_for(ObservatoryCapability.PLATE_SOLVE_ALIGNMENT) == DelegationState.SHADOWED
    assert control.delegation_policy().state_for(ObservatoryCapability.PLATE_SOLVE_ALIGNMENT) == (
        DelegationState.SHADOWED
    )

    summary = control.summarize_divergence_evidence(ObservatoryCapability.PLATE_SOLVE_ALIGNMENT)
    assert summary.sample_count == 0


def test_connect_lazily_initializes_the_driver_via_the_private_alias(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify connect() works via the `._driver` alias, not just `.driver`.

    Regression test: `tasks.control_tasks.hardware_operations.connect`
    (and `indi_properties`/`set_indi_property`) read `observatory._driver`
    directly rather than through the lazily-initializing `.driver`
    property -- a plain `self._driver = None` instance attribute (as
    opposed to a property forwarding to `.driver`) left `connect()`
    crashing on `NoneType` even though `.driver` itself worked fine.
    """
    monkeypatch.setenv("ASTROMETRICS_TESTING", "1")
    control = ObservatoryControl(config=object())

    assert control.connect() is True
    assert control.driver is not None


def test_sync_and_is_syncing_raise_without_configured_sync_service():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify sync()/is_syncing() raise in standalone mode."""
    control = ObservatoryControl(config=object())

    with pytest.raises(RuntimeError, match="standalone mode"):
        control.sync("M 81")
    with pytest.raises(RuntimeError, match="standalone mode"):
        control.is_syncing("M 81")


def test_remote_transfer_methods_delegate_to_the_task_module(mocker, control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the four remote-transfer methods forward to tasks."""
    from wayfindinglib.tasks.control_tasks import remote_transfer_tasks

    mocker.patch.object(remote_transfer_tasks, "list_remote_targets", return_value=["M 81"])
    mocker.patch.object(remote_transfer_tasks, "list_remote_files", return_value=["frame1.fits"])
    mocker.patch.object(remote_transfer_tasks, "check_remote_connection", return_value=True)
    mocker.patch.object(remote_transfer_tasks, "download_remote_targets", return_value=True)

    assert control.list_remote_targets() == ["M 81"]
    remote_transfer_tasks.list_remote_targets.assert_called_once_with(control)

    assert control.list_remote_files("M 81") == ["frame1.fits"]
    remote_transfer_tasks.list_remote_files.assert_called_once_with(control, "M 81")

    assert control.check_remote_connection() is True
    remote_transfer_tasks.check_remote_connection.assert_called_once_with(control)

    assert control.download_remote_targets("M 81", local_path="/local/M81") is True
    remote_transfer_tasks.download_remote_targets.assert_called_once_with(
        "M 81", None, None, "/local/M81", True
    )


def test_discover_unassociated_remote_targets_delegates_via_astrometrics(mocker, control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify discovery constructs a high-level interface and delegates."""
    from wayfindinglib.tasks.control_tasks import remote_transfer_tasks

    fake_astrometrics = mocker.Mock()
    mocker.patch("astrometricslib.Astrometrics", return_value=fake_astrometrics)
    mocker.patch.object(
        remote_transfer_tasks, "discover_unassociated_remote_targets", return_value=["Unassociated"]
    )

    assert control.discover_unassociated_remote_targets() == ["Unassociated"]
    remote_transfer_tasks.discover_unassociated_remote_targets.assert_called_once_with(
        control, fake_astrometrics.targets
    )


def test_list_camera_profiles_and_get_equipment_configuration(control, app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the two equipment-config methods report the active rig."""
    app_config.update_config({
        "Observatory.Telescope": {"focal_length_mm": "450.0", "focal_ratio": "6.0"},
        "Observatory.Camera": {"models": "CamA", "default_primary_camera": "CamA"},
        "Observatory.Camera.CamA": {
            "pixel_size_μm": "3.76",
            "sensor_width_px": "6248",
            "sensor_height_px": "4176",
        },
    })

    profiles = control.list_camera_profiles()
    assert len(profiles) == 1
    assert profiles[0]["name"] == "CamA"

    configuration = control.get_equipment_configuration()
    assert configuration is not None
    assert configuration["camera"]["name"] == "CamA"


def test_save_and_get_commissioning_runs_round_trips(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify commissioning runs record and are returned by get_all."""
    from wayfindinglib.models.policy.commissioning import CommissioningObservation, CommissioningRun

    run = CommissioningRun(
        id="run-1",
        phase=1,
        drill_name="phase1_device_state_survey",
        observations=[
            CommissioningObservation(
                criterion="Every device reports a summary state",
                observed_value="ENABLED",
                expected="any valid state",
                passed=True,
            )
        ],
    )
    control.save_commissioning_run(run)

    runs = control.get_commissioning_runs()
    assert len(runs) == 1
    assert runs[0].id == "run-1"
    assert runs[0].all_passed() is True


def test_get_safety_rule_set_returns_none_when_unconfigured(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an unconfigured safety rule set reports None, not a default."""
    assert control.get_safety_rule_set() is None


def test_save_and_get_safety_rule_set_round_trips(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a saved safety rule set is returned by get_safety_rule_set."""
    from wayfindinglib.models.policy.safety import SafetyRule, SafetyRuleSet

    rule_set = SafetyRuleSet(
        id="default",
        rules=[
            SafetyRule(
                id="rule-1",
                measurement="wind_speed_kph",
                comparison="greater_than",
                unsafe_threshold=40.0,
            )
        ],
    )
    control.save_safety_rule_set(rule_set)

    fetched = control.get_safety_rule_set()
    assert fetched is not None
    assert fetched.id == "default"
    assert fetched.rules[0].measurement == "wind_speed_kph"


def test_non_hardware_methods_do_not_reference_indi_driver():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify non-hardware methods never reference the INDI layer.

    A static source-text scan, mirroring
    `test_planning_registry.py::test_planning_module_tree_imports_no_device_driver`:
    a runtime `sys.modules` snapshot is order-dependent (other tests in
    the same session legitimately import INDI modules to exercise
    hardware operations), so this checks each non-hardware method's own
    source text instead, which is deterministic regardless of what else
    has run in the process. An operator inspecting safety, calibration,
    or delegation state should not need a connected telescope to do so.
    """
    import inspect

    non_hardware_methods = [
        ObservatoryControl.active_telescope,
        ObservatoryControl.active_camera,
        ObservatoryControl.set_active_telescope,
        ObservatoryControl.set_active_camera,
        ObservatoryControl.compute_pointing_correction,
        ObservatoryControl.compute_guiding_correction,
        ObservatoryControl.compute_focus_correction,
        ObservatoryControl.active_guider_calibration,
        ObservatoryControl.save_guider_calibration,
        ObservatoryControl.active_focus_model,
        ObservatoryControl.save_focus_model,
        ObservatoryControl.assess_safety,
        ObservatoryControl.active_enclosure,
        ObservatoryControl.execute_safe_state,
        ObservatoryControl.cooling_ramp_rate,
        ObservatoryControl.summarize_device,
        ObservatoryControl.delegation_policy,
        ObservatoryControl.apply_promotion_decision,
        ObservatoryControl.summarize_divergence_evidence,
    ]
    forbidden_substrings = ("wayfindinglib.drivers.indi", "import PyIndi", "from PyIndi")

    offending = []
    for method in non_hardware_methods:
        source = inspect.getsource(method)
        if any(needle in source for needle in forbidden_substrings):
            offending.append(method.__name__)

    assert offending == []
