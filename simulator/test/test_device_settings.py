"""Purpose: Test how the world's settings become INDI simulator properties.

Description: The settings of the world (a copy of the real settings) name the
primary and secondary cameras, the optic and the guide scope. The tests check
the list of properties that follows, the way a property is written for
``indi_setprop``, and that the exposure driver sets a property again when a
resolution change undid it.
"""

from pathlib import Path

import pytest

from simulator.device_settings import (
    GUIDE_MINIMUM_STAR_FWHM_PIXELS,
    DeviceSetting,
    camera_setpoint,
    device_settings_from_world,
    guide_pixel_scale,
    guide_star_seeing_floor,
    load_world_settings,
    matches,
    real_filter_names,
)
from simulator.exposure_driver import ExposureDriver

WORLD = {
    "Observatory.Telescope": {"focal_length_mm": "405", "focal_ratio": "5.4"},
    "Observatory.Camera": {
        "default_primary_camera": "ZWO ASI 533MM Pro",
        "default_secondary_camera": "ZWO ASI120MC-S",
    },
    "Observatory.Camera.ZWO ASI 533MM Pro": {
        "pixel_size_μm": "3.76",
        "sensor_width_px": "3008",
        "sensor_height_px": "3008",
        "bias_levels": [
            {
                "gain_setting": "0",
                "offset": "10",
                "binning": "1x1",
                "bias_level_adu": {"value": 394.85, "kind": "measured", "source": "test"},
            }
        ],
    },
    "Observatory.Camera.ZWO ASI120MC-S": {
        "pixel_size_μm": "3.75",
        "sensor_width_px": "1280",
        "sensor_height_px": "960",
    },
    "Observatory.GuideScope": {"active_guide_scope": "Apertura 32mm"},
    "Observatory.GuideScope.Apertura 32mm": {"focal_length_mm": "121.05", "aperture_mm": "32"},
}


def _specifications(world: dict) -> list[str]:
    """Write the world's properties the way ``indi_setprop`` takes them.

    Parameters
    ----------
    world : `dict`
        Parsed settings.

    Returns
    -------
    specifications : `list` [`str`]
        One text per property.
    """
    return [setting.specification() for setting in device_settings_from_world(world)]


def test_the_cameras_optics_and_capture_setting_come_from_the_real_settings() -> None:
    """Each simulator gets the real sensor, optic and capture setting."""
    assert _specifications(WORLD) == [
        "CCD Simulator.SIMULATOR_SETTINGS.SIM_XRES;SIM_YRES;SIM_XSIZE;SIM_YSIZE=3008;3008;3.76;3.76",
        "Guide Simulator.SIMULATOR_SETTINGS.SIM_XRES;SIM_YRES;SIM_XSIZE;SIM_YSIZE=1280;960;3.75;3.75",
        "CCD Simulator.SCOPE_INFO.FOCAL_LENGTH;APERTURE=405;75",
        "Guide Simulator.SCOPE_INFO.FOCAL_LENGTH;APERTURE=121.05;32",
        "Guide Simulator.SIMULATOR_SETTINGS.SIM_SEEING=15.89210227",
        "CCD Simulator.CCD_GAIN.GAIN=0",
        "CCD Simulator.CCD_OFFSET.OFFSET=394.85",
    ]


def test_the_simulator_pedestal_is_the_real_bias_level_and_the_header_keeps_the_offset() -> None:
    """INDI reads CCD_OFFSET as the empty-frame level: send the bias."""
    settings = {s.property_name: s for s in device_settings_from_world(WORLD)}
    assert settings["CCD_OFFSET"].elements == (("OFFSET", 394.85),)
    assert settings["CCD_OFFSET"].header_value == pytest.approx(10.0)


def test_a_missing_bias_level_falls_back_to_the_offset_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """With no bias level, the offset is sent and a warning logged."""
    profile = dict(WORLD["Observatory.Camera.ZWO ASI 533MM Pro"])
    profile["bias_levels"] = [{"gain_setting": "0", "offset": "10", "binning": "1x1"}]
    with caplog.at_level("WARNING"):
        specifications = _specifications({**WORLD, "Observatory.Camera.ZWO ASI 533MM Pro": profile})
    assert "CCD Simulator.CCD_OFFSET.OFFSET=10" in specifications
    assert "No bias level is recorded" in caplog.text


def test_gain_and_offset_come_last_so_a_resolution_change_cannot_undo_them() -> None:
    """The resolution is set before the gain and offset."""
    names = [setting.property_name for setting in device_settings_from_world(WORLD)]
    assert names.index("SIMULATOR_SETTINGS") < names.index("CCD_GAIN") < names.index("CCD_OFFSET")


def test_an_empty_world_gets_the_made_up_optic_only() -> None:
    """Without profiles, only the focal length and aperture fall back."""
    assert _specifications({}) == ["CCD Simulator.SCOPE_INFO.FOCAL_LENGTH;APERTURE=405;75"]


def test_a_dslr_iso_is_not_set_as_a_gain() -> None:
    """A gain setting that is not a number is left out."""
    world = {
        **WORLD,
        "Observatory.Camera.ZWO ASI 533MM Pro": {
            **WORLD["Observatory.Camera.ZWO ASI 533MM Pro"],
            "bias_levels": [{"gain_setting": "ISO 800", "offset": "0"}],
        },
    }
    assert not [text for text in _specifications(world) if "CCD_GAIN" in text]


def test_the_noise_measurement_gives_the_capture_setting_when_there_is_no_bias_level() -> None:
    """The first noise entry is the second source of gain and offset."""
    profile = dict(WORLD["Observatory.Camera.ZWO ASI 533MM Pro"])
    del profile["bias_levels"]
    profile["sensor_noise"] = [{"gain_setting": "100", "offset": "50"}]
    specifications = _specifications({**WORLD, "Observatory.Camera.ZWO ASI 533MM Pro": profile})
    assert "CCD Simulator.CCD_GAIN.GAIN=100" in specifications
    assert "CCD Simulator.CCD_OFFSET.OFFSET=50" in specifications


def test_a_missing_or_broken_settings_file_gives_no_settings(tmp_path: Path) -> None:
    """The loader never raises."""
    assert load_world_settings(tmp_path / "missing.toml") == {}
    broken = tmp_path / "broken.toml"
    broken.write_text("[[[", encoding="utf-8")
    assert load_world_settings(broken) == {}


def test_read_back_comparison_allows_the_server_rounding() -> None:
    """A float printed with many digits still matches."""
    assert matches(3.76, "3.7599999904632568359")
    assert not matches(10, "0")
    assert not matches(10, "text")


class _FakeDriver(ExposureDriver):
    """A driver whose server drops the first attempt at each value."""

    def __init__(self, settings: list[DeviceSetting]) -> None:
        """Keep the settings; no server is used."""
        super().__init__(1, Path("."), Path("log"), Path("stats"), 1.0, "M 13", 0.0, 0.0, settings)
        self.values: dict[str, str] = {}
        self.attempts = 0

    def _set(self, *settings: str) -> None:
        """Take a setting on the second attempt only."""
        self.attempts += 1
        if self.attempts % 2 == 0:
            for text in settings:
                device_property, _, values = text.partition("=")
                device, prop, names = device_property.split(".", 2)
                for name, value in zip(names.split(";"), values.split(";"), strict=True):
                    self.values[f"{device}.{prop}.{name}"] = value

    def _get(self, name: str) -> str:
        """Read what was taken.

        Parameters
        ----------
        name : `str`
            ``device.property.element``.

        Returns
        -------
        value : `str`
            The value, or an empty text.
        """
        return self.values.get(name, "")


def test_a_value_the_server_did_not_take_is_set_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """The driver reads each value back and tries again."""
    monkeypatch.setattr("simulator.exposure_driver.SETTING_WAIT_SECONDS", 0.2)
    driver = _FakeDriver(device_settings_from_world(WORLD))
    assert driver.apply_device_settings() == []
    assert driver.values["CCD Simulator.CCD_OFFSET.OFFSET"] == "394.85"


def test_a_value_that_never_takes_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    """A property the server refuses is named in the result."""
    monkeypatch.setattr("simulator.exposure_driver.SETTING_WAIT_SECONDS", 0.1)
    driver = _FakeDriver(device_settings_from_world(WORLD))
    monkeypatch.setattr(driver, "_set", lambda *settings: None)
    failed = driver.apply_device_settings()
    assert "CCD Simulator.CCD_GAIN.GAIN" in failed


def test_filter_names_come_from_the_settings_and_the_camera_is_linked_to_the_wheel() -> None:
    """The wheel slots take the real names, in order."""
    world = {**WORLD, "Observatory.Filters": {"available": "NONE, L, SPEC"}}
    specifications = _specifications(world)
    assert specifications[:2] == [
        "CCD Simulator.ACTIVE_DEVICES.ACTIVE_FILTER=Filter Simulator",
        "Filter Simulator.FILTER_NAME.FILTER_SLOT_NAME_1;FILTER_SLOT_NAME_2;FILTER_SLOT_NAME_3=NONE;L;SPEC",
    ]


def test_the_older_owned_key_and_repeats_and_blanks_are_handled() -> None:
    """``owned`` is read without ``available``; blanks and repeats go."""
    world = {"Observatory.Filters": {"owned": "L, , SPEC, L"}}
    assert real_filter_names(world) == ["L", "SPEC"]


def test_a_filter_list_longer_than_the_wheel_is_cut() -> None:
    """The wheel has 8 slots."""
    names = ", ".join(f"F{number}" for number in range(12))
    setting = next(
        item
        for item in device_settings_from_world({"Observatory.Filters": {"available": names}})
        if item.property_name == "FILTER_NAME"
    )
    assert len(setting.elements) == 8


def test_no_filters_in_the_settings_leaves_the_wheel_alone() -> None:
    """Nothing is set without a list."""
    assert not [item for item in _specifications(WORLD) if "FILTER" in item]


def test_the_cooler_setpoint_comes_from_the_profile_and_is_not_read_back() -> None:
    """The temperature is set last, and its moving value is not checked."""
    profile = dict(WORLD["Observatory.Camera.ZWO ASI 533MM Pro"])
    profile["bias_levels"] = [{"gain_setting": "0", "offset": "10", "temperature_c": -10.0}]
    settings = device_settings_from_world({**WORLD, "Observatory.Camera.ZWO ASI 533MM Pro": profile})
    assert settings[-1].specification() == "CCD Simulator.CCD_TEMPERATURE.CCD_TEMPERATURE_VALUE=-10"
    assert settings[-1].read_back_names() == []
    assert camera_setpoint({"sensor_noise": [{"temperature_c": -5}]}) == pytest.approx(-5.0)
    assert camera_setpoint({}) is None
    assert not [item for item in _specifications(WORLD) if "CCD_TEMPERATURE" in item]


def test_the_driver_waits_for_the_sensor_to_reach_the_setpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wait ends when the temperature is within a degree."""
    setting = DeviceSetting(
        "CCD Simulator", "CCD_TEMPERATURE", (("CCD_TEMPERATURE_VALUE", -10.0),), check=False
    )
    driver = _FakeDriver([setting])
    readings = iter(["0", "-5", "-9.5"])
    monkeypatch.setattr(driver, "_get", lambda name: next(readings))
    monkeypatch.setattr(driver.stop_event, "wait", lambda seconds: False)
    assert driver.wait_for_cooling(timeout=60) is not None
    assert driver.cooling_seconds is not None
    assert _FakeDriver([]).wait_for_cooling() is None


def test_the_guide_star_is_wide_enough_for_phd2_to_use() -> None:
    """A 6.39 arcsec pixel needs a star 3.5 pixels wide: seeing about 15.9."""
    scale = guide_pixel_scale(WORLD)
    assert scale == pytest.approx(6.39, abs=0.01)
    seeing = guide_star_seeing_floor(scale)
    assert seeing is not None
    # The driver draws a Gaussian of full width 1.41 times SIM_SEEING.
    assert 1.4142 * seeing / scale == pytest.approx(GUIDE_MINIMUM_STAR_FWHM_PIXELS, rel=0.01)


def test_the_guide_star_width_is_left_alone_when_the_optics_are_unknown() -> None:
    """With no guide scope there is no pixel scale and no seeing setting."""
    assert guide_pixel_scale({}) is None
    assert guide_star_seeing_floor(None) is None
    assert guide_star_seeing_floor(100.0) == pytest.approx(20.0)
