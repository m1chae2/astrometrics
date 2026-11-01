"""Purpose: Tests for the bias that calibrates the flats.

Description: A flat holds the camera's bias level, and only a bias taken at the
flats' own gain and offset removes it. These tests check that staging picks
that bias when the flats were taken at another offset than the lights, that
nothing extra is staged when the offsets match, that a missing bias is flagged
and the stack keeps going, that the master flat's cache key follows the bias,
and that the Siril commands subtract the right master. Spectral flats are
covered with their own filter name.
"""

import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib._internal.drivers import siril_interface
from astrometricslib._internal.drivers.calibration_library import (
    BIAS_FRAME_PATHS_KEY,
    CALIBRATION_MATCH_BLOCKING_FLAGS_KEY,
    FLAT_BIAS_FRAME_PATHS_KEY,
    CalibrationLibrary,
)
from astrometricslib._internal.drivers.siril_interface import build_flat_master_commands
from astrometricslib._internal.drivers.siril_interface.script_builders import (
    StagedFrameCounts,
    build_master_calibration_commands,
)
from astrometricslib._internal.drivers.siril_interface.staging import FLAT_MASTER_RECIPE, _master_recipe

CAMERA = "ZWO ASI 533MM Pro"
LIGHT_OFFSET = 0
FLAT_OFFSET = 10


def make_library(tmp_path: Path) -> CalibrationLibrary:
    """Build an empty library that saves and loads inside `tmp_path`.

    Returns
    -------
    library : `CalibrationLibrary`
        A library with no frames.
    """
    app_config = SimpleNamespace(get_library_file_path=lambda name: str(tmp_path / name))
    return CalibrationLibrary(app_config=app_config)


def write_frame(path: Path, offset: int, flat_filter: str | None = None, exposure: float = 0.0) -> str:
    """Write a small FITS frame with the given offset.

    Returns
    -------
    path : `str`
        The path written.
    """
    hdu = fits.PrimaryHDU(np.full((8, 8), 100.0, dtype=np.float32))
    header = hdu.header
    header["INSTRUME"] = "ZWO CCD ASI533MM Pro"
    header["EXPTIME"] = exposure
    header["GAIN"] = 100
    header["OFFSET"] = offset
    header["XBINNING"] = 1
    header["YBINNING"] = 1
    if flat_filter is not None:
        header["FILTER"] = flat_filter
    hdu.writeto(path)
    return str(path)


def add_frames(
    tmp_path: Path,
    library: CalibrationLibrary,
    biases_at_offsets: list[int],
    flats_at_offset: int,
    flat_filter: str = "Luminance",
) -> dict[str, list[str]]:
    """Fill a library with two biases at each offset and two flats.

    Returns
    -------
    paths : `dict` [`str`, `list` [`str`]]
        The bias paths by offset (keys ``"bias_<offset>"``) and the flats.
    """
    paths: dict[str, list[str]] = {"flats": []}
    for offset in biases_at_offsets:
        paths[f"bias_{offset}"] = []
        for index in range(2):
            path = write_frame(tmp_path / f"bias_{offset}_{index}.fits", offset)
            library.add_bias_frame(path)
            paths[f"bias_{offset}"].append(path)
    for index in range(2):
        path = write_frame(tmp_path / f"flat_{index}.fits", flats_at_offset, flat_filter=flat_filter)
        library.add_flat_frame(path, telescope="T")
        paths["flats"].append(path)
    return paths


def stage(
    tmp_path: Path, library: CalibrationLibrary, light_filter: str = "Luminance"
) -> siril_interface.ImageProcessing:
    """Stage one light at offset 0 against a library, without Siril.

    Returns
    -------
    processor : `ImageProcessing`
        The processor after staging.
    """
    light = write_frame(tmp_path / "light_001.fits", LIGHT_OFFSET, exposure=30.0)
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
            "offset": str(LIGHT_OFFSET),
            "exposure": 30.0,
            "filter": light_filter,
            "binning": 1,
        }
    ]
    processor.build_directories("target", frames)
    return processor


def staged_targets(processor: siril_interface.ImageProcessing, folder: str) -> list[str]:
    """Give the real files that the links in one staged folder point to.

    Returns
    -------
    paths : `list` [`str`]
        The link targets, sorted.
    """
    directory = os.path.join(processor.workdir, "target", folder)
    return sorted(os.path.realpath(os.path.join(directory, name)) for name in os.listdir(directory))


def flat_bias_flags(processor: siril_interface.ImageProcessing) -> list[str]:
    """Pick the soft flags that are about the flats' bias.

    Returns
    -------
    flags : `list` [`str`]
        The matching flags.
    """
    return [
        flag
        for flag in processor.last_run_diagnostics["calibration_mismatch_flags"]
        if "applied to the flats" in flag or "calibrate the flats" in flag
    ]


def test_flats_at_another_offset_get_their_own_bias(tmp_path: Path) -> None:
    """Offset-0 lights with offset-10 flats get an offset-10 flat bias."""
    library = make_library(tmp_path)
    paths = add_frames(tmp_path, library, [LIGHT_OFFSET, FLAT_OFFSET], FLAT_OFFSET)

    processor = stage(tmp_path, library)

    assert staged_targets(processor, "biases") == sorted(paths["bias_0"])
    assert staged_targets(processor, "flat_biases") == sorted(paths["bias_10"])
    assert processor.last_run_diagnostics[BIAS_FRAME_PATHS_KEY] == paths["bias_0"]
    assert processor.last_run_diagnostics[FLAT_BIAS_FRAME_PATHS_KEY] == paths["bias_10"]
    assert flat_bias_flags(processor) == []


def test_matching_offsets_stage_no_extra_bias(tmp_path: Path) -> None:
    """Flats at the lights' offset use the lights' bias."""
    library = make_library(tmp_path)
    add_frames(tmp_path, library, [LIGHT_OFFSET, FLAT_OFFSET], LIGHT_OFFSET)

    processor = stage(tmp_path, library)

    assert staged_targets(processor, "flat_biases") == []
    assert FLAT_BIAS_FRAME_PATHS_KEY not in processor.last_run_diagnostics
    assert flat_bias_flags(processor) == []


def test_no_bias_at_the_flats_offset_warns_and_uses_the_lights_bias(tmp_path: Path) -> None:
    """With no offset-10 bias, the stack runs and a soft flag says why."""
    library = make_library(tmp_path)
    paths = add_frames(tmp_path, library, [LIGHT_OFFSET], FLAT_OFFSET)

    processor = stage(tmp_path, library)

    assert staged_targets(processor, "flat_biases") == []
    assert staged_targets(processor, "biases") == sorted(paths["bias_0"])
    flags = flat_bias_flags(processor)
    assert len(flags) == 1
    assert "offset 10" in flags[0]
    assert "gain 100, offset 0" in flags[0]
    assert "pedestal" in flags[0]
    assert processor.last_run_diagnostics[CALIBRATION_MATCH_BLOCKING_FLAGS_KEY] == []


def test_spectral_flats_follow_the_same_rule(tmp_path: Path) -> None:
    """Flats filed under a spectral filter name get their own bias too."""
    library = make_library(tmp_path)
    paths = add_frames(tmp_path, library, [LIGHT_OFFSET, FLAT_OFFSET], FLAT_OFFSET, flat_filter="SPEC")

    processor = stage(tmp_path, library, light_filter="SPEC")

    assert staged_targets(processor, "flats") == sorted(paths["flats"])
    assert staged_targets(processor, "flat_biases") == sorted(paths["bias_10"])


def test_no_flats_means_no_flat_bias(tmp_path: Path) -> None:
    """Without staged flats there is nothing to calibrate, so no extra bias."""
    library = make_library(tmp_path)
    add_frames(tmp_path, library, [LIGHT_OFFSET, FLAT_OFFSET], FLAT_OFFSET, flat_filter="Red")

    processor = stage(tmp_path, library, light_filter="Luminance")

    assert staged_targets(processor, "flats") == []
    assert staged_targets(processor, "flat_biases") == []


def test_the_flat_cache_key_follows_the_bias(tmp_path: Path) -> None:
    """The same flats with different biases keep different cache keys."""
    mismatched_directory = tmp_path / "mismatched"
    matching_directory = tmp_path / "matching"
    mismatched_directory.mkdir()
    matching_directory.mkdir()
    mismatched_library = make_library(mismatched_directory)
    add_frames(mismatched_directory, mismatched_library, [LIGHT_OFFSET, FLAT_OFFSET], FLAT_OFFSET)
    matching_library = make_library(matching_directory)
    add_frames(matching_directory, matching_library, [LIGHT_OFFSET], LIGHT_OFFSET)
    mismatched = stage(mismatched_directory, mismatched_library)
    matching = stage(matching_directory, matching_library)

    mismatched_recipe = _master_recipe("flat", os.path.join(mismatched.workdir, "target"))
    matching_recipe = _master_recipe("flat", os.path.join(matching.workdir, "target"))

    assert mismatched_recipe.startswith(f"{FLAT_MASTER_RECIPE}|bias=")
    assert matching_recipe.startswith(f"{FLAT_MASTER_RECIPE}|bias=")
    assert mismatched_recipe != matching_recipe
    # The same staging gives the same key again.
    assert _master_recipe("flat", os.path.join(mismatched.workdir, "target")) == mismatched_recipe


def test_the_recipe_is_version_six_and_other_kinds_have_none() -> None:
    """Raising the recipe retires every older flat master."""
    assert FLAT_MASTER_RECIPE == "flat-recipe-6"
    assert _master_recipe("flat_bias") == ""
    assert _master_recipe("flat") == FLAT_MASTER_RECIPE


def test_a_flat_with_no_bias_at_all_has_a_key_that_says_so(tmp_path: Path) -> None:
    """With no bias staged, the key holds the word none, not an empty gap."""
    target = tmp_path / "target"
    (target / "biases").mkdir(parents=True)
    assert _master_recipe("flat", str(target)) == f"{FLAT_MASTER_RECIPE}|bias=none"


def test_the_flat_is_calibrated_with_the_named_bias_master() -> None:
    """The flat commands subtract the master they are given."""
    several = build_flat_master_commands(5, True, "", None, "flat_bias_stacked")
    assert "calibrate flat -bias=flat_bias_stacked" in several
    lone = build_flat_master_commands(1, True, "", None, "flat_bias_stacked")
    assert "calibrate_single flat_00001.fits -bias=flat_bias_stacked" in lone
    assert build_flat_master_commands(5, True, "", None) == build_flat_master_commands(
        5, True, "", None, "bias_stacked"
    )
    assert "calibrate flat -bias=bias_stacked" in build_flat_master_commands(5, True, "", None)


def test_the_script_builds_the_flat_bias_master_only_when_it_is_staged() -> None:
    """Only differing offsets add a flat bias master to the script."""
    same = build_master_calibration_commands("/w", StagedFrameCounts(5, 5, 5, 20), set(), False, None)
    assert not any("flat_bias" in command or "flatbias" in command for command in same)
    assert "calibrate flat -bias=bias_stacked" in same

    different = build_master_calibration_commands(
        "/w", StagedFrameCounts(5, 5, 5, 20, flat_biases=4), set(), False, None
    )
    assert "cd /w/flat_biases" in different
    assert "stack flatbias rej 3 3 -nonorm -out=flat_bias_stacked" in different
    assert "calibrate flat -bias=flat_bias_stacked" in different
    # The lights' own bias master is still built.
    assert "stack bias rej 3 3 -nonorm -out=bias_stacked" in different

    cached_flat = build_master_calibration_commands(
        "/w", StagedFrameCounts(5, 5, 5, 20, flat_biases=4), {"flat"}, False, None
    )
    assert not any("flatbias" in command for command in cached_flat)


@pytest.mark.parametrize("lone_flat", [True, False])
def test_flat_bias_is_used_even_without_a_lights_bias(lone_flat: bool) -> None:
    """A flat bias alone still makes the flat subtract a bias."""
    counts = StagedFrameCounts(0, 0, 1 if lone_flat else 3, 5, flat_biases=2)
    commands = build_master_calibration_commands("/w", counts, set(), False, None)
    assert any("-bias=flat_bias_stacked" in command for command in commands)
