"""Purpose: Unit tests for CalibrationLibrary and its compatibility checks.

Description: Verifies addition of dark, bias, and flat calibration frames,
their recording behavior, and the gain/exposure compatibility checks
used to flag a mismatched calibration master.
"""

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.drivers.calibration_library import (
    CalibrationLibrary,
    is_calibration_gain_compatible,
    is_dark_calibration_metadata_compatible,
)


def _make_small_fits(path, shape=(20, 20)):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Generate a small dummy FITS calibration frame."""
    arr = np.zeros(shape, dtype=np.float32)
    hdu = fits.PrimaryHDU(arr)
    # Calibration code checks INSTRUME header for specific camera strings;
    # set it to a commonly-checked value so the code path executes.
    hdu.header["INSTRUME"] = "Nikon D5300"
    hdu.writeto(path)


def test_add_dark_frames(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify dark frames can be registered into the library and saved."""
    calibration_frames = CalibrationLibrary()
    dark_files = [str(tmp_path / f"dark_{i}.fits") for i in (1, 2, 3)]
    for p in dark_files:
        _make_small_fits(p)
        calibration_frames.add_dark_frame(p)
    calibration_frames.save_library()


def test_add_bias_frames(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify bias frames can be registered into the library and saved."""
    calibration_frames = CalibrationLibrary()
    bias_files = [str(tmp_path / f"bias_{i}.fits") for i in (1, 2)]
    for p in bias_files:
        _make_small_fits(p)
        calibration_frames.add_bias_frame(p)
    calibration_frames.save_library()


def test_add_flat_frames(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify flat frames can be registered into the library and saved."""
    calibration_frames = CalibrationLibrary()
    flat_files = [str(tmp_path / f"flat_{i}.fits") for i in (1, 2)]
    for p in flat_files:
        _make_small_fits(p)
        calibration_frames.add_flat_frame(p)
    calibration_frames.save_library()


def test_calibration_gain_compatible_matches():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify matching gain values are compatible."""
    assert is_calibration_gain_compatible(light_gain="100", master_gain="100")


def test_calibration_gain_incompatible_on_mismatch():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify any gain mismatch is flagged, independent of calibration type."""
    assert not is_calibration_gain_compatible(light_gain="100", master_gain="200")


def test_dark_calibration_metadata_compatible_matches_within_tolerance():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a small exposure difference at matching gain is accepted."""
    assert is_dark_calibration_metadata_compatible(
        light_exposure=120.0, light_gain="100", master_exposure=120.5, master_gain="100"
    )


def test_dark_calibration_metadata_incompatible_on_gain_mismatch():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify any gain mismatch is flagged regardless of exposure match."""
    assert not is_dark_calibration_metadata_compatible(
        light_exposure=120.0, light_gain="100", master_exposure=120.0, master_gain="200"
    )


def test_dark_calibration_metadata_incompatible_beyond_exposure_tolerance():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a dark's exposure difference beyond tolerance is flagged.

    Even at otherwise matching gain.
    """
    assert not is_dark_calibration_metadata_compatible(
        light_exposure=120.0,
        light_gain="100",
        master_exposure=125.0,
        master_gain="100",
        exposure_tolerance_seconds=1.0,
    )


def test_dark_calibration_metadata_compatible_ignores_bias_like_short_exposure_only_via_gain_check():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify bias/flat-style short exposures flag incompatible for darks.

    This documents why bias/flat masters must use
    is_calibration_gain_compatible instead -- a bias master's
    near-zero exposure would always fail the dark-specific exposure
    tolerance against a real light exposure, which is expected and
    correct for darks but would be a false positive if applied to
    bias/flat masters.
    """
    assert not is_dark_calibration_metadata_compatible(
        light_exposure=30.0, light_gain="0", master_exposure=3.2e-05, master_gain="0"
    )


# ---- camera offset and gain in the library key ---------------------------


def _library(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Build an empty library that saves and loads inside `tmp_path`.

    Returns
    -------
    library : `CalibrationLibrary`
        A library with no frames.
    """
    from types import SimpleNamespace

    app_config = SimpleNamespace(get_library_file_path=lambda name: str(tmp_path / name))
    return CalibrationLibrary(app_config=app_config)


def _write_frame(path, exposure=0.5, gain=0.0, offset=None, camera="ZWO CCD ASI533MM Pro", flat_filter=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Write a tiny FITS frame with the given camera settings.

    Returns
    -------
    path : `str`
        The path written.
    """
    hdu = fits.PrimaryHDU(np.zeros((8, 8), dtype=np.float32))
    hdu.header["INSTRUME"] = camera
    hdu.header["EXPTIME"] = exposure
    hdu.header["GAIN"] = gain
    if offset is not None:
        hdu.header["OFFSET"] = offset
    if flat_filter is not None:
        hdu.header["FILTER"] = flat_filter
    hdu.writeto(path)
    return str(path)


CAMERA = "ZWO ASI 533MM Pro"


def test_setting_key_is_plain_for_offset_zero_or_unknown():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Every key from before offset was tracked stays valid unchanged."""
    from astrometricslib.drivers.calibration_library import calibration_setting_key

    assert calibration_setting_key("0.0") == "0.0"
    assert calibration_setting_key("0.0", None) == "0.0"
    assert calibration_setting_key("0.0", 0) == "0.0"
    assert calibration_setting_key("0.0", "0") == "0.0"
    assert calibration_setting_key("800", "not a number") == "800"


def test_setting_key_carries_a_nonzero_offset_and_splits_back():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A nonzero offset joins the key and is recovered from it."""
    from astrometricslib.drivers.calibration_library import (
        calibration_setting_key,
        split_calibration_setting_key,
    )

    key = calibration_setting_key("0.0", 30.0)

    assert key == "0.0@offset=30"
    assert split_calibration_setting_key(key) == ("0.0", 30.0)
    assert split_calibration_setting_key("0.0") == ("0.0", 0.0)


def test_darks_at_different_offsets_are_filed_apart(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A dark at offset 30 does not share a slot with one at offset 0."""
    library = _library(tmp_path)
    library.add_dark_frame(_write_frame(tmp_path / "old.fits"))
    library.add_dark_frame(_write_frame(tmp_path / "new.fits", offset=30))

    assert set(library.dark_frames["ZWO ASI 533MM Pro"]) == {"0.0", "0.0@offset=30"}


def test_a_dark_at_the_lights_own_offset_is_chosen(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Each offset's lights get only that offset's dark."""
    library = _library(tmp_path)
    old = _write_frame(tmp_path / "old.fits")
    new = _write_frame(tmp_path / "new.fits", offset=30)
    library.add_dark_frame(old)
    library.add_dark_frame(new)

    assert library.get_dark_frames(camera=CAMERA, exposure=0.5, iso="0.0", offset="30") == [new]
    assert library.get_dark_frames(camera=CAMERA, exposure=0.5, iso="0.0", offset="0") == [old]
    assert library.get_dark_frames(camera=CAMERA, exposure=0.5, iso="0", offset=None) == [old]


def test_darks_at_other_settings_are_used_with_a_warning_when_none_match(tmp_path, caplog):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A dark at another offset beats none, and the log says so."""
    library = _library(tmp_path)
    old = _write_frame(tmp_path / "old.fits")
    library.add_dark_frame(old)

    with caplog.at_level("WARNING"):
        frames = library.get_dark_frames(camera=CAMERA, exposure=0.5, iso="0.0", offset="50")

    assert frames == [old]
    assert "offset 50" in caplog.text


def test_darks_of_another_gain_are_not_mixed_in(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Gain is honoured now: gain-100 lights get only gain-100 darks."""
    library = _library(tmp_path)
    gain_zero = _write_frame(tmp_path / "g0.fits", gain=0.0)
    gain_hundred = _write_frame(tmp_path / "g100.fits", gain=100.0)
    library.add_dark_frame(gain_zero)
    library.add_dark_frame(gain_hundred)

    assert library.get_dark_frames(camera=CAMERA, exposure=0.5, iso="100.0", offset="0") == [gain_hundred]


def test_without_a_gain_every_setting_is_used_as_before(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A caller that gives no gain still gets every dark."""
    library = _library(tmp_path)
    old = _write_frame(tmp_path / "old.fits")
    new = _write_frame(tmp_path / "new.fits", offset=30)
    library.add_dark_frame(old)
    library.add_dark_frame(new)

    assert sorted(library.get_dark_frames(camera=CAMERA, exposure=0.5)) == sorted([old, new])


def test_exposure_is_still_matched_within_a_tenth_of_a_second(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Offset selection leaves the exposure rule alone."""
    library = _library(tmp_path)
    near = _write_frame(tmp_path / "near.fits", exposure=0.55)
    far = _write_frame(tmp_path / "far.fits", exposure=2.0)
    library.add_dark_frame(near)
    library.add_dark_frame(far)

    assert library.get_dark_frames(camera=CAMERA, exposure=0.5, iso="0.0", offset="0") == [near]


def test_biases_follow_the_lights_offset(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Bias frames are chosen by gain and offset too."""
    library = _library(tmp_path)
    old = _write_frame(tmp_path / "old_bias.fits", exposure=0.0)
    new = _write_frame(tmp_path / "new_bias.fits", exposure=0.0, offset=30)
    library.add_bias_frame(old)
    library.add_bias_frame(new)

    assert library.get_bias_frames(camera=CAMERA, iso="0.0", offset="30") == [new]
    assert library.get_bias_frames(camera=CAMERA, iso="0.0", offset="0") == [old]
    assert sorted(library.get_bias_frames(camera=CAMERA)) == sorted([old, new])


def test_flats_follow_the_lights_offset(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Flat frames are chosen by gain and offset too."""
    library = _library(tmp_path)
    old = _write_frame(tmp_path / "old_flat.fits", flat_filter="Luminance")
    new = _write_frame(tmp_path / "new_flat.fits", offset=30, flat_filter="Luminance")
    library.add_flat_frame(old, telescope="T")
    library.add_flat_frame(new, telescope="T")

    assert library.get_flat_frames(
        telescope="T", camera=CAMERA, filter_type="Luminance", iso="0.0", offset="30"
    ) == [new]
    assert library.get_flat_frames(
        telescope="T", camera=CAMERA, filter_type="Luminance", iso="0.0", offset="0"
    ) == [old]


def test_a_library_saved_before_offset_existed_still_works(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Old gain-only keys are read as offset 0, so no migration is needed."""
    library = _library(tmp_path)
    old = _write_frame(tmp_path / "old.fits")
    library.deserialize({"dark_frames": {CAMERA: {"0.0": {"0.5": [old]}}}})

    assert library.get_dark_frames(camera=CAMERA, exposure=0.5, iso="0.0", offset="0") == [old]


def test_offset_compatibility_treats_a_missing_offset_as_zero():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Equal offsets match, different ones do not, and unknown means zero."""
    from astrometricslib.drivers.calibration_library import is_calibration_offset_compatible

    assert is_calibration_offset_compatible("30", 30.0)
    assert is_calibration_offset_compatible(None, "0")
    assert not is_calibration_offset_compatible("30", "0")


def test_stats_report_the_offset(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """The summary lists gain and offset separately."""
    library = _library(tmp_path)
    library.add_dark_frame(_write_frame(tmp_path / "new.fits", offset=30))

    dark = library.get_stats()["darks"][0]

    assert dark["iso"] == "0.0"
    assert dark["offset"] == pytest.approx(30.0)
