"""Purpose: Unit tests for equipment domain models.

Description: Verifies EquipmentCatalog active-selection resolution and
validation. The equipment models themselves are the core library's and are
tested in `astrometricslib/models/test/test_observatory_configuration.py`.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from astrometricslib import Camera, GuideScope, Telescope
from wayfindinglib.models.equipment_and_site.equipment import EquipmentCatalog


def _make_telescope(**overrides: Any) -> Telescope:
    defaults = {"id": "t1", "name": "Apertura 75Q", "focal_length_mm": 450.0, "focal_ratio": 6.0}
    defaults.update(overrides)
    return Telescope(**defaults)


def _make_camera(**overrides: Any) -> Camera:
    defaults = {
        "id": "c1",
        "name": "ZWO ASI533MM Pro",
        "pixel_size_um": 3.76,
        "sensor_width_px": 3008,
        "sensor_height_px": 3008,
    }
    defaults.update(overrides)
    return Camera(**defaults)


def test_equipment_catalog_resolves_active_telescope_and_camera() -> None:
    """Verify active_telescope()/active_camera() resolve configured entries."""
    telescope = _make_telescope()
    camera = _make_camera()
    catalog = EquipmentCatalog(
        id="cat1",
        telescopes=[telescope],
        cameras=[camera],
        active_telescope_id="t1",
        active_camera_id="c1",
    )
    assert catalog.active_telescope() is telescope
    assert catalog.active_camera() is camera


def test_equipment_catalog_active_none_when_unset() -> None:
    """Verify active_telescope() returns None when no telescope is active."""
    catalog = EquipmentCatalog(id="cat1", telescopes=[_make_telescope()], cameras=[])
    assert catalog.active_telescope() is None


def test_equipment_catalog_rejects_unresolvable_active_telescope_id() -> None:
    """Verify an active_telescope_id absent from telescopes is rejected."""
    with pytest.raises(ValidationError):
        EquipmentCatalog(
            id="cat1",
            telescopes=[_make_telescope()],
            cameras=[],
            active_telescope_id="does-not-exist",
        )


def test_equipment_catalog_active_guide_scope_none_when_unset() -> None:
    """Verify active_guide_scope() returns None -- guiding via the main OTA."""
    catalog = EquipmentCatalog(id="cat1", telescopes=[], cameras=[])
    assert catalog.active_guide_scope() is None


def test_equipment_catalog_rejects_unresolvable_active_guide_scope_id() -> None:
    """Verify an active_guide_scope_id absent from guide_scopes is rejected."""
    with pytest.raises(ValidationError):
        EquipmentCatalog(
            id="cat1",
            telescopes=[],
            cameras=[],
            guide_scopes=[GuideScope(id="g1", name="Orion 50mm", focal_length_mm=162.0)],
            active_guide_scope_id="does-not-exist",
        )


def test_equipment_catalog_resolves_active_guide_scope() -> None:
    """Verify active_guide_scope() resolves the matching configured entry."""
    guide_scope = GuideScope(id="g1", name="Orion 50mm", focal_length_mm=162.0, aperture_mm=50.0)
    catalog = EquipmentCatalog(
        id="cat1", telescopes=[], cameras=[], guide_scopes=[guide_scope], active_guide_scope_id="g1"
    )
    assert catalog.active_guide_scope() is guide_scope


def test_equipment_catalog_active_guide_camera_none_when_unset() -> None:
    """Verify active_guide_camera() returns None -- the main camera guides."""
    catalog = EquipmentCatalog(id="cat1", telescopes=[], cameras=[])
    assert catalog.active_guide_camera() is None


def test_equipment_catalog_rejects_unresolvable_active_guide_camera_id() -> None:
    """Verify an active_guide_camera_id absent from cameras is rejected."""
    with pytest.raises(ValidationError):
        EquipmentCatalog(id="cat1", telescopes=[], cameras=[_make_camera()], active_guide_camera_id="nope")


def test_equipment_catalog_resolves_active_guide_camera() -> None:
    """Verify active_guide_camera() resolves the matching configured camera."""
    guide_camera = _make_camera(id="guide", name="ZWO ASI120MC-S", pixel_size_um=3.75)
    catalog = EquipmentCatalog(
        id="cat1",
        telescopes=[],
        cameras=[_make_camera(), guide_camera],
        active_guide_camera_id="guide",
    )
    assert catalog.active_guide_camera() is guide_camera
