"""Tests for the noise ratio and the master dark's noise (D22)."""

import math
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from astrometricslib.models.quality.measurement import MeasurementStatus
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.stacking.metrics import (
    STACKING_EXPECTED_MASTER_DARK_NOISE_STACK_UNIT,
    STACKING_MEASURED_TO_PREDICTED_NOISE_RATIO,
)
from astrometricslib.pipelines.stacking.post_processing.expected_noise import (
    STACK_UNITS_PER_ADU,
    ExpectedNoise,
    ExpectedNoiseOutcome,
    FrameNoiseInput,
    MasterDarkNoiseOutcome,
    effective_aperture_area_px,
    expected_master_dark_noise,
    expected_stack_noise,
)
from astrometricslib.pipelines.stacking.post_processing.normalization_scale import (
    NormalizationScale,
    NormalizationScaleOutcome,
    apply_normalization_scale,
)
from astrometricslib.pipelines.stacking.post_processing.record_science import (
    StackScience,
    compute_stack_science,
    master_dark_noise_measurements,
    noise_ratio_measurements,
    science_measurements,
)
from astrometricslib.pipelines.stacking.post_processing.stack_noise import StackNoiseOutcome, StackNoiseResult

CONTEXT = PipelineRunContext(
    "M 99", "stacking", "1.3.0", "run-1", "run-1", datetime(2026, 10, 26, tzinfo=UTC)
)
SUBJECT = CONTEXT.stack_subject("M99_Stacked.fits")


def _measured(noise: float, uncertainty: float = 0.0, count: int = 100) -> StackNoiseOutcome:
    """Build a measured noise outcome.

    Parameters
    ----------
    noise : `float`
        The empty-aperture noise.
    uncertainty : `float`, optional
        Its 1-sigma uncertainty.
    count : `int`, optional
        The number of apertures.

    Returns
    -------
    outcome : `StackNoiseOutcome`
        An outcome holding the result.
    """
    return StackNoiseOutcome(
        StackNoiseResult(
            noise_adu=noise,
            noise_uncertainty_adu=uncertainty,
            aperture_count=count,
            reference_flux_adu=1.0,
            reference_uncertainty_adu=0.0,
            reference_star_count=10,
            normalized_noise=noise,
            normalized_uncertainty=uncertainty,
            wcs_source="stack header",
            apertures_created=False,
        )
    )


def _expected(total: float, assumed: str = "") -> ExpectedNoiseOutcome:
    """Build an expected-noise outcome.

    Parameters
    ----------
    total : `float`
        The predicted noise.
    assumed : `str`, optional
        The reason the prediction is assumed.

    Returns
    -------
    outcome : `ExpectedNoiseOutcome`
        An outcome holding the result.
    """
    return ExpectedNoiseOutcome(
        ExpectedNoise(total, 0.0, total, 59.0, STACK_UNITS_PER_ADU, 10, 0.01, assumed_reason=assumed)
    )


def test_the_ratio_is_measured_over_predicted_with_the_measured_error() -> None:
    """The row is the quotient with the measured noise's relative error."""
    (row,) = noise_ratio_measurements(_measured(3.0e-3, 0.3e-3), _expected(2.0e-3), CONTEXT, SUBJECT)

    assert row.metric == STACKING_MEASURED_TO_PREDICTED_NOISE_RATIO
    assert row.status == MeasurementStatus.MEASURED
    assert row.value == pytest.approx(1.5)
    assert row.uncertainty == pytest.approx(0.15)
    assert row.sample_count == 100


def test_the_ratio_is_assumed_when_the_prediction_is() -> None:
    """An assumed input makes the ratio assumed, with the reason kept."""
    (row,) = noise_ratio_measurements(
        _measured(2.0e-3), _expected(1.0e-3, "the gain is a datasheet value"), CONTEXT, SUBJECT
    )

    assert row.status == MeasurementStatus.ASSUMED
    assert "datasheet" in row.reason


def test_a_missing_measurement_or_prediction_gives_not_measured_with_the_reason() -> None:
    """Either input missing is a stored reason, never a made-up ratio."""
    (no_noise,) = noise_ratio_measurements(
        StackNoiseOutcome(None, "no solution"), _expected(1.0e-3), CONTEXT, SUBJECT
    )
    (no_prediction,) = noise_ratio_measurements(
        _measured(1.0e-3), ExpectedNoiseOutcome(None, "no sky rates"), CONTEXT, SUBJECT
    )
    (no_outcome,) = noise_ratio_measurements(None, _expected(1.0e-3), CONTEXT, SUBJECT)
    (zero,) = noise_ratio_measurements(_measured(1.0e-3), _expected(0.0), CONTEXT, SUBJECT)

    assert all(
        row.status == MeasurementStatus.NOT_MEASURED for row in (no_noise, no_prediction, no_outcome, zero)
    )
    assert "no solution" in no_noise.reason
    assert "no sky rates" in no_prediction.reason
    assert "not above zero" in zero.reason


def _frames(
    count: int, read_noise: float = 2.0, gain: float = 0.5, scale: float = 1.0
) -> list[FrameNoiseInput]:
    """Build equal frames.

    Parameters
    ----------
    count : `int`
        How many frames.
    read_noise : `float`, optional
        The read noise in electrons.
    gain : `float`, optional
        The gain in electrons per ADU.
    scale : `float`, optional
        The exposure scale.

    Returns
    -------
    frames : `list` [`FrameNoiseInput`]
        The inputs.
    """
    return [FrameNoiseInput(1.0, 100.0, read_noise, gain, exposure_scale=scale) for _ in range(count)]


def test_the_coherent_read_noise_is_one_frames_read_noise_over_the_aperture() -> None:
    """One pattern in every frame adds up to one frame's read noise."""
    outcome = expected_stack_noise(_frames(20))

    assert outcome.result is not None
    area = effective_aperture_area_px()
    per_pixel_adu = 2.0 / 0.5
    assert outcome.result.coherent_read_noise == pytest.approx(
        STACK_UNITS_PER_ADU * math.sqrt(area) * per_pixel_adu
    )
    # The random read noise of the stack, in contrast, averages down.
    assert outcome.result.read_noise == pytest.approx(outcome.result.coherent_read_noise / math.sqrt(20))


def test_the_master_dark_noise_falls_with_the_square_root_of_its_frame_count() -> None:
    """A master of 16 darks has a quarter of the noise of a single dark."""
    expected = expected_stack_noise(_frames(20)).result

    one = expected_master_dark_noise(expected, 1, False)
    sixteen = expected_master_dark_noise(expected, 16, False)

    assert one.value == pytest.approx(expected.coherent_read_noise)
    assert sixteen.value == pytest.approx(one.value / 4.0)
    assert sixteen.dark_frame_count == 16


def test_the_master_dark_noise_follows_the_normalization_scale() -> None:
    """Siril's scale multiplies the calibration pattern too."""
    expected = expected_stack_noise(_frames(20))
    scale = NormalizationScaleOutcome(NormalizationScale(1.2, 0.01, 10, ""))
    scaled = apply_normalization_scale(expected, scale).result

    plain = expected_master_dark_noise(expected.result, 9, False)
    corrected = expected_master_dark_noise(scaled, 9, False)

    assert corrected.value == pytest.approx(1.2 * plain.value)
    assert "1.200" in corrected.scale_note


def test_the_master_dark_noise_says_why_it_is_missing() -> None:
    """Each missing input gives its own reason."""
    expected = expected_stack_noise(_frames(20)).result

    assert "no dark frame" in expected_master_dark_noise(expected, 0, False).reason
    assert "no dark frame" in expected_master_dark_noise(expected, None, False).reason
    assert "several exposure groups" in expected_master_dark_noise(expected, 10, True).reason
    assert expected_master_dark_noise(None, 10, False).value is None


def test_an_assumed_read_noise_makes_the_master_dark_row_assumed() -> None:
    """The row follows the status of the inputs behind the prediction."""
    frames = [FrameNoiseInput(1.0, 100.0, 2.0, 0.5, assumed_reason="the gain is assumed")] * 10
    expected = expected_stack_noise(frames).result
    (row,) = master_dark_noise_measurements(expected_master_dark_noise(expected, 25, False), CONTEXT, SUBJECT)

    assert row.metric == STACKING_EXPECTED_MASTER_DARK_NOISE_STACK_UNIT
    assert row.status == MeasurementStatus.ASSUMED
    assert row.sample_count == 25


def test_a_measured_master_dark_row_names_the_dark_count_and_what_it_leaves_out() -> None:
    """The measured row notes its dark count and what it leaves out."""
    expected = expected_stack_noise(_frames(10)).result
    (row,) = master_dark_noise_measurements(expected_master_dark_noise(expected, 25, False), CONTEXT, SUBJECT)

    assert row.status == MeasurementStatus.MEASURED
    assert "25 dark frame" in row.reason and "shot noise" in row.reason
    (missing,) = master_dark_noise_measurements(
        MasterDarkNoiseOutcome(None, "no dark frame was applied"), CONTEXT, SUBJECT
    )
    assert missing.status == MeasurementStatus.NOT_MEASURED and "no dark" in missing.reason


def test_the_science_rows_include_the_ratio_and_the_master_dark() -> None:
    """Both new rows are part of a stack's science rows."""
    expected = expected_stack_noise(_frames(10))
    science = StackScience(
        expected_noise=expected,
        flat_noise=SimpleNamespace(result=None, reason="none"),
        stack_sky_level=0.01,
        measured_noise=_measured(2.0 * expected.result.total_noise),
        master_dark_noise=expected_master_dark_noise(expected.result, 10, False),
    )

    rows = science_measurements(science, CONTEXT, SUBJECT, [], [])
    by_metric = {row.metric: row for row in rows}

    assert by_metric[STACKING_MEASURED_TO_PREDICTED_NOISE_RATIO].value == pytest.approx(2.0)
    assert by_metric[STACKING_EXPECTED_MASTER_DARK_NOISE_STACK_UNIT].status == MeasurementStatus.MEASURED


def test_compute_stack_science_counts_the_darks_from_the_run_and_refuses_several_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The staged darks are counted; several groups get a reason."""
    from astrometricslib.pipelines.stacking.post_processing import record_science

    expected = expected_stack_noise(_frames(10))
    monkeypatch.setattr(record_science, "compute_expected_noise", lambda *args, **keywords: expected)
    monkeypatch.setattr(record_science, "resolve_stack_sky_level", lambda noise, path: 0.01)
    arguments = ("stack.fits", None, None, None, "", "M 99")

    one_group = compute_stack_science(
        *arguments, {"dark_frame_paths": ["a", "b", "c", "d"]}, noise=_measured(1e-3)
    )
    two_groups = compute_stack_science(
        *arguments,
        {"dark_frame_paths": ["a", "b"], "exposure_group_weights": [{}, {}]},
        noise=_measured(1e-3),
    )

    assert one_group.master_dark_noise.dark_frame_count == 4
    assert one_group.measured_noise is not None
    assert two_groups.master_dark_noise.value is None
    assert "several exposure groups" in two_groups.master_dark_noise.reason
