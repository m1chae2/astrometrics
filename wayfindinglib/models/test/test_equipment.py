"""Purpose: Unit tests for equipment domain models.

Description: Verifies Telescope's derived meridian-flip delay, altitude
envelope validation, EquipmentCatalog active-selection resolution and
validation, and EquipmentConfiguration's plate-scale/FOV arithmetic
against known values.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from wayfindinglib.models.equipment_and_site.equipment import (
    Camera,
    EquipmentCatalog,
    EquipmentConfiguration,
    GuideScope,
    Telescope,
)


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


def test_meridian_flip_delay_derives_from_hour_angle() -> None:
    """Verify meridian_flip_delay_min derives from flip_hour_angle_deg."""
    telescope = _make_telescope(flip_hour_angle_deg=1.0)
    assert telescope.meridian_flip_delay_min == pytest.approx(4.0)


def test_meridian_flip_delay_scales_with_hour_angle() -> None:
    """Verify the derived delay scales linearly with the stored hour angle."""
    telescope = _make_telescope(flip_hour_angle_deg=7.5)
    assert telescope.meridian_flip_delay_min == pytest.approx(30.0)


def test_telescope_rejects_inverted_altitude_envelope() -> None:
    """Verify min_altitude_deg > max_altitude_deg is rejected."""
    with pytest.raises(ValidationError):
        _make_telescope(min_altitude_deg=50.0, max_altitude_deg=10.0)


def test_telescope_accepts_default_envelope() -> None:
    """Verify a Telescope constructs with only the required fields."""
    telescope = _make_telescope()
    assert telescope.altitude_limits_enabled is True
    assert telescope.min_altitude_deg == pytest.approx(0.0)
    assert telescope.max_altitude_deg == pytest.approx(90.0)


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


def test_equipment_configuration_plate_scale_matches_known_value() -> None:
    """Verify plate scale matches the standard formula for known inputs.

    206.265 x 3.76 / 450 ~= 1.72346 arcsec/px.
    """
    config = EquipmentConfiguration(telescope=_make_telescope(), camera=_make_camera())
    assert config.plate_scale_arcsec_per_px == pytest.approx(1.723459, abs=1e-4)


def test_equipment_configuration_fov_matches_known_value() -> None:
    """Verify field-of-view width/height derive correctly from plate scale."""
    config = EquipmentConfiguration(telescope=_make_telescope(), camera=_make_camera())
    expected_fov_deg = config.plate_scale_arcsec_per_px * 3008 / 3600.0
    assert config.fov_width_deg == pytest.approx(expected_fov_deg)
    assert config.fov_height_deg == pytest.approx(expected_fov_deg)


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


def test_guider_plate_scale_falls_back_to_main_telescope_with_no_guide_scope() -> None:
    """Verify guider_plate_scale_arcsec_per_px(None) matches the main scale.

    The common case: guiding through the main OTA (on-axis or
    off-axis), not a separate guide scope.
    """
    config = EquipmentConfiguration(telescope=_make_telescope(), camera=_make_camera())
    assert config.guider_plate_scale_arcsec_per_px(None) == pytest.approx(config.plate_scale_arcsec_per_px)


def test_guider_plate_scale_uses_guide_scope_focal_length_when_active() -> None:
    """Verify an active guide scope's focal length replaces the telescope's.

    206.265 x 3.76 / 162 ~= 4.78740 arcsec/px -- a materially different
    (coarser) plate scale than the main telescope's ~1.72 arcsec/px,
    since a 162mm guide scope has far less focal length than the
    450mm main OTA.
    """
    config = EquipmentConfiguration(telescope=_make_telescope(), camera=_make_camera())
    guide_scope = GuideScope(id="g1", name="Orion 50mm", focal_length_mm=162.0)
    plate_scale = config.guider_plate_scale_arcsec_per_px(guide_scope)
    assert plate_scale == pytest.approx(4.78740, abs=1e-4)
    assert plate_scale != pytest.approx(config.plate_scale_arcsec_per_px)


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


def test_guider_plate_scale_uses_guide_camera_pixel_size_and_guide_scope_focal_length() -> None:
    """Verify the guide plate scale uses the guide camera's own pixel size.

    The user's real guide train: a 121.05 mm guide scope and an
    ASI120MC-S with 3.75 um pixels, about 6.39 arcsec/px. Using the
    main camera's 3.76 um pixels (the old behaviour) would give 6.41,
    and using the main telescope's focal length too would give 1.9.
    """
    main = EquipmentConfiguration(telescope=_make_telescope(focal_length_mm=405.0), camera=_make_camera())
    guide_scope = GuideScope(id="g", name="Apertura 32mm", focal_length_mm=121.05, aperture_mm=32.0)
    guide_camera = _make_camera(id="guide", name="ZWO ASI120MC-S", pixel_size_um=3.75)

    scale = main.guider_plate_scale_arcsec_per_px(guide_scope, guide_camera)

    assert scale == pytest.approx(206.265 * 3.75 / 121.05)
    assert scale != pytest.approx(main.guider_plate_scale_arcsec_per_px(guide_scope))


def test_guider_plate_scale_uses_guide_camera_with_the_main_telescope() -> None:
    """Verify a guide camera alone changes only the pixel size."""
    main = EquipmentConfiguration(telescope=_make_telescope(focal_length_mm=450.0), camera=_make_camera())
    guide_camera = _make_camera(id="guide", name="Guide", pixel_size_um=3.75)
    assert main.guider_plate_scale_arcsec_per_px(None, guide_camera) == pytest.approx(206.265 * 3.75 / 450.0)
