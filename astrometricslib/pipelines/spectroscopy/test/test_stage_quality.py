"""Purpose: Tests for the four spectroscopy quality checkpoints.

Description: A spectrum passes four checkpoints: the raw frame, the calibrated
spectrum, the processing result and the final result. These tests check that
the `metric` helper sets ``passed`` from the limit and the direction, that
each stage's builder reports the numbers it should, that a result built by the
real pipeline from a synthetic frame carries all four in stage order, that the
older `InputQualityAssessment` and `OutputQualityAssessment` keep their fields,
and that the run-level roll-up counts failures and takes medians.
"""

import math
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from astrometricslib.models.spectroscopy_quality import (
    STAGE_ORDER,
    InputQualityAssessment,
    OutputQualityAssessment,
    StageQualityCheckpoint,
    StageQualityMetric,
    metric,
)
from astrometricslib.models.stellar_source import (
    AMBIGUOUS_RMS_GAP,
    NO_GOOD_MATCH_RMS,
    SpectroscopyResult,
)
from astrometricslib.pipelines.shared.quality.saturation import DEFAULT_SATURATION_FLAG_THRESHOLD
from astrometricslib.pipelines.spectroscopy.post_processing.assess_output_quality import (
    assess_output_quality,
    output_quality_checkpoint,
)
from astrometricslib.pipelines.spectroscopy.post_processing.run_gates import (
    stage_quality_rows,
    summarize_stage_quality,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.assess_input_quality import (
    assess_input_quality,
    input_quality_checkpoint,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.assess_raw_frame_quality import (
    assess_raw_frame_quality,
)
from astrometricslib.pipelines.spectroscopy.processing.assess_processing_quality import (
    assess_processing_quality,
)
from astrometricslib.pipelines.spectroscopy.processing.spectrum_signal import (
    MINIMUM_SPECTRUM_SIGNAL_TO_NOISE,
)
from astrometricslib.pipelines.spectroscopy.test.test_result_diagnostics import _build_result


def by_name(checkpoint: StageQualityCheckpoint) -> dict[str, StageQualityMetric]:
    """Index a checkpoint's metrics by name.

    Parameters
    ----------
    checkpoint : `StageQualityCheckpoint`
        The checkpoint to index.

    Returns
    -------
    metrics : `dict` [`str`, `StageQualityMetric`]
        The metrics keyed by name.
    """
    return {entry.name: entry for entry in checkpoint.metrics}


def good_classification(**overrides: object) -> dict[str, object]:
    """Build a classification result with a clear, close match.

    Parameters
    ----------
    **overrides : `object`
        Entries to replace.

    Returns
    -------
    classification : `dict`
        A result shaped like `classify_spectral_type`'s.
    """
    result: dict[str, object] = {
        "spectral_type": "G2V",
        "classification_rms": 0.05,
        "rms_gap_to_second_best": 0.05,
        "rms_gap_to_next_class": 0.08,
        "ranked_types": [{"spectral_type": "G2V", "rms": 0.05}, {"spectral_type": "G5V", "rms": 0.10}],
    }
    result.update(overrides)
    return result


def processing_checkpoint(**overrides: Any) -> StageQualityCheckpoint:
    """Build a processing checkpoint, with good defaults for every input.

    Parameters
    ----------
    **overrides : `Any`
        Inputs of `assess_processing_quality` to replace.

    Returns
    -------
    checkpoint : `StageQualityCheckpoint`
        The processing checkpoint.
    """
    inputs: dict[str, Any] = {
        "classification": good_classification(),
        "features": [
            {"verdict": "detected", "p_value": 0.001, "p_value_method": "control_calibrated"},
            {"verdict": "not_detected", "p_value": 0.6, "p_value_method": "control_calibrated"},
            {"verdict": "not_covered"},
        ],
        "synthetic_b_minus_v": 0.6,
        "emission_lines": [],
        "is_emission_line_source": False,
        "second_order_blue_to_red_ratio": [0.0, 0.5, 1.0],
    }
    inputs.update(overrides)
    return assess_processing_quality(**inputs)


# ---------------------------------------------------- the metric helper


@pytest.mark.parametrize(
    ("value", "limit", "higher_is_better", "expected"),
    [
        (2.0, 1.5, True, True),
        (1.0, 1.5, True, False),
        (1.5, 1.5, True, True),
        (1.0, 1.5, False, True),
        (2.0, 1.5, False, False),
        (1.5, 1.5, False, True),
    ],
)
def test_metric_passed_follows_the_limit_and_the_direction(
    value: float, limit: float, higher_is_better: bool, expected: bool
) -> None:
    """A value on the good side of the limit passes, as does one at it."""
    result = metric("m", value, "unit", limit=limit, higher_is_better=higher_is_better)

    assert result.passed is expected


def test_metric_has_no_verdict_without_a_limit_or_a_value() -> None:
    """No limit or no value leaves ``passed`` as `None`."""
    assert metric("m", 1.0, "unit").passed is None
    assert metric("m", None, "unit", limit=1.0).passed is None


def test_metric_equal_to_the_limit_can_be_made_to_fail() -> None:
    """``limit_is_a_pass=False`` makes a value at the limit fail."""
    assert metric("m", 1.0, "unit", limit=1.0, higher_is_better=False).passed is True
    assert metric("m", 1.0, "unit", limit=1.0, higher_is_better=False, limit_is_a_pass=False).passed is False


def test_metric_casts_numpy_values_and_drops_non_finite_ones() -> None:
    """NumPy numbers become plain `float`; NaN and infinity become `None`."""
    result = metric("m", np.float32(0.5), "unit", limit=np.float64(1.0), higher_is_better=False)
    assert type(result.value) is float
    assert type(result.limit) is float
    assert result.passed is True
    assert metric("m", math.nan, "unit", limit=1.0).value is None
    assert metric("m", math.inf, "unit", limit=1.0).passed is None


def test_a_checkpoint_reports_a_failed_metric_only_for_a_false_verdict() -> None:
    """``has_failed_metric`` ignores metrics with no verdict."""
    quiet = StageQualityCheckpoint(
        stage="processing", metrics=[metric("a", 1.0, "u"), metric("b", None, "u", 1.0)]
    )
    loud = StageQualityCheckpoint(
        stage="processing", metrics=[metric("a", 5.0, "u", 1.0, higher_is_better=False)]
    )

    assert quiet.has_failed_metric is False
    assert loud.has_failed_metric is True


# ------------------------------------------------ checkpoint 0: raw frame


def test_raw_frame_checkpoint_reuses_the_extraction_numbers() -> None:
    """The metrics hold the extraction's values and saturation is judged."""
    checkpoint = assess_raw_frame_quality(
        zero_order_saturated_pixel_fraction=0.0,
        valid_fraction=0.8,
        trail_width_px=[0.0, 3.0, 5.0],
        extraction_diagnostics={"contaminated_sky_fraction": 0.25, "dominant_sky_mode": "both_bands"},
    )
    metrics = by_name(checkpoint)

    assert checkpoint.stage == "raw_frame"
    assert metrics["zero_order_saturated_fraction"].value == pytest.approx(0.0)
    assert metrics["zero_order_saturated_fraction"].passed is True
    assert metrics["valid_fraction"].value == pytest.approx(0.8)
    assert metrics["median_trail_width"].value == pytest.approx(4.0)
    assert metrics["contaminated_sky_fraction"].value == pytest.approx(0.25)
    assert "both_bands" in metrics["contaminated_sky_fraction"].note
    assert checkpoint.flags == ["trail_partly_off_image", "sky_band_contaminated"]
    assert "streak_tilt" not in metrics
    assert "peak_above_sky" not in metrics


def test_raw_frame_saturation_fails_at_the_gate_limit() -> None:
    """A saturated fraction at the run gate's limit fails, as the gate does."""
    checkpoint = assess_raw_frame_quality(
        zero_order_saturated_pixel_fraction=DEFAULT_SATURATION_FLAG_THRESHOLD,
        valid_fraction=1.0,
        trail_width_px=None,
        extraction_diagnostics=None,
    )

    assert by_name(checkpoint)["zero_order_saturated_fraction"].passed is False
    assert checkpoint.flags == ["zero_order_saturated"]
    assert by_name(checkpoint)["median_trail_width"].value is None


def test_raw_frame_checkpoint_adds_the_frame_check_numbers_when_given() -> None:
    """A frame-level result adds tilt and peak-to-sky and names its source."""
    checkpoint = assess_raw_frame_quality(
        zero_order_saturated_pixel_fraction=0.0,
        valid_fraction=1.0,
        trail_width_px=[3.0],
        extraction_diagnostics={},
        frame_check={
            "tilt_degrees": 2.1,
            "spectrum_peak_above_sky_adu": 480.0,
            "saturation_threshold_source": "ASI533 profile, 16383 ADU",
        },
    )
    metrics = by_name(checkpoint)

    assert metrics["streak_tilt"].value == pytest.approx(2.1)
    assert metrics["peak_above_sky"].value == pytest.approx(480.0)
    assert "ASI533 profile" in metrics["zero_order_saturated_fraction"].note


# ---------------------------------------- checkpoint 1: calibrated spectrum


def test_input_quality_checkpoint_carries_the_assessments_numbers() -> None:
    """Every input number appears, and signal-to-noise is judged."""
    assessment = assess_input_quality(
        resolution_element_angstrom=48.0,
        is_resolution_measured=False,
        zero_order_saturated_pixel_fraction=0.0,
        valid_fraction=1.0,
        signal_to_noise=MINIMUM_SPECTRUM_SIGNAL_TO_NOISE - 0.1,
    )
    checkpoint = input_quality_checkpoint(assessment)
    metrics = by_name(checkpoint)

    assert checkpoint.stage == "pre_processing"
    assert metrics["resolution_element"].value == pytest.approx(48.0)
    assert metrics["resolution_measured"].value == pytest.approx(0.0)
    assert metrics["signal_to_noise"].passed is False
    assert metrics["signal_to_noise"].limit == MINIMUM_SPECTRUM_SIGNAL_TO_NOISE
    assert checkpoint.flags == ["resolution_assumed", "low_signal_to_noise"]


def test_input_quality_checkpoint_passes_a_strong_spectrum() -> None:
    """A measured resolution and a strong signal raise no flag."""
    checkpoint = input_quality_checkpoint(
        assess_input_quality(
            resolution_element_angstrom=40.0,
            is_resolution_measured=True,
            zero_order_saturated_pixel_fraction=0.0,
            valid_fraction=1.0,
            signal_to_noise=12.0,
        )
    )

    assert checkpoint.flags == []
    assert checkpoint.has_failed_metric is False


# ---------------------------------------------- checkpoint 2: processing


def test_processing_checkpoint_passes_a_clean_classification() -> None:
    """A close, clear match with a colour passes every limit."""
    checkpoint = processing_checkpoint()
    metrics = by_name(checkpoint)

    assert checkpoint.stage == "processing"
    assert checkpoint.has_failed_metric is False
    assert metrics["classification_rms"].limit == NO_GOOD_MATCH_RMS
    assert metrics["rms_gap_to_second_best"].limit is None
    assert metrics["rms_gap_to_next_class"].limit == AMBIGUOUS_RMS_GAP
    assert metrics["significant_feature_count"].value == pytest.approx(1.0)
    assert metrics["best_feature_p_value"].value == pytest.approx(0.001)
    assert metrics["synthetic_colour_measured"].value == pytest.approx(1.0)
    assert metrics["synthetic_b_minus_v"].value == pytest.approx(0.6)
    assert metrics["second_order_risky_fraction"].value == pytest.approx(0.0)
    assert metrics["emission_lines_detected"].value == pytest.approx(0.0)
    assert checkpoint.flags == []


@pytest.mark.parametrize(
    ("change", "failed_metric"),
    [
        ({"classification_rms": NO_GOOD_MATCH_RMS + 0.01}, "classification_rms"),
        ({"rms_gap_to_next_class": 0.0}, "rms_gap_to_next_class"),
    ],
)
def test_processing_checkpoint_fails_each_classification_limit(
    change: dict[str, float], failed_metric: str
) -> None:
    """Each classification metric fails on the bad side of its own limit."""
    checkpoint = processing_checkpoint(classification=good_classification(**change))

    failed = [entry.name for entry in checkpoint.metrics if entry.passed is False]
    assert failed == [failed_metric]


def test_a_subtype_tie_alone_does_not_fail_the_processing_checkpoint() -> None:
    """A tie between neighbouring subtypes is reported but fails nothing.

    At R of about 100, neighbouring subtypes often fit equally well, so only
    ambiguity between spectral classes counts as a failure.
    """
    checkpoint = processing_checkpoint(classification=good_classification(rms_gap_to_second_best=0.0))

    assert by_name(checkpoint)["rms_gap_to_second_best"].passed is None
    assert checkpoint.has_failed_metric is False


def test_processing_checkpoint_sits_on_the_limits_without_failing() -> None:
    """A value exactly at a classification limit passes."""
    checkpoint = processing_checkpoint(
        classification=good_classification(
            classification_rms=NO_GOOD_MATCH_RMS, rms_gap_to_second_best=AMBIGUOUS_RMS_GAP
        )
    )

    assert checkpoint.has_failed_metric is False


def test_processing_checkpoint_fails_when_no_colour_was_measured() -> None:
    """A classified spectrum with no synthetic colour fails that metric."""
    checkpoint = processing_checkpoint(synthetic_b_minus_v=None)
    metrics = by_name(checkpoint)

    assert metrics["synthetic_colour_measured"].value == pytest.approx(0.0)
    assert metrics["synthetic_colour_measured"].passed is False
    assert metrics["synthetic_b_minus_v"].value is None


def test_processing_checkpoint_counts_uncalibrated_p_values() -> None:
    """A Gaussian-fallback p-value fails the calibration metric."""
    checkpoint = processing_checkpoint(
        features=[
            {"verdict": "possible", "p_value": 0.04, "p_value_method": "gaussian"},
            {"verdict": "detected", "p_value": 0.005, "p_value_method": "control_calibrated"},
        ]
    )
    metrics = by_name(checkpoint)

    assert metrics["significant_feature_count"].value == pytest.approx(2.0)
    assert metrics["best_feature_p_value"].value == pytest.approx(0.005)
    assert metrics["uncalibrated_feature_count"].value == pytest.approx(1.0)
    assert metrics["uncalibrated_feature_count"].passed is False


def test_processing_checkpoint_leaves_feature_metrics_empty_without_features() -> None:
    """An extended target has no feature tests, so those metrics are `None`."""
    metrics = by_name(processing_checkpoint(features=[]))

    assert metrics["significant_feature_count"].value is None
    assert metrics["best_feature_p_value"].value is None
    assert metrics["uncalibrated_feature_count"].passed is None


def test_processing_checkpoint_flags_second_order_risk_and_emission() -> None:
    """A large blue-to-red ratio and two detected lines raise their flags."""
    checkpoint = processing_checkpoint(
        second_order_blue_to_red_ratio=[0.0, 2.0, 50.0, 50.0],
        emission_lines=[{"verdict": "detected"}, {"verdict": "detected"}, {"verdict": "unclear"}],
        is_emission_line_source=True,
    )
    metrics = by_name(checkpoint)

    assert metrics["second_order_risky_fraction"].value == pytest.approx(0.5)
    assert metrics["second_order_max_blue_to_red_ratio"].value == pytest.approx(50.0)
    assert metrics["emission_lines_detected"].value == pytest.approx(2.0)
    assert checkpoint.flags == ["emission_line_source", "second_order_risk"]


def test_processing_checkpoint_for_an_unclassified_spectrum_judges_nothing() -> None:
    """An unclassified spectrum has no verdicts and is flagged."""
    checkpoint = processing_checkpoint(
        classification={"spectral_type": "Unknown", "classification_rms": None}, synthetic_b_minus_v=None
    )
    metrics = by_name(checkpoint)

    assert checkpoint.flags == ["unclassified"]
    assert metrics["classification_rms"].value is None
    assert metrics["synthetic_colour_measured"].passed is None
    assert checkpoint.has_failed_metric is False


# ------------------------------------------------ checkpoint 3: final result


def final_checkpoint(
    classification: dict[str, object], catalog_type: str | None, own_type: str
) -> tuple[OutputQualityAssessment, StageQualityCheckpoint]:
    """Assess a classification and build its final checkpoint.

    Parameters
    ----------
    classification : `dict`
        The classification result.
    catalog_type : `str`, optional
        The catalog spectral type.
    own_type : `str`
        The measured spectral type.

    Returns
    -------
    assessment : `OutputQualityAssessment`
        The older assessment.
    checkpoint : `StageQualityCheckpoint`
        The final checkpoint built from it.
    """
    assessment = assess_output_quality(classification, None, 40.0)
    checkpoint = output_quality_checkpoint(
        assessment, own_spectral_type=own_type, catalog_spectral_type=catalog_type, catalog_comparison=None
    )
    return assessment, checkpoint


def test_final_checkpoint_agrees_with_the_output_assessment() -> None:
    """A trustworthy result passes every verdict and the catalog distance."""
    assessment, checkpoint = final_checkpoint(good_classification(), "G5V", "G2V")
    metrics = by_name(checkpoint)

    assert checkpoint.stage == "post_processing"
    assert assessment.is_trustworthy is True
    assert metrics["trustworthy"].value == pytest.approx(1.0)
    assert metrics["catalog_type_steps_apart"].value == pytest.approx(3.0)
    assert metrics["catalog_type_steps_apart"].passed is True
    assert checkpoint.has_failed_metric is False
    assert checkpoint.flags == []


def test_final_checkpoint_flags_a_poor_ambiguous_distant_result() -> None:
    """A poor match, a tie and a far catalog type each fail their metric."""
    classification = good_classification(
        classification_rms=0.3,
        ranked_types=[{"spectral_type": "G2V", "rms": 0.3}, {"spectral_type": "G5V", "rms": 0.3}],
    )
    assessment, checkpoint = final_checkpoint(classification, "B0V", "G2V")
    metrics = by_name(checkpoint)

    assert assessment.is_trustworthy is False
    assert metrics["poor_match"].passed is False
    assert metrics["subtype_ambiguous"].passed is False
    assert metrics["trustworthy"].passed is False
    assert metrics["catalog_type_steps_apart"].passed is False
    assert {"poor_match", "subtype_ambiguous", "not_trustworthy"} <= set(checkpoint.flags)


def test_final_checkpoint_for_an_unclassified_spectrum_judges_nothing() -> None:
    """An unclassified spectrum has `None` values and a flag."""
    _, checkpoint = final_checkpoint({"spectral_type": "Unknown", "ranked_types": []}, "G2V", "Unknown")

    assert checkpoint.flags == ["unclassified"]
    assert all(entry.value is None for entry in checkpoint.metrics)
    assert checkpoint.has_failed_metric is False


# ------------------------------------------ a result from the pipeline


@pytest.fixture(scope="module")
def pipeline_result() -> SpectroscopyResult:
    """Build one result from a synthetic frame with the real pipeline.

    Returns
    -------
    result : `SpectroscopyResult`
        What the pipeline saved on the star.
    """
    return _build_result("traced")


def test_a_pipeline_built_result_has_the_four_checkpoints_in_stage_order(
    pipeline_result: SpectroscopyResult,
) -> None:
    """The saved result lists the four stages in order."""
    assert [checkpoint.stage for checkpoint in pipeline_result.stage_quality] == list(STAGE_ORDER)
    assert list(STAGE_ORDER) == ["raw_frame", "pre_processing", "processing", "post_processing"]


def test_every_metric_of_a_pipeline_built_result_follows_its_limit(
    pipeline_result: SpectroscopyResult,
) -> None:
    """Each metric is unique, and ``passed`` agrees with limit and value."""
    for checkpoint in pipeline_result.stage_quality:
        names = [entry.name for entry in checkpoint.metrics]
        assert len(names) == len(set(names)), checkpoint.stage
        assert names, checkpoint.stage
        for entry in checkpoint.metrics:
            if entry.limit is None or entry.value is None:
                assert entry.passed is None, (checkpoint.stage, entry.name)
            else:
                assert entry.passed is not None, (checkpoint.stage, entry.name)
            for number in (entry.value, entry.limit):
                assert number is None or type(number) is float


def test_the_checkpoints_of_a_pipeline_built_result_repeat_the_stored_numbers(
    pipeline_result: SpectroscopyResult,
) -> None:
    """Checkpoints 0, 1 and 3 hold the same numbers as the older records."""
    raw, pre, _, post = (by_name(checkpoint) for checkpoint in pipeline_result.stage_quality)
    input_quality = pipeline_result.input_quality
    output_quality = pipeline_result.output_quality
    assert input_quality is not None
    assert output_quality is not None

    assert raw["valid_fraction"].value == pytest.approx(pipeline_result.valid_fraction)
    assert raw["zero_order_saturated_fraction"].value == input_quality.zero_order_saturated_pixel_fraction
    assert pre["resolution_element"].value == pytest.approx(input_quality.resolution_element_angstrom)
    assert pre["signal_to_noise"].value == input_quality.signal_to_noise
    assert pre["valid_fraction"].value == pytest.approx(pipeline_result.valid_fraction)
    unclassified = pipeline_result.self_determined_spectral_type in ("", "Unknown")
    assert post["trustworthy"].value == (None if unclassified else float(output_quality.is_trustworthy))


def test_the_processing_checkpoint_of_a_pipeline_built_result_matches_the_classification(
    pipeline_result: SpectroscopyResult,
) -> None:
    """Checkpoint 2 repeats the classification RMS and gaps."""
    processing = by_name(pipeline_result.stage_quality[2])

    if pipeline_result.self_determined_spectral_type in ("", "Unknown"):
        assert processing["classification_rms"].value is None
        assert "unclassified" in pipeline_result.stage_quality[2].flags
    else:
        assert processing["classification_rms"].value == pytest.approx(
            pipeline_result.self_determined_spectral_type_rms
        )
        assert processing["rms_gap_to_second_best"].value == pipeline_result.rms_gap_to_second_best
    assert processing["second_order_risky_fraction"].value is not None


def test_the_stage_quality_round_trips_through_camel_case(pipeline_result: SpectroscopyResult) -> None:
    """The result keeps its checkpoints through ``stageQuality`` and back."""
    dumped = pipeline_result.model_dump(by_alias=True)

    assert [checkpoint["stage"] for checkpoint in dumped["stageQuality"]] == list(STAGE_ORDER)
    assert "metrics" in dumped["stageQuality"][0]
    assert SpectroscopyResult.model_validate(dumped).stage_quality == pipeline_result.stage_quality


def test_a_result_saved_before_the_checkpoints_loads_with_an_empty_list() -> None:
    """An old stored spectrum has no ``stageQuality`` and still loads."""
    assert SpectroscopyResult.model_validate({}).stage_quality == []


# ------------------------------------- the older assessments are unchanged


def test_the_input_and_output_assessments_keep_their_fields() -> None:
    """The UI reads these two models; the checkpoints add no field to them."""
    assert list(InputQualityAssessment.model_fields) == [
        "resolution_element_angstrom",
        "is_resolution_measured",
        "zero_order_saturated_pixel_fraction",
        "valid_fraction",
        "signal_to_noise",
    ]
    assert list(OutputQualityAssessment.model_fields) == [
        "is_poor_match",
        "is_ambiguous",
        "is_class_ambiguous",
        "is_subtype_finer_than_resolution",
        "catalog_agrees",
        "is_trustworthy",
    ]


def test_the_assessment_functions_still_return_the_older_models() -> None:
    """Both assessment functions return the same models as before."""
    input_quality = assess_input_quality(
        resolution_element_angstrom=40.0,
        is_resolution_measured=True,
        zero_order_saturated_pixel_fraction=None,
        valid_fraction=1.0,
        signal_to_noise=5.0,
    )
    output_quality = assess_output_quality(good_classification(), None, 40.0)

    assert type(input_quality) is InputQualityAssessment
    assert type(output_quality) is OutputQualityAssessment
    assert output_quality.is_trustworthy is True
    assert output_quality.is_subtype_finer_than_resolution is None


# ------------------------------------------------------ the run-level roll-up


def checkpoints_for(processing_rms: float | None, colour: float | None) -> list[StageQualityCheckpoint]:
    """Build two checkpoints for one fake spectrum.

    Parameters
    ----------
    processing_rms : `float`, optional
        The classification RMS to record.
    colour : `float`, optional
        A metric with no limit, to take a median of.

    Returns
    -------
    checkpoints : `list` [`StageQualityCheckpoint`]
        Two checkpoints in stage order.
    """
    return [
        StageQualityCheckpoint(
            stage="processing",
            metrics=[
                metric("classification_rms", processing_rms, "relative RMS", NO_GOOD_MATCH_RMS, False),
                metric("synthetic_b_minus_v", colour, "magnitude"),
            ],
        ),
        StageQualityCheckpoint(stage="post_processing", metrics=[metric("trustworthy", 1.0, "flag", 1.0)]),
    ]


def test_the_rollup_counts_failed_spectra_and_takes_medians() -> None:
    """Each stage counts failing spectra once and takes medians."""
    rows = [
        checkpoints_for(0.05, 0.4),
        checkpoints_for(0.30, 0.6),
        checkpoints_for(0.40, None),
        checkpoints_for(0.10, 1.0),
    ]

    rollups = summarize_stage_quality(rows)

    assert [rollup.stage for rollup in rollups] == ["processing", "post_processing"]
    processing, post = rollups
    assert processing.spectrum_count == 4
    assert processing.failed_spectrum_count == 2
    assert processing.metric_medians["classification_rms"] == pytest.approx(0.2)
    assert processing.metric_medians["synthetic_b_minus_v"] == pytest.approx(0.6)
    assert post.spectrum_count == 4
    assert post.failed_spectrum_count == 0
    assert post.metric_medians == {"trustworthy": 1.0}


def test_the_rollup_leaves_out_a_metric_no_spectrum_measured() -> None:
    """A metric whose every value is `None` has no median."""
    rollups = summarize_stage_quality([checkpoints_for(None, None)])

    assert "classification_rms" not in rollups[0].metric_medians
    assert rollups[0].failed_spectrum_count == 0


def test_the_rollup_accepts_the_dictionaries_a_batch_worker_returns() -> None:
    """Worker rows give the same roll-up as the objects."""
    stars = [
        SimpleNamespace(spectroscopy=SimpleNamespace(stage_quality=checkpoints_for(0.3, 0.5))),
        SimpleNamespace(spectroscopy=SimpleNamespace(stage_quality=checkpoints_for(0.1, 0.7))),
        SimpleNamespace(spectroscopy=None),
        SimpleNamespace(spectroscopy=SimpleNamespace(stage_quality=[])),
    ]
    rows = stage_quality_rows(stars)

    assert len(rows) == 2
    assert isinstance(rows[0][0], dict)
    assert summarize_stage_quality(rows) == summarize_stage_quality([
        star.spectroscopy.stage_quality for star in stars[:2]
    ])


def test_the_rollup_of_no_spectra_is_empty() -> None:
    """With no checkpoints there is nothing to summarise."""
    assert summarize_stage_quality([]) == []
