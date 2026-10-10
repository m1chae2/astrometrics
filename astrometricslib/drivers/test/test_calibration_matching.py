"""Purpose: Tests for how the calibration library keys and matches frames.

Description: Darks are filed by gain, offset, binning and a temperature slot,
and bias and flat frames by gain, offset and binning. These tests check the
key builder for each of those dimensions and the exposure rule, that darks
taken at different temperatures make separate masters, that a light with no
dark close in temperature gets the nearest one with a flag, and that a binning
mismatch applies no calibration and says why. The last tests drive the
staging step that fills a run's darks, biases and flats folders, without
Siril.
"""

import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.drivers import siril_interface
from astrometricslib.drivers.calibration_library import (
    CALIBRATION_MATCH_BLOCKING_FLAGS_KEY,
    DEFAULT_DARK_TEMPERATURE_TOLERANCE_C,
    CalibrationLibrary,
    calibration_setting_key,
    dark_temperature_flag,
    format_binning,
    header_binning,
    header_temperature_c,
    parse_calibration_setting_key,
    temperature_slot_c,
)

CAMERA = "ZWO ASI 533MM Pro"
CAMERA_HEADER_NAME = "ZWO CCD ASI533MM Pro"


def make_library(tmp_path: Path) -> CalibrationLibrary:
    """Build an empty library that saves and loads inside `tmp_path`.

    Returns
    -------
    library : `CalibrationLibrary`
        A library with no frames.
    """
    app_config = SimpleNamespace(get_library_file_path=lambda name: str(tmp_path / name))
    return CalibrationLibrary(app_config=app_config)


def write_frame(
    path: Path,
    exposure: float = 30.0,
    temperature_c: float | None = None,
    binning: int | None = None,
    gain: int = 100,
    offset: float = 30.0,
    flat_filter: str | None = None,
) -> str:
    """Write a small FITS frame with the given camera settings.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write the file.
    exposure : `float`, optional
        The exposure in seconds.
    temperature_c : `float`, optional
        The ``CCD-TEMP`` card. Left out when `None`.
    binning : `int`, optional
        The ``XBINNING`` and ``YBINNING`` cards. Left out when `None`.
    gain, offset : `float`, optional
        The ``GAIN`` and ``OFFSET`` cards.
    flat_filter : `str`, optional
        The ``FILTER`` card, for flats.

    Returns
    -------
    path : `str`
        The path written.
    """
    hdu = fits.PrimaryHDU(np.full((8, 8), 100.0, dtype=np.float32))
    header = hdu.header
    header["INSTRUME"] = CAMERA_HEADER_NAME
    header["EXPTIME"] = exposure
    header["GAIN"] = gain
    header["OFFSET"] = offset
    if temperature_c is not None:
        header["CCD-TEMP"] = temperature_c
    if binning is not None:
        header["XBINNING"] = binning
        header["YBINNING"] = binning
    if flat_filter is not None:
        header["FILTER"] = flat_filter
    hdu.writeto(path)
    return str(path)


# ------------------------------------------------------------ the key builder


@pytest.mark.parametrize(
    ("temperature_c", "expected_slot"),
    [(-10.2, -9.0), (-9.0, -9.0), (-10.6, -12.0), (5.0, 6.0), (0.4, 0.0), (-1.4, 0.0), (None, None)],
)
def test_temperature_is_binned_to_the_tolerance(
    temperature_c: float | None, expected_slot: float | None
) -> None:
    """A temperature rounds to the middle of a slot one tolerance wide."""
    assert DEFAULT_DARK_TEMPERATURE_TOLERANCE_C == pytest.approx(3.0)
    assert temperature_slot_c(temperature_c) == expected_slot


def test_the_temperature_slot_follows_the_tolerance_given() -> None:
    """A wider tolerance makes wider slots."""
    assert temperature_slot_c(-10.2, tolerance_c=5.0) == pytest.approx(-10.0)
    assert temperature_slot_c(-7.6, tolerance_c=5.0) == pytest.approx(-10.0)
    assert temperature_slot_c(-7.4, tolerance_c=5.0) == pytest.approx(-5.0)


def test_a_dark_key_carries_the_temperature_slot() -> None:
    """The slot, not the exact temperature, goes into the key."""
    assert calibration_setting_key("100", 30, temperature_c=-10.2) == "100@offset=30@temp=-9"
    assert calibration_setting_key("100", 30, temperature_c=-9.0) == "100@offset=30@temp=-9"
    assert calibration_setting_key("100", 30, temperature_c=5.0) == "100@offset=30@temp=6"
    assert calibration_setting_key("100", 30, temperature_c=-10.2) != calibration_setting_key(
        "100", 30, temperature_c=5.0
    )


def test_a_key_carries_the_binning_unless_it_is_one_by_one() -> None:
    """Unbinned frames keep the older key, so saved libraries still match."""
    assert calibration_setting_key("100", 30) == "100@offset=30"
    assert calibration_setting_key("100", 30, binning="1x1") == "100@offset=30"
    assert calibration_setting_key("100", 30, binning="2x2") == "100@offset=30@bin=2x2"
    assert calibration_setting_key("100", 30, binning="2X2") == "100@offset=30@bin=2x2"
    assert calibration_setting_key("100", 0, binning=2) == "100@bin=2x2"


def test_a_full_key_is_read_back_into_its_settings() -> None:
    """Gain, offset, binning and temperature slot all come back out."""
    key = calibration_setting_key("100", 30, binning="2x2", temperature_c=-10.2)
    setting = parse_calibration_setting_key(key)

    assert key == "100@offset=30@bin=2x2@temp=-9"
    assert (setting.gain, setting.offset, setting.binning, setting.temperature_c) == (
        "100",
        30.0,
        "2x2",
        -9.0,
    )
    older = parse_calibration_setting_key("0.0")
    assert (older.gain, older.offset, older.binning, older.temperature_c) == ("0.0", 0.0, "1x1", None)


def test_binning_is_read_from_the_header_cards() -> None:
    """Both cards are used; missing cards mean no binning."""
    assert header_binning({"XBINNING": 2, "YBINNING": 2}) == "2x2"
    assert header_binning({"XBINNING": 2, "YBINNING": 1}) == "2x1"
    assert header_binning({"XBINNING": 3}) == "3x3"
    assert header_binning({}) == "1x1"
    assert header_binning({"XBINNING": "not a number"}) == "1x1"
    assert format_binning() == "1x1"


def test_the_temperature_comes_from_ccd_temp_then_the_cooler_target() -> None:
    """A measured temperature wins; the cooler target is the fallback."""
    assert header_temperature_c({"CCD-TEMP": -10.2, "SET-TEMP": -10.0}) == pytest.approx(-10.2)
    assert header_temperature_c({"SET-TEMP": -10.0}) == pytest.approx(-10.0)
    assert header_temperature_c({}) is None


def test_darks_of_different_exposures_are_filed_apart(tmp_path: Path) -> None:
    """The exposure is its own level, and the 0.1 s rule is kept."""
    library = make_library(tmp_path)
    short = write_frame(tmp_path / "short.fits", exposure=30.0)
    long = write_frame(tmp_path / "long.fits", exposure=60.0)
    library.add_dark_frame(short)
    library.add_dark_frame(long)

    exposures = library.dark_frames[CAMERA]["100@offset=30"]

    assert set(exposures) == {"30.0", "60.0"}
    assert library.get_dark_frames(camera=CAMERA, exposure=30.05, iso="100", offset="30") == [short]
    assert library.get_dark_frames(camera=CAMERA, exposure=30.5, iso="100", offset="30") == []


# --------------------------------------------------- darks by temperature


def add_temperature_darks(tmp_path: Path, library: CalibrationLibrary) -> tuple[list[str], list[str]]:
    """File three darks at about -10 C and three at about +5 C.

    Returns
    -------
    cold, warm : `tuple` [`list` [`str`], `list` [`str`]]
        The cold and the warm dark paths.
    """
    cold = [write_frame(tmp_path / f"cold_{i}.fits", temperature_c=-10.0 + 0.1 * i) for i in range(3)]
    warm = [write_frame(tmp_path / f"warm_{i}.fits", temperature_c=5.0 + 0.1 * i) for i in range(3)]
    for path in [*cold, *warm]:
        library.add_dark_frame(path)
    return cold, warm


def test_darks_at_two_temperatures_make_two_masters(tmp_path: Path) -> None:
    """The library keeps the cold and warm darks in separate slots."""
    library = make_library(tmp_path)
    cold, warm = add_temperature_darks(tmp_path, library)

    slots = library.dark_frames[CAMERA]

    assert set(slots) == {"100@offset=30@temp=-9", "100@offset=30@temp=6"}
    assert slots["100@offset=30@temp=-9"]["30.0"] == cold
    assert slots["100@offset=30@temp=6"]["30.0"] == warm


def test_a_light_gets_only_the_darks_of_its_own_temperature(tmp_path: Path) -> None:
    """A -10 C light is given the cold darks only, with no flag."""
    library = make_library(tmp_path)
    cold, warm = add_temperature_darks(tmp_path, library)

    cold_selection = library.select_dark_frames(
        camera=CAMERA, exposure=30.0, iso="100", offset="30", temperature_c=-10.0
    )
    warm_selection = library.select_dark_frames(
        camera=CAMERA, exposure=30.0, iso="100", offset="30", temperature_c=5.0
    )

    assert cold_selection.frames == cold
    assert warm_selection.frames == warm
    assert cold_selection.flags == []
    assert warm_selection.flags == []
    assert cold_selection.blocking_flags == []


def test_a_light_far_from_every_dark_gets_the_nearest_master_and_a_flag(tmp_path: Path) -> None:
    """A -10 C light with only +5 C darks gets them and a flag."""
    library = make_library(tmp_path)
    warm = [write_frame(tmp_path / f"warm_{i}.fits", temperature_c=5.0 + 0.1 * i) for i in range(3)]
    for path in warm:
        library.add_dark_frame(path)

    selection = library.select_dark_frames(
        camera=CAMERA,
        exposure=30.0,
        iso="100",
        offset="30",
        temperature_c=-10.0,
        light_label="the 12 light frame(s) starting with light_001.fits",
    )

    assert selection.frames == warm
    assert selection.blocking_flags == []
    assert len(selection.flags) == 1
    flag = selection.flags[0]
    assert "-10.0 C" in flag
    assert "5.1 C" in flag
    assert "3 C" in flag
    assert "light_001.fits" in flag


def test_the_nearest_slot_is_chosen_among_several(tmp_path: Path) -> None:
    """With cold and warm darks, a +0.5 C light is given the warm ones."""
    library = make_library(tmp_path)
    _cold, warm = add_temperature_darks(tmp_path, library)

    selection = library.select_dark_frames(
        camera=CAMERA, exposure=30.0, iso="100", offset="30", temperature_c=0.5
    )

    assert selection.frames == warm
    assert len(selection.flags) == 1
    assert "0.5 C" in selection.flags[0]


def test_every_dark_in_the_master_counts_toward_the_temperature_check(tmp_path: Path) -> None:
    """The first dark is near the lights, but the master as a whole is not."""
    library = make_library(tmp_path)
    near_first = write_frame(tmp_path / "a.fits", temperature_c=-10.4)
    far_second = write_frame(tmp_path / "b.fits", temperature_c=-7.7)
    library.add_dark_frame(near_first)
    library.add_dark_frame(far_second)
    assert len(library.dark_frames[CAMERA]) == 1

    selection = library.select_dark_frames(
        camera=CAMERA, exposure=30.0, iso="100", offset="30", temperature_c=-13.0
    )

    assert abs(-10.4 - -13.0) <= DEFAULT_DARK_TEMPERATURE_TOLERANCE_C
    assert len(selection.flags) == 1
    assert "-9.1 C" in selection.flags[0]
    assert "-10.4 to -7.7 C" in selection.flags[0]


def test_dark_temperature_flag_is_none_within_the_tolerance(tmp_path: Path) -> None:
    """A master 2 C away is fine, one 4 C away is flagged."""
    close = write_frame(tmp_path / "close.fits", temperature_c=-8.0)
    far = write_frame(tmp_path / "far.fits", temperature_c=-6.0)
    unknown = write_frame(tmp_path / "unknown.fits")

    assert dark_temperature_flag(-10.0, [close]) is None
    assert dark_temperature_flag(-10.0, [far]) is not None
    assert dark_temperature_flag(-10.0, [unknown]) is None
    assert dark_temperature_flag(-10.0, [far], tolerance_c=5.0) is None


def test_an_unknown_light_temperature_uses_the_largest_set_with_a_flag(tmp_path: Path) -> None:
    """Without the lights' temperature, darks are never pooled across slots."""
    library = make_library(tmp_path)
    cold, _warm = add_temperature_darks(tmp_path, library)
    extra_cold = write_frame(tmp_path / "cold_extra.fits", temperature_c=-10.0)
    library.add_dark_frame(extra_cold)

    selection = library.select_dark_frames(camera=CAMERA, exposure=30.0, iso="100", offset="30")

    assert selection.frames == [*cold, extra_cold]
    assert len(selection.flags) == 1
    assert "not recorded" in selection.flags[0]


def test_get_dark_frames_without_a_temperature_still_lists_every_dark(tmp_path: Path) -> None:
    """The plain getter keeps its meaning for callers that want everything."""
    library = make_library(tmp_path)
    cold, warm = add_temperature_darks(tmp_path, library)

    everything = library.get_dark_frames(camera=CAMERA, exposure=30.0, iso="100", offset="30")
    only_warm = library.get_dark_frames(
        camera=CAMERA, exposure=30.0, iso="100", offset="30", temperature_c=5.0
    )

    assert sorted(everything) == sorted([*cold, *warm])
    assert only_warm == warm


def test_a_dark_with_no_temperature_is_used_only_when_no_dark_has_one(tmp_path: Path) -> None:
    """Darks with no recorded temperature cannot be judged."""
    library = make_library(tmp_path)
    unknown = write_frame(tmp_path / "unknown.fits")
    library.add_dark_frame(unknown)

    alone = library.select_dark_frames(
        camera=CAMERA, exposure=30.0, iso="100", offset="30", temperature_c=-10.0
    )
    assert alone.frames == [unknown]
    assert alone.flags == []

    known = write_frame(tmp_path / "known.fits", temperature_c=-10.0)
    library.add_dark_frame(known)
    together = library.select_dark_frames(
        camera=CAMERA, exposure=30.0, iso="100", offset="30", temperature_c=-10.0
    )
    assert together.frames == [known]


def test_a_rescan_moves_a_dark_filed_under_an_older_key(tmp_path: Path) -> None:
    """A library saved before temperatures were tracked is re-filed."""
    library = make_library(tmp_path)
    dark = write_frame(tmp_path / "dark.fits", temperature_c=-10.0)
    library.deserialize({"dark_frames": {CAMERA: {"100@offset=30": {"30.0": [dark]}}}})

    library.add_dark_frame(dark)

    assert library.dark_frames == {CAMERA: {"100@offset=30@temp=-9": {"30.0": [dark]}}}


# ------------------------------------------------------------ binning


def test_a_binned_light_with_unbinned_darks_gets_no_dark_and_a_blocking_flag(tmp_path: Path) -> None:
    """A 2x2 light cannot use 1x1 darks, and the flag says why."""
    library = make_library(tmp_path)
    for index in range(3):
        library.add_dark_frame(write_frame(tmp_path / f"dark_{index}.fits", temperature_c=-10.0, binning=1))

    selection = library.select_dark_frames(
        camera=CAMERA,
        exposure=30.0,
        iso="100",
        offset="30",
        binning="2x2",
        temperature_c=-10.0,
        light_label="the 4 light frame(s) starting with light_001.fits",
    )

    assert selection.frames == []
    assert selection.flags == []
    assert len(selection.blocking_flags) == 1
    flag = selection.blocking_flags[0]
    assert "2x2" in flag
    assert "1x1" in flag
    assert "light_001.fits" in flag
    assert library.get_dark_frames(camera=CAMERA, exposure=30.0, iso="100", offset="30", binning="2x2") == []


def test_darks_of_each_binning_are_filed_apart_and_matched(tmp_path: Path) -> None:
    """A 2x2 light gets 2x2 darks only, and a 1x1 light 1x1 darks only."""
    library = make_library(tmp_path)
    unbinned = write_frame(tmp_path / "bin1.fits", temperature_c=-10.0, binning=1)
    binned = write_frame(tmp_path / "bin2.fits", temperature_c=-10.0, binning=2)
    library.add_dark_frame(unbinned)
    library.add_dark_frame(binned)

    assert set(library.dark_frames[CAMERA]) == {"100@offset=30@temp=-9", "100@offset=30@bin=2x2@temp=-9"}
    for binning, expected in (("1x1", unbinned), ("2x2", binned)):
        selection = library.select_dark_frames(
            camera=CAMERA, exposure=30.0, iso="100", offset="30", binning=binning, temperature_c=-10.0
        )
        assert selection.frames == [expected]
        assert selection.blocking_flags == []


def test_frames_with_no_binning_cards_count_as_unbinned(tmp_path: Path) -> None:
    """A header without XBINNING files as 1x1 and matches a 1x1 light."""
    library = make_library(tmp_path)
    dark = write_frame(tmp_path / "dark.fits", temperature_c=-10.0, binning=None)
    library.add_dark_frame(dark)

    selection = library.select_dark_frames(
        camera=CAMERA, exposure=30.0, iso="100", offset="30", binning="1x1", temperature_c=-10.0
    )

    assert selection.frames == [dark]


def test_no_darks_at_all_is_not_a_binning_mismatch(tmp_path: Path) -> None:
    """An empty library has nothing to refuse, so it raises no flag."""
    selection = make_library(tmp_path).select_dark_frames(
        camera=CAMERA, exposure=30.0, iso="100", offset="30", binning="2x2", temperature_c=-10.0
    )

    assert selection.frames == []
    assert selection.blocking_flags == []


def test_bias_and_flats_are_matched_by_binning(tmp_path: Path) -> None:
    """Bias and flat frames block on a binning mismatch."""
    library = make_library(tmp_path)
    bias_1 = write_frame(tmp_path / "bias1.fits", exposure=0.0, binning=1)
    flat_1 = write_frame(tmp_path / "flat1.fits", flat_filter="Luminance", binning=1)
    flat_2 = write_frame(tmp_path / "flat2.fits", flat_filter="Luminance", binning=2)
    library.add_bias_frame(bias_1)
    library.add_flat_frame(flat_1, telescope="T")
    library.add_flat_frame(flat_2, telescope="T")

    bias = library.select_bias_frames(camera=CAMERA, iso="100", offset="30", binning="2x2")
    flat_for_2x2 = library.select_flat_frames(
        telescope="T", camera=CAMERA, filter_type="Luminance", iso="100", offset="30", binning="2x2"
    )
    flat_for_1x1 = library.select_flat_frames(
        telescope="T", camera=CAMERA, filter_type="Luminance", iso="100", offset="30", binning="1x1"
    )

    assert bias.frames == []
    assert len(bias.blocking_flags) == 1
    assert "bias" in bias.blocking_flags[0]
    assert flat_for_2x2.frames == [flat_2]
    assert flat_for_1x1.frames == [flat_1]
    assert library.select_bias_frames(camera=CAMERA, iso="100", offset="30", binning="1x1").frames == [bias_1]


def test_flats_of_each_binning_are_listed_as_separate_groups(tmp_path: Path) -> None:
    """The flat sets are one per gain, offset and binning."""
    library = make_library(tmp_path)
    library.add_flat_frame(
        write_frame(tmp_path / "flat1.fits", flat_filter="Luminance", binning=1), telescope="T"
    )
    library.add_flat_frame(
        write_frame(tmp_path / "flat2.fits", flat_filter="Luminance", binning=2), telescope="T"
    )

    groups = library.list_flat_groups()

    assert sorted(group.binning for group in groups) == ["1x1", "2x2"]


def test_stats_report_binning_and_dark_temperature(tmp_path: Path) -> None:
    """The summary lists binning, and the temperature slot for darks."""
    library = make_library(tmp_path)
    library.add_dark_frame(write_frame(tmp_path / "dark.fits", temperature_c=-10.0, binning=2))

    dark = library.get_stats()["darks"][0]

    assert dark["binning"] == "2x2"
    assert dark["temperature_c"] == pytest.approx(-9.0)


# ------------------------------------------------------------ gain and offset


def test_a_gain_offset_fallback_names_what_was_applied_to_which_light(tmp_path: Path) -> None:
    """The soft flag names the gain and offset used and the lights."""
    library = make_library(tmp_path)
    dark = write_frame(tmp_path / "dark.fits", temperature_c=-10.0, gain=100, offset=30)
    library.add_dark_frame(dark)

    selection = library.select_dark_frames(
        camera=CAMERA,
        exposure=30.0,
        iso="0",
        offset="0",
        temperature_c=-10.0,
        light_label="the 7 light frame(s) starting with light_001.fits",
    )

    assert selection.frames == [dark]
    assert len(selection.flags) == 1
    flag = selection.flags[0]
    assert "gain 0 and offset 0" in flag
    assert "gain 100, offset 30" in flag
    assert "light_001.fits" in flag


def test_a_matching_gain_and_offset_raises_no_flag(tmp_path: Path) -> None:
    """Darks at the lights' own settings need no note."""
    library = make_library(tmp_path)
    library.add_dark_frame(write_frame(tmp_path / "dark.fits", temperature_c=-10.0))

    selection = library.select_dark_frames(
        camera=CAMERA, exposure=30.0, iso="100.0", offset="30", temperature_c=-10.0
    )

    assert len(selection.frames) == 1
    assert selection.flags == []


# ------------------------------------------------------------ staging a run


def stage_batch(
    tmp_path: Path, library: CalibrationLibrary, light_binning: int | None, light_temperature_c: float
) -> siril_interface.ImageProcessing:
    """Stage one light frame against a library, without starting Siril.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        A scratch folder for the light and the work directory.
    library : `CalibrationLibrary`
        The calibration library to draw from.
    light_binning : `int` or `None`
        The light's ``XBINNING``/``YBINNING``; no cards when `None`.
    light_temperature_c : `float`
        The light's ``CCD-TEMP``.

    Returns
    -------
    processor : `ImageProcessing`
        The processor after staging; its ``workdir`` holds the folders and its
        ``last_run_diagnostics`` the flags.
    """
    light = write_frame(tmp_path / "light_001.fits", temperature_c=light_temperature_c, binning=light_binning)
    processor = object.__new__(siril_interface.ImageProcessing)
    processor.workdir = str(tmp_path / "work")
    processor.calibration_library = library
    processor.last_run_diagnostics = {
        "corrupt_frames_skipped": [],
        "calibration_mismatch_flags": [],
        "calibration_blocking_flags": [],
        CALIBRATION_MATCH_BLOCKING_FLAGS_KEY: [],
    }
    frames = [
        {
            "path": light,
            "camera": CAMERA,
            "telescope": "T",
            "iso": "100",
            "offset": "30",
            "exposure": 30.0,
            "filter": "Luminance",
            "sensor_temperature_c": light_temperature_c,
            "binning": light_binning,
        }
    ]
    processor.build_directories("target", frames)
    return processor


def staged(processor: siril_interface.ImageProcessing, folder: str) -> list[str]:
    """List the files staged in one of the run's calibration folders.

    Returns
    -------
    names : `list` [`str`]
        The file names, sorted.
    """
    return sorted(os.listdir(os.path.join(processor.workdir, "target", folder)))


def test_staging_stacks_only_the_darks_of_the_lights_temperature(tmp_path: Path) -> None:
    """Cold and warm darks in one library no longer pool into one master."""
    library = make_library(tmp_path)
    add_temperature_darks(tmp_path, library)

    processor = stage_batch(tmp_path, library, light_binning=1, light_temperature_c=-10.0)

    assert len(staged(processor, "darks")) == 3
    assert processor.last_run_diagnostics["calibration_mismatch_flags"] == []
    assert processor.last_run_diagnostics[CALIBRATION_MATCH_BLOCKING_FLAGS_KEY] == []


def test_staging_a_far_temperature_records_a_soft_flag(tmp_path: Path) -> None:
    """A -10 C light with only +5 C darks stages them and records the flag."""
    library = make_library(tmp_path)
    for index in range(3):
        library.add_dark_frame(write_frame(tmp_path / f"warm_{index}.fits", temperature_c=5.0))

    processor = stage_batch(tmp_path, library, light_binning=1, light_temperature_c=-10.0)

    assert len(staged(processor, "darks")) == 3
    flags = processor.last_run_diagnostics["calibration_mismatch_flags"]
    assert len(flags) == 1
    assert "-10.0 C" in flags[0]
    assert "5.0 C" in flags[0]
    assert "light_001.fits" in flags[0]
    assert processor.last_run_diagnostics[CALIBRATION_MATCH_BLOCKING_FLAGS_KEY] == []


def test_staging_a_binned_light_applies_no_unbinned_dark_and_records_a_blocking_flag(tmp_path: Path) -> None:
    """A 2x2 light stages no 1x1 dark, flat or bias, and says so."""
    library = make_library(tmp_path)
    for index in range(3):
        library.add_dark_frame(write_frame(tmp_path / f"dark_{index}.fits", temperature_c=-10.0, binning=1))
    library.add_bias_frame(write_frame(tmp_path / "bias.fits", exposure=0.0, binning=1))
    library.add_flat_frame(
        write_frame(tmp_path / "flat.fits", flat_filter="Luminance", binning=1), telescope="T"
    )

    processor = stage_batch(tmp_path, library, light_binning=2, light_temperature_c=-10.0)

    assert staged(processor, "darks") == []
    assert staged(processor, "biases") == []
    assert staged(processor, "flats") == []
    blocking = processor.last_run_diagnostics[CALIBRATION_MATCH_BLOCKING_FLAGS_KEY]
    assert len(blocking) == 3
    assert all("2x2" in flag and "1x1" in flag for flag in blocking)
    assert processor.last_run_diagnostics["calibration_mismatch_flags"] == []
