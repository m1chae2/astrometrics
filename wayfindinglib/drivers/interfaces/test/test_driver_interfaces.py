"""Tests for the per-device-type driver interfaces (abstract base classes).

Confirms each ABC enforces its full abstract method set (mirrors
`wayfindinglib/drivers/catalog/test/`'s approach for `CatalogDriver`)
and that the INDI adapters conform to their respective ABCs.
"""

from datetime import datetime

import pytest

from wayfindinglib.drivers.indi.camera_driver import IndiCameraDriver
from wayfindinglib.drivers.indi.enclosure_driver import IndiEnclosureDriver
from wayfindinglib.drivers.indi.filter_wheel_driver import IndiFilterWheelDriver
from wayfindinglib.drivers.indi.focuser_driver import IndiFocuserDriver
from wayfindinglib.drivers.indi.mount_driver import IndiMountDriver
from wayfindinglib.drivers.indi.switch_driver import IndiSwitchDriver
from wayfindinglib.drivers.indi.weather_driver import IndiWeatherDriver
from wayfindinglib.drivers.interfaces.camera_driver import CameraDriver
from wayfindinglib.drivers.interfaces.enclosure_driver import EnclosureDriver
from wayfindinglib.drivers.interfaces.filter_wheel_driver import FilterWheelDriver
from wayfindinglib.drivers.interfaces.focuser_driver import FocuserDriver
from wayfindinglib.drivers.interfaces.mount_driver import MountDriver
from wayfindinglib.drivers.interfaces.remote_transfer_driver import RemoteTransferDriver
from wayfindinglib.drivers.interfaces.switch_driver import SwitchDriver
from wayfindinglib.drivers.interfaces.weather_driver import WeatherDriver
from wayfindinglib.drivers.stellarmate_interface import StellarMateInterface
from wayfindinglib.models.equipment_and_site.enclosure import EnclosureState


def test_mount_driver_cannot_be_instantiated_directly() -> None:
    """Verify `MountDriver` cannot be instantiated missing methods."""
    with pytest.raises(TypeError):
        MountDriver()  # pyrefly: ignore[bad-instantiation]


def test_camera_driver_cannot_be_instantiated_directly() -> None:
    """Verify `CameraDriver` cannot be instantiated missing methods."""
    with pytest.raises(TypeError):
        CameraDriver()  # pyrefly: ignore[bad-instantiation]


def test_focuser_driver_cannot_be_instantiated_directly() -> None:
    """Verify `FocuserDriver` cannot be instantiated missing methods."""
    with pytest.raises(TypeError):
        FocuserDriver()  # pyrefly: ignore[bad-instantiation]


def test_filter_wheel_driver_cannot_be_instantiated_directly() -> None:
    """Verify `FilterWheelDriver` cannot be instantiated missing methods."""
    with pytest.raises(TypeError):
        FilterWheelDriver()  # pyrefly: ignore[bad-instantiation]


def test_enclosure_driver_cannot_be_instantiated_directly() -> None:
    """Verify `EnclosureDriver` cannot be instantiated missing methods."""
    with pytest.raises(TypeError):
        EnclosureDriver()  # pyrefly: ignore[bad-instantiation]


def test_indi_mount_driver_conforms_to_mount_driver() -> None:
    """Verify `IndiMountDriver` implements every `MountDriver` method."""
    driver = IndiMountDriver(session=object())
    assert isinstance(driver, MountDriver)
    assert driver.protocol_name == "indi"


def test_indi_camera_driver_conforms_to_camera_driver() -> None:
    """Verify `IndiCameraDriver` implements every `CameraDriver` method."""
    driver = IndiCameraDriver(session=object(), role="guide")
    assert isinstance(driver, CameraDriver)
    assert driver.role == "guide"


def test_indi_focuser_driver_conforms_to_focuser_driver() -> None:
    """Verify `IndiFocuserDriver` implements every `FocuserDriver` method."""
    driver = IndiFocuserDriver(session=object())
    assert isinstance(driver, FocuserDriver)


def test_indi_filter_wheel_driver_conforms_to_filter_wheel_driver() -> None:
    """Verify `IndiFilterWheelDriver` implements the full ABC.

    Every `FilterWheelDriver` abstract method is implemented.
    """
    driver = IndiFilterWheelDriver(session=object())
    assert isinstance(driver, FilterWheelDriver)


def test_switch_driver_cannot_be_instantiated_directly() -> None:
    """Verify `SwitchDriver` cannot be instantiated missing methods."""
    with pytest.raises(TypeError):
        SwitchDriver()  # pyrefly: ignore[bad-instantiation]


def test_weather_driver_cannot_be_instantiated_directly() -> None:
    """Verify `WeatherDriver` cannot be instantiated missing methods."""
    with pytest.raises(TypeError):
        WeatherDriver()  # pyrefly: ignore[bad-instantiation]


def test_indi_switch_driver_conforms_to_switch_driver() -> None:
    """Verify `IndiSwitchDriver` implements every `SwitchDriver` method."""
    driver = IndiSwitchDriver(session=object())
    assert isinstance(driver, SwitchDriver)
    assert driver.protocol_name == "indi"


def test_indi_weather_driver_conforms_to_weather_driver() -> None:
    """Verify `IndiWeatherDriver` implements every `WeatherDriver` method."""
    driver = IndiWeatherDriver(session=object())
    assert isinstance(driver, WeatherDriver)
    assert driver.protocol_name == "indi"


def test_remote_transfer_driver_cannot_be_instantiated_directly() -> None:
    """Verify `RemoteTransferDriver` cannot be instantiated missing methods."""
    with pytest.raises(TypeError):
        RemoteTransferDriver()  # pyrefly: ignore[bad-instantiation]


def test_stellarmate_interface_conforms_to_remote_transfer_driver() -> None:
    """Verify `StellarMateInterface` implements `RemoteTransferDriver`."""
    driver = StellarMateInterface()
    assert isinstance(driver, RemoteTransferDriver)
    assert driver.driver_name == "stellarmate"


def test_indi_enclosure_driver_conforms_to_enclosure_driver() -> None:
    """Verify `IndiEnclosureDriver` implements the full ABC.

    Every `EnclosureDriver` abstract method is implemented.
    """
    driver = IndiEnclosureDriver(session=object())
    assert isinstance(driver, EnclosureDriver)


@pytest.fixture
def anyio_backend() -> str:
    """Restrict anyio-marked tests in this module to the asyncio backend.

    Returns
    -------
    backend : `str`
        The anyio backend name to run these tests under.
    """
    return "asyncio"


@pytest.mark.anyio
async def test_indi_mount_driver_set_slew_rate_delegates_to_session() -> None:
    """Verify `set_slew_rate` delegates to the session's real implementation.

    M2 fixed the verified gap where the real `IndiInterface` had no
    `set_slew_rate` at all (only the simulator did).
    """

    class _FakeSession:
        """A stand-in session recording the requested slew rate."""

        def __init__(self) -> None:
            self.requested_rate_index: int | None = None

        def set_slew_rate(self, rate_index: int) -> bool:
            """Record the requested rate index and report success.

            Returns
            -------
            success : `bool`
                Always `True`.
            """
            self.requested_rate_index = rate_index
            return True

    session = _FakeSession()
    driver = IndiMountDriver(session=session)
    assert await driver.set_slew_rate(3) is True
    assert session.requested_rate_index == 3


@pytest.mark.anyio
async def test_indi_camera_driver_main_role_reads_camera_temperature_field() -> None:
    """Verify the main-camera role reads `camera_temperature`.

    Regression test for an M1 bug fixed in M4: `TelescopeStatus.temperature`
    is the *ambient* powerbox/weather reading, not a camera's own sensor --
    `TelescopeStatus.camera_temperature` (from `CCD_TEMPERATURE` on the main
    camera device) is the correct source for the main camera's temperature.
    """

    class _FakeStatus:
        """A stand-in `TelescopeStatus` with distinct ambient/camera temps."""

        temperature = "5.0°C"
        camera_temperature = "-10.5°C"

    class _FakeSession:
        """A stand-in session returning a fixed status."""

        def get_status(self) -> _FakeStatus:
            """Return the fixed fake status.

            Returns
            -------
            status : `_FakeStatus`
                The fixed fake status.
            """
            return _FakeStatus()

    main_driver = IndiCameraDriver(session=_FakeSession(), role="main")
    assert await main_driver.get_sensor_temperature_c() == pytest.approx(-10.5)


@pytest.mark.anyio
async def test_indi_camera_driver_guide_role_has_no_temperature_source() -> None:
    """Verify the guide-camera role honestly returns `None`.

    No guide-camera temperature is tracked anywhere in `IndiInterface`
    today -- fabricating one from an unrelated field would be worse
    than admitting the gap.
    """
    guide_driver = IndiCameraDriver(session=object(), role="guide")
    assert await guide_driver.get_sensor_temperature_c() is None


@pytest.mark.anyio
async def test_indi_enclosure_driver_delegates_to_session_enclosure_methods() -> None:
    """Verify `IndiEnclosureDriver` delegates to the session's M6 methods.

    Regression test for M6: earlier (M1-scaffolded) versions of these
    methods raised `NotImplementedError` -- this confirms they now
    delegate to `IndiInterface.get_enclosure_state`/`open_enclosure`/
    `close_enclosure`, backed by `EnclosureController` (M6).
    """
    from wayfindinglib.models.equipment_and_site.enclosure import EnclosureState

    class _FakeSession:
        """A stand-in session recording enclosure command calls."""

        def get_enclosure_state(self) -> EnclosureState:
            """Report a fixed `OPEN` state.

            Returns
            -------
            state : `EnclosureState`
                Always `EnclosureState.OPEN`.
            """
            return EnclosureState.OPEN

        def open_enclosure(self) -> bool:
            """Report a successful open command.

            Returns
            -------
            success : `bool`
                Always `True`.
            """
            return True

        def close_enclosure(self) -> bool:
            """Report a successful close command.

            Returns
            -------
            success : `bool`
                Always `True`.
            """
            return True

    driver = IndiEnclosureDriver(session=_FakeSession())
    assert await driver.get_state() == EnclosureState.OPEN
    assert await driver.open() is True
    assert await driver.close() is True


@pytest.mark.anyio
async def test_indi_switch_driver_delegates_to_session_switch_methods() -> None:
    """Verify `IndiSwitchDriver` delegates to the session's M12 methods."""

    class _FakeSession:
        """A stand-in session recording switch command calls."""

        def get_switch_states(self) -> dict[str, bool]:
            """Report a fixed outlet state map.

            Returns
            -------
            states : `dict` [`str`, `bool`]
                A single fixed outlet's state.
            """
            return {"POWER_CONTROL_1": True}

        def set_switch_state(self, switch_name: str, on: bool) -> bool:
            """Record the requested outlet command and report success.

            Returns
            -------
            success : `bool`
                Always `True`.
            """
            self.requested = (switch_name, on)
            return True

        def get_switch_variable_values(self) -> dict[str, float]:
            """Report a fixed dew-heater value map.

            Returns
            -------
            values : `dict` [`str`, `float`]
                A single fixed variable's value.
            """
            return {"DEW_A": 40.0}

        def set_switch_variable_value(self, name: str, value: float) -> bool:
            """Record the requested variable command and report success.

            Returns
            -------
            success : `bool`
                Always `True`.
            """
            self.requested_variable = (name, value)
            return True

    session = _FakeSession()
    driver = IndiSwitchDriver(session=session)
    assert await driver.get_switch_states() == {"POWER_CONTROL_1": True}
    assert await driver.set_switch_state("POWER_CONTROL_1", False) is True
    assert session.requested == ("POWER_CONTROL_1", False)
    assert await driver.get_variable_values() == {"DEW_A": 40.0}
    assert await driver.set_variable_value("DEW_A", 75.0) is True
    assert session.requested_variable == ("DEW_A", 75.0)


@pytest.mark.anyio
async def test_indi_weather_driver_delegates_to_session_weather_method() -> None:
    """Verify `IndiWeatherDriver` delegates to the session's M12 method."""
    from datetime import UTC, datetime

    fixed_reading = {"WEATHER_TEMPERATURE": (12.5, datetime.now(UTC))}

    class _FakeSession:
        """A stand-in session returning a fixed weather reading."""

        def get_weather_readings(self) -> dict[str, tuple[float, datetime]]:
            """Return the fixed reading map.

            Returns
            -------
            readings : `SensorReadings`
                The fixed reading map.
            """
            return fixed_reading

    driver = IndiWeatherDriver(session=_FakeSession())
    assert await driver.get_readings() == fixed_reading
