"""Purpose: Test how the equipment service chooses a configuration.

Description: The screens choose a camera or a telescope on its own; the
library makes a whole observatory configuration active. These tests give
`EquipmentService` a stand-in equipment control and stand-in stored
configurations and check which configuration it makes active, that a choice
no stored configuration has returns `False`, and that bad limits come back
as `ValueError`.
"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import create_autospec

import pytest

from astrometricslib import Camera, ObservatoryConfiguration, Telescope
from astrometricslib.exceptions import InvalidArgumentError
from backend.services.observatory.equipment_service import EquipmentService
from wayfindinglib.api.control.equipment import EquipmentControl


def _configuration(
    identifier: str, camera: str, telescope: str, **fields: object
) -> ObservatoryConfiguration:
    """Build a stored configuration of one camera on one telescope.

    Parameters
    ----------
    identifier : `str`
        Its id.
    camera : `str`
        The camera's name.
    telescope : `str`
        The telescope's name.
    **fields
        Other fields.

    Returns
    -------
    configuration : `ObservatoryConfiguration`
        The configuration.
    """
    return ObservatoryConfiguration(
        id=identifier,
        telescope=Telescope(id=telescope, name=telescope, focal_length_mm=400.0),
        camera=Camera(id=camera, name=camera, pixel_size_um=3.76, sensor_width_px=10, sensor_height_px=10),
        **fields,
    )


MONO_REFRACTOR = _configuration("mono-refractor", "Mono Camera", "Refractor")
COLOR_REFRACTOR = _configuration("color-refractor", "Color Camera", "Refractor")
COLOR_LENS = _configuration("color-lens", "Color Camera", "Lens", valid_from=date(2026, 1, 1))


def _service(active: ObservatoryConfiguration | None) -> tuple[EquipmentService, object]:
    """Build the service over a stand-in control with an active configuration.

    Parameters
    ----------
    active : `ObservatoryConfiguration` or `None`
        The configuration the control reports as active.

    Returns
    -------
    service, equipment : `EquipmentService`, `EquipmentControl`
        The service and its stand-in equipment control.
    """
    equipment = create_autospec(EquipmentControl, instance=True)
    equipment.get_active.return_value = active
    configurations = SimpleNamespace(query=lambda **arguments: [MONO_REFRACTOR, COLOR_REFRACTOR, COLOR_LENS])
    return EquipmentService(SimpleNamespace(equipment=equipment), configurations=configurations), equipment


def test_a_new_camera_keeps_the_telescope_in_use() -> None:
    """The color camera stays on the refractor, not the lens."""
    service, equipment = _service(MONO_REFRACTOR)
    assert service.set_active_camera("color camera") is True
    equipment.set_active.assert_called_once_with(COLOR_REFRACTOR)


def test_a_new_telescope_may_bring_another_camera() -> None:
    """Only the color camera is used on the lens, so the camera changes too."""
    service, equipment = _service(MONO_REFRACTOR)
    assert service.set_active_telescope("Lens") is True
    equipment.set_active.assert_called_once_with(COLOR_LENS)


def test_a_choice_no_configuration_has_is_refused() -> None:
    """An unknown camera, or one the library refuses, returns `False`."""
    service, equipment = _service(MONO_REFRACTOR)
    assert service.set_active_camera("No Such Camera") is False
    equipment.set_active.assert_not_called()
    equipment.set_active.side_effect = InvalidArgumentError("not listed")
    assert service.set_active_camera("Color Camera") is False


def test_bad_limits_come_back_as_value_errors() -> None:
    """A refused limit, or a key that is not a limit, raises `ValueError`."""
    service, equipment = _service(MONO_REFRACTOR)
    equipment.set_pointing_limits.side_effect = InvalidArgumentError("minimum above maximum")
    with pytest.raises(ValueError, match="minimum above maximum"):
        service.set_active_telescope("Refractor", {"min_altitude_deg": 80.0, "max_altitude_deg": 10.0})
