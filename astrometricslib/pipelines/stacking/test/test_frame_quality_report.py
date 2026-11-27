"""Tests for `measure_frame_quality`, the owner of ``quality.frames.measure``.

The raw sky level in ADU grows with the exposure length, so a ladder of
longer exposures under a steady sky must not look like a drift. These tests
build small fake frames, replace the batch check, and check which numbers are
trended, that the batch check's star width names its estimator, and how the
frames' kind is worked out.
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest

from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.target import FrameRecord
from astrometricslib.pipelines.shared.quality.star_width_labels import RAW_CHECK_WIDTH_ESTIMATOR
from astrometricslib.pipelines.stacking import frame_quality_report

RAW_CHECK = "astrometricslib.pipelines.shared.quality.raw_frame_check.check_raw_frames"
MEASURED_AT = datetime(2026, 11, 27, tzinfo=UTC)


def _frames(exposures: list[float]) -> list[FrameRecord]:
    """Build one light frame per exposure, in time order.

    Parameters
    ----------
    exposures : `list` [`float`]
        The exposure of each frame, in seconds.

    Returns
    -------
    frames : `list` [`FrameRecord`]
        The frames.
    """
    return [
        FrameRecord(
            path=f"/lights/f{number}.fits",
            role="LIGHT",
            camera="cam",
            exposure=str(exposure),
            timestamp=float(number),
        )
        for number, exposure in enumerate(exposures)
    ]


def _checked(paths: list[str], exposures: list[float]) -> dict[str, Any]:
    """Stand in for the batch check: a steady sky, so ADU follows the exposure.

    Parameters
    ----------
    paths : `list` [`str`]
        The frames checked.
    exposures : `list` [`float`]
        Their exposures, in the same order.

    Returns
    -------
    report : `dict`
        One row per frame and a batch summary.
    """
    rows = [
        {"path": path, "fwhm_px": 2.5, "star_count": 100, "sky_median_adu": 400.0 + 10.0 * exposure}
        for path, exposure in zip(paths, exposures, strict=True)
    ]
    return {"frames": rows, "batch": {"frame_count": len(rows)}}


def _measure(exposures: list[float]) -> Any:
    """Run the owner on fake frames with the batch check replaced.

    Parameters
    ----------
    exposures : `list` [`float`]
        The exposure of each frame, in seconds.

    Returns
    -------
    report : `FrameQualityReport`
        The report.
    """
    with patch(RAW_CHECK, side_effect=lambda paths: _checked(paths, exposures)):
        return frame_quality_report.measure_frame_quality(
            _frames(exposures), None, MEASURED_AT, trend_frames=10, trend_threshold_pct=15.0
        )


def test_a_ladder_of_exposures_is_not_a_sky_drift() -> None:
    """The ADU sky of longer exposures is left out of the drifts."""
    report = _measure([30.0, 30.0, 60.0, 60.0, 120.0, 120.0])

    assert frame_quality_report.SKY_LEVEL_KEY not in report.checks.trends["metrics"]
    assert not report.checks.trends.get("alerts")
    assert "different exposure lengths" in report.checks.trends["notes"][0]


def test_one_exposure_length_trends_the_sky_level() -> None:
    """With one exposure length the sky level is trended."""
    report = _measure([60.0] * 6)

    assert frame_quality_report.SKY_LEVEL_KEY in report.checks.trends["metrics"]
    assert "notes" not in report.checks.trends


def test_the_raw_check_names_its_width_estimator_and_why_it_is_off_the_scale() -> None:
    """The batch check's width has no ratio to the scale, and says so."""
    report = _measure([30.0])

    assert report.kind == "imaging"
    assert report.checks.fwhm_estimator == RAW_CHECK_WIDTH_ESTIMATOR
    assert report.checks.fwhm_not_on_scale_reason is not None
    assert "AST-17" in report.checks.fwhm_not_on_scale_reason


def test_a_mix_of_imaging_and_spectrum_frames_is_refused() -> None:
    """The frames' kind comes from their filters; a mix names both counts."""
    frames = _frames([30.0, 30.0])
    frames[1] = frames[1].model_copy(update={"filter": "Star Analyzer 200"})
    with pytest.raises(InvalidArgumentError, match=r"1 imaging, 1 spectral"):
        frame_quality_report.measure_frame_quality(frames, None, MEASURED_AT)
