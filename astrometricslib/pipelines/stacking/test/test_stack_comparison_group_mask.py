"""Tests for finding a combined stack's patched cores from its group files.

The star-width tools read a combined stack from disk. To leave out the stars
on cores patched from a shorter exposure, as the stacking stage does, they
rebuild the mask from the ``groups`` folder beside the stack. The stage also
trims the stack's edges afterwards, so the mask has to be moved by that trim.
"""

import json
from pathlib import Path

import numpy as np

from astrometricslib.drivers.fits_access import write_image
from astrometricslib.pipelines.stacking.post_processing.stack_comparison import (
    combined_stack_excluded_mask,
)


def make_star_field(size: int = 300) -> np.ndarray:
    """Build a noisy field with a clipped star and some fainter ones.

    Returns
    -------
    image : `numpy.ndarray`
        A float image holding values up to 1.0 at the clipped star.
    """
    generator = np.random.default_rng(3)
    image = generator.normal(0.1, 0.01, (size, size))
    rows, columns = np.mgrid[:size, :size]
    for row, column, height in [
        (100, 120, 5.0),
        (180, 60, 0.4),
        (60, 220, 0.3),
        (230, 200, 0.5),
        (140, 150, 0.35),
    ]:
        image += height * np.exp(-((rows - row) ** 2 + (columns - column) ** 2) / 8.0)
    return np.clip(image, 0.0, 1.0)


def write_stack_with_manifest(directory: Path, crop: int) -> tuple[str, str]:
    """Write a group stack, a combined stack trimmed by `crop`, and a manifest.

    Returns
    -------
    combined_path, group_path : `str`, `str`
        The two files' paths.
    """
    group = make_star_field()
    groups_folder = directory / "groups"
    groups_folder.mkdir()
    group_path = str(groups_folder / "Target_exp60s.fits")
    write_image(group_path, group.astype(np.float32))
    combined_path = str(directory / "Target.fits")
    write_image(combined_path, group[crop : crop + 250, crop + 3 : crop + 253].astype(np.float32))
    manifest = {
        "groups": [{"weight": 1.0, "stack_path": group_path}, {"weight": 5.0, "stack_path": group_path}]
    }
    (groups_folder / "Target_manifest.json").write_text(json.dumps(manifest))
    return combined_path, group_path


def test_the_mask_is_moved_by_the_trim(tmp_path: Path) -> None:
    """The clipped star lands in the same place in the trimmed image."""
    combined_path, _ = write_stack_with_manifest(tmp_path, crop=12)
    mask = combined_stack_excluded_mask(combined_path, (250, 250))
    assert mask is not None
    assert mask.shape == (250, 250)
    assert mask[100 - 12, 120 - 15]
    assert not mask[180 - 12, 60 - 15]


def test_a_stack_without_a_manifest_has_no_mask(tmp_path: Path) -> None:
    """With no groups folder the whole image is measured."""
    path = str(tmp_path / "Lone.fits")
    write_image(path, make_star_field().astype(np.float32))
    assert combined_stack_excluded_mask(path, (300, 300)) is None
