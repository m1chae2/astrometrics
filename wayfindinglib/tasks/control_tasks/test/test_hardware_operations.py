"""Purpose: Unit tests for the hardware operations behind `control`.

Description: Verifies `tasks/control_tasks/hardware_operations.py` against
a duck-typed fake `ControlContext` rather than a real INDI connection:
each command checks its capability in the delegation policy, then calls
the right driver; each read calls its driver with no check.
"""

import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from astropy.time import Time
from pytest_mock import MockerFixture

from astrometricslib import (
    ConfigurationError,
    ConflictError,
    HardwareError,
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
)
from wayfindinglib.astronomy.coordinate_transforms import icrs_to_current_epoch
from wayfindinglib.data_access.site_profile_reader import configured_observer_location
from wayfindinglib.models.equipment_and_site.enclosure import Enclosure, EnclosureState, EnclosureType
from wayfindinglib.models.policy.delegation import (
    CapabilityDelegation,
    DelegationPolicy,
    DelegationState,
    ObservatoryCapability,
)
from wayfindinglib.models.sky_position import SkyPosition
from wayfindinglib.tasks.control_tasks import hardware_operations as ops


def _assert_sent_in_current_epoch(driver_call: Any, ra_deg: float, dec_deg: float) -> None:
    """Check a driver call got the current-epoch (JNow) form of a position.

    Parameters
    ----------
    driver_call : `Any`
        The mocked driver method (``slew`` or ``sync``), called once.
    ra_deg, dec_deg : `float`
        The ICRS position the caller asked for, in degrees.
    """
    expected_ra_deg, expected_dec_deg = icrs_to_current_epoch(ra_deg, dec_deg, Time.now())
    (sent_ra_hours, sent_dec_deg), _keywords = driver_call.call_args
    assert sent_ra_hours == pytest.approx(expected_ra_deg / 15.0, abs=1e-5)
    assert sent_dec_deg == pytest.approx(expected_dec_deg, abs=1e-4)
    # Precession since 2000 moves the position by roughly 0.3 degrees.
    assert abs(sent_ra_hours * 15.0 - ra_deg) > 0.1


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
    """A stand-in for `ControlContext` with only what the operations use."""

    def __init__(
        self,
        driver: Any = None,
        mount_driver: Any = None,
        focuser_driver: Any = None,
        filter_wheel_driver: Any = None,
        camera_driver: Any = None,
        guide_camera_driver: Any = None,
        enclosure_driver: Any = None,
        enclosure: Any = None,
        config: Any = None,
        policy: DelegationPolicy | None = None,
        astrometrics: Any = None,
    ) -> None:
        """Hold the given drivers, configuration and policy."""
        self.motion_stop = threading.Event()
        self.astrometrics = astrometrics if astrometrics is not None else MagicMock()
        self.driver = driver
        self.mount_driver = mount_driver
        self.focuser_driver = focuser_driver
        self.filter_wheel_driver = filter_wheel_driver
        self.camera_driver = camera_driver
        self.guide_camera_driver = guide_camera_driver
        self.enclosure_driver = enclosure_driver
        self._enclosure = enclosure
        self.config = config
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

    def active_enclosure(self) -> Any:
        """Return this fake manager's configured `Enclosure`.

        Returns
        -------
        enclosure : `Any`
            This fake manager's configured enclosure, or `None`.
        """
        return self._enclosure


class _FakeMountStatus:
    """A stand-in for `MountStatus` with the fields the status read uses."""

    def __init__(
        self,
        ra: str = "10:00:00",
        dec: str = "+20:00:00",
        altitude: str = "45:00:00",
        azimuth: str = "180:00:00",
        tracking_status: str = "Tracking",
        connection_status: str = "Connected",
        target_name: str | None = None,
        pier_side: str | None = "WEST",
        parked: bool | None = False,
        track_mode: str | None = "SIDEREAL",
    ) -> None:
        """Hold the mount status fields."""
        self.ra = ra
        self.dec = dec
        self.altitude = altitude
        self.azimuth = azimuth
        self.tracking_status = tracking_status
        self.connection_status = connection_status
        self.target_name = target_name
        self.pier_side = pier_side
        self.parked = parked
        self.track_mode = track_mode


def _manager_for_status_reassembly(
    mocker: MockerFixture, driver_status: dict[str, str] | None = None
) -> _FakeManager:
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
    )


def test_mount_status_reassembles_the_four_driver_reads(mocker: MockerFixture) -> None:
    """Verify status is reassembled from the four driver calls."""
    manager = _manager_for_status_reassembly(
        mocker, driver_status={"TEMPERATURE": "15.0°C", "HUMIDITY": "40.0%", "CAMERA_STATUS": "Idle"}
    )

    status = ops.mount_status(manager)

    ra_text = status.pop("ra")
    dec_text = status.pop("dec")
    assert ra_text.endswith("s") and "h " in ra_text
    assert dec_text.startswith("+") and dec_text.endswith("″")
    assert status == {
        "altitude": "45:00:00",
        "azimuth": "180:00:00",
        "temperature": "15.0°C",
        "humidity": "40.0%",
        "trackingStatus": "Tracking",
        "connectionStatus": "Connected",
        "focuserPosition": 12345,
        "filter": "Luminance",
        "cameraTemperature": "-10.0°C",
        "cameraStatus": "Idle",
        "targetName": None,
        "pierSide": "WEST",
        "parked": False,
        "trackMode": "SIDEREAL",
    }


def test_mount_status_reads_only_the_sections_asked_for(mocker: MockerFixture) -> None:
    """Verify `include` limits the device reads and rejects unknown names."""
    manager = _manager_for_status_reassembly(mocker)

    status = ops.mount_status(manager, include=["focuser"])

    assert status == {"focuserPosition": 12345}
    manager.mount_driver.get_status.assert_not_called()
    with pytest.raises(InvalidArgumentError, match="Unknown section"):
        ops.mount_status(manager, include=["weather"])


def test_mount_status_reports_the_mount_position_in_icrs(mocker: MockerFixture) -> None:
    """Verify the mount's JNow reading comes back as the ICRS position.

    The fake mount reports Betelgeuse's current-epoch position, the way
    an INDI mount does; the status read must give the catalog (J2000)
    position, about 0.36 degrees away.
    """
    catalog_ra_deg, catalog_dec_deg = 88.79294, 7.40706
    current_ra_deg, current_dec_deg = icrs_to_current_epoch(catalog_ra_deg, catalog_dec_deg, Time.now())
    manager = _manager_for_status_reassembly(mocker)
    manager.mount_driver.get_status = mocker.AsyncMock(
        return_value=_FakeMountStatus(ra=f"{current_ra_deg / 15.0:.8f}", dec=f"{current_dec_deg:.8f}")
    )

    status = ops.mount_status(manager, include=["mount"])

    from astrometricslib import parse_coordinate_string

    assert parse_coordinate_string(status["ra"], is_ra=True) == pytest.approx(catalog_ra_deg, abs=2e-4)
    assert parse_coordinate_string(status["dec"], is_ra=False) == pytest.approx(catalog_dec_deg, abs=2e-4)


def test_mount_position_to_icrs_reads_indi_text_and_passes_unknown_through() -> None:
    """Verify INDI display text is read and an unknown position is kept."""
    obstime = Time("2026-10-07T02:15:00", scale="utc")
    current_ra_deg, current_dec_deg = icrs_to_current_epoch(101.28716, -16.71612, obstime)
    ra_hours = current_ra_deg / 15.0
    whole_hours, minutes = int(ra_hours), int((ra_hours % 1) * 60)
    seconds = ((ra_hours * 60) % 1) * 60
    whole_deg, dec_minutes = int(abs(current_dec_deg)), int((abs(current_dec_deg) % 1) * 60)
    dec_seconds = ((abs(current_dec_deg) * 60) % 1) * 60

    ra_text, dec_text = ops.mount_position_to_icrs(
        f"{whole_hours}° {minutes}′ {seconds:.4f}″",
        f"-{whole_deg}° {dec_minutes}′ {dec_seconds:.4f}″",
        obstime,
    )

    assert ra_text == "6h 45m 08.92s"
    assert dec_text == "-16° 42′ 58.0″"
    assert ops.mount_position_to_icrs("Unknown", "Unknown", obstime) == ("Unknown", "Unknown")


def test_resolve_destination_raises_for_unknown_target(mocker: MockerFixture) -> None:
    """Verify an unrecognized target raises NotFoundError."""
    manager = _FakeManager(config=mocker.Mock())
    manager.astrometrics.targets.get.return_value = None

    with pytest.raises(NotFoundError, match="not found"):
        ops.resolve_destination(manager, "does-not-exist")


def test_resolve_destination_raises_for_unresolved_placeholder_coordinates(mocker: MockerFixture) -> None:
    """Verify a target with placeholder (unsolved) coordinates raises."""
    target = mocker.Mock(id="M 81", ra="0h 0m 0s", dec="0d 0m 0s")
    manager = _FakeManager(config=mocker.Mock())
    manager.astrometrics.targets.get.return_value = target

    with pytest.raises(InvalidArgumentError, match="hasn't been plate-solved"):
        ops.resolve_destination(manager, "M 81")


def test_resolve_destination_reads_a_target_s_coordinates(mocker: MockerFixture) -> None:
    """Verify a target id becomes its plate-solved position in degrees."""
    target = mocker.Mock(ra="12h 00m 00s", dec="+45d 00m 00s")
    manager = _FakeManager(config=mocker.Mock())
    manager.astrometrics.targets.get.return_value = target

    position = ops.resolve_destination(manager, "M 81")

    assert position.ra_deg == pytest.approx(180.0, abs=1e-3)
    assert position.dec_deg == pytest.approx(45.0, abs=1e-3)


def test_resolve_destination_accepts_a_position_or_its_dictionary() -> None:
    """Verify a `SkyPosition`, or its JSON form, passes through unchanged."""
    position = SkyPosition(ra_deg=10.0, dec_deg=20.0)
    manager = _FakeManager()

    assert ops.resolve_destination(manager, position) is position
    assert ops.resolve_destination(manager, {"ra_deg": 10.0, "dec_deg": 20.0}) == position


def test_slew_delegates_to_mount_driver(mocker: MockerFixture) -> None:
    """Verify slew sends the JNow position, RA in hours and Dec in degrees."""
    mount_driver = mocker.Mock()
    mount_driver.slew = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(mount_driver=mount_driver)

    assert ops.slew(manager, SkyPosition(ra_deg=150.0, dec_deg=20.0)) is True
    mount_driver.slew.assert_called_once()
    _assert_sent_in_current_epoch(mount_driver.slew, 150.0, 20.0)


def test_slew_raises_when_mount_control_not_authoritative(mocker: MockerFixture) -> None:
    """Verify a command is refused when MOUNT_CONTROL is not AUTHORITATIVE."""
    mount_driver = mocker.Mock()
    mount_driver.slew = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(mount_driver=mount_driver, policy=_authoritative_policy())

    with pytest.raises(PermissionDeniedError, match="MOUNT_CONTROL"):
        ops.slew(manager, SkyPosition(ra_deg=150.0, dec_deg=20.0))
    mount_driver.slew.assert_not_called()


def test_park_and_unpark_delegate_to_mount_driver(mocker: MockerFixture) -> None:
    """Verify park/unpark delegate to the mount driver.

    Each should return the driver's own result.
    """
    mount_driver = mocker.Mock()
    mount_driver.park = mocker.AsyncMock(return_value=True)
    mount_driver.unpark = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(mount_driver=mount_driver)

    assert ops.park(manager) is True
    assert ops.unpark(manager) is True


def test_set_tracking_delegates_to_mount_driver(mocker: MockerFixture) -> None:
    """Verify set_tracking passes the enabled flag to the mount driver."""
    mount_driver = mocker.Mock()
    mount_driver.set_tracking = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(mount_driver=mount_driver)

    assert ops.set_tracking(manager, True) is True
    mount_driver.set_tracking.assert_called_once_with(True)


def test_abort_motion_delegates_to_mount_driver(mocker: MockerFixture) -> None:
    """Verify abort_motion delegates to the mount driver."""
    mount_driver = mocker.Mock()
    mount_driver.abort_motion = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(mount_driver=mount_driver)

    assert ops.abort_motion(manager) is True
    mount_driver.abort_motion.assert_called_once()
    assert manager.motion_stop.is_set(), "abort must also stop a centering loop"


def test_pulse_delegates_to_mount_driver(mocker: MockerFixture) -> None:
    """Verify pulse delegates to the mount driver."""
    mount_driver = mocker.Mock()
    mount_driver.pulse_guide = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(mount_driver=mount_driver)

    assert ops.pulse(manager, "N", 250.0) is True
    mount_driver.pulse_guide.assert_called_once_with("N", 250.0)


def test_pulse_turns_direction_names_into_the_drivers_initials(mocker: MockerFixture) -> None:
    """Verify "west" reaches the driver as "W"; a bad direction is refused."""
    mount_driver = mocker.Mock()
    mount_driver.pulse_guide = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(mount_driver=mount_driver)

    assert ops.pulse(manager, "west", 100.0) is True
    mount_driver.pulse_guide.assert_called_once_with("W", 100.0)
    with pytest.raises(InvalidArgumentError):
        ops.pulse(manager, "up", 100.0)


def test_pulse_raises_when_autoguiding_not_authoritative(mocker: MockerFixture) -> None:
    """Verify pulse is gated on AUTOGUIDING, not MOUNT_CONTROL."""
    mount_driver = mocker.Mock()
    mount_driver.pulse_guide = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(
        mount_driver=mount_driver,
        policy=_authoritative_policy(ObservatoryCapability.MOUNT_CONTROL),
    )

    with pytest.raises(PermissionDeniedError, match="AUTOGUIDING"):
        ops.pulse(manager, "N", 250.0)
    mount_driver.pulse_guide.assert_not_called()


def test_sync_mount_delegates_to_mount_driver(mocker: MockerFixture) -> None:
    """Verify sync_mount sends the JNow position (RA hours, Dec degrees)."""
    mount_driver = mocker.Mock()
    mount_driver.sync = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(mount_driver=mount_driver)

    assert ops.sync_mount(manager, SkyPosition(ra_deg=150.0, dec_deg=20.0)) is True
    mount_driver.sync.assert_called_once()
    _assert_sent_in_current_epoch(mount_driver.sync, 150.0, 20.0)


def test_sync_mount_raises_when_alignment_not_authoritative(mocker: MockerFixture) -> None:
    """Verify sync_mount is gated on alignment, not mount control."""
    mount_driver = mocker.Mock()
    mount_driver.sync = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(
        mount_driver=mount_driver,
        policy=_authoritative_policy(ObservatoryCapability.MOUNT_CONTROL),
    )

    with pytest.raises(PermissionDeniedError, match="PLATE_SOLVE_ALIGNMENT"):
        ops.sync_mount(manager, SkyPosition(ra_deg=150.0, dec_deg=20.0))
    mount_driver.sync.assert_not_called()


def test_capture_image_delegates_to_camera_driver(mocker: MockerFixture) -> None:
    """Verify capture_image delegates to the main-camera driver."""
    camera_driver = mocker.Mock()
    camera_driver.expose = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(camera_driver=camera_driver)

    assert ops.capture_image(manager, 30.0) is True
    camera_driver.expose.assert_called_once_with(30.0)


def test_capture_image_raises_when_capture_orchestration_not_authoritative(mocker: MockerFixture) -> None:
    """Verify capture_image is refused without orchestration authority."""
    camera_driver = mocker.Mock()
    camera_driver.expose = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(camera_driver=camera_driver, policy=_authoritative_policy())

    with pytest.raises(PermissionDeniedError, match="CAPTURE_ORCHESTRATION"):
        ops.capture_image(manager, 30.0)
    camera_driver.expose.assert_not_called()


def test_guide_expose_delegates_to_guide_camera_driver(mocker: MockerFixture) -> None:
    """Verify guide_expose delegates to the guide-camera driver."""
    guide_camera_driver = mocker.Mock()
    guide_camera_driver.expose = mocker.AsyncMock(return_value="blob")
    manager = _FakeManager(guide_camera_driver=guide_camera_driver)

    assert ops.guide_expose(manager, 1.5, gain=100) == "blob"
    guide_camera_driver.expose.assert_called_once_with(1.5, gain=100)


def test_guide_expose_raises_when_autoguiding_not_authoritative(mocker: MockerFixture) -> None:
    """Verify guide_expose is refused without AUTOGUIDING authority."""
    guide_camera_driver = mocker.Mock()
    guide_camera_driver.expose = mocker.AsyncMock(return_value="blob")
    manager = _FakeManager(guide_camera_driver=guide_camera_driver, policy=_authoritative_policy())

    with pytest.raises(PermissionDeniedError, match="AUTOGUIDING"):
        ops.guide_expose(manager, 1.5)
    guide_camera_driver.expose.assert_not_called()


def test_guide_image_is_a_read_with_no_authority_check(mocker: MockerFixture) -> None:
    """Verify guide_image delegates without requiring authority."""
    guide_camera_driver = mocker.Mock()
    guide_camera_driver.get_last_image = mocker.AsyncMock(return_value="blob")
    manager = _FakeManager(guide_camera_driver=guide_camera_driver, policy=_authoritative_policy())

    assert ops.guide_image(manager) == "blob"


def test_set_filter_raises_for_unrecognized_filter(mocker: MockerFixture) -> None:
    """Verify an unrecognized filter name raises NotFoundError."""
    filter_wheel_driver = mocker.Mock()
    filter_wheel_driver.get_names = mocker.AsyncMock(return_value=["Luminance", "Red"])
    filter_wheel_driver.resolve_name = mocker.AsyncMock(return_value=None)
    manager = _FakeManager(filter_wheel_driver=filter_wheel_driver)

    with pytest.raises(NotFoundError, match="not recognized"):
        ops.set_filter(manager, "Nonexistent")


def test_set_filter_raises_hardware_error_on_failed_command(mocker: MockerFixture) -> None:
    """Verify a hardware-level filter failure raises the hardware error."""
    filter_wheel_driver = mocker.Mock()
    filter_wheel_driver.get_names = mocker.AsyncMock(return_value=["Luminance"])
    filter_wheel_driver.resolve_name = mocker.AsyncMock(return_value="Luminance")
    filter_wheel_driver.set_position = mocker.AsyncMock(return_value=False)
    manager = _FakeManager(filter_wheel_driver=filter_wheel_driver)

    with pytest.raises(HardwareError):
        ops.set_filter(manager, "Luminance")


def test_set_filter_succeeds_with_resolved_name(mocker: MockerFixture) -> None:
    """Verify a successful filter change returns True."""
    filter_wheel_driver = mocker.Mock()
    filter_wheel_driver.get_names = mocker.AsyncMock(return_value=["Luminance"])
    filter_wheel_driver.resolve_name = mocker.AsyncMock(return_value="Luminance")
    filter_wheel_driver.set_position = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(filter_wheel_driver=filter_wheel_driver)

    assert ops.set_filter(manager, "lum") is True
    filter_wheel_driver.set_position.assert_called_once_with("Luminance")


def test_set_filter_raises_when_capture_orchestration_not_authoritative(mocker: MockerFixture) -> None:
    """Verify set_filter is refused without CAPTURE_ORCHESTRATION authority."""
    filter_wheel_driver = mocker.Mock()
    filter_wheel_driver.set_position = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(filter_wheel_driver=filter_wheel_driver, policy=_authoritative_policy())

    with pytest.raises(PermissionDeniedError, match="CAPTURE_ORCHESTRATION"):
        ops.set_filter(manager, "Luminance")
    filter_wheel_driver.set_position.assert_not_called()


def test_filter_names_delegates_to_filter_wheel_driver(mocker: MockerFixture) -> None:
    """Verify filter_names is a read with no authority check."""
    filter_wheel_driver = mocker.Mock()
    filter_wheel_driver.get_names = mocker.AsyncMock(return_value=["Luminance", "Red"])
    manager = _FakeManager(filter_wheel_driver=filter_wheel_driver, policy=_authoritative_policy())

    assert ops.filter_names(manager) == ["Luminance", "Red"]


def test_manual_move_and_slew_rate_delegate_to_mount_driver(mocker: MockerFixture) -> None:
    """Verify move/set-slew-rate delegate to the mount driver."""
    mount_driver = mocker.Mock()
    mount_driver.move = mocker.AsyncMock(return_value=True)
    mount_driver.set_slew_rate = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(mount_driver=mount_driver)

    assert ops.manual_move(manager, "north", True) is True
    mount_driver.move.assert_called_once_with("north", True)
    assert ops.set_slew_rate(manager, 3) is True
    mount_driver.set_slew_rate.assert_called_once_with(3)


def test_focus_move_delegates_to_focuser_driver(mocker: MockerFixture) -> None:
    """Verify focus_move delegates to the focuser driver."""
    focuser_driver = mocker.Mock()
    focuser_driver.move_relative = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(focuser_driver=focuser_driver)

    assert ops.focus_move(manager, 100) is True
    focuser_driver.move_relative.assert_called_once_with(100)


def test_focus_move_raises_when_autofocus_not_authoritative(mocker: MockerFixture) -> None:
    """Verify focus_move is refused when AUTOFOCUS isn't authoritative."""
    focuser_driver = mocker.Mock()
    focuser_driver.move_relative = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(focuser_driver=focuser_driver, policy=_authoritative_policy())

    with pytest.raises(PermissionDeniedError, match="AUTOFOCUS"):
        ops.focus_move(manager, 100)
    focuser_driver.move_relative.assert_not_called()


def test_focuser_position_is_a_read_with_no_authority_check(mocker: MockerFixture) -> None:
    """Verify focuser_position delegates without requiring authority."""
    focuser_driver = mocker.Mock()
    focuser_driver.get_position = mocker.AsyncMock(return_value=5000)
    manager = _FakeManager(focuser_driver=focuser_driver, policy=_authoritative_policy())

    assert ops.focuser_position(manager) == 5000


def _manager_with_all_drivers(mocker: MockerFixture) -> tuple[_FakeManager, Any]:
    """Build a `_FakeManager` with all six connect/disconnect-capable drivers.

    Returns
    -------
    manager : `_FakeManager`
        A manager whose `mount_driver`/`focuser_driver`/
        `filter_wheel_driver`/`camera_driver`/`guide_camera_driver`/
        `enclosure_driver` each have mocked async `connect`/
        `disconnect` methods.
    """
    drivers = {
        name: mocker.Mock(
            connect=mocker.AsyncMock(return_value=True), disconnect=mocker.AsyncMock(return_value=True)
        )
        for name in (
            "mount_driver",
            "focuser_driver",
            "filter_wheel_driver",
            "camera_driver",
            "enclosure_driver",
        )
    }
    guide_camera_driver = mocker.Mock(
        connect=mocker.AsyncMock(return_value=True), disconnect=mocker.AsyncMock(return_value=True)
    )
    return _FakeManager(guide_camera_driver=guide_camera_driver, **drivers), guide_camera_driver


def test_connect_connects_every_configured_driver(mocker: MockerFixture) -> None:
    """Verify connect() connects each of the six drivers and returns True."""
    manager, guide_camera_driver = _manager_with_all_drivers(mocker)

    assert ops.connect(manager) is True
    manager.mount_driver.connect.assert_called_once()
    manager.focuser_driver.connect.assert_called_once()
    manager.filter_wheel_driver.connect.assert_called_once()
    manager.camera_driver.connect.assert_called_once()
    guide_camera_driver.connect.assert_called_once()
    manager.enclosure_driver.connect.assert_called_once()


def test_disconnect_disconnects_every_configured_driver(mocker: MockerFixture) -> None:
    """Verify disconnect() disconnects each of the six drivers."""
    manager, guide_camera_driver = _manager_with_all_drivers(mocker)

    assert ops.disconnect(manager) is True
    manager.mount_driver.disconnect.assert_called_once()
    manager.focuser_driver.disconnect.assert_called_once()
    manager.filter_wheel_driver.disconnect.assert_called_once()
    manager.camera_driver.disconnect.assert_called_once()
    guide_camera_driver.disconnect.assert_called_once()
    manager.enclosure_driver.disconnect.assert_called_once()


def test_observer_location_returns_none_when_unreported(mocker: MockerFixture) -> None:
    """Verify observer_location returns None when nothing is reported."""
    mount_driver = mocker.Mock()
    mount_driver.get_observer_location = mocker.AsyncMock(return_value=None)
    manager = _FakeManager(mount_driver=mount_driver)

    assert ops.observer_location(manager) is None


def test_observer_location_returns_dict_when_reported(mocker: MockerFixture) -> None:
    """Verify observer_location returns the mount driver's dict."""
    mount_driver = mocker.Mock()
    mount_driver.get_observer_location = mocker.AsyncMock(
        return_value={"latitude": 39.7392, "longitude": -104.9903, "elevation": 1600.0}
    )
    manager = _FakeManager(mount_driver=mount_driver)

    assert ops.observer_location(manager) == {
        "latitude": 39.7392,
        "longitude": -104.9903,
        "elevation": 1600.0,
    }


def _test_enclosure() -> Enclosure:
    """Build an `Enclosure` parked at alt 45deg/az 180deg with 2deg clearance.

    Returns
    -------
    enclosure : `Enclosure`
        A roll-off-roof enclosure configured for the clearance tests
        below.
    """
    return Enclosure(
        id="test-enclosure",
        enclosure_type=EnclosureType.ROLL_OFF_ROOF,
        park_azimuth_deg=180.0,
        park_altitude_deg=45.0,
        clearance_tolerance_deg=2.0,
    )


def test_enclosure_state_is_a_read_with_no_authority_check(mocker: MockerFixture) -> None:
    """Verify enclosure_state delegates to the driver with no gating."""
    enclosure_driver = mocker.Mock()
    enclosure_driver.get_state = mocker.AsyncMock(return_value=EnclosureState.OPEN)
    manager = _FakeManager(enclosure_driver=enclosure_driver, policy=_authoritative_policy())

    assert ops.enclosure_state(manager) == EnclosureState.OPEN


def test_open_enclosure_delegates_to_enclosure_driver(mocker: MockerFixture) -> None:
    """Verify open_enclosure delegates once OBSERVATORY_SAFETY is granted."""
    enclosure_driver = mocker.Mock()
    enclosure_driver.open = mocker.AsyncMock(return_value=True)
    manager = _FakeManager(
        enclosure_driver=enclosure_driver,
        policy=_authoritative_policy(ObservatoryCapability.OBSERVATORY_SAFETY),
    )

    assert ops.open_enclosure(manager) is True
    enclosure_driver.open.assert_called_once()


def test_open_enclosure_raises_when_observatory_safety_not_authoritative(mocker: MockerFixture) -> None:
    """Verify open_enclosure refuses without OBSERVATORY_SAFETY authority."""
    enclosure_driver = mocker.Mock()
    manager = _FakeManager(enclosure_driver=enclosure_driver, policy=_authoritative_policy())

    with pytest.raises(PermissionDeniedError, match="OBSERVATORY_SAFETY"):
        ops.open_enclosure(manager)
    enclosure_driver.open.assert_not_called()


def test_close_enclosure_delegates_when_mount_is_within_clearance(mocker: MockerFixture) -> None:
    """Verify close_enclosure dispatches when the mount is parked clear."""
    enclosure_driver = mocker.Mock()
    enclosure_driver.close = mocker.AsyncMock(return_value=True)
    mount_driver = mocker.Mock()
    mount_driver.get_status = mocker.AsyncMock(
        return_value=_FakeMountStatus(altitude="45:00:00", azimuth="180:00:00")
    )
    manager = _FakeManager(
        enclosure_driver=enclosure_driver,
        mount_driver=mount_driver,
        enclosure=_test_enclosure(),
        policy=_authoritative_policy(ObservatoryCapability.OBSERVATORY_SAFETY),
    )

    assert ops.close_enclosure(manager) is True
    enclosure_driver.close.assert_called_once()


def test_close_enclosure_refuses_when_mount_is_outside_clearance(mocker: MockerFixture) -> None:
    """Verify close_enclosure refuses rather than closing on the mount.

    This is the damage case `enclosure_control.can_close_enclosure`
    exists to prevent -- the mount is far from its configured park
    position, so closing the roof/dome now would strike it.
    """
    enclosure_driver = mocker.Mock()
    enclosure_driver.close = mocker.AsyncMock(return_value=True)
    mount_driver = mocker.Mock()
    mount_driver.get_status = mocker.AsyncMock(
        return_value=_FakeMountStatus(altitude="10:00:00", azimuth="0:00:00")
    )
    manager = _FakeManager(
        enclosure_driver=enclosure_driver,
        mount_driver=mount_driver,
        enclosure=_test_enclosure(),
        policy=_authoritative_policy(ObservatoryCapability.OBSERVATORY_SAFETY),
    )

    with pytest.raises(ConflictError, match="clearance"):
        ops.close_enclosure(manager)
    enclosure_driver.close.assert_not_called()


def test_close_enclosure_raises_when_observatory_safety_not_authoritative(mocker: MockerFixture) -> None:
    """Verify close_enclosure refuses without OBSERVATORY_SAFETY authority."""
    enclosure_driver = mocker.Mock()
    manager = _FakeManager(
        enclosure_driver=enclosure_driver, enclosure=_test_enclosure(), policy=_authoritative_policy()
    )

    with pytest.raises(PermissionDeniedError, match="OBSERVATORY_SAFETY"):
        ops.close_enclosure(manager)
    enclosure_driver.close.assert_not_called()


def test_close_enclosure_raises_when_no_enclosure_configured(mocker: MockerFixture) -> None:
    """Verify close_enclosure raises a clear error with no `Enclosure`."""
    enclosure_driver = mocker.Mock()
    manager = _FakeManager(
        enclosure_driver=enclosure_driver,
        enclosure=None,
        policy=_authoritative_policy(ObservatoryCapability.OBSERVATORY_SAFETY),
    )

    with pytest.raises(ConfigurationError, match="no Enclosure is configured"):
        ops.close_enclosure(manager)
    enclosure_driver.close.assert_not_called()


class _FakeAppConfig:
    """A stand-in for the configuration's `app_config` lookup."""

    def __init__(self, values: dict[tuple[str, str], str]) -> None:
        """Hold the settings as `{(section, key): value}`."""
        self._values = values

    def get(self, section: str, key: str, fallback: Any = None) -> Any:
        """Return one setting, or `fallback` when it is not set.

        Returns
        -------
        value : `Any`
            The stored value, or `fallback`.
        """
        return self._values.get((section, key), fallback)


def _configured_site(**overrides: str) -> Any:
    """Build a fake configuration holding an observatory site.

    Returns
    -------
    config : `Any`
        An object with an `app_config` carrying the site settings.
    """
    values = {
        ("Observatory.Location", "latitude"): "45.76",
        ("Observatory.Location", "longitude"): "-110.74",
        ("Observatory.Location", "elevation"): "1500",
    }
    values.update({("Observatory.Location", key): value for key, value in overrides.items()})

    class _Config:
        """Carries the fake settings."""

        app_config = _FakeAppConfig(values)

    return _Config()


def test_observer_location_falls_back_to_the_configuration_when_mount_offline(
    mocker: MockerFixture,
) -> None:
    """An unreachable mount does not leave the tool with no answer."""
    mount_driver = mocker.Mock()
    mount_driver.get_observer_location = mocker.AsyncMock(side_effect=ConnectionError("not connected"))
    manager = _FakeManager(mount_driver=mount_driver, config=_configured_site())

    assert ops.observer_location(manager) == {
        "latitude": 45.76,
        "longitude": -110.74,
        "elevation": 1500.0,
    }


def test_observer_location_falls_back_when_the_mount_reports_nothing(mocker: MockerFixture) -> None:
    """A mount that reports no location defers to the configured site."""
    mount_driver = mocker.Mock()
    mount_driver.get_observer_location = mocker.AsyncMock(return_value=None)
    manager = _FakeManager(mount_driver=mount_driver, config=_configured_site())

    assert ops.observer_location(manager)["latitude"] == pytest.approx(45.76)


def test_observer_location_prefers_the_mount_over_the_configuration(mocker: MockerFixture) -> None:
    """The telescope's own location wins when it reports one."""
    mount_driver = mocker.Mock()
    mount_driver.get_observer_location = mocker.AsyncMock(
        return_value={"latitude": 1.0, "longitude": 2.0, "elevation": 3.0}
    )
    manager = _FakeManager(mount_driver=mount_driver, config=_configured_site())

    assert ops.observer_location(manager)["latitude"] == pytest.approx(1.0)


def test_configured_observer_location_is_none_without_a_latitude_and_longitude() -> None:
    """A configuration with no site gives no location, not a made-up one."""

    class _EmptyConfig:
        """Carries no site settings."""

        app_config = _FakeAppConfig({})

    assert configured_observer_location(_EmptyConfig()) is None
    assert configured_observer_location(None) is None


def test_a_position_dictionary_becomes_a_sky_position() -> None:
    """The mount methods take the dictionary a client sends."""
    position = ops.sky_position_from({"ra_deg": 10.5, "dec_deg": -5.25})

    assert position == SkyPosition(ra_deg=10.5, dec_deg=-5.25)


def test_a_bad_position_dictionary_is_an_invalid_argument() -> None:
    """A dictionary that is not a position names the expected keys."""
    with pytest.raises(InvalidArgumentError, match="ra_deg"):
        ops.sky_position_from({"ra": 10.5})


def test_a_slew_target_id_is_read_fresh_from_the_library() -> None:
    """A target id is looked up after re-reading the catalog."""
    calls = []

    def get(target_id: str, refresh: bool = False) -> None:
        """Record the lookup; no target has the id."""
        calls.append((target_id, refresh))

    context = SimpleNamespace(astrometrics=SimpleNamespace(targets=SimpleNamespace(get=get)))

    with pytest.raises(NotFoundError, match="Nope"):
        ops.resolve_destination(context, "Nope")
    assert calls == [("Nope", True)]
