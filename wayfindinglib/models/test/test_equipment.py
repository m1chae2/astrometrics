"""Purpose: Unit tests for equipment domain models.

Description: Verifies Telescope's derived meridian-flip delay, altitude
envelope validation, EquipmentCatalog active-selection resolution and
validation, and EquipmentConfiguration's plate-scale/FOV arithmetic
against the values the deprecated observatorylib implementation produced.
"""

import pytest
from pydantic import ValidationError

from wayfindinglib.models.equipment_and_site.equipment import (
    Camera,
    EquipmentCatalog,
    EquipmentConfiguration,
    Telescope,
)


def _make_telescope(**overrides):  # ruff: ignore[missing-type-kwargs, missing-return-type-private-function]
    defaults = {"id": "t1", "name": "Apertura 75Q", "focal_length_mm": 450.0, "focal_ratio": 6.0}
    defaults.update(overrides)
    return Telescope(**defaults)


def _make_camera(**overrides):  # ruff: ignore[missing-type-kwargs, missing-return-type-private-function]
    defaults = {
        "id": "c1",
        "name": "ZWO ASI533MM Pro",
        "pixel_size_um": 3.76,
        "sensor_width_px": 3008,
        "sensor_height_px": 3008,
    }
    defaults.update(overrides)
    return Camera(**defaults)


def test_meridian_flip_delay_derives_from_hour_angle():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify meridian_flip_delay_min derives from flip_hour_angle_deg."""
    telescope = _make_telescope(flip_hour_angle_deg=1.0)
    assert telescope.meridian_flip_delay_min == pytest.approx(4.0)


def test_meridian_flip_delay_scales_with_hour_angle():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the derived delay scales linearly with the stored hour angle."""
    telescope = _make_telescope(flip_hour_angle_deg=7.5)
    assert telescope.meridian_flip_delay_min == pytest.approx(30.0)


def test_telescope_rejects_inverted_altitude_envelope():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify min_altitude_deg > max_altitude_deg is rejected."""
    with pytest.raises(ValidationError):
        _make_telescope(min_altitude_deg=50.0, max_altitude_deg=10.0)


def test_telescope_accepts_default_envelope():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a Telescope constructs with only the required fields."""
    telescope = _make_telescope()
    assert telescope.altitude_limits_enabled is True
    assert telescope.min_altitude_deg == pytest.approx(0.0)
    assert telescope.max_altitude_deg == pytest.approx(90.0)


def test_equipment_catalog_resolves_active_telescope_and_camera():  # ruff: ignore[missing-return-type-undocumented-public-function]
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


def test_equipment_catalog_active_none_when_unset():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify active_telescope() returns None when no telescope is active."""
    catalog = EquipmentCatalog(id="cat1", telescopes=[_make_telescope()], cameras=[])
    assert catalog.active_telescope() is None


def test_equipment_catalog_rejects_unresolvable_active_telescope_id():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an active_telescope_id absent from telescopes is rejected."""
    with pytest.raises(ValidationError):
        EquipmentCatalog(
            id="cat1",
            telescopes=[_make_telescope()],
            cameras=[],
            active_telescope_id="does-not-exist",
        )


def test_equipment_configuration_plate_scale_matches_known_value():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify plate scale matches the standard formula for known inputs.

    206.265 x 3.76 / 450 ~= 1.72346 arcsec/px -- the same formula and
    constant the deprecated observatorylib.EquipmentConfiguration used.
    """
    config = EquipmentConfiguration(telescope=_make_telescope(), camera=_make_camera())
    assert config.plate_scale_arcsec_per_px == pytest.approx(1.723459, abs=1e-4)


def test_equipment_configuration_fov_matches_known_value():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify field-of-view width/height derive correctly from plate scale."""
    config = EquipmentConfiguration(telescope=_make_telescope(), camera=_make_camera())
    expected_fov_deg = config.plate_scale_arcsec_per_px * 3008 / 3600.0
    assert config.fov_width_deg == pytest.approx(expected_fov_deg)
    assert config.fov_height_deg == pytest.approx(expected_fov_deg)
