"""Purpose: Unit tests for narrow-contaminant rejection in a nebula's box.

Description: A nebula's reading box is wide enough that other stars' trails
cross it. These tests build lines of pixels with a known smooth nebula level
and a known narrow spike, then check that the spike is replaced and the
smooth light is kept, and that the extractor only does this when asked.
"""

import numpy as np
import pytest

from astrometricslib.pipelines.spectroscopy.spectrum_extractor import (
    CONTAMINANT_BASELINE_WIDTH_PX,
    SpectrumExtractor,
    replace_narrow_spikes,
)

BOX_WIDTH_PX = 121
NEBULA_LEVEL = 10.0
SPIKE_HEIGHT = 200.0


def _noisy_flat_line(seed: int = 1) -> np.ndarray:
    """Build a flat nebula level with a little noise.

    Returns
    -------
    line : `numpy.ndarray`
        One line of pixels.
    """
    return NEBULA_LEVEL + np.random.default_rng(seed).normal(0.0, 0.5, BOX_WIDTH_PX)


def test_a_narrow_spike_is_replaced_by_the_smooth_level() -> None:
    """A 3-pixel trail spike goes back to about the nebula level."""
    line = _noisy_flat_line()
    line[40:43] += SPIKE_HEIGHT

    cleaned = replace_narrow_spikes(line)

    assert cleaned[40:43] == pytest.approx(NEBULA_LEVEL, abs=1.5)
    assert cleaned.sum() == pytest.approx(NEBULA_LEVEL * BOX_WIDTH_PX, rel=0.02)


def test_smooth_wide_light_is_kept() -> None:
    """A nebula's own broad bump is wider than the window and survives."""
    positions = np.arange(BOX_WIDTH_PX)
    line = NEBULA_LEVEL + 20.0 * np.exp(-0.5 * ((positions - 60) / 20.0) ** 2)
    line += np.random.default_rng(2).normal(0.0, 0.5, BOX_WIDTH_PX)

    cleaned = replace_narrow_spikes(line)

    assert cleaned.sum() == pytest.approx(line.sum(), rel=0.01)


def test_the_input_line_is_not_changed() -> None:
    """The helper works on a copy."""
    line = _noisy_flat_line()
    line[70] += SPIKE_HEIGHT
    original = line.copy()

    replace_narrow_spikes(line)

    assert np.array_equal(line, original)


def test_a_dark_dip_is_left_alone() -> None:
    """Other stars only add light, so a dip is not a contaminant."""
    line = _noisy_flat_line()
    line[50:53] -= 8.0

    assert np.array_equal(replace_narrow_spikes(line), line)


def test_a_line_shorter_than_the_window_comes_back_unchanged() -> None:
    """There is no smooth level to compare against on a tiny line."""
    line = np.full(CONTAMINANT_BASELINE_WIDTH_PX - 1, NEBULA_LEVEL)
    line[5] += SPIKE_HEIGHT

    assert np.array_equal(replace_narrow_spikes(line), line)


def test_a_line_with_missing_values_comes_back_unchanged() -> None:
    """A NaN makes the smooth level unreliable, so nothing is replaced."""
    line = _noisy_flat_line()
    line[3] = np.nan
    line[70] += SPIKE_HEIGHT

    assert np.array_equal(replace_narrow_spikes(line), line, equal_nan=True)


def _image_with_crossing_trail() -> np.ndarray:
    """Build an image whose every row has a flat nebula and a bright spike.

    Returns
    -------
    image : `numpy.ndarray`
        A 200 x 200 image; the spectrum runs down the rows, so each row is a
        cross-section, and a spike 3 pixels wide sits at column 40.
    """
    image = np.full((200, 200), NEBULA_LEVEL)
    image[:, 40:43] += SPIKE_HEIGHT
    return image


def test_the_extractor_removes_the_spike_only_when_asked() -> None:
    """The switch is off by default, so ordinary stars are not affected."""
    image = _image_with_crossing_trail()
    box_kwargs = {
        "line_index": 100,
        "aperture_center": 100,
        "aperture_half_width": 60,
        "is_horizontal": False,
    }

    plain = SpectrumExtractor(radius=60, subtract_sky_background=False)
    rejecting = SpectrumExtractor(radius=60, subtract_sky_background=False, reject_narrow_contaminants=True)

    assert plain._sum_aperture_minus_sky(image, **box_kwargs) == pytest.approx(
        NEBULA_LEVEL * BOX_WIDTH_PX + 3 * SPIKE_HEIGHT
    )
    assert rejecting._sum_aperture_minus_sky(image, **box_kwargs) == pytest.approx(
        NEBULA_LEVEL * BOX_WIDTH_PX, rel=1e-6
    )
