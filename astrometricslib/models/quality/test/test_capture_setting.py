"""Tests for `CaptureSetting`: header precision must not split a setting."""

import pytest

from astrometricslib.models.quality.capture_setting import CaptureSetting


def _setting(pixel_scale_arcsec: float | None) -> CaptureSetting:
    """Build an M 13-like setting with one pixel scale.

    Parameters
    ----------
    pixel_scale_arcsec : `float` or `None`
        The pixel scale to give.

    Returns
    -------
    setting : `CaptureSetting`
        The setting.
    """
    return CaptureSetting(
        camera="ZWO ASI 533MM Pro",
        telescope="Apertura 75Q",
        focal_length_mm=405.0,
        pixel_scale_arcsec=pixel_scale_arcsec,
        filter_name="Luminance",
        exposure_s=30.0,
        gain=0,
        offset=0,
        binning=1,
    )


def test_two_header_precisions_of_one_scale_are_one_setting() -> None:
    """The two M 13 header values give equal settings with one hash."""
    short, long = _setting(1.915279), _setting(1.9152790075)
    assert short == long
    assert len({short, long}) == 1
    assert short.pixel_scale_arcsec == pytest.approx(1.915, abs=1e-12)


def test_the_two_optics_stay_apart() -> None:
    """The 300 mm and 405 mm scales of the same camera remain two settings."""
    assert _setting(2.586) != _setting(1.915)


def test_scales_of_other_sizes_keep_four_significant_figures() -> None:
    """Rounding is by significant figures, not decimal places."""
    assert _setting(12.34567).pixel_scale_arcsec == pytest.approx(12.35, abs=1e-12)
    assert _setting(0.4567891).pixel_scale_arcsec == pytest.approx(0.4568, abs=1e-12)


def test_a_missing_or_zero_scale_is_kept() -> None:
    """None and zero pass through unchanged."""
    assert _setting(None).pixel_scale_arcsec is None
    assert _setting(0.0).pixel_scale_arcsec == pytest.approx(0.0, abs=0.0)


def test_unknown_setting_is_marked_and_other_settings_are_not() -> None:
    """The stand-in setting says it is unknown; a real camera does not."""
    from astrometricslib.models.quality.capture_setting import UNKNOWN_CAPTURE_SETTING_CAMERA

    unknown = CaptureSetting.unknown()
    assert unknown.camera == UNKNOWN_CAPTURE_SETTING_CAMERA
    assert unknown.is_unknown
    assert not CaptureSetting(camera="ZWO ASI533MM Pro").is_unknown


def test_setting_is_spectral_follows_the_filter_name() -> None:
    """A grating filter is spectral; a missing or ordinary filter is not."""
    assert CaptureSetting(camera="Cam", filter_name="SPEC").is_spectral
    assert not CaptureSetting(camera="Cam", filter_name="L").is_spectral
    assert not CaptureSetting(camera="Cam").is_spectral


def test_the_unit_named_fields_read_the_old_names_and_write_the_new_json() -> None:
    """Old field and JSON names load; the new JSON names are written."""
    from_old_names = CaptureSetting.model_validate({
        "camera": "Cam",
        "gain_setting": 100,
        "exposure_seconds": 60.0,
    })
    from_old_json = CaptureSetting.model_validate({
        "camera": "Cam",
        "gainSetting": 100,
        "exposureSeconds": 60.0,
    })
    from_json = CaptureSetting.model_validate({"camera": "Cam", "gain": 100, "exposureS": 60.0})
    new = CaptureSetting(camera="Cam", gain=100, exposure_s=60.0)

    assert from_old_names == from_old_json == from_json == new
    assert (new.gain, new.exposure_s) == (100, 60.0)
    dumped = new.model_dump(by_alias=True)
    assert (dumped["gain"], dumped["exposureS"]) == (100, 60.0)
    assert not {"gainSetting", "exposureSeconds"} & set(dumped)


def test_the_sensor_temperature_is_an_optional_field() -> None:
    """A setting can say which darks it means by their sensor temperature."""
    setting = CaptureSetting(camera="Cam", temperature_c=-10.0)

    assert setting.temperature_c == pytest.approx(-10.0)
    assert setting.model_dump(by_alias=True)["temperatureC"] == pytest.approx(-10.0)
    assert CaptureSetting(camera="Cam").temperature_c is None
