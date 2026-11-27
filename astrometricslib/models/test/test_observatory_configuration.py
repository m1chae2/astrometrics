"""Purpose: Test the core equipment and observatory configuration models.

Description: Checks the telescope's meridian-flip delay, altitude limits and
aperture; an observatory configuration's plate scale, field of view, guide
plate scale, key, date range, short description and date check; and the
page of configurations.
"""

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from astrometricslib import Camera, GuideScope, ObservatoryConfiguration, Telescope
from astrometricslib.models.observatory_configuration import ObservatoryConfigurationPage


def _make_telescope(**overrides: Any) -> Telescope:
    """Build a telescope, with any field changed.

    Returns
    -------
    telescope : `Telescope`
        The telescope.
    """
    defaults = {"id": "t1", "name": "Apertura 75Q", "focal_length_mm": 450.0, "focal_ratio": 6.0}
    defaults.update(overrides)
    return Telescope(**defaults)


def _make_camera(**overrides: Any) -> Camera:
    """Build a camera, with any field changed.

    Returns
    -------
    camera : `Camera`
        The camera.
    """
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


def test_equipment_configuration_plate_scale_matches_known_value() -> None:
    """Verify plate scale matches the standard formula for known inputs.

    206.265 x 3.76 / 450 ~= 1.72346 arcsec/px.
    """
    config = ObservatoryConfiguration(telescope=_make_telescope(), camera=_make_camera())
    assert config.plate_scale_arcsec_per_px == pytest.approx(1.723459, abs=1e-4)


def test_equipment_configuration_fov_matches_known_value() -> None:
    """Verify field-of-view width/height derive correctly from plate scale."""
    config = ObservatoryConfiguration(telescope=_make_telescope(), camera=_make_camera())
    expected_fov_deg = config.plate_scale_arcsec_per_px * 3008 / 3600.0
    assert config.fov_width_deg == pytest.approx(expected_fov_deg)
    assert config.fov_height_deg == pytest.approx(expected_fov_deg)


def test_guider_plate_scale_falls_back_to_main_telescope_with_no_guide_scope() -> None:
    """Verify guider_plate_scale_arcsec_per_px(None) matches the main scale.

    The common case: guiding through the main OTA (on-axis or
    off-axis), not a separate guide scope.
    """
    config = ObservatoryConfiguration(telescope=_make_telescope(), camera=_make_camera())
    assert config.guider_plate_scale_arcsec_per_px(None) == pytest.approx(config.plate_scale_arcsec_per_px)


def test_guider_plate_scale_uses_guide_scope_focal_length_when_active() -> None:
    """Verify an active guide scope's focal length replaces the telescope's.

    206.265 x 3.76 / 162 ~= 4.78740 arcsec/px -- a materially different
    (coarser) plate scale than the main telescope's ~1.72 arcsec/px,
    since a 162mm guide scope has far less focal length than the
    450mm main OTA.
    """
    config = ObservatoryConfiguration(telescope=_make_telescope(), camera=_make_camera())
    guide_scope = GuideScope(id="g1", name="Orion 50mm", focal_length_mm=162.0)
    plate_scale = config.guider_plate_scale_arcsec_per_px(guide_scope)
    assert plate_scale == pytest.approx(4.78740, abs=1e-4)
    assert plate_scale != pytest.approx(config.plate_scale_arcsec_per_px)


def test_guider_plate_scale_uses_guide_camera_pixel_size_and_guide_scope_focal_length() -> None:
    """Verify the guide plate scale uses the guide camera's own pixel size.

    The user's real guide train: a 121.05 mm guide scope and an
    ASI120MC-S with 3.75 um pixels, about 6.39 arcsec/px. Using the
    main camera's 3.76 um pixels (the old behaviour) would give 6.41,
    and using the main telescope's focal length too would give 1.9.
    """
    main = ObservatoryConfiguration(telescope=_make_telescope(focal_length_mm=405.0), camera=_make_camera())
    guide_scope = GuideScope(id="g", name="Apertura 32mm", focal_length_mm=121.05, aperture_mm=32.0)
    guide_camera = _make_camera(id="guide", name="ZWO ASI120MC-S", pixel_size_um=3.75)

    scale = main.guider_plate_scale_arcsec_per_px(guide_scope, guide_camera)

    assert scale == pytest.approx(206.265 * 3.75 / 121.05)
    assert scale != pytest.approx(main.guider_plate_scale_arcsec_per_px(guide_scope))


def test_guider_plate_scale_uses_guide_camera_with_the_main_telescope() -> None:
    """Verify a guide camera alone changes only the pixel size."""
    main = ObservatoryConfiguration(telescope=_make_telescope(focal_length_mm=450.0), camera=_make_camera())
    guide_camera = _make_camera(id="guide", name="Guide", pixel_size_um=3.75)
    assert main.guider_plate_scale_arcsec_per_px(None, guide_camera) == pytest.approx(206.265 * 3.75 / 450.0)


def test_aperture_is_focal_length_over_focal_ratio() -> None:
    """Verify the aperture is focal length over focal ratio."""
    assert _make_telescope(focal_length_mm=450.0, focal_ratio=6.0).aperture_mm == pytest.approx(75.0)


def test_aperture_is_unknown_when_the_focal_ratio_is_zero() -> None:
    """Verify a focal ratio of 0 gives no aperture."""
    assert _make_telescope(focal_ratio=0.0).aperture_mm is None


def test_the_key_dates_and_description_of_a_configuration() -> None:
    """The key rounds the focal length; the dates are open at either end."""
    configuration = ObservatoryConfiguration(
        name="Main",
        telescope=_make_telescope(focal_length_mm=405.4),
        camera=_make_camera(),
        valid_from=date(2026, 1, 1),
    )
    assert configuration.configuration_key == "ZWO ASI533MM Pro@405mm"
    assert configuration.is_valid_on(date(2026, 6, 1))
    assert not configuration.is_valid_on(date(2025, 12, 31))
    assert "Main" in repr(configuration) and "now" in repr(configuration)


def test_a_configuration_cannot_end_before_it_starts() -> None:
    """An end before the start is refused, and the model is frozen."""
    with pytest.raises(ValidationError):
        ObservatoryConfiguration(
            telescope=_make_telescope(),
            camera=_make_camera(),
            valid_from=date(2026, 2, 1),
            valid_until=date(2026, 1, 1),
        )
    configuration = ObservatoryConfiguration(telescope=_make_telescope(), camera=_make_camera())
    with pytest.raises(ValidationError):
        configuration.name = "changed"


def test_a_page_can_be_looped_counted_and_indexed() -> None:
    """The page offers `for`, `len` and indexing over its items."""
    item = ObservatoryConfiguration(telescope=_make_telescope(), camera=_make_camera())
    page = ObservatoryConfigurationPage(items=[item], total=3, next_offset=1)
    assert list(page) == [item] and len(page) == 1 and page[0] is item
