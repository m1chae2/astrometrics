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

import pytest

from wayfindinglib.exceptions import AstrometryHardwareError
from wayfindinglib.tasks.control_tasks import hardware_operations as ops


class _FakeStatus:
    def model_dump(self, by_alias=True):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        return {"ra": "10:00:00", "dec": "+20:00:00"}


class _FakeManager:
    def __init__(self, driver=None, config=None, guiding_service=None, sync_service=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.driver = driver
        self._config = config
        self._guiding_service = guiding_service
        self._sync_service = sync_service


def test_get_telescope_status_without_guiding_service(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify status is returned unmodified when no guiding service is set."""
    driver = mocker.Mock()
    driver.get_status.return_value = _FakeStatus()
    manager = _FakeManager(driver=driver)

    status = ops.get_telescope_status(manager)

    assert status == {"ra": "10:00:00", "dec": "+20:00:00"}


def test_get_telescope_status_adds_guiding_history(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify guidingHistory is merged in when a guiding service is present."""
    driver = mocker.Mock()
    driver.get_status.return_value = _FakeStatus()
    guiding_service = mocker.Mock()
    guiding_service.get_status.return_value = {"history": [{"time": 1.0}]}
    manager = _FakeManager(driver=driver, guiding_service=guiding_service)

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


def test_slew_to_coordinates_delegates_to_driver(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify slew_to_coordinates calls driver.slew with the given values."""
    driver = mocker.Mock()
    driver.slew.return_value = True
    manager = _FakeManager(driver=driver)

    assert ops.slew_to_coordinates(manager, 10.0, 20.0) is True
    driver.slew.assert_called_once_with(10.0, 20.0)


def test_park_and_unpark_delegate_to_driver(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify park/unpark delegate to the driver and return its result."""
    driver = mocker.Mock()
    driver.park.return_value = True
    driver.unpark.return_value = True
    manager = _FakeManager(driver=driver)

    assert ops.park(manager) is True
    assert ops.unpark(manager) is True


def test_set_tracking_delegates_to_driver(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify set_tracking passes the enabled flag through to the driver."""
    driver = mocker.Mock()
    driver.set_tracking.return_value = True
    manager = _FakeManager(driver=driver)

    assert ops.set_tracking(manager, True) is True
    driver.set_tracking.assert_called_once_with(True)


def test_set_filter_raises_for_unrecognized_filter(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an unrecognized filter name raises ValueError."""
    driver = mocker.Mock()
    driver.get_filter_names.return_value = ["Luminance", "Red"]
    driver.resolve_filter_name.return_value = None
    manager = _FakeManager(driver=driver)

    with pytest.raises(ValueError, match="not recognized"):
        ops.set_filter(manager, "Nonexistent")


def test_set_filter_raises_hardware_error_on_failed_command(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a hardware-level filter failure raises the hardware error."""
    driver = mocker.Mock()
    driver.get_filter_names.return_value = ["Luminance"]
    driver.resolve_filter_name.return_value = "Luminance"
    driver.set_filterwheel_position.return_value = False
    manager = _FakeManager(driver=driver)

    with pytest.raises(AstrometryHardwareError):
        ops.set_filter(manager, "Luminance")


def test_set_filter_succeeds_with_resolved_name(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a successful filter change returns True."""
    driver = mocker.Mock()
    driver.get_filter_names.return_value = ["Luminance"]
    driver.resolve_filter_name.return_value = "Luminance"
    driver.set_filterwheel_position.return_value = True
    manager = _FakeManager(driver=driver)

    assert ops.set_filter(manager, "lum") is True
    driver.set_filterwheel_position.assert_called_once_with("Luminance")


def test_get_indi_devices_uses_device_map_when_available(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify device names come from deviceMap when it is populated."""
    driver = mocker.Mock()
    driver.isServerConnected.return_value = True
    driver.deviceMap = {"Telescope Simulator": object(), "CCD Simulator": object()}
    manager = _FakeManager(driver=driver)

    devices = ops.get_indi_devices(manager)

    assert set(devices) == {"Telescope Simulator", "CCD Simulator"}
    driver.connect_to_server.assert_not_called()


def test_get_indi_devices_connects_when_server_not_connected(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the driver connects if the server isn't connected yet."""
    driver = mocker.Mock()
    driver.isServerConnected.return_value = False
    driver.deviceMap = {}
    driver.getDevices.return_value = []
    manager = _FakeManager(driver=driver)

    ops.get_indi_devices(manager)

    driver.connect_to_server.assert_called_once()


def test_manual_move_slew_rate_focus_delegate_to_driver(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify move/slew-rate/focus/get-position all delegate to the driver."""
    driver = mocker.Mock()
    driver.move.return_value = True
    driver.set_slew_rate.return_value = True
    driver.focus_move.return_value = True
    driver.get_focuser_position.return_value = 5000
    manager = _FakeManager(driver=driver)

    assert ops.manual_move(manager, "north", True) is True
    driver.move.assert_called_once_with("north", True)
    assert ops.set_slew_rate(manager, 3) is True
    driver.set_slew_rate.assert_called_once_with(3)
    assert ops.focus_move(manager, 100) is True
    driver.focus_move.assert_called_once_with(100)
    assert ops.get_focuser_position(manager) == 5000


def test_connect_ensures_connection_and_returns_true(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify connect() ensures the connection and always returns True."""
    driver = mocker.Mock()
    observatory = mocker.Mock(_driver=driver)

    assert ops.connect(observatory) is True
    driver._ensure_connection.assert_called_once()


def test_disconnect_calls_disconnect_server_and_returns_true(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify disconnect() calls disconnectServer and always returns True."""
    driver = mocker.Mock()
    observatory = mocker.Mock(_driver=driver)

    assert ops.disconnect(observatory) is True
    driver.disconnectServer.assert_called_once()


def test_indi_properties_ensures_connection_then_reads_properties(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify indi_properties ensures connection before reading properties."""
    driver = mocker.Mock()
    driver.get_device_properties.return_value = {"CONNECTION": {}}
    observatory = mocker.Mock(_driver=driver)

    result = ops.indi_properties(observatory, "Telescope Simulator")

    driver._ensure_connection.assert_called_once()
    assert result == {"CONNECTION": {}}


def test_set_indi_property_ensures_connection_then_sets(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify set_indi_property ensures connection before setting."""
    driver = mocker.Mock()
    driver.set_property.return_value = True
    observatory = mocker.Mock(_driver=driver)

    assert ops.set_indi_property(observatory, "Telescope Simulator", "CONNECTION", "On") is True
    driver._ensure_connection.assert_called_once()


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
    driver = mocker.Mock()
    driver.get_observer_location.return_value = None
    manager = _FakeManager(driver=driver)

    assert ops.get_observer_location(manager) is None


def test_get_observer_location_returns_dict_when_reported(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify get_observer_location maps the reported tuple to a dict."""
    driver = mocker.Mock()
    driver.get_observer_location.return_value = (39.7392, -104.9903, 1600.0)
    manager = _FakeManager(driver=driver)

    assert ops.get_observer_location(manager) == {
        "latitude": 39.7392,
        "longitude": -104.9903,
        "elevation": 1600.0,
    }
