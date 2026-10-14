"""Tests for how a stacking run prepares its master flat.

When the staged flat frames would give a noisy master flat, the run builds a
smoothed master itself and puts it where the Siril script expects it. These
tests check that, that colour sensors and cached masters are left alone, and
that the master flat's cache key changes with the smoothing recipe.
"""

import os
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.drivers import siril_interface
from astrometricslib.drivers.siril_interface import (
    _calibration_source_fingerprint,
    _master_recipe,
    build_flat_master_commands,
)

SIZE = 96


def make_processor() -> siril_interface.ImageProcessing:
    """Build an ImageProcessing without starting Siril.

    Returns
    -------
    processor : `ImageProcessing`
        An instance with no work directory or Siril process.
    """
    return object.__new__(siril_interface.ImageProcessing)


def stage_flats(target_folder: Path, count: int, level: float, noise: float) -> None:
    """Write flat frames into a staging folder, with an empty bias folder.

    Parameters
    ----------
    target_folder : `pathlib.Path`
        The run's staging directory.
    count : `int`
        The number of flat frames.
    level : `float`
        Mean brightness as a fraction of full scale (65535).
    noise : `float`
        Relative noise of each pixel.
    """
    (target_folder / "flats").mkdir(parents=True)
    (target_folder / "biases").mkdir()
    for index in range(count):
        generator = np.random.default_rng(index)
        data = level * 65535.0 * (1.0 + noise * generator.standard_normal((SIZE, SIZE)))
        fits.writeto(target_folder / "flats" / f"flat_{index:05d}.fits", data.astype(np.uint16))


def test_a_noisy_single_flat_is_assessed_for_smoothing(tmp_path: Path) -> None:
    """The assessment carries the blur width and the issues."""
    stage_flats(tmp_path, count=1, level=0.01, noise=0.045)
    assessment = make_processor().assess_staged_flats(str(tmp_path), uses_color_filter_array=False)
    assert assessment.needs_smoothing
    assert assessment.smoothing_sigma_pixels == pytest.approx(2.5, abs=0.2)
    assert any("take more flats" in issue for issue in assessment.issues)


def test_a_good_set_of_flats_is_left_alone(tmp_path: Path) -> None:
    """A quiet master flat has no issues and no blur."""
    stage_flats(tmp_path, count=30, level=0.4, noise=0.01)
    assessment = make_processor().assess_staged_flats(str(tmp_path), uses_color_filter_array=False)
    assert assessment.issues == []
    assert not assessment.needs_smoothing


def test_a_colour_sensor_flat_is_measured_but_not_smoothed(tmp_path: Path) -> None:
    """Blurring a Bayer mosaic would mix colours, so it is only reported."""
    stage_flats(tmp_path, count=1, level=0.01, noise=0.045)
    assessment = make_processor().assess_staged_flats(str(tmp_path), uses_color_filter_array=True)
    assert not assessment.needs_smoothing
    assert any("colour sensor" in issue for issue in assessment.issues)


def test_one_flat_is_loaded_blurred_and_saved() -> None:
    """A lone flat with no bias is used as it is, blurred before the save."""
    assert build_flat_master_commands(1, False, "", 2.5) == [
        "convert flat -out=../process",
        "cd ../process",
        "load flat_00001.fits",
        "gauss 2.5000",
        "save flat_stacked",
    ]
    assert "gauss" not in " ".join(build_flat_master_commands(1, False, "", None))


def test_one_flat_with_a_bias_is_calibrated_then_blurred_and_saved() -> None:
    """The bias comes off the lone flat first, then the blur, then the save."""
    assert build_flat_master_commands(1, True, "", 2.5) == [
        "convert flat -out=../process",
        "cd ../process",
        "calibrate_single flat_00001.fits -bias=bias_stacked",
        "load pp_flat_00001.fits",
        "gauss 2.5000",
        "save flat_stacked",
    ]


def test_several_flats_are_stacked_and_then_blurred() -> None:
    """The blur comes after the rejection stack, on the saved master."""
    commands = build_flat_master_commands(20, True, " -cfa", 3.0)
    assert commands[2] == "calibrate flat -bias=bias_stacked -cfa"
    assert commands[3] == "stack pp_flat rej 3 3 -norm=mul -out=flat_stacked"
    assert commands[4:] == ["load flat_stacked", "gauss 3.0000", "save flat_stacked"]
    assert build_flat_master_commands(20, False, "", None)[2] == "calibrate flat "
    assert len(build_flat_master_commands(20, False, "", None)) == 4


def test_only_the_flat_master_has_a_recipe_in_its_cache_key() -> None:
    """Bias and dark masters keep their old keys; the flat key is new."""
    assert _master_recipe("bias") == ""
    assert _master_recipe("dark") == ""
    assert _master_recipe("flat") != ""


def test_the_recipe_changes_the_fingerprint_of_the_same_frames(tmp_path: Path) -> None:
    """The same frames built a different way must not share a cache entry."""
    library_frame = tmp_path / "library.fits"
    library_frame.write_text("frame")
    staging = tmp_path / "staging"
    staging.mkdir()
    os.symlink(library_frame, staging / "staged_00000.fits")
    plain = _calibration_source_fingerprint(str(staging))
    assert _calibration_source_fingerprint(str(staging), "") == plain
    assert _calibration_source_fingerprint(str(staging), "flat-recipe-2") != plain


def test_staged_flats_are_judged_against_the_named_cameras_clip_ceiling(tmp_path: Path) -> None:
    """A D5300 flat at 4000 counts passes only when the camera is named."""
    (tmp_path / "flats").mkdir()
    generator = np.random.default_rng(7)
    data = 4000.0 * (1.0 + 0.01 * generator.standard_normal((SIZE, SIZE)))
    fits.writeto(tmp_path / "flats" / "flat_00000.fits", data.astype(np.uint16))
    processor = make_processor()
    without_camera = processor.assess_staged_flats(str(tmp_path), uses_color_filter_array=False)
    with_camera = processor.assess_staged_flats(
        str(tmp_path), uses_color_filter_array=False, camera="Nikon D5300"
    )
    assert any(issue.startswith("flats are faint:") for issue in without_camera.issues)
    assert not any(issue.startswith("flats are faint:") for issue in with_camera.issues)
    assert with_camera.full_scale == pytest.approx(16383.0)
