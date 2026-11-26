"""Tests for the check that a preview step's result is a usable image."""

from pathlib import Path

import numpy as np
from astropy.io import fits

from astrometricslib.pipelines.stacking.post_processing import (
    preview_steps,
)


def test_the_usability_check_accepts_a_good_image_and_rejects_bad_ones(tmp_path: Path) -> None:
    """Verify valid, mostly invalid, flat and unreadable images."""
    good = np.random.default_rng(0).normal(0.0, 1.0, (40, 40)).astype(np.float32)
    mostly_invalid = good.copy()
    mostly_invalid[:30, :] = np.nan
    flat = np.full((40, 40), 0.5, dtype=np.float32)
    paths = {}
    for name, data in (("good", good), ("invalid", mostly_invalid), ("flat", flat)):
        paths[name] = tmp_path / f"{name}.fits"
        fits.writeto(paths[name], data)
    broken = tmp_path / "broken.fits"
    broken.write_bytes(b"not a fits file")

    assert preview_steps._is_usable_image(str(paths["good"]))
    assert not preview_steps._is_usable_image(str(paths["invalid"]))
    assert not preview_steps._is_usable_image(str(paths["flat"]))
    assert not preview_steps._is_usable_image(str(broken))
