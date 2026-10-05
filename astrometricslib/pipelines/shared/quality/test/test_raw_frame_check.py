"""Purpose: Unit tests for the raw light-frame check.

Description: Builds small synthetic star fields (Gaussian stars on noisy
sky) so each failure the check looks for can be made on purpose: a trailed
frame, a soft frame, a frame that lost its stars, and a frame the field
moved in. A clean batch must come back unflagged.
"""

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.api.processing import QualityDiagnostics
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.pipelines.shared.quality import raw_frame_check

_SIZE = 512
"""Side of each synthetic frame, in pixels."""

_STAR_POSITIONS = np.random.default_rng(7).uniform(30, _SIZE - 30, size=(60, 2))
"""The same 60 star centres in every frame, so shifts can be recovered."""


def _frame(
    sigma_x: float = 1.6,
    sigma_y: float = 1.6,
    shift: tuple[float, float] = (0.0, 0.0),
    star_fraction: float = 1.0,
    trail_length: int = 0,
    seed: int = 0,
) -> np.ndarray:
    """Draw one synthetic frame.

    Parameters
    ----------
    sigma_x, sigma_y : `float`
        Star widths along x and y, in pixels.
    shift : `tuple` [`float`, `float`]
        Move of the whole field, ``(dy, dx)``.
    star_fraction : `float`
        Share of the stars that are drawn.
    trail_length : `int`
        If positive, each star is smeared this many pixels along x.
    seed : `int`
        Noise seed.

    Returns
    -------
    image : `numpy.ndarray`
        The frame as 16-bit counts.
    """
    rng = np.random.default_rng(seed)
    image = rng.normal(1300.0, 15.0, (_SIZE, _SIZE))
    yy, xx = np.mgrid[0:_SIZE, 0:_SIZE]
    count = int(len(_STAR_POSITIONS) * star_fraction)
    for centre_y, centre_x in _STAR_POSITIONS[:count]:
        centre_y, centre_x = centre_y + shift[0], centre_x + shift[1]
        if not (10 < centre_y < _SIZE - 10 and 10 < centre_x < _SIZE - 10):
            continue
        window = (
            slice(int(centre_y) - 40, int(centre_y) + 41),
            slice(int(centre_x) - 40, int(centre_x) + 41),
        )
        y_local, x_local = yy[window], xx[window]
        steps = max(trail_length, 1)
        for step in range(steps):
            x_offset = centre_x + step - steps / 2 if trail_length else centre_x
            profile = np.exp(
                -((x_local - x_offset) ** 2) / (2 * sigma_x**2) - (y_local - centre_y) ** 2 / (2 * sigma_y**2)
            )
            image[window] += 9000.0 / steps * profile
    return np.clip(image, 0, 65535).astype(np.uint16)


def _write_batch(folder: Path, frames: list[np.ndarray]) -> None:
    """Write `frames` as numbered FITS files in `folder`."""
    for index, frame in enumerate(frames):
        fits.PrimaryHDU(frame).writeto(folder / f"Target_Light_Luminance_{index:03d}.fits")


def _clean_batch(count: int = 5) -> list[np.ndarray]:
    """Build `count` clean frames that differ only in their noise.

    Returns
    -------
    frames : `list` [`numpy.ndarray`]
        The frames.
    """
    return [_frame(seed=seed) for seed in range(count)]


def test_a_clean_batch_is_not_flagged(tmp_path: Path) -> None:
    """Frames that differ only in noise raise no flags."""
    _write_batch(tmp_path, _clean_batch())

    report = raw_frame_check.check_raw_frames(folder=str(tmp_path))

    assert report["batch"]["frame_count"] == 5
    assert report["batch"]["flagged_count"] == 0


def test_a_frame_that_lost_its_stars_is_flagged(tmp_path: Path) -> None:
    """A frame with a fifth of the stars reads as trailed or clouded."""
    _write_batch(tmp_path, [*_clean_batch(4), _frame(star_fraction=0.2, seed=9)])

    flags = raw_frame_check.check_raw_frames(folder=str(tmp_path))["frames"][-1]["flags"]

    assert any("trailed or clouded" in flag for flag in flags)


def test_a_trailed_frame_is_flagged_for_its_streaks(tmp_path: Path) -> None:
    """Stars smeared 90 pixels long are caught as trails."""
    _write_batch(tmp_path, [*_clean_batch(4), _frame(trail_length=90, seed=9)])

    last = raw_frame_check.check_raw_frames(folder=str(tmp_path))["frames"][-1]

    assert last["longest_trail_px"] > 60
    assert any("streak" in flag for flag in last["flags"])


def test_a_soft_frame_is_flagged_for_its_star_width(tmp_path: Path) -> None:
    """Stars 1.6 times as wide as the rest read as soft."""
    _write_batch(tmp_path, [*_clean_batch(4), _frame(sigma_x=2.6, sigma_y=2.6, seed=9)])

    flags = raw_frame_check.check_raw_frames(folder=str(tmp_path))["frames"][-1]["flags"]

    assert any("soft" in flag for flag in flags)


def test_an_elongated_frame_is_flagged_for_its_roundness(tmp_path: Path) -> None:
    """Stars twice as wide as tall read as elongated."""
    _write_batch(tmp_path, [*_clean_batch(4), _frame(sigma_x=2.4, sigma_y=1.2, seed=9)])

    flags = raw_frame_check.check_raw_frames(folder=str(tmp_path))["frames"][-1]["flags"]

    assert any("elongated" in flag for flag in flags)


def test_the_shift_between_frames_is_measured_and_a_jump_flagged(tmp_path: Path) -> None:
    """A 5 px dither is reported quietly; a 60 px move is flagged."""
    frames = [_frame(seed=0), _frame(shift=(3.0, -4.0), seed=1), _frame(shift=(63.0, -4.0), seed=2)]
    _write_batch(tmp_path, frames)

    report = raw_frame_check.check_raw_frames(folder=str(tmp_path))["frames"]

    assert report[0]["shift_from_previous_px"] is None
    assert report[1]["shift_from_previous_px"] == pytest.approx([3.0, -4.0], abs=1.0)
    assert report[1]["flags"] == []
    assert report[2]["shift_from_previous_px"] == pytest.approx([60.0, 0.0], abs=1.0)
    assert any("moved" in flag for flag in report[2]["flags"])


def test_last_count_checks_only_the_newest_frames(tmp_path: Path) -> None:
    """The newest two of five frames are the ones measured."""
    _write_batch(tmp_path, _clean_batch())

    report = raw_frame_check.check_raw_frames(folder=str(tmp_path), last_count=2)

    assert [Path(f["path"]).name for f in report["frames"]] == [
        "Target_Light_Luminance_003.fits",
        "Target_Light_Luminance_004.fits",
    ]


def test_an_empty_folder_gives_an_empty_report(tmp_path: Path) -> None:
    """No frames is not an error."""
    assert raw_frame_check.check_raw_frames(folder=str(tmp_path)) == {
        "frames": [],
        "batch": {"frame_count": 0},
    }


def test_the_diagnostics_api_runs_the_check_on_a_folder(tmp_path: Path) -> None:
    """`frame_quality(kind="raw_check")` runs the same check on a folder."""
    _write_batch(tmp_path, _clean_batch(3))

    diagnostics = QualityDiagnostics(AppConfiguration(), MagicMock())
    report = diagnostics.frame_quality(folder_path=str(tmp_path), kind="raw_check", register_job=False)

    assert report.batch["frame_count"] == 3
    assert report.frames_checked == 3
