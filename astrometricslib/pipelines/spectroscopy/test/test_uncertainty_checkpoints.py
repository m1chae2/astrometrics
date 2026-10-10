"""Purpose: Tests for the per-sample errors on a pipeline-built spectrum.

Description: The spectroscopy pipeline gives every extracted sample a 1-sigma
error and carries it through the corrections, the equivalent widths and the
classification. These tests build a result from a noisy synthetic frame with
the real pipeline and check that the error arrays line up with the brightness
arrays, that the corrected errors scale as the brightness does, that
checkpoints 1 and 2 carry the new metrics (with no limits), and that the
checkpoint builders compute those metrics from the numbers they are given.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from astrometricslib.models.stellar_source import SpectroscopyResult, StellarObject
from astrometricslib.pipelines.spectroscopy.pipeline import SpectroscopyPipeline
from astrometricslib.pipelines.spectroscopy.post_processing.run_gates import (
    stage_quality_rows,
    summarize_stage_quality,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.assess_input_quality import (
    assess_input_quality,
    input_quality_checkpoint,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.intensity_variance import (
    PixelNoiseModel,
    SpectrumNoiseSummary,
)
from astrometricslib.pipelines.spectroscopy.processing.assess_processing_quality import (
    assess_processing_quality,
)
from astrometricslib.pipelines.spectroscopy.test.test_result_diagnostics import (
    FRAME_SHAPE,
    ZERO_ORDER_XY,
    _ArrayImage,
)
from astrometricslib.pipelines.spectroscopy.test.test_stage_quality import by_name
from astrometricslib.test.synthetic import make_spectral_frame
from astrometricslib.utilities import CameraConfig, SpectroscopyConfig

NEW_PRE_PROCESSING_METRICS = (
    "median_snr_per_resolution_element",
    "fraction_samples_snr_below_5",
    "snr_estimate_ratio",
)
NEW_PROCESSING_METRICS = (
    "median_equivalent_width_relative_error",
    "hbeta_equivalent_width_angstrom",
    "halpha_equivalent_width_angstrom",
    "best_template_reduced_chi_square",
)


@pytest.fixture(scope="module")
def pipeline_result() -> SpectroscopyResult:
    """Build one result from a noisy synthetic frame with the real pipeline.

    The trail is level (no tilt) and the grating is 33 mm from the sensor,
    which gives 11.4 Angstroms per pixel, fine enough for the feature
    detector to measure the Balmer lines.

    Returns
    -------
    result : `SpectroscopyResult`
        What the pipeline saved on the star.
    """
    camera = CameraConfig(
        name="ZWO ASI 533MM Pro",
        pixel_size_um=3.76,
        sensor_width_px=FRAME_SHAPE[1],
        sensor_height_px=FRAME_SHAPE[0],
        sensor_min_wavelength=300.0,
        sensor_max_wavelength=1000.0,
    )
    config = SpectroscopyConfig(
        camera=camera,
        grating_distance_mm=33.0,
        dispersion_orientation="horizontal",
        dispersion_direction="positive",
        dispersion_start_px=200.0,
        extraction_method="traced",
        use_flare_mask_extraction=True,
    )
    pipeline = SpectroscopyPipeline(config=config)
    frame = make_spectral_frame(
        zero_order_xy=ZERO_ORDER_XY, angle_deg=0.0, lines=(), shape=FRAME_SHAPE, seed=3
    )
    image = _ArrayImage(frame.image, header={"AIRMASS": 1.4})
    extraction = pipeline._process_single_star(image, ZERO_ORDER_XY, auto_detect_angle=False)
    star = StellarObject(id="TestStar")
    pipeline._apply_result_to_stellar_object(star, extraction, image)
    assert star.spectroscopy is not None
    return star.spectroscopy


# ------------------------------------------------------- the stored arrays


def test_the_error_array_lines_up_with_the_intensities(pipeline_result: SpectroscopyResult) -> None:
    """One positive finite error per kept sample, in the same order."""
    errors = np.array(pipeline_result.intensity_errors)

    assert errors.shape == (len(pipeline_result.intensities),)
    assert errors.shape == (len(pipeline_result.wavelengths_angstrom),)
    assert np.all(np.isfinite(errors))
    assert np.all(errors > 0)
    # The unusable samples were dropped from the brightness, so the arrays
    # are shorter than the request; the errors were dropped with the same mask.
    assert pipeline_result.valid_fraction is not None and pipeline_result.valid_fraction < 1.0


def test_the_signal_to_noise_of_a_known_frame_is_plausible(pipeline_result: SpectroscopyResult) -> None:
    """The per-sample error matches the frame's photon and read noise.

    The trail holds 3000 ADU per column over a box of about 21 rows on a sky
    of 150 ADU per pixel. With the gain of the camera profile the signal to
    noise is 3000 over the square root of the summed variance.
    """
    model = pipeline_result.intensity_noise_model
    assert model is not None
    box_rows = 21
    expected_variance = (3000.0 + box_rows * 150.0) / model.gain_e_per_adu + box_rows * (
        model.read_noise_e / model.gain_e_per_adu
    ) ** 2
    samples = np.array(pipeline_result.intensities)[:200]
    errors = np.array(pipeline_result.intensity_errors)[:200]
    on_trail = samples > 1500.0

    # Sky subtraction adds the sky estimate's variance, a few tens of percent
    # more, so the reported error sits between 1x and 1.6x the box-only value.
    ratio = np.median(errors[on_trail]) / np.sqrt(expected_variance)
    assert 1.0 < ratio < 1.6


def test_corrected_errors_scale_with_the_brightness(pipeline_result: SpectroscopyResult) -> None:
    """The relative error is the same before and after the corrections."""
    corrected = pipeline_result.response_corrected_intensities
    corrected_errors = pipeline_result.response_corrected_intensity_errors
    assert corrected is not None and corrected_errors is not None
    assert len(corrected_errors) == len(corrected)

    brightness = np.array(pipeline_result.intensities)
    errors = np.array(pipeline_result.intensity_errors)
    corrected = np.array(corrected)
    corrected_errors = np.array(corrected_errors)
    # The errors are NaN exactly where the brightness is NaN (outside the
    # response's range).
    assert np.array_equal(np.isnan(corrected), np.isnan(corrected_errors))
    finite = np.isfinite(corrected)
    assert finite.sum() > 100
    assert corrected_errors[finite] / np.abs(corrected[finite]) == pytest.approx(
        errors[finite] / np.abs(brightness[finite]), rel=1e-9
    )


def test_the_noise_model_record_is_stored(pipeline_result: SpectroscopyResult) -> None:
    """The gain, read noise and assumptions travel with the errors."""
    model = pipeline_result.intensity_noise_model

    assert model is not None
    assert model.gain_e_per_adu > 0
    assert model.read_noise_e >= 0
    assert model.adu_per_stored_unit == pytest.approx(1.0)
    assert model.frames_averaged == 1
    flags = pipeline_result.stage_quality[1].flags
    assert ("gain_assumed" in flags) == model.gain_is_assumed
    assert ("read_noise_assumed" in flags) == model.read_noise_is_assumed


def test_the_new_fields_round_trip_through_camel_case(pipeline_result: SpectroscopyResult) -> None:
    """The error arrays and the noise model survive a camelCase round trip."""
    dumped = pipeline_result.model_dump(by_alias=True)

    assert len(dumped["intensityErrors"]) == len(pipeline_result.intensities)
    assert "responseCorrectedIntensityErrors" in dumped
    assert set(dumped["intensityNoiseModel"]) == {
        "gainEPerAdu",
        "readNoiseE",
        "gainIsAssumed",
        "readNoiseIsAssumed",
        "aduPerStoredUnit",
        "framesAveraged",
        "framesAreAssumed",
    }
    reloaded = SpectroscopyResult.model_validate(dumped)
    assert reloaded.intensity_errors == pipeline_result.intensity_errors
    assert reloaded.intensity_noise_model == pipeline_result.intensity_noise_model


def test_a_result_saved_before_the_errors_still_loads() -> None:
    """The new fields default to `None`."""
    result = SpectroscopyResult.model_validate({"wavelengthsAngstrom": [5000.0], "intensities": [1.0]})

    assert result.intensity_errors is None
    assert result.response_corrected_intensity_errors is None
    assert result.intensity_noise_model is None


# ---------------------------------------------------------- the checkpoints


def test_checkpoint_one_carries_the_signal_to_noise_metrics(pipeline_result: SpectroscopyResult) -> None:
    """The pre-processing checkpoint has three finite, unjudged metrics."""
    metrics = by_name(pipeline_result.stage_quality[1])

    for name in NEW_PRE_PROCESSING_METRICS:
        assert name in metrics, name
        assert metrics[name].value is not None, name
        assert type(metrics[name].value) is float
        assert metrics[name].limit is None
        assert metrics[name].passed is None
        assert metrics[name].note
    assert metrics["median_snr_per_resolution_element"].value > 0
    assert 0.0 <= metrics["fraction_samples_snr_below_5"].value <= 1.0
    assert metrics["snr_estimate_ratio"].value > 0
    assert "far from 1" in metrics["snr_estimate_ratio"].note


def test_checkpoint_two_carries_the_equivalent_width_and_chi_square_metrics(
    pipeline_result: SpectroscopyResult,
) -> None:
    """The processing checkpoint has the new metrics, with no limits."""
    metrics = by_name(pipeline_result.stage_quality[2])

    for name in NEW_PROCESSING_METRICS:
        assert name in metrics, name
        assert metrics[name].limit is None, name
        assert metrics[name].passed is None, name
    # Both Balmer lines are covered, so both widths are measured.
    for name in ("hbeta_equivalent_width_angstrom", "halpha_equivalent_width_angstrom"):
        assert metrics[name].value is not None, name
        assert "+/-" in metrics[name].note
    assert metrics["best_template_reduced_chi_square"].value is not None
    assert metrics["best_template_reduced_chi_square"].value > 0
    features = {entry["feature"]: entry for entry in pipeline_result.probable_spectral_features}
    hbeta = features["Hydrogen Balmer series (H-beta)"]
    assert metrics["hbeta_equivalent_width_angstrom"].value == pytest.approx(
        hbeta["equivalent_width_angstrom"]
    )
    assert "depth" in hbeta  # the depth fields are kept


def test_the_classification_candidates_carry_the_chi_square(pipeline_result: SpectroscopyResult) -> None:
    """Every stored candidate has a reduced chi-square next to its RMS."""
    candidates = pipeline_result.self_determined_spectral_type_candidates

    assert candidates
    assert all(entry["reduced_chi_square"] is not None for entry in candidates)
    metrics = by_name(pipeline_result.stage_quality[2])
    assert metrics["best_template_reduced_chi_square"].value == pytest.approx(
        candidates[0]["reduced_chi_square"]
    )


def test_the_run_summary_takes_medians_of_the_new_metrics(pipeline_result: SpectroscopyResult) -> None:
    """The roll-up picks the new metrics up by name, with no change to it."""
    star = StellarObject(id="TestStar")
    star.spectroscopy = pipeline_result

    rollup = summarize_stage_quality(stage_quality_rows([star]))

    assert rollup is not None
    by_stage = {entry.stage: entry for entry in rollup}
    assert "median_snr_per_resolution_element" in by_stage["pre_processing"].metric_medians
    assert "best_template_reduced_chi_square" in by_stage["processing"].metric_medians


# ------------------------------------------------- the checkpoint builders


def _feature(
    name: str, verdict: str, width: float | None, error: float | None, kind: str = "absorption"
) -> dict[str, object]:
    """Build a feature entry like the detector's, with an equivalent width.

    Parameters
    ----------
    name : `str`
        The feature's name.
    verdict : `str`
        The detector's verdict.
    width, error : `float` or `None`
        The equivalent width and its error, in Angstroms.
    kind : `str`, optional
        ``"absorption"`` or ``"emission"``.

    Returns
    -------
    entry : `dict`
        A feature entry with the keys the checkpoint reads.
    """
    return {
        "feature": name,
        "verdict": verdict,
        "kind": kind,
        "p_value": 0.01,
        "p_value_method": "control_calibrated",
        "equivalent_width_angstrom": width,
        "equivalent_width_error_angstrom": error,
    }


def _processing_metrics(
    features: list[dict[str, object]], classification: dict[str, object] | None = None
) -> dict[str, object]:
    """Build checkpoint 2 for some features and index its metrics.

    Parameters
    ----------
    features : `list` [`dict`]
        The feature entries.
    classification : `dict`, optional
        The classification result. Defaults to a classified A0V.

    Returns
    -------
    metrics : `dict`
        The checkpoint's metrics by name.
    """
    checkpoint = assess_processing_quality(
        classification=classification
        or {
            "spectral_type": "A0V",
            "classification_rms": 0.05,
            "rms_gap_to_second_best": 0.03,
            "rms_gap_to_next_class": 0.04,
            "reduced_chi_square": 2.5,
        },
        features=features,
        synthetic_b_minus_v=0.0,
        emission_lines=[],
        is_emission_line_source=False,
        second_order_blue_to_red_ratio=None,
    )
    return by_name(checkpoint)


def test_the_median_relative_error_uses_only_detected_and_possible_features() -> None:
    """Features with other verdicts, or no width, do not enter the median."""
    metrics = _processing_metrics([
        _feature("Hydrogen Balmer series (H-beta)", "detected", 10.0, 1.0),
        _feature("Hydrogen Balmer series (H-alpha)", "possible", -20.0, 4.0, "emission"),
        _feature("Magnesium b triplet", "not_detected", 5.0, 50.0),
        _feature("Sodium D doublet", "detected", None, None),
        _feature("Calcium II H & K", "possible", 0.0, 1.0),
    ])

    assert metrics["median_equivalent_width_relative_error"].value == pytest.approx(0.15)


def test_the_balmer_widths_and_their_errors_are_reported() -> None:
    """H-beta and H-alpha get their own metric, with the error in the note."""
    metrics = _processing_metrics([
        _feature("Hydrogen Balmer series (H-beta)", "detected", 12.34, 1.5),
        _feature("Hydrogen Balmer series (H-alpha)", "possible", -8.0, 2.0, "emission"),
    ])

    assert metrics["hbeta_equivalent_width_angstrom"].value == pytest.approx(12.34)
    assert "12.3 +/- 1.5" in metrics["hbeta_equivalent_width_angstrom"].note
    assert metrics["halpha_equivalent_width_angstrom"].value == pytest.approx(-8.0)
    assert "emission" in metrics["halpha_equivalent_width_angstrom"].note
    assert metrics["hbeta_equivalent_width_angstrom"].unit == "angstrom"


def test_missing_widths_leave_the_metrics_empty() -> None:
    """Without a width, each equivalent-width metric is `None`."""
    metrics = _processing_metrics([_feature("Hydrogen Balmer series (H-beta)", "detected", None, None)])

    assert metrics["median_equivalent_width_relative_error"].value is None
    assert metrics["hbeta_equivalent_width_angstrom"].value is None
    assert metrics["halpha_equivalent_width_angstrom"].value is None
    assert "not measured" in metrics["halpha_equivalent_width_angstrom"].note
    assert _processing_metrics([])["hbeta_equivalent_width_angstrom"].value is None


def test_the_chi_square_metric_reads_the_classification() -> None:
    """The best template's reduced chi-square is reported when classified."""
    assert _processing_metrics([])["best_template_reduced_chi_square"].value == pytest.approx(2.5)

    unclassified = _processing_metrics(
        [], {"spectral_type": "Unknown", "classification_rms": None, "reduced_chi_square": None}
    )
    assert unclassified["best_template_reduced_chi_square"].value is None


def test_checkpoint_one_adds_the_metrics_only_when_given_a_summary() -> None:
    """Without a noise summary the checkpoint keeps its older shape."""
    assessment = assess_input_quality(
        resolution_element_angstrom=45.0,
        is_resolution_measured=True,
        zero_order_saturated_pixel_fraction=0.0,
        valid_fraction=1.0,
        signal_to_noise=10.0,
    )
    summary = SpectrumNoiseSummary(25.0, 0.1, 1.2)

    plain = by_name(input_quality_checkpoint(assessment))
    full = by_name(input_quality_checkpoint(assessment, noise_summary=summary, noise_model=PixelNoiseModel()))

    assert not set(NEW_PRE_PROCESSING_METRICS) & set(plain)
    assert full["median_snr_per_resolution_element"].value == pytest.approx(25.0)
    assert full["fraction_samples_snr_below_5"].value == pytest.approx(0.1)
    assert full["snr_estimate_ratio"].value == pytest.approx(1.2)
    assert "gain assumed" in full["median_snr_per_resolution_element"].note


def test_assumed_noise_numbers_raise_flags_and_known_ones_do_not() -> None:
    """The gain and read-noise assumptions show as flags on checkpoint 1."""
    assessment = SimpleNamespace(
        resolution_element_angstrom=45.0,
        is_resolution_measured=True,
        zero_order_saturated_pixel_fraction=0.0,
        valid_fraction=1.0,
        signal_to_noise=10.0,
    )
    quality = assess_input_quality(**vars(assessment))
    summary = SpectrumNoiseSummary(25.0, 0.1, 1.0)
    known = PixelNoiseModel(
        gain_e_per_adu=2.0,
        read_noise_e=3.0,
        gain_is_assumed=False,
        read_noise_is_assumed=False,
    )

    assumed_flags = input_quality_checkpoint(
        quality, noise_summary=summary, noise_model=PixelNoiseModel()
    ).flags
    known_flags = input_quality_checkpoint(quality, noise_summary=summary, noise_model=known).flags

    assert "gain_assumed" in assumed_flags
    assert "read_noise_assumed" in assumed_flags
    assert "gain_assumed" not in known_flags
    assert "read_noise_assumed" not in known_flags
