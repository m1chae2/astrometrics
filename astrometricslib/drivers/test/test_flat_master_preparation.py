"""Tests for how a stacking run prepares its master flat.

When the staged flat frames would give a noisy master flat, the run builds a
smoothed master itself and puts it where the Siril script expects it. These
tests check that, that colour sensors and cached masters are left alone, and
that the master flat's cache key changes with the smoothing recipe.
"""

import os
from pathlib import Path

import numpy as np
from astropy.io import fits

from astrometricslib.drivers import siril_interface
from astrometricslib.drivers.siril_interface import (
    _calibration_source_fingerprint,
    _master_recipe,
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


def test_a_noisy_single_flat_gets_a_smoothed_master(tmp_path: Path) -> None:
    """The master is written into the process folder and reported."""
    stage_flats(tmp_path, count=1, level=0.01, noise=0.045)
    assessment, written = make_processor().prepare_flat_master(
        str(tmp_path), uses_color_filter_array=False, restored_from_cache=False
    )
    assert written
    assert assessment.needs_smoothing
    master_path = tmp_path / "process" / "flat_stacked.fits"
    assert master_path.exists()
    with fits.open(master_path, memmap=False) as hdul:
        assert abs(float(np.mean(hdul[0].data)) - 0.5) < 1e-3


def test_a_good_set_of_flats_is_left_for_siril_to_stack(tmp_path: Path) -> None:
    """Nothing is written when the master flat is quiet enough."""
    stage_flats(tmp_path, count=30, level=0.4, noise=0.01)
    assessment, written = make_processor().prepare_flat_master(
        str(tmp_path), uses_color_filter_array=False, restored_from_cache=False
    )
    assert not written
    assert assessment.issues == []
    assert not (tmp_path / "process" / "flat_stacked.fits").exists()


def test_a_colour_sensor_flat_is_measured_but_not_smoothed(tmp_path: Path) -> None:
    """Blurring a Bayer mosaic would mix colours, so it is only reported."""
    stage_flats(tmp_path, count=1, level=0.01, noise=0.045)
    assessment, written = make_processor().prepare_flat_master(
        str(tmp_path), uses_color_filter_array=True, restored_from_cache=False
    )
    assert not written
    assert not assessment.needs_smoothing
    assert any("colour sensor" in issue for issue in assessment.issues)
    assert not (tmp_path / "process" / "flat_stacked.fits").exists()


def test_a_cached_master_is_not_rebuilt(tmp_path: Path) -> None:
    """A master restored from the cache already has the right recipe."""
    stage_flats(tmp_path, count=1, level=0.01, noise=0.045)
    assessment, written = make_processor().prepare_flat_master(
        str(tmp_path), uses_color_filter_array=False, restored_from_cache=True
    )
    assert not written
    assert assessment.needs_smoothing
    assert not (tmp_path / "process" / "flat_stacked.fits").exists()


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
