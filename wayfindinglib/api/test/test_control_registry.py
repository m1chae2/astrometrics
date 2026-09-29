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

    config_path = tmp_path / "astrometrics.config.toml"
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


def test_connect_lazily_initializes_every_configured_driver(control, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify connect() lazily initializes the shared driver and succeeds.

    `._driver` (a bare alias for `.driver`) was retired in M5: nothing
    in production reads it anymore -- `connect()` now loops over the
    five per-device-type driver properties (§4), each of which lazily
    builds the shared session via `.driver` on its own.
    """
    monkeypatch.setenv("ASTROMETRICS_TESTING", "1")

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
        control, "M 81", None, None, "/local/M81", True
    )


def test_remote_transfer_driver_lazily_builds_stellarmate_interface(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify remote_transfer_driver builds a cached StellarMateInterface.

    M7: `ObservatoryControl.remote_transfer_driver` resolves once from
    config (default ``"stellarmate"``) instead of every
    `remote_transfer_tasks.py` function constructing its own instance.
    """
    from wayfindinglib.drivers.protocols.remote_transfer_driver import RemoteTransferDriver
    from wayfindinglib.drivers.stellarmate_interface import StellarMateInterface

    driver = control.remote_transfer_driver
    assert isinstance(driver, StellarMateInterface)
    assert isinstance(driver, RemoteTransferDriver)
    assert driver.driver_name == "stellarmate"
    assert control.remote_transfer_driver is driver


def test_remote_transfer_driver_can_be_injected_for_tests(mocker, control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the setter overrides the lazily-built driver."""
    fake_driver = mocker.Mock()
    control.remote_transfer_driver = fake_driver
    assert control.remote_transfer_driver is fake_driver


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


def test_mount_driver_slew_end_to_end_against_simulator(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Exercise slew/park/unpark/tracking/abort through the real driver stack.

    `ObservatoryControl.slew_to_coordinates()` -> `hardware_operations` ->
    the `MountDriver` ABC -> `IndiMountDriver` -> `SimulatorIndiInterface`,
    confirming parity with pre-redesign behavior end to end (per the
    plan's Verification section) rather than only against a
    duck-typed fake manager.
    """
    from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface

    control.driver = SimulatorIndiInterface(config=control._config)
    control.apply_promotion_decision(ObservatoryCapability.MOUNT_CONTROL, DelegationState.AUTHORITATIVE)

    assert control.slew_to_coordinates(10.0, 20.0) is True
    assert control.set_tracking(True) is True
    assert control.park() is True
    assert control.unpark() is True
    assert control.abort_motion() is True


def test_mount_driver_commands_refused_when_not_authoritative(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify mount commands are refused by default (monitoring mode).

    `MOUNT_CONTROL` defaults to `DELEGATED`, not `AUTHORITATIVE` -- this
    is the concrete mechanism behind "wayfindinglib starts in
    monitoring mode": hardware commands fail closed until the operator
    explicitly promotes the capability.
    """
    from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface
    from wayfindinglib.exceptions import AstrometryHardwareError

    control.driver = SimulatorIndiInterface(config=control._config)

    with pytest.raises(AstrometryHardwareError, match="MOUNT_CONTROL"):
        control.slew_to_coordinates(10.0, 20.0)


def test_focuser_and_filter_wheel_end_to_end_against_simulator(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Exercise focuser/filter-wheel commands through the real driver stack.

    `ObservatoryControl.focus_move()`/`.set_filter()` -> `hardware_operations`
    -> the `FocuserDriver`/`FilterWheelDriver` ABCs -> the INDI adapters ->
    `SimulatorIndiInterface`, confirming parity with pre-redesign behavior
    (per the plan's Verification section).
    """
    from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface
    from wayfindinglib.models.policy.delegation import CapabilityDelegation, DelegationPolicy

    control.driver = SimulatorIndiInterface(config=control._config)
    # Persisted directly rather than via apply_promotion_decision(): AUTOFOCUS
    # and CAPTURE_ORCHESTRATION each have their own multi-step validity-rule
    # promotion path (covered by their own dedicated tests), orthogonal to
    # what this test verifies -- that the driver plumbing works once a
    # capability is AUTHORITATIVE.
    control._butler.put(
        DelegationPolicy(
            id="default",
            capability_delegations=[
                CapabilityDelegation(
                    capability=ObservatoryCapability.AUTOFOCUS, state=DelegationState.AUTHORITATIVE
                ),
                CapabilityDelegation(
                    capability=ObservatoryCapability.CAPTURE_ORCHESTRATION,
                    state=DelegationState.AUTHORITATIVE,
                ),
            ],
        ),
        "delegation_policy",
        {"id": "default"},
    )

    assert control.focus_move(50) is True
    assert control.get_focuser_position() == 0
    assert control.set_filter("Luminance") is True
    assert "Luminance" in control.get_filter_names()


def test_focuser_and_filter_wheel_commands_refused_when_not_authoritative(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify focuser/filter-wheel commands fail closed by default."""
    from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface
    from wayfindinglib.exceptions import AstrometryHardwareError

    control.driver = SimulatorIndiInterface(config=control._config)

    with pytest.raises(AstrometryHardwareError, match="AUTOFOCUS"):
        control.focus_move(50)
    with pytest.raises(AstrometryHardwareError, match="CAPTURE_ORCHESTRATION"):
        control.set_filter("Luminance")


def test_get_telescope_status_golden_output_matches_pre_redesign_shape(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the four-call-assembled status matches the old single-call one.

    Regression-sensitive point (plan Verification, M4): `get_telescope_status`
    now reassembles its result from four separate driver calls
    (`mount_driver`, `filter_wheel_driver`, `focuser_driver`, `camera_driver`)
    instead of one `IndiInterface.get_status()` call. Since nothing here
    changes the simulator's state between the two reads, both must
    produce byte-identical output.
    """
    from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface

    control.driver = SimulatorIndiInterface(config=control._config)

    golden = control.driver.get_status().model_dump(by_alias=True)
    golden["guidingHistory"] = []  # Populated by a guiding service, not configured here.

    from wayfindinglib.tasks.control_tasks import hardware_operations

    reassembled = hardware_operations.get_telescope_status(control)

    assert reassembled == golden


def test_capture_image_and_guide_camera_end_to_end_against_simulator(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Exercise capture_image/guide_expose/sync_coordinates end to end.

    `ObservatoryControl.capture_image()`/`.guide_expose()`/`.sync_coordinates()`
    -> `hardware_operations` -> the `CameraDriver`/`MountDriver` ABCs ->
    the INDI adapters -> `SimulatorIndiInterface` (per the plan's
    Verification section for M2-M4).
    """
    from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface
    from wayfindinglib.models.policy.delegation import CapabilityDelegation, DelegationPolicy

    control.driver = SimulatorIndiInterface(config=control._config)
    control._butler.put(
        DelegationPolicy(
            id="default",
            capability_delegations=[
                CapabilityDelegation(
                    capability=ObservatoryCapability.CAPTURE_ORCHESTRATION,
                    state=DelegationState.AUTHORITATIVE,
                ),
                CapabilityDelegation(
                    capability=ObservatoryCapability.AUTOGUIDING, state=DelegationState.AUTHORITATIVE
                ),
                CapabilityDelegation(
                    capability=ObservatoryCapability.PLATE_SOLVE_ALIGNMENT,
                    state=DelegationState.AUTHORITATIVE,
                ),
            ],
        ),
        "delegation_policy",
        {"id": "default"},
    )

    assert control.capture_image(1.0) is True
    assert control.guide_expose(1.0) is True
    assert control.get_guide_image() is None
    assert control.sync_coordinates(10.0, 20.0) is True


def test_capture_image_and_sync_coordinates_refused_when_not_authoritative(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify capture/sync commands fail closed by default."""
    from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface
    from wayfindinglib.exceptions import AstrometryHardwareError

    control.driver = SimulatorIndiInterface(config=control._config)

    with pytest.raises(AstrometryHardwareError, match="CAPTURE_ORCHESTRATION"):
        control.capture_image(1.0)
    with pytest.raises(AstrometryHardwareError, match="AUTOGUIDING"):
        control.guide_expose(1.0)
    with pytest.raises(AstrometryHardwareError, match="PLATE_SOLVE_ALIGNMENT"):
        control.sync_coordinates(10.0, 20.0)


def test_enclosure_state_and_commands_against_simulator(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Exercise enclosure state/authority gating through the real driver stack.

    `ObservatoryControl.get_enclosure_state()`/`.open_enclosure()`/
    `.close_enclosure()` -> `hardware_operations` -> `EnclosureDriver` ->
    `IndiEnclosureDriver` -> `SimulatorIndiInterface` (M6). No dome/roof
    device is simulated today, so `get_enclosure_state()` honestly
    reports `UNKNOWN` (the "Unknown Is Unsafe" invariant) rather than a
    fabricated open/closed value.
    """
    from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface
    from wayfindinglib.models.equipment_and_site.enclosure import EnclosureState

    control.driver = SimulatorIndiInterface(config=control._config)

    assert control.get_enclosure_state() == EnclosureState.UNKNOWN


def test_enclosure_commands_refused_when_not_authoritative(control):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify open_enclosure/close_enclosure fail closed by default."""
    from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface
    from wayfindinglib.exceptions import AstrometryHardwareError

    control.driver = SimulatorIndiInterface(config=control._config)

    with pytest.raises(AstrometryHardwareError, match="OBSERVATORY_SAFETY"):
        control.open_enclosure()
    with pytest.raises(AstrometryHardwareError, match="OBSERVATORY_SAFETY"):
        control.close_enclosure()


def test_close_enclosure_refuses_when_mount_outside_clearance(control, mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify close_enclosure refuses rather than closing on the mount.

    Plan Verification (M6): asserts refusal, not just the happy path --
    a mount position far from the configured park position must reject
    the close through the real `ObservatoryControl.close_enclosure` ->
    `hardware_operations.close_enclosure` ->
    `enclosure_control.can_close_enclosure` interlock chain, not just
    the unit-level fakes in `test_hardware_operations.py`. The mount's
    reported position is stubbed directly (rather than driven through
    the simulator's real slew physics) so the test asserts the
    interlock wiring, not incidental simulator behavior.
    """
    from wayfindinglib.drivers.protocols.mount_driver import MountStatus
    from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface
    from wayfindinglib.exceptions import AstrometryHardwareError
    from wayfindinglib.models.equipment_and_site.enclosure import Enclosure, EnclosureType
    from wayfindinglib.models.policy.delegation import CapabilityDelegation, DelegationPolicy

    control.driver = SimulatorIndiInterface(config=control._config)
    stub_mount_driver = mocker.Mock()
    stub_mount_driver.get_status = mocker.AsyncMock(
        return_value=MountStatus(
            ra="00:00:00",
            dec="+00:00:00",
            altitude="0° 0′ 0″",
            azimuth="0° 0′ 0″",
            trackingStatus="Tracking",
        )
    )
    control.mount_driver = stub_mount_driver
    control._butler.put(
        DelegationPolicy(
            id="default",
            capability_delegations=[
                CapabilityDelegation(
                    capability=ObservatoryCapability.OBSERVATORY_SAFETY,
                    state=DelegationState.AUTHORITATIVE,
                ),
            ],
        ),
        "delegation_policy",
        {"id": "default"},
    )
    control._butler.put(
        Enclosure(
            id="enc-1",
            enclosure_type=EnclosureType.ROLL_OFF_ROOF,
            park_azimuth_deg=180.0,
            park_altitude_deg=45.0,
            clearance_tolerance_deg=2.0,
        ),
        "enclosure",
        {"id": "enc-1"},
    )

    with pytest.raises(AstrometryHardwareError, match="clearance"):
        control.close_enclosure()
