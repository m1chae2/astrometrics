"""Purpose: Unit tests for the relocated INDI hardware operations.

Description: Verifies `tasks/control_tasks/hardware_operations.py`
behaves identically to the deprecated `observatorylib.hardware_operations`
it was mechanically relocated from (§2.5.11's "hardware operations
behave identically after relocation"). The original module had no
direct unit tests of its own -- it was exercised only indirectly via
`ObservatoryManager` -- so this suite is new coverage written against
the relocated module directly, using a duck-typed fake manager rather
than a real INDI connection.
"""

from typing import Any

import pytest

from wayfindinglib.exceptions import AstrometryHardwareError
from wayfindinglib.models.policy.delegation import (
    CapabilityDelegation,
    DelegationPolicy,
    DelegationState,
    ObservatoryCapability,
)
from wayfindinglib.tasks.control_tasks import hardware_operations as ops


def _authoritative_policy(*capabilities: ObservatoryCapability) -> DelegationPolicy:
    """Build a `DelegationPolicy` with the given capabilities `AUTHORITATIVE`.

    Returns
    -------
    policy : `DelegationPolicy`
        A policy with each of `capabilities` set `AUTHORITATIVE`.
    """
    return DelegationPolicy(
        id="test",
        capability_delegations=[
            CapabilityDelegation(capability=capability, state=DelegationState.AUTHORITATIVE)
            for capability in capabilities
        ],
    )


class _FakeManager:
    def __init__(  # ruff: ignore[missing-return-type-special-method]
        self,
        driver: Any = None,
        mount_driver: Any = None,
        focuser_driver: Any = None,
        filter_wheel_driver: Any = None,
        camera_driver: Any = None,
        guide_camera_driver: Any = None,
        config: Any = None,
        guiding_service: Any = None,
        sync_service: Any = None,
        policy: DelegationPolicy | None = None,
    ):
        self.driver = driver
        self.mount_driver = mount_driver
        self.focuser_driver = focuser_driver
        self.filter_wheel_driver = filter_wheel_driver
        self.camera_driver = camera_driver
        self.guide_camera_driver = guide_camera_driver
        self._config = config
        self._guiding_service = guiding_service
        self._sync_service = sync_service
        self._policy = (
            policy
            if policy is not None
            else _authoritative_policy(
                ObservatoryCapability.MOUNT_CONTROL,
                ObservatoryCapability.AUTOGUIDING,
                ObservatoryCapability.AUTOFOCUS,
                ObservatoryCapability.CAPTURE_ORCHESTRATION,
                ObservatoryCapability.PLATE_SOLVE_ALIGNMENT,
            )
        )

    def delegation_policy(self) -> DelegationPolicy:
        """Return this fake manager's configured `DelegationPolicy`.

        Returns
        -------
        policy : `DelegationPolicy`
            This fake manager's configured policy.
        """
        return self._policy


class _FakeMountStatus:
    """A stand-in for `MountStatus` with the fields the status read uses."""

    def __init__(  # ruff: ignore[missing-return-type-special-method]
        self,
        ra="10:00:00",  # ruff: ignore[missing-type-function-argument]
        dec="+20:00:00",  # ruff: ignore[missing-type-function-argument]
        altitude="45:00:00",  # ruff: ignore[missing-type-function-argument]
        azimuth="180:00:00",  # ruff: ignore[missing-type-function-argument]
        tracking_status="Tracking",  # ruff: ignore[missing-type-function-argument]
        connection_status="Connected",  # ruff: ignore[missing-type-function-argument]
        target_name=None,  # ruff: ignore[missing-type-function-argument]
    ):
        self.ra = ra
        self.dec = dec
        self.altitude = altitude
        self.azimuth = azimuth
        self.tracking_status = tracking_status
        self.connection_status = connection_status
        self.target_name = target_name


def _manager_for_status_reassembly(mocker, guiding_service=None, driver_status=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Build a `_FakeManager` with the four drivers a status read uses.

    Returns
    -------
    manager : `_FakeManager`
        A manager whose `mount_driver`/`filter_wheel_driver`/
        `focuser_driver`/`camera_driver` produce deterministic,
        known values.
    """
    driver = mocker.Mock()
    driver.status = driver_status if driver_status is not None else {}

    mount_driver = mocker.Mock()
    mount_driver.get_status = mocker.AsyncMock(return_value=_FakeMountStatus())

    filter_wheel_driver = mocker.Mock()
    filter_wheel_driver.get_current_filter = mocker.AsyncMock(return_value="Luminance")

    focuser_driver = mocker.Mock()
    focuser_driver.get_position = mocker.AsyncMock(return_value=12345)

    camera_driver = mocker.Mock()
    camera_driver.get_sensor_temperature_c = mocker.AsyncMock(return_value=-10.0)

    return _FakeManager(
        driver=driver,
        mount_driver=mount_driver,
        filter_wheel_driver=filter_wheel_driver,
        focuser_driver=focuser_driver,
        camera_driver=camera_driver,
        guiding_service=guiding_service,
    )


def test_get_telescope_status_without_guiding_service(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify status is reassembled from the four driver calls."""
    manager = _manager_for_status_reassembly(
        mocker, driver_status={"TEMPERATURE": "15.0°C", "HUMIDITY": "40.0%", "CAMERA_STATUS": "Idle"}
    )

    status = ops.get_telescope_status(manager)

    assert status == {
        "ra": "10:00:00",
        "dec": "+20:00:00",
        "altitude": "45:00:00",
        "azimuth": "180:00:00",
        "temperature": "15.0°C",
        "humidity": "40.0%",
        "trackingStatus": "Tracking",
        "connectionStatus": "Connected",
        "focuserPosition": 12345,
        "filter": "Luminance",
        "guidingHistory": [],
        "cameraTemperature": "-10.0°C",
        "cameraStatus": "Idle",
        "targetName": None,
    }


def test_get_telescope_status_adds_guiding_history(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify guidingHistory is merged in when a guiding service is present."""
    guiding_service = mocker.Mock()
    guiding_service.get_status.return_value = {"history": [{"time": 1.0}]}
    manager = _manager_for_status_reassembly(mocker, guiding_service=guiding_service)

    status = ops.get_telescope_status(manager)

    guiding_service.poll_external_telemetry.assert_called_once()
    assert status["guidingHistory"] == [{"time": 1.0}]


def test_slew_to_target_raises_for_unknown_target(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an unrecognized target raises ValueError."""
    mocker.patch("astrometricslib.Astrometrics").return_value.targets.get.return_value = None
    manager = _FakeManager(config=mocker.Mock())

    with pytest.raises(ValueError, match="not found"):
        ops.slew_to_target(manager, "does-not-exist")


def test_slew_to_target_raises_for_unresolved_placeholder_coordinates(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a target with placeholder (unsolved) coordinates raises."""
    target = mocker.Mock(ra="0h 0m 0s", dec="0d 0m 0s")
    mocker.patch("astrometricslib.Astrometrics").return_value.targets.get.return_value = target
    manager = _FakeManager(config=mocker.Mock())

    with pytest.raises(ValueError, match="hasn't been plate-solved"):
        ops.slew_to_target(manager, "M 81")


def test_slew_to_target_resolves_coordinates_and_delegates(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a valid target resolves coordinates and delegates the slew."""
    target = mocker.Mock(ra="12h 00m 00s", dec="+45d 00m 00s")
    mocker.patch("astrometricslib.Astrometrics").return_value.targets.get.return_value = target
    manager = _FakeManager(config=mocker.Mock())
    manager.slew_to_coordinates = mocker.Mock(return_value=True)

    result = ops.slew_to_target(manager, "M 81")

    assert result is True
    manager.slew_to_coordinates.assert_called_once()
    called_ra, called_dec = manager.slew_to_coordinates.call_args[0]
    assert called_ra == pytest.approx(12.0, abs=1e-3)
    assert called_dec == pytest.approx(45.0, abs=1e-3)


def test_slew_to_coordinates_delegates_to_mount_driver(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify slew_to_coordinates calls mount_driver.slew with the values."""
    mount_driver = mocker.Mock()
    mount_driver.slew = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(mount_driver=mount_driver)

    assert ops.slew_to_coordinates(manager, 10.0, 20.0) is True
    mount_driver.slew.assert_called_once_with(10.0, 20.0)


def test_slew_to_coordinates_raises_when_mount_control_not_authoritative(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a command is refused when MOUNT_CONTROL is not AUTHORITATIVE."""
    mount_driver = mocker.Mock()
    mount_driver.slew = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(mount_driver=mount_driver, policy=_authoritative_policy())

    with pytest.raises(AstrometryHardwareError, match="MOUNT_CONTROL"):
        ops.slew_to_coordinates(manager, 10.0, 20.0)
    mount_driver.slew.assert_not_called()


def test_park_and_unpark_delegate_to_mount_driver(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify park/unpark delegate to the mount driver.

    Each should return the driver's own result.
    """
    mount_driver = mocker.Mock()
    mount_driver.park = mocker.AsyncMock(return_value=True)
    mount_driver.unpark = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(mount_driver=mount_driver)

    assert ops.park(manager) is True
    assert ops.unpark(manager) is True


def test_set_tracking_delegates_to_mount_driver(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify set_tracking passes the enabled flag to the mount driver."""
    mount_driver = mocker.Mock()
    mount_driver.set_tracking = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(mount_driver=mount_driver)

    assert ops.set_tracking(manager, True) is True
    mount_driver.set_tracking.assert_called_once_with(True)


def test_abort_motion_delegates_to_mount_driver(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify abort_motion delegates to the mount driver."""
    mount_driver = mocker.Mock()
    mount_driver.abort_motion = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(mount_driver=mount_driver)

    assert ops.abort_motion(manager) is True
    mount_driver.abort_motion.assert_called_once()


def test_pulse_guide_delegates_to_mount_driver(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify pulse_guide delegates to the mount driver."""
    mount_driver = mocker.Mock()
    mount_driver.pulse_guide = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(mount_driver=mount_driver)

    assert ops.pulse_guide(manager, "N", 250.0) is True
    mount_driver.pulse_guide.assert_called_once_with("N", 250.0)


def test_pulse_guide_raises_when_autoguiding_not_authoritative(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify pulse_guide is gated on AUTOGUIDING, not MOUNT_CONTROL."""
    mount_driver = mocker.Mock()
    mount_driver.pulse_guide = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(
        mount_driver=mount_driver,
        policy=_authoritative_policy(ObservatoryCapability.MOUNT_CONTROL),
    )

    with pytest.raises(AstrometryHardwareError, match="AUTOGUIDING"):
        ops.pulse_guide(manager, "N", 250.0)
    mount_driver.pulse_guide.assert_not_called()


def test_sync_coordinates_delegates_to_mount_driver(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify sync_coordinates delegates to the mount driver."""
    mount_driver = mocker.Mock()
    mount_driver.sync = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(mount_driver=mount_driver)

    assert ops.sync_coordinates(manager, 10.0, 20.0) is True
    mount_driver.sync.assert_called_once_with(10.0, 20.0)


def test_sync_coordinates_raises_when_alignment_not_authoritative(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify sync_coordinates is gated on alignment, not mount control."""
    mount_driver = mocker.Mock()
    mount_driver.sync = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(
        mount_driver=mount_driver,
        policy=_authoritative_policy(ObservatoryCapability.MOUNT_CONTROL),
    )

    with pytest.raises(AstrometryHardwareError, match="PLATE_SOLVE_ALIGNMENT"):
        ops.sync_coordinates(manager, 10.0, 20.0)
    mount_driver.sync.assert_not_called()


def test_capture_image_delegates_to_camera_driver(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify capture_image delegates to the main-camera driver."""
    camera_driver = mocker.Mock()
    camera_driver.expose = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(camera_driver=camera_driver)

    assert ops.capture_image(manager, 30.0) is True
    camera_driver.expose.assert_called_once_with(30.0)


def test_capture_image_raises_when_capture_orchestration_not_authoritative(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify capture_image is refused without orchestration authority."""
    camera_driver = mocker.Mock()
    camera_driver.expose = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(camera_driver=camera_driver, policy=_authoritative_policy())

    with pytest.raises(AstrometryHardwareError, match="CAPTURE_ORCHESTRATION"):
        ops.capture_image(manager, 30.0)
    camera_driver.expose.assert_not_called()


def test_guide_expose_delegates_to_guide_camera_driver(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify guide_expose delegates to the guide-camera driver."""
    guide_camera_driver = mocker.Mock()
    guide_camera_driver.expose = mocker.AsyncMock(return_value="blob")
    manager = _FakeManager(guide_camera_driver=guide_camera_driver)

    assert ops.guide_expose(manager, 1.5, gain=100) == "blob"
    guide_camera_driver.expose.assert_called_once_with(1.5, gain=100)


def test_guide_expose_raises_when_autoguiding_not_authoritative(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify guide_expose is refused without AUTOGUIDING authority."""
    guide_camera_driver = mocker.Mock()
    guide_camera_driver.expose = mocker.AsyncMock(return_value="blob")
    manager = _FakeManager(guide_camera_driver=guide_camera_driver, policy=_authoritative_policy())

    with pytest.raises(AstrometryHardwareError, match="AUTOGUIDING"):
        ops.guide_expose(manager, 1.5)
    guide_camera_driver.expose.assert_not_called()


def test_get_guide_image_is_a_read_with_no_authority_check(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify get_guide_image delegates without requiring authority."""
    guide_camera_driver = mocker.Mock()
    guide_camera_driver.get_last_image = mocker.AsyncMock(return_value="blob")
    manager = _FakeManager(guide_camera_driver=guide_camera_driver, policy=_authoritative_policy())

    assert ops.get_guide_image(manager) == "blob"


def test_set_filter_raises_for_unrecognized_filter(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an unrecognized filter name raises ValueError."""
    filter_wheel_driver = mocker.Mock()
    filter_wheel_driver.get_names = mocker.AsyncMock(return_value=["Luminance", "Red"])
    filter_wheel_driver.resolve_name = mocker.AsyncMock(return_value=None)
    manager = _FakeManager(filter_wheel_driver=filter_wheel_driver)

    with pytest.raises(ValueError, match="not recognized"):
        ops.set_filter(manager, "Nonexistent")


def test_set_filter_raises_hardware_error_on_failed_command(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a hardware-level filter failure raises the hardware error."""
    filter_wheel_driver = mocker.Mock()
    filter_wheel_driver.get_names = mocker.AsyncMock(return_value=["Luminance"])
    filter_wheel_driver.resolve_name = mocker.AsyncMock(return_value="Luminance")
    filter_wheel_driver.set_position = mocker.AsyncMock(return_value=False)
    manager = _FakeManager(filter_wheel_driver=filter_wheel_driver)

    with pytest.raises(AstrometryHardwareError):
        ops.set_filter(manager, "Luminance")


def test_set_filter_succeeds_with_resolved_name(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a successful filter change returns True."""
    filter_wheel_driver = mocker.Mock()
    filter_wheel_driver.get_names = mocker.AsyncMock(return_value=["Luminance"])
    filter_wheel_driver.resolve_name = mocker.AsyncMock(return_value="Luminance")
    filter_wheel_driver.set_position = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(filter_wheel_driver=filter_wheel_driver)

    assert ops.set_filter(manager, "lum") is True
    filter_wheel_driver.set_position.assert_called_once_with("Luminance")


def test_set_filter_raises_when_capture_orchestration_not_authoritative(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify set_filter is refused without CAPTURE_ORCHESTRATION authority."""
    filter_wheel_driver = mocker.Mock()
    filter_wheel_driver.set_position = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(filter_wheel_driver=filter_wheel_driver, policy=_authoritative_policy())

    with pytest.raises(AstrometryHardwareError, match="CAPTURE_ORCHESTRATION"):
        ops.set_filter(manager, "Luminance")
    filter_wheel_driver.set_position.assert_not_called()


def test_get_filter_names_delegates_to_filter_wheel_driver(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify get_filter_names is a read with no authority check."""
    filter_wheel_driver = mocker.Mock()
    filter_wheel_driver.get_names = mocker.AsyncMock(return_value=["Luminance", "Red"])
    manager = _FakeManager(filter_wheel_driver=filter_wheel_driver, policy=_authoritative_policy())

    assert ops.get_filter_names(manager) == ["Luminance", "Red"]


def test_manual_move_and_slew_rate_delegate_to_mount_driver(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify move/set-slew-rate delegate to the mount driver."""
    mount_driver = mocker.Mock()
    mount_driver.move = mocker.AsyncMock(return_value=True)
    mount_driver.set_slew_rate = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(mount_driver=mount_driver)

    assert ops.manual_move(manager, "north", True) is True
    mount_driver.move.assert_called_once_with("north", True)
    assert ops.set_slew_rate(manager, 3) is True
    mount_driver.set_slew_rate.assert_called_once_with(3)


def test_focus_move_delegates_to_focuser_driver(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify focus_move delegates to the focuser driver."""
    focuser_driver = mocker.Mock()
    focuser_driver.move_relative = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(focuser_driver=focuser_driver)

    assert ops.focus_move(manager, 100) is True
    focuser_driver.move_relative.assert_called_once_with(100)


def test_focus_move_raises_when_autofocus_not_authoritative(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify focus_move is refused when AUTOFOCUS isn't authoritative."""
    focuser_driver = mocker.Mock()
    focuser_driver.move_relative = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(focuser_driver=focuser_driver, policy=_authoritative_policy())

    with pytest.raises(AstrometryHardwareError, match="AUTOFOCUS"):
        ops.focus_move(manager, 100)
    focuser_driver.move_relative.assert_not_called()


def test_get_focuser_position_is_a_read_with_no_authority_check(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify get_focuser_position delegates without requiring authority."""
    focuser_driver = mocker.Mock()
    focuser_driver.get_position = mocker.AsyncMock(return_value=5000)
    manager = _FakeManager(focuser_driver=focuser_driver, policy=_authoritative_policy())

    assert ops.get_focuser_position(manager) == 5000


def _manager_with_all_drivers(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Build a `_FakeManager` with all five connect/disconnect-capable drivers.

    Returns
    -------
    manager : `_FakeManager`
        A manager whose `mount_driver`/`focuser_driver`/
        `filter_wheel_driver`/`camera_driver`/`guide_camera_driver`
        each have mocked async `connect`/`disconnect` methods.
    """
    drivers = {
        name: mocker.Mock(
            connect=mocker.AsyncMock(return_value=True), disconnect=mocker.AsyncMock(return_value=True)
        )
        for name in ("mount_driver", "focuser_driver", "filter_wheel_driver", "camera_driver")
    }
    guide_camera_driver = mocker.Mock(
        connect=mocker.AsyncMock(return_value=True), disconnect=mocker.AsyncMock(return_value=True)
    )
    return _FakeManager(guide_camera_driver=guide_camera_driver, **drivers), guide_camera_driver


def test_connect_connects_every_configured_driver(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify connect() connects each of the five drivers and returns True."""
    manager, guide_camera_driver = _manager_with_all_drivers(mocker)

    assert ops.connect(manager) is True
    manager.mount_driver.connect.assert_called_once()
    manager.focuser_driver.connect.assert_called_once()
    manager.filter_wheel_driver.connect.assert_called_once()
    manager.camera_driver.connect.assert_called_once()
    guide_camera_driver.connect.assert_called_once()


def test_disconnect_disconnects_every_configured_driver(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify disconnect() disconnects each of the five drivers."""
    manager, guide_camera_driver = _manager_with_all_drivers(mocker)

    assert ops.disconnect(manager) is True
    manager.mount_driver.disconnect.assert_called_once()
    manager.focuser_driver.disconnect.assert_called_once()
    manager.filter_wheel_driver.disconnect.assert_called_once()
    manager.camera_driver.disconnect.assert_called_once()
    guide_camera_driver.disconnect.assert_called_once()


def test_sync_raises_without_sync_service():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify sync raises RuntimeError in standalone mode (no sync service)."""
    observatory = _FakeManager(sync_service=None)
    with pytest.raises(RuntimeError, match="standalone mode"):
        ops.sync(observatory, "M 81")


def test_sync_delegates_to_sync_service(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify sync starts a sync task through the configured sync service."""
    sync_service = mocker.Mock()
    sync_service.start_sync.return_value = {"status": "started"}
    observatory = _FakeManager(sync_service=sync_service)

    assert ops.sync(observatory, "M 81") == {"status": "started"}
    sync_service.start_sync.assert_called_once_with("M 81")


def test_is_syncing_raises_without_sync_service():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify is_syncing raises RuntimeError in standalone mode."""
    observatory = _FakeManager(sync_service=None)
    with pytest.raises(RuntimeError, match="standalone mode"):
        ops.is_syncing(observatory, "M 81")


def test_get_observer_location_returns_none_when_unreported(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify get_observer_location returns None when nothing is reported."""
    mount_driver = mocker.Mock()
    mount_driver.get_observer_location = mocker.AsyncMock(return_value=None)
    manager = _FakeManager(mount_driver=mount_driver)

    assert ops.get_observer_location(manager) is None


def test_get_observer_location_returns_dict_when_reported(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify get_observer_location returns the mount driver's dict."""
    mount_driver = mocker.Mock()
    mount_driver.get_observer_location = mocker.AsyncMock(
        return_value={"latitude": 39.7392, "longitude": -104.9903, "elevation": 1600.0}
    )
    manager = _FakeManager(mount_driver=mount_driver)

    assert ops.get_observer_location(manager) == {
        "latitude": 39.7392,
        "longitude": -104.9903,
        "elevation": 1600.0,
    }
