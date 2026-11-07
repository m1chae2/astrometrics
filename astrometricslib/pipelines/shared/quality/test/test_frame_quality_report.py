"""Tests for the sky-level drift check in the input-quality report.

The raw background in ADU grows with the exposure length, so a ladder of
longer exposures under a steady sky must not look like a drift. These tests
build small fake frames and check which numbers are trended and what the
alert says.
"""

from collections.abc import Callable
from typing import Any
from unittest.mock import patch

from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.quality import frame_quality_report, frame_selection

BIAS_ADU = 400.0
ADU_PER_SECOND_PER_RATE = 1.22


def _target(exposures: list[float]) -> Target:
    """Build a target with one light frame per exposure, in time order.

    Parameters
    ----------
    exposures : `list` [`float`]
        The exposure of each frame, in seconds.

    Returns
    -------
    target : `Target`
        The target.
    """
    frames = [
        FrameRecord(
            path=f"/lights/f{number}.fits",
            role="LIGHT",
            camera="cam",
            exposure=str(exposure),
            timestamp=float(number),
        )
        for number, exposure in enumerate(exposures)
    ]
    return Target(id="T 1", frames=frames)


def _report(exposures: list[float], rates: list[float] | None) -> Any:
    """Run the input-quality report on fake frames with a known sky.

    Parameters
    ----------
    exposures : `list` [`float`]
        The exposure of each frame, in seconds.
    rates : `list` [`float`] or `None`
        The sky rate of each frame, or `None` for no stored rates. The fake
        background is the bias plus the rate times the exposure.

    Returns
    -------
    report : `InputQualityReport`
        The report.
    """
    target = _target(exposures)
    rate_list = rates or [10.0] * len(exposures)

    def fake_measure(
        working: Target, measure_fwhm: Callable | None, remeasure: bool, camera_id: str | None
    ) -> dict[str, int]:
        """Give each frame the background its exposure and rate imply.

        Returns
        -------
        counts : `dict` [`str`, `int`]
            The measured count.
        """
        for frame, rate in zip(working.frames, rate_list, strict=True):
            frame.measurements.background_level = BIAS_ADU + ADU_PER_SECOND_PER_RATE * rate * float(
                frame.exposure
            )
        return {"measured": len(working.frames), "skipped": 0, "failed": 0}

    by_path = (
        {frame.path: rate for frame, rate in zip(target.frames, rate_list, strict=True)} if rates else None
    )
    with patch(
        "astrometricslib.pipelines.shared.quality.frame_statistics.measure_frame_input_quality", fake_measure
    ):
        return frame_quality_report.input_quality_report(
            target, frame_selection.FrameSelection(), None, 50, None, True, 10, 15.0, by_path
        )


def test_a_longer_exposure_ladder_with_a_steady_sky_is_not_a_drift() -> None:
    """A 20/30/60/120 s ladder under a flat sky rate raises no alert."""
    report = _report([20.0, 30.0, 60.0, 120.0], [10.17, 10.08, 9.99, 9.86])
    assert report.trends["alerts"] == []
    assert "background_level" not in report.trends["metrics"]
    assert not report.trends["metrics"][frame_quality_report.SKY_RATE_KEY]["flagged"]
    assert "different exposure lengths" in report.trends["notes"][0]


def test_a_rising_sky_rate_at_one_exposure_is_flagged() -> None:
    """Twilight or cloud glow at a fixed exposure still raises the alert."""
    rates = [10.0 * (1 + 0.08 * number) for number in range(6)]
    report = _report([30.0] * 6, rates)
    assert report.trends["metrics"]["background_level"]["worrying"]
    assert report.trends["metrics"][frame_quality_report.SKY_RATE_KEY]["worrying"]
    assert report.trends["alerts"]


def test_a_rising_rate_across_mixed_exposures_is_flagged_per_second() -> None:
    """A real rate rise across mixed exposures is flagged per second."""
    rates = [10.0 * (1 + 0.1 * number) for number in range(6)]
    report = _report([20.0, 30.0, 60.0, 120.0, 30.0, 60.0], rates)
    assert len(report.trends["alerts"]) == 1
    assert report.trends["alerts"][0].startswith(frame_quality_report.SKY_RATE_KEY)
    assert "background_level" not in report.trends["metrics"]


def test_mixed_exposures_without_stored_rates_say_so() -> None:
    """With no rates and mixed exposures, a note says why."""
    report = _report([20.0, 30.0, 60.0, 120.0], None)
    assert report.trends["alerts"] == []
    assert report.trends["metrics"].keys() == {"measured_fwhm_px"}
    assert "none has a stored" in report.trends["notes"][0]
