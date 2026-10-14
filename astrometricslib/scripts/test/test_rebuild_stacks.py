"""Purpose: Unit tests for the rebuild-stacks script's decisions.

Description: The script reads the library and lists stacks that need
rebuilding: blank ones (mostly exact zeros, except spectral stacks whose sky is
legitimately dark) and sessions shot at several exposure lengths. These tests
check those decisions, that a stack file is read for its zero share, and that
rebuilding cannot be started without naming a target and a backup folder.
"""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
from astropy.io import fits

from astrometricslib.scripts.rebuild_stacks import (
    blank_stack_reason,
    main,
    mixed_exposure_counts,
    stack_zero_fraction,
)


def test_a_mostly_zero_imaging_stack_is_blank() -> None:
    """A stack that is 99% zero is reported with the share in the reason."""
    reason = blank_stack_reason("/lib/M_31_NONE_Stacked.fits", 0.99)

    assert reason is not None
    assert "99%" in reason


def test_a_normal_or_spectral_stack_is_not_blank() -> None:
    """A stack with few zeros, or a spectral stack with many, is left alone."""
    assert blank_stack_reason("/lib/M_13_L_Stacked.fits", 0.01) is None
    assert blank_stack_reason("/lib/Vega_SPEC_Stacked.fits", 0.93) is None
    assert blank_stack_reason("/lib/M_13_L_Stacked.fits", None) is None


def test_only_sessions_with_two_large_exposure_groups_count_as_mixed() -> None:
    """Two lengths of five or more frames are mixed; a stray frame is not."""
    frames = [SimpleNamespace(exposure="60.0")] * 14 + [SimpleNamespace(exposure="300.0")] * 20
    stray = [*frames, SimpleNamespace(exposure="30.0")]

    assert mixed_exposure_counts(frames) == {60.0: 14, 300.0: 20}
    assert mixed_exposure_counts(stray) == {60.0: 14, 300.0: 20}
    assert mixed_exposure_counts([SimpleNamespace(exposure="30.0")] * 40) == {}
    assert mixed_exposure_counts([SimpleNamespace(exposure="60.0")] * 3 + frames[14:]) == {}


def test_the_zero_share_of_a_stack_file_is_measured(tmp_path: Path) -> None:
    """A file about 90% zero reads as about 0.9; a missing file as `None`."""
    generator = np.random.default_rng(1)
    data = generator.uniform(0.1, 1.0, (120, 120)).astype(np.float32)
    data[generator.random((120, 120)) < 0.9] = 0.0
    path = tmp_path / "stack.fits"
    fits.writeto(path, data)

    assert 0.8 < stack_zero_fraction(str(path)) < 0.97
    assert stack_zero_fraction(str(tmp_path / "missing.fits")) is None


def test_rebuilding_needs_a_named_target_and_a_backup_folder() -> None:
    """`--apply` alone is refused, so nothing is rebuilt in bulk."""
    assert main(["--apply"]) == 2
    assert main(["--apply", "--target", "M 31"]) == 2
