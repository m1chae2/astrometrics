"""Purpose: Backend tests for the camera noise read and the framing corners.

Description: `observatory:list_cameras` returns the camera profiles with the
gain and read noise recorded for each setting, and `mosaic:preview` returns
panels that carry their four sky corners. Both are plain pass-throughs of the
library. The library classes are `create_autospec` stand-ins, so a wrong
argument or method name fails here.
"""

from types import SimpleNamespace
from unittest.mock import create_autospec

import pytest

from backend.services.observatory.equipment_service import EquipmentService
from backend.services.observatory.mosaic_service import MosaicService
from wayfindinglib import EquipmentStatus, MosaicPanel
from wayfindinglib.api.control.equipment import EquipmentControl
from wayfindinglib.api.planning import ObservationPlanning


def test_list_cameras_reads_the_camera_profiles_section_and_keeps_the_noise() -> None:
    """The camera list reads `camera_profiles` and keeps the noise."""
    noise_entry = {"gainSetting": "0", "gain": {"value": 0.78, "status": "measured"}}
    profiles = [{"name": "ASI533", "pixelSizeUm": 3.76, "sensorNoise": [noise_entry]}]
    equipment = create_autospec(EquipmentControl, instance=True)
    equipment.status.return_value = EquipmentStatus(camera_profiles=profiles)
    service = EquipmentService(SimpleNamespace(equipment=equipment))
    assert service.list_camera_profiles() == profiles
    equipment.status.assert_called_once_with(include=["camera_profiles"])


def test_mosaic_preview_returns_the_panels_with_their_corners() -> None:
    """The preview returns the corners and convention unchanged."""
    corners = [{"ra_deg": 10.0 + step, "dec_deg": 20.0} for step in range(4)]
    panel = MosaicPanel(
        row=0,
        col=0,
        ra_str="00 40 00.0",
        dec_str="+20 00 00.0",
        ra_deg=10.0,
        dec_deg=20.0,
        panel_id="P1_1",
        corners=corners,
    )
    planning = create_autospec(ObservationPlanning, instance=True)
    planning.calculate_panels.return_value = [panel]
    result = MosaicService(SimpleNamespace(planning=planning)).preview_mosaic(
        10.0, 20.0, {"rows": 1, "cols": 1, "overlap": 0}, rotation_deg=15.0
    )
    assert [c.model_dump() for c in result[0].corners] == corners
    assert result[0].rotation_convention.startswith("Degrees east of north")
    assert planning.calculate_panels.call_args.kwargs["rotation_deg"] == pytest.approx(15.0)
