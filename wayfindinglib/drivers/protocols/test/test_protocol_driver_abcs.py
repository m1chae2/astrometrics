"""Tests for the per-device-type hardware-control protocol driver ABCs.

Confirms each ABC enforces its full abstract method set (mirrors
`wayfindinglib/drivers/catalog/test/`'s approach for `CatalogDriver`)
and that the INDI adapters conform to their respective ABCs.
"""

import pytest

from wayfindinglib.drivers.indi.camera_driver import IndiCameraDriver
from wayfindinglib.drivers.indi.enclosure_driver import IndiEnclosureDriver
from wayfindinglib.drivers.indi.filter_wheel_driver import IndiFilterWheelDriver
from wayfindinglib.drivers.indi.focuser_driver import IndiFocuserDriver
from wayfindinglib.drivers.indi.mount_driver import IndiMountDriver
from wayfindinglib.drivers.protocols.camera_driver import CameraDriver
from wayfindinglib.drivers.protocols.enclosure_driver import EnclosureDriver
from wayfindinglib.drivers.protocols.filter_wheel_driver import FilterWheelDriver
from wayfindinglib.drivers.protocols.focuser_driver import FocuserDriver
from wayfindinglib.drivers.protocols.mount_driver import MountDriver


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


def test_indi_enclosure_driver_conforms_to_enclosure_driver() -> None:
    """Verify `IndiEnclosureDriver` implements the full ABC.

    Every `EnclosureDriver` abstract method is implemented.
    """
    driver = IndiEnclosureDriver(session=object())
    assert isinstance(driver, EnclosureDriver)


@pytest.fixture
def anyio_backend():  # ruff: ignore[missing-return-type-undocumented-public-function]
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

        def get_status(self):  # ruff: ignore[missing-return-type-private-function]
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
