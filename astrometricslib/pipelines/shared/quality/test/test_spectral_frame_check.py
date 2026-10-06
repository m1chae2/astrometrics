"""Tests for measuring a raw slitless-spectrum frame.

A made-up frame is used: flat noisy sky, a bright zero-order star, and a
vertical streak running down from it with a known width and brightness.
"""

from pathlib import Path

import numpy as np
import pytest

from astrometricslib.pipelines.shared.quality.spectral_frame_check import (
    SATURATION_ADU,
    analyze_spectral_frame,
    summarize_spectral_frames,
)

SKY = 400.0
STAR_ROW, STAR_COLUMN = 200, 300
OFFSET, LENGTH = 50.0, 300.0
GEOMETRY = {"vertical": True, "positive": True, "offset_px": OFFSET, "length_px": LENGTH}


def make_frame(
    streak_peak: float = 3000.0, width_sigma: float = 2.0, clip_at: int | None = None
) -> np.ndarray:
    """Build a noisy sky, a zero-order star and a vertical streak.

    Parameters
    ----------
    streak_peak : `float`
        The streak's height above the sky.
    width_sigma : `float`
        The streak's Gaussian width across.
    clip_at : `int`, optional
        A row inside the spectrum where a small saturated patch is placed.

    Returns
    -------
    frame : `numpy.ndarray`
        The image.
    """
    generator = np.random.default_rng(5)
    frame = generator.normal(SKY, 5.0, (700, 600))
    rows, columns = np.mgrid[:700, :600]
    frame += 66000.0 * np.exp(-((rows - STAR_ROW) ** 2 + (columns - STAR_COLUMN) ** 2) / 8.0)
    along = rows - STAR_ROW
    inside = (along >= OFFSET) & (along <= OFFSET + LENGTH)
    frame += np.where(inside, streak_peak, 0.0) * np.exp(
        -((columns - STAR_COLUMN) ** 2) / (2 * width_sigma**2)
    )
    if clip_at is not None:
        frame[clip_at : clip_at + 3, STAR_COLUMN - 1 : STAR_COLUMN + 2] = SATURATION_ADU + 100
    return frame


def analyze(frame: np.ndarray, predict: float | None = None) -> dict:
    """Run the analysis on a frame made by `make_frame`.

    Returns
    -------
    measurements : `dict`
        The result of `analyze_spectral_frame`.
    """
    return analyze_spectral_frame(
        frame, (STAR_ROW, STAR_COLUMN), exposure_seconds=2.0, predict_exposure_seconds=predict, **GEOMETRY
    )


def test_width_and_peak_of_the_streak_are_recovered() -> None:
    """A Gaussian of sigma 2 has a full width at half maximum of 4.7 px."""
    result = analyze(make_frame())
    assert 4.3 < result["spectrum_width_px"] < 5.1
    assert 2500 < result["spectrum_peak_above_sky_adu"] < 3300
    assert result["spectrum_pixels_saturated"] == 0


def test_the_zero_order_clipping_is_not_counted_as_spectrum_loss() -> None:
    """A clipped star core is reported on its own."""
    result = analyze(make_frame(), None)
    assert result["zero_order_saturated"] is True
    assert result["saturated_patches_in_spectrum"] == []


def test_a_clipped_patch_in_the_spectrum_is_located_along_it() -> None:
    """A patch 120 rows below the star is reported at 120 px."""
    result = analyze(make_frame(clip_at=STAR_ROW + 120))
    patch = result["saturated_patches_in_spectrum"][0]
    assert abs(patch["distance_along_spectrum_px"] - 121) <= 2
    assert patch["pixels"] == 9


def test_the_peak_is_predicted_at_another_exposure() -> None:
    """Doubling the exposure doubles the peak above the sky."""
    result = analyze(make_frame(), predict=4.0)
    prediction = result["predicted"]
    assert abs(prediction["spectrum_peak_adu"] - (2 * result["spectrum_peak_above_sky_adu"] + SKY)) < 2
    assert prediction["spectrum_would_saturate"] is False
    assert (
        analyze(make_frame(streak_peak=40000.0), predict=4.0)["predicted"]["spectrum_would_saturate"] is True
    )


def test_the_summary_groups_by_exposure_and_by_pier_side() -> None:
    """Tilts are summarised per pier side, and clipped frames per exposure."""
    base = {
        "trail_contrast": 500.0,
        "zero_order_saturated": True,
        "spectrum_pixels_saturated": 0,
        "spectrum_width_px": 5.0,
    }
    rows = [
        {**base, "exposure_seconds": 2.0, "pier_side": "EAST", "tilt_degrees": 4.1},
        {**base, "exposure_seconds": 2.0, "pier_side": "WEST", "tilt_degrees": 1.0},
        {
            **base,
            "exposure_seconds": 5.0,
            "pier_side": "WEST",
            "tilt_degrees": 1.2,
            "spectrum_pixels_saturated": 9,
        },
        {"exposure_seconds": 5.0, "pier_side": "WEST", "error": "no zero order"},
    ]
    summary = summarize_spectral_frames(rows, minimum_contrast=10.0)
    assert summary["by_exposure"]["5"]["frames"] == 2
    assert summary["by_exposure"]["5"]["measured"] == 1
    assert summary["by_exposure"]["5"]["spectrum_has_saturated_pixels"] == 1
    assert summary["by_pier_side"]["EAST"]["median_tilt_degrees"] == pytest.approx(4.1)
    assert summary["by_pier_side"]["WEST"]["largest_tilt_degrees"] == pytest.approx(1.2)


def test_a_frame_without_a_zero_order_star_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a frame with no clear zero order raises `ProcessingError`.

    It used to return ``{"error": ...}``, which a caller could mistake for
    a measurement.
    """
    from astropy.io import fits

    from astrometricslib.foundation.errors import ProcessingError
    from astrometricslib.pipelines.shared.quality import spectral_frame_check
    from astrometricslib.pipelines.stacking.processing import group_alignment

    path = tmp_path / "flat.fits"
    fits.PrimaryHDU(np.full((64, 64), SKY, dtype=np.float32)).writeto(path)
    monkeypatch.setattr(group_alignment, "find_zero_order_position", lambda plane: None)

    with pytest.raises(ProcessingError, match="zero-order"):
        spectral_frame_check.measure_spectral_frame_file(str(path), "camera", 5.0, GEOMETRY)
