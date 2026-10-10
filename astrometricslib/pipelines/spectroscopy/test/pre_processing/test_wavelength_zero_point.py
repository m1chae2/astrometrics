"""Purpose: Tests for the per-spectrum wavelength zero-point measurement.

Description: A spectrum's wavelength scale is anchored on the star's
zero-order image. If that anchor is wrong, every wavelength shifts by the same
amount. These tests build synthetic spectral frames (`make_spectral_frame`)
whose lines sit at known wavelengths, then read the spectrum with a
deliberately wrong anchor, so the true error is known. They check that three
lines recover a 25 A error, that one line is measured but not applied, that
noise alone gives no line, that disagreeing lines are not applied, how the
checkpoint reports it all, and that the pipeline applies the shift before the
corrections that depend on wavelength.

The generator's wavelength model is a straight line (``wavelength = (column -
zero-order column) * dispersion``). A wrong anchor is a constant added to the
wavelength of every sample.
"""

from typing import Any

import numpy as np
import pytest

from astrometricslib.models.spectroscopy_quality import StageQualityMetric
from astrometricslib.models.stellar_source import SpectroscopyResult, StellarObject
from astrometricslib.models.wavelength_scale import WavelengthZeroPointRecord
from astrometricslib.pipelines.spectroscopy import pipeline as pipeline_module
from astrometricslib.pipelines.spectroscopy.pre_processing.assess_input_quality import (
    assess_input_quality,
    input_quality_checkpoint,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.wavelength_zero_point import (
    AGREEMENT_MINIMUM_P_VALUE,
    ZERO_POINT_OFFSET_LIMIT_ANGSTROM,
    measure_wavelength_zero_point,
    shift_wavelengths,
)
from astrometricslib.pipelines.spectroscopy.test.test_result_diagnostics import (
    FRAME_SHAPE,
    ZERO_ORDER_XY,
    _ArrayImage,
    _build_pipeline,
)
from astrometricslib.test.synthetic import SyntheticSpectralFrame, make_spectral_frame

H_BETA = 4861.3
NA_D = 5892.9
H_ALPHA = 6562.8
LINE_DEPTH = 0.5
ZERO_POINT_ERROR_ANGSTROM = 25.0
# The width of the blur to search with. The synthetic lines are 3 pixels wide,
# which is 33 A at 11 A per pixel; 40 A is a little wider, like a real blur.
BLUR_ANGSTROM = 40.0


def read_spectrum(
    frame: SyntheticSpectralFrame, anchor_error_angstrom: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Read a synthetic frame's trail with a wrong wavelength anchor.

    The brightness of each column is the sum of 15 rows centred on the true
    trail, less the known flat sky.

    Parameters
    ----------
    frame : `SyntheticSpectralFrame`
        The frame to read.
    anchor_error_angstrom : `float`
        How much too high every wavelength reads. This is the zero-point error
        the measurement should find.

    Returns
    -------
    read_wavelengths : `numpy.ndarray`
        The wavelengths the wrong anchor gives, in Angstroms.
    true_wavelengths : `numpy.ndarray`
        The wavelengths of the same samples with the right anchor.
    brightness : `numpy.ndarray`
        The brightness of each sample.
    """
    zero_order_x, _ = frame.zero_order_xy
    columns = np.arange(int(zero_order_x) + 2, frame.image.shape[1])
    brightness = []
    for column in columns:
        centre = round(float(frame.trace_center_y(column)))
        rows = np.arange(centre - 7, centre + 8)
        brightness.append(frame.image[rows, column].sum() - 150.0 * rows.size)
    true_wavelengths = (columns - zero_order_x) * frame.dispersion_a_per_px
    return true_wavelengths + anchor_error_angstrom, true_wavelengths, np.array(brightness)


def frame_with_lines(wavelengths: tuple[float, ...], seed: int = 0) -> SyntheticSpectralFrame:
    """Make a frame with absorption lines at the given wavelengths.

    Parameters
    ----------
    wavelengths : `tuple` [`float`, ...]
        The true wavelength of each line, in Angstroms.
    seed : `int`, optional
        The noise seed.

    Returns
    -------
    frame : `SyntheticSpectralFrame`
        The frame, 800 columns of trail at 11 A per pixel.
    """
    return make_spectral_frame(
        lines=tuple((wavelength, LINE_DEPTH) for wavelength in wavelengths),
        seed=seed,
        trail_length_px=800,
    )


def metrics_by_name(record: WavelengthZeroPointRecord) -> dict[str, StageQualityMetric]:
    """Build checkpoint 1 for a record and index its metrics.

    Parameters
    ----------
    record : `WavelengthZeroPointRecord`
        The zero-point measurement.

    Returns
    -------
    metrics : `dict` [`str`, `StageQualityMetric`]
        The checkpoint's metrics by name.
    """
    checkpoint = input_quality_checkpoint(
        assess_input_quality(
            resolution_element_angstrom=40.0,
            is_resolution_measured=True,
            zero_order_saturated_pixel_fraction=0.0,
            valid_fraction=1.0,
            signal_to_noise=12.0,
        ),
        record,
    )
    return {entry.name: entry for entry in checkpoint.metrics}


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_a_25_angstrom_zero_point_error_with_three_lines_is_corrected(seed: int) -> None:
    """Three lines recover a 25 A error; every sample ends within 2 A."""
    frame = frame_with_lines((H_BETA, NA_D, H_ALPHA), seed=seed)
    read_wavelengths, true_wavelengths, brightness = read_spectrum(frame, ZERO_POINT_ERROR_ANGSTROM)

    record = measure_wavelength_zero_point(read_wavelengths, brightness, BLUR_ANGSTROM)
    corrected = shift_wavelengths(read_wavelengths, record)

    assert record.line_count == 3
    assert {line.name for line in record.lines} == {"H-beta", "Na D", "H-alpha"}
    assert record.is_applied is True
    assert record.offset_angstrom == pytest.approx(ZERO_POINT_ERROR_ANGSTROM, abs=2.0)
    assert np.max(np.abs(corrected - true_wavelengths)) < 2.0
    assert record.agreement_p_value is not None
    assert record.agreement_p_value >= AGREEMENT_MINIMUM_P_VALUE


def test_a_negative_zero_point_error_is_corrected_too() -> None:
    """A scale that reads 25 A too low is raised by 25 A."""
    frame = frame_with_lines((H_BETA, NA_D, H_ALPHA))
    read_wavelengths, true_wavelengths, brightness = read_spectrum(frame, -ZERO_POINT_ERROR_ANGSTROM)

    record = measure_wavelength_zero_point(read_wavelengths, brightness, BLUR_ANGSTROM)
    corrected = shift_wavelengths(read_wavelengths, record)

    assert record.is_applied is True
    assert np.max(np.abs(corrected - true_wavelengths)) < 2.0


def test_a_correct_anchor_measures_a_near_zero_offset() -> None:
    """With no error, the measured offset is near zero."""
    frame = frame_with_lines((H_BETA, NA_D, H_ALPHA))
    read_wavelengths, _, brightness = read_spectrum(frame, 0.0)

    record = measure_wavelength_zero_point(read_wavelengths, brightness, BLUR_ANGSTROM)

    assert record.offset_angstrom is not None
    assert record.uncertainty_angstrom is not None
    assert abs(record.offset_angstrom) < 2.0
    assert record.uncertainty_angstrom > 0.0


def test_a_single_line_is_measured_but_not_applied_and_is_flagged() -> None:
    """One line gives an offset that is measured but not applied."""
    frame = frame_with_lines((H_ALPHA,))
    read_wavelengths, _, brightness = read_spectrum(frame, ZERO_POINT_ERROR_ANGSTROM)

    record = measure_wavelength_zero_point(read_wavelengths, brightness, BLUR_ANGSTROM)
    checkpoint_metrics = metrics_by_name(record)
    corrected = shift_wavelengths(read_wavelengths, record)

    assert record.line_count == 1
    assert record.offset_angstrom == pytest.approx(ZERO_POINT_ERROR_ANGSTROM, abs=2.0)
    assert record.is_applied is False
    assert np.array_equal(corrected, read_wavelengths)
    assert checkpoint_metrics["wavelength_zero_point_line_count"].value == pytest.approx(1.0)
    assert checkpoint_metrics["wavelength_zero_point_applied"].value == pytest.approx(0.0)
    assert checkpoint_metrics["wavelength_zero_point_offset_angstrom"].value == pytest.approx(
        ZERO_POINT_ERROR_ANGSTROM, abs=2.0
    )


def test_the_checkpoint_flags_a_spectrum_with_fewer_than_two_lines() -> None:
    """One line or none raises ``wavelength_zero_point_unconstrained``."""
    one_line = frame_with_lines((H_ALPHA,))
    no_line = frame_with_lines(())
    flags = {}
    for name, frame in (("one", one_line), ("none", no_line)):
        read_wavelengths, _, brightness = read_spectrum(frame, ZERO_POINT_ERROR_ANGSTROM)
        record = measure_wavelength_zero_point(read_wavelengths, brightness, BLUR_ANGSTROM)
        checkpoint = input_quality_checkpoint(
            assess_input_quality(
                resolution_element_angstrom=40.0,
                is_resolution_measured=True,
                zero_order_saturated_pixel_fraction=0.0,
                valid_fraction=1.0,
                signal_to_noise=12.0,
            ),
            record,
        )
        flags[name] = checkpoint.flags

    assert "wavelength_zero_point_unconstrained" in flags["one"]
    assert "wavelength_zero_point_unconstrained" in flags["none"]


def test_two_lines_that_are_enough_clear_the_unconstrained_flag() -> None:
    """Two agreeing lines constrain the spectrum and the offset applies."""
    frame = frame_with_lines((H_BETA, H_ALPHA))
    read_wavelengths, _, brightness = read_spectrum(frame, ZERO_POINT_ERROR_ANGSTROM)

    record = measure_wavelength_zero_point(read_wavelengths, brightness, BLUR_ANGSTROM)
    checkpoint = input_quality_checkpoint(
        assess_input_quality(
            resolution_element_angstrom=40.0,
            is_resolution_measured=True,
            zero_order_saturated_pixel_fraction=0.0,
            valid_fraction=1.0,
            signal_to_noise=12.0,
        ),
        record,
    )

    assert record.line_count == 2
    assert record.is_applied is True
    assert "wavelength_zero_point_unconstrained" not in checkpoint.flags


@pytest.mark.parametrize("seed", range(6))
def test_a_noise_only_spectrum_finds_no_lines(seed: int) -> None:
    """Pure noise passes the significance test for no line."""
    frame = frame_with_lines((), seed=seed)
    read_wavelengths, _, brightness = read_spectrum(frame, ZERO_POINT_ERROR_ANGSTROM)

    record = measure_wavelength_zero_point(read_wavelengths, brightness, BLUR_ANGSTROM)

    assert record.line_count == 0
    assert record.lines == []
    assert record.offset_angstrom is None
    assert record.uncertainty_angstrom is None
    assert record.is_applied is False


def test_lines_that_disagree_are_measured_but_not_applied() -> None:
    """H-beta on the scale and H-alpha 30 A off cannot share one offset."""
    frame = frame_with_lines((H_BETA, H_ALPHA + 30.0))
    read_wavelengths, _, brightness = read_spectrum(frame, 0.0)

    record = measure_wavelength_zero_point(read_wavelengths, brightness, BLUR_ANGSTROM)
    checkpoint_metrics = metrics_by_name(record)

    assert record.line_count == 2
    assert record.agreement_p_value is not None
    assert record.agreement_p_value < AGREEMENT_MINIMUM_P_VALUE
    assert record.is_applied is False
    assert checkpoint_metrics["wavelength_zero_point_applied"].value == pytest.approx(0.0)


def test_a_spectrum_that_does_not_reach_a_line_does_not_find_it() -> None:
    """A short spectrum with no room for side bands measures nothing."""
    wavelengths = np.arange(5000.0, 5200.0, 11.0)

    record = measure_wavelength_zero_point(wavelengths, np.full(wavelengths.size, 1000.0), BLUR_ANGSTROM)

    assert record.line_count == 0


def test_a_flat_noiseless_spectrum_finds_no_lines() -> None:
    """A spectrum with no structure at all has nothing to measure."""
    wavelengths = np.arange(4000.0, 9000.0, 11.0)

    record = measure_wavelength_zero_point(wavelengths, np.full(wavelengths.size, 3000.0), BLUR_ANGSTROM)

    assert record.line_count == 0


def test_unusable_samples_are_left_out() -> None:
    """NaN samples do not stop a line from being found."""
    frame = frame_with_lines((H_BETA, NA_D, H_ALPHA))
    read_wavelengths, _, brightness = read_spectrum(frame, ZERO_POINT_ERROR_ANGSTROM)
    brightness = brightness.copy()
    brightness[10:14] = np.nan

    record = measure_wavelength_zero_point(read_wavelengths, brightness, BLUR_ANGSTROM)

    assert record.line_count == 3
    assert record.is_applied is True


def test_the_zero_order_saturation_is_recorded_with_the_offset() -> None:
    """The record keeps the saturated fraction and the flag level result."""
    frame = frame_with_lines((H_BETA, H_ALPHA))
    read_wavelengths, _, brightness = read_spectrum(frame, ZERO_POINT_ERROR_ANGSTROM)

    saturated = measure_wavelength_zero_point(
        read_wavelengths, brightness, BLUR_ANGSTROM, zero_order_saturated_pixel_fraction=0.2
    )
    clean = measure_wavelength_zero_point(
        read_wavelengths, brightness, BLUR_ANGSTROM, zero_order_saturated_pixel_fraction=0.0
    )
    unmeasured = measure_wavelength_zero_point(read_wavelengths, brightness, BLUR_ANGSTROM)

    assert saturated.is_zero_order_saturated is True
    assert saturated.zero_order_saturated_pixel_fraction == pytest.approx(0.2)
    assert clean.is_zero_order_saturated is False
    assert unmeasured.is_zero_order_saturated is None
    assert unmeasured.zero_order_saturated_pixel_fraction is None


def test_the_offset_metric_has_a_designed_limit_and_fails_above_it() -> None:
    """A 25 A offset is over the 20 A limit; the metric fails."""
    frame = frame_with_lines((H_BETA, NA_D, H_ALPHA))
    read_wavelengths, _, brightness = read_spectrum(frame, ZERO_POINT_ERROR_ANGSTROM)
    record = measure_wavelength_zero_point(read_wavelengths, brightness, BLUR_ANGSTROM)
    checkpoint = input_quality_checkpoint(
        assess_input_quality(
            resolution_element_angstrom=40.0,
            is_resolution_measured=True,
            zero_order_saturated_pixel_fraction=0.0,
            valid_fraction=1.0,
            signal_to_noise=12.0,
        ),
        record,
    )
    offset_metric = {entry.name: entry for entry in checkpoint.metrics}[
        "wavelength_zero_point_offset_angstrom"
    ]

    assert ZERO_POINT_OFFSET_LIMIT_ANGSTROM == pytest.approx(20.0)
    assert offset_metric.limit == pytest.approx(20.0)
    assert "designed" in offset_metric.note
    assert offset_metric.passed is False
    assert "wavelength_zero_point_large" in checkpoint.flags
    assert checkpoint.has_failed_metric is True


def test_a_small_offset_passes_the_limit_and_raises_no_flag() -> None:
    """A 5 A offset is inside the limit."""
    frame = frame_with_lines((H_BETA, NA_D, H_ALPHA))
    read_wavelengths, _, brightness = read_spectrum(frame, 5.0)
    record = measure_wavelength_zero_point(read_wavelengths, brightness, BLUR_ANGSTROM)
    checkpoint_metrics = metrics_by_name(record)

    assert checkpoint_metrics["wavelength_zero_point_offset_angstrom"].passed is True
    assert checkpoint_metrics["wavelength_zero_point_applied"].value == pytest.approx(1.0)
    assert checkpoint_metrics["wavelength_zero_point_line_count"].value == pytest.approx(3.0)


def test_a_checkpoint_without_a_measurement_keeps_its_old_metrics() -> None:
    """Leaving the record out adds no ``wavelength_zero_point`` metric."""
    checkpoint = input_quality_checkpoint(
        assess_input_quality(
            resolution_element_angstrom=40.0,
            is_resolution_measured=True,
            zero_order_saturated_pixel_fraction=0.0,
            valid_fraction=1.0,
            signal_to_noise=12.0,
        )
    )

    assert not [entry for entry in checkpoint.metrics if entry.name.startswith("wavelength_zero_point")]


def test_the_record_survives_its_camel_case_form() -> None:
    """A measured record round-trips through ``model_dump(by_alias=True)``."""
    frame = frame_with_lines((H_BETA, NA_D, H_ALPHA))
    read_wavelengths, _, brightness = read_spectrum(frame, ZERO_POINT_ERROR_ANGSTROM)
    record = measure_wavelength_zero_point(read_wavelengths, brightness, BLUR_ANGSTROM)

    dumped = record.model_dump(by_alias=True)

    assert dumped["lineCount"] == 3
    assert dumped["isApplied"] is True
    assert dumped["lines"][0]["restWavelengthAngstrom"] == pytest.approx(H_BETA)
    assert type(dumped["offsetAngstrom"]) is float
    assert WavelengthZeroPointRecord.model_validate(dumped) == record


# --------------------------------------------------- the pipeline's order


def build_pipeline_frame(
    pipeline: pipeline_module.SpectroscopyPipeline, rest_wavelengths: tuple[float, ...], error_angstrom: float
) -> _ArrayImage:
    """Make a frame whose lines the pipeline reads ``error_angstrom`` too high.

    The pipeline's own wavelength model is the grating equation, not the
    generator's straight line. So the frame is drawn in two passes. A first,
    line-free frame shows which pipeline wavelength each column gets. A second
    frame then puts each line at the column where the pipeline reads
    ``rest + error_angstrom``.

    Parameters
    ----------
    pipeline : `SpectroscopyPipeline`
        The pipeline that will read the frame.
    rest_wavelengths : `tuple` [`float`, ...]
        The true wavelength of each line, in Angstroms.
    error_angstrom : `float`
        How much too high the pipeline should read each line.

    Returns
    -------
    image : `_ArrayImage`
        The frame to process.
    """
    blank = make_spectral_frame(zero_order_xy=ZERO_ORDER_XY, angle_deg=2.0, lines=(), shape=FRAME_SHAPE)
    extraction = pipeline._process_single_star(
        _ArrayImage(blank.image, header={"AIRMASS": 1.2}), ZERO_ORDER_XY, auto_detect_angle=False
    )
    distances = np.array(extraction["sample_distances_px"])
    wavelengths = np.array(extraction["wavelengths"]) * 10.0
    # With a dispersion of 1 A per pixel, the generator's "wavelength" of a
    # line is its distance from the zero order in pixels.
    line_distances = [
        float(np.interp(rest + error_angstrom, wavelengths, distances)) for rest in rest_wavelengths
    ]
    frame = make_spectral_frame(
        zero_order_xy=ZERO_ORDER_XY,
        angle_deg=2.0,
        dispersion_a_per_px=1.0,
        lines=tuple((distance, LINE_DEPTH) for distance in line_distances),
        shape=FRAME_SHAPE,
    )
    return _ArrayImage(frame.image, header={"AIRMASS": 1.2})


def process_star(
    pipeline: pipeline_module.SpectroscopyPipeline, image: _ArrayImage
) -> tuple[SpectroscopyResult, dict[str, Any]]:
    """Extract the star at the zero order and build its result.

    Parameters
    ----------
    pipeline : `SpectroscopyPipeline`
        The pipeline.
    image : `_ArrayImage`
        The frame.

    Returns
    -------
    result : `SpectroscopyResult`
        What the pipeline saved on the star.
    extraction : `dict`
        The extraction result, with the uncorrected wavelengths.
    """
    extraction = pipeline._process_single_star(image, ZERO_ORDER_XY, auto_detect_angle=False)
    star = StellarObject(id="TestStar")
    pipeline._apply_result_to_stellar_object(star, extraction, image)
    assert star.spectroscopy is not None
    return star.spectroscopy, extraction


def test_the_pipeline_corrects_the_zero_point_before_the_wavelength_dependent_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wavelength-dependent steps see the corrected wavelengths.

    These are the quantum-efficiency, response and extinction steps. The test
    wraps those three steps to record the wavelengths each receives.
    """
    pipeline = _build_pipeline("fixed")
    rest = (H_BETA, NA_D, H_ALPHA)
    image = build_pipeline_frame(pipeline, rest, ZERO_POINT_ERROR_ANGSTROM)
    seen: dict[str, np.ndarray] = {}

    original_qe = pipeline_module.apply_quantum_efficiency_correction
    original_response = pipeline_module.apply_instrument_response
    original_extinction = pipeline_module.apply_extinction_correction

    def record_qe(*, wavelength_nm: np.ndarray, **kwargs: Any) -> np.ndarray:
        """Record the wavelengths the quantum-efficiency step receives.

        Returns
        -------
        corrected : `numpy.ndarray`
            The real step's result.
        """
        seen["qe"] = np.array(wavelength_nm) * 10.0
        return original_qe(wavelength_nm=wavelength_nm, **kwargs)

    def record_response(wavelength_angstrom: np.ndarray, *args: Any, **kwargs: Any) -> Any:
        """Record the wavelengths the response step receives.

        Returns
        -------
        corrected : `Any`
            The real step's result.
        """
        seen["response"] = np.array(wavelength_angstrom)
        return original_response(wavelength_angstrom, *args, **kwargs)

    def record_extinction(wavelength_angstrom: np.ndarray, *args: Any, **kwargs: Any) -> Any:
        """Record the wavelengths the extinction step receives.

        Returns
        -------
        corrected : `Any`
            The real step's result.
        """
        seen["extinction"] = np.array(wavelength_angstrom)
        return original_extinction(wavelength_angstrom, *args, **kwargs)

    monkeypatch.setattr(pipeline_module, "apply_quantum_efficiency_correction", record_qe)
    monkeypatch.setattr(pipeline_module, "apply_instrument_response", record_response)
    monkeypatch.setattr(pipeline_module, "apply_extinction_correction", record_extinction)

    result, extraction = process_star(pipeline, image)
    uncorrected = np.array(extraction["wavelengths"]) * 10.0
    record = result.wavelength_zero_point

    assert record is not None
    assert record.is_applied is True
    assert record.line_count >= 2
    # The pipeline's wavelength grid is 22 A per pixel here, so the offset is
    # found to within about a quarter of a sample, not to 2 A.
    assert record.offset_angstrom == pytest.approx(ZERO_POINT_ERROR_ANGSTROM, abs=6.0)
    stored = np.array(result.wavelengths_angstrom)
    assert np.allclose(stored, uncorrected - record.offset_angstrom)
    assert set(seen) == {"qe", "response", "extinction"}
    for name, wavelengths in seen.items():
        assert np.allclose(wavelengths, stored), f"the {name} step did not see the corrected wavelengths"


def test_the_pipeline_records_the_zero_point_metrics_in_checkpoint_one() -> None:
    """The saved result carries the record and the four checkpoint metrics."""
    pipeline = _build_pipeline("fixed")
    image = build_pipeline_frame(pipeline, (H_BETA, NA_D, H_ALPHA), ZERO_POINT_ERROR_ANGSTROM)

    result, _ = process_star(pipeline, image)
    checkpoint = next(entry for entry in result.stage_quality if entry.stage == "pre_processing")
    metrics = {entry.name: entry for entry in checkpoint.metrics}

    assert metrics["wavelength_zero_point_line_count"].value >= 2.0
    assert metrics["wavelength_zero_point_applied"].value == pytest.approx(1.0)
    assert metrics["wavelength_zero_point_offset_angstrom"].value == pytest.approx(
        ZERO_POINT_ERROR_ANGSTROM, abs=6.0
    )
    assert "wavelength_zero_point_unconstrained" not in checkpoint.flags
    assert result.wavelength_zero_point is not None
    assert result.wavelength_zero_point.zero_order_saturated_pixel_fraction is not None


def test_the_pipeline_leaves_the_wavelengths_alone_when_no_line_is_found() -> None:
    """A spectrum with no lines keeps the extractor's wavelengths exactly."""
    pipeline = _build_pipeline("fixed")
    image = build_pipeline_frame(pipeline, (), 0.0)

    result, extraction = process_star(pipeline, image)

    assert result.wavelength_zero_point is not None
    assert result.wavelength_zero_point.line_count == 0
    assert result.wavelength_zero_point.is_applied is False
    assert result.wavelengths_angstrom == [float(w) * 10.0 for w in extraction["wavelengths"]]
    checkpoint = next(entry for entry in result.stage_quality if entry.stage == "pre_processing")
    assert "wavelength_zero_point_unconstrained" in checkpoint.flags
