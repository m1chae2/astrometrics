"""Red-team tests for the stacking gates.

Each gate has to go red on input that is truly bad, stay green on good input,
and say "not checked" when it could not look. A gate that has never failed
could be hard-wired to pass; these tests make every one fail on purpose.

Verdicts come from the real threshold functions and the real flat assessment,
so a test cannot pass by agreeing with a stand-in.
"""

from pathlib import Path
from typing import Any

import pytest

from astrometricslib.foundation.enums import FilterType
from astrometricslib.models.gate_result import GateResult, GateStatus
from astrometricslib.models.quality_summary import ExcludedFrame, StackingPipelineQualityMetrics
from astrometricslib.models.target import FrameRecord
from astrometricslib.pipelines.shared.quality.saturation import is_saturation_significant
from astrometricslib.pipelines.stacking.post_processing import assess_output_quality as aoq
from astrometricslib.pipelines.stacking.post_processing.exposure_group_report import (
    RAW_FRAME_CLIPPING_GATE_NAME,
    raw_frame_clipping_gate,
)
from astrometricslib.pipelines.stacking.post_processing.stack_quality import (
    DEFAULT_ZERO_FRACTION_FLAG_THRESHOLD,
    SPECTRAL_ZERO_FRACTION_FLAG_THRESHOLD,
    is_negative_pixel_percent_significant,
    is_rejected_fraction_significant,
    is_stacked_fwhm_degraded,
    is_zero_fraction_significant,
    zero_fraction_threshold,
)
from astrometricslib.pipelines.stacking.pre_processing import flat_calibration
from astrometricslib.pipelines.stacking.pre_processing.assess_input_quality import calibration_gates
from astrometricslib.pipelines.stacking.pre_processing.background_homogeneity import (
    BACKGROUND_GATE_NAME,
    background_homogeneity_gate,
    find_dominant_background_subset_by_exposure,
)
from astrometricslib.pipelines.stacking.pre_processing.frame_homogeneity import (
    GAIN_GATE_NAME,
    find_dominant_gain_subset,
    gain_homogeneity_gate,
)
from astrometricslib.pipelines.stacking.processing.exposure_groups import CLIPPED_FRAME_ZERO_FRACTION
from astrometricslib.pipelines.stacking.test.test_flat_calibration import write_flat


def by_name(gates: list[GateResult]) -> dict[str, GateResult]:
    """Index gates by name.

    Returns
    -------
    gates_by_name : `dict` [`str`, `GateResult`]
        The same gates, keyed by their names.
    """
    return {gate.name: gate for gate in gates}


# ---------------------------------------------------------------- gain


def make_frame(index: int, gain: Any, background: float | None = None) -> FrameRecord:
    """Build a light frame record with a gain and a measured background.

    Returns
    -------
    frame : `FrameRecord`
        A 120 s luminance frame.
    """
    frame = FrameRecord(
        path=f"/fake/l_{index}.fits",
        filter=FilterType.L,
        camera="ZWO ASI 533MM Pro",
        exposure="120.0",
        timestamp=float(index) * 120.0,
        iso=gain,
    )
    frame.measurements.background_level = background
    return frame


def test_gain_gate_is_not_checked_when_no_frame_records_its_gain() -> None:
    """Frames with no gain form one "unknown" group; that is no check."""
    frames = [make_frame(index, "None") for index in range(10)]
    kept, excluded = find_dominant_gain_subset(frames)

    gate = gain_homogeneity_gate(frames, excluded)

    assert len(kept) == 10
    assert gate.name == GAIN_GATE_NAME
    assert gate.status is GateStatus.NOT_CHECKED


def test_gain_gate_passes_and_counts_the_minority_it_set_aside() -> None:
    """A minority gain is corrected, not failed; the count is on record."""
    frames = [make_frame(index, "100") for index in range(9)] + [make_frame(9, "200")]
    _kept, excluded = find_dominant_gain_subset(frames)

    gate = gain_homogeneity_gate(frames, excluded)

    assert gate.name == GAIN_GATE_NAME
    assert gate.status is GateStatus.PASSED
    assert gate.measured_value == pytest.approx(1.0)
    assert "1 frame(s)" in gate.detail


# ---------------------------------------------------------- background


def split_check(backgrounds: list[float | None]) -> tuple[list[FrameRecord], list[dict[str, Any]]]:
    """Run the real background check on frames with the given backgrounds.

    Returns
    -------
    frames : `list` [`FrameRecord`]
        The frames handed to the check.
    splits : `list` [`dict`]
        The splits the check found.
    """
    frames = [make_frame(index, "100", background) for index, background in enumerate(backgrounds)]
    _kept, _excluded, splits = find_dominant_background_subset_by_exposure(frames)
    return frames, splits


def test_background_gate_fails_on_a_cloud_ramp() -> None:
    """Eight steady frames and two washed-out ones are a split."""
    frames, splits = split_check([100.0, 101.0, 99.0, 100.5, 100.2, 99.5, 100.8, 100.1, 600.0, 610.0])

    gate = background_homogeneity_gate(frames, splits)

    assert gate.name == BACKGROUND_GATE_NAME
    assert gate.status is GateStatus.FAILED
    assert gate.detail.startswith("background split:")


def test_background_gate_passes_on_steady_frames() -> None:
    """Steady frames pass, and say how many exposure lengths were checked."""
    frames, splits = split_check([100.0, 101.0, 99.0, 100.5, 100.2, 99.5, 100.8, 100.1])

    gate = background_homogeneity_gate(frames, splits)

    assert gate.name == BACKGROUND_GATE_NAME
    assert gate.status is GateStatus.PASSED
    assert "1 exposure length(s) checked" in gate.detail


def test_background_gate_is_not_checked_when_no_background_was_measured() -> None:
    """Frames with no measured background are skipped, not passed."""
    frames, splits = split_check([None] * 8)

    gate = background_homogeneity_gate(frames, splits)

    assert gate.name == BACKGROUND_GATE_NAME
    assert gate.status is GateStatus.NOT_CHECKED


def test_background_gate_is_not_checked_with_too_few_measured_frames() -> None:
    """Two measured frames cannot show a split."""
    frames, splits = split_check([100.0, 900.0])

    gate = background_homogeneity_gate(frames, splits)

    assert gate.name == BACKGROUND_GATE_NAME
    assert gate.status is GateStatus.NOT_CHECKED


def test_background_gate_is_not_checked_when_the_setting_is_off() -> None:
    """A switched-off check is on record as not run."""
    frames, splits = split_check([100.0, 101.0, 99.0, 100.5])

    gate = background_homogeneity_gate(frames, splits, enabled=False)

    assert gate.name == BACKGROUND_GATE_NAME
    assert gate.status is GateStatus.NOT_CHECKED
    assert "turned off" in gate.detail


# ---------------------------------------------------------------- flats


def flat_diagnostics(tmp_path: Path, level: float, noise: float, count: int) -> dict[str, Any]:
    """Assess real synthetic flats and return the diagnostics the stage sees.

    Returns
    -------
    diagnostics : `dict`
        ``flat_calibration`` from the real `assess_flats`, plus one flat.
    """
    paths = [write_flat(tmp_path / f"flat_{i}.fits", level, noise, 30 + i) for i in range(count)]
    assessment = flat_calibration.assess_flats(paths)
    return {"flat_calibration": assessment.as_diagnostics(), "calibration_applied": {"flat": True}}


def test_flat_gates_fail_on_a_faint_noisy_flat(tmp_path: Path) -> None:
    """One faint, noisy flat fails both the level and the noise gate."""
    gates = by_name(calibration_gates(flat_diagnostics(tmp_path, level=0.01, noise=0.045, count=1)))

    assert gates["flat_level"].status is GateStatus.FAILED
    assert gates["flat_noise"].status is GateStatus.FAILED
    assert gates["flat_noise"].limit == pytest.approx(flat_calibration.MAXIMUM_FLAT_NOISE_FRACTION)


def test_flat_level_gate_fails_on_a_flat_near_saturation(tmp_path: Path) -> None:
    """A flat near full scale fails the level gate only."""
    gates = by_name(calibration_gates(flat_diagnostics(tmp_path, level=0.97, noise=0.002, count=1)))

    assert gates["flat_level"].status is GateStatus.FAILED


def test_flat_gates_pass_on_a_good_set(tmp_path: Path) -> None:
    """Forty bright, quiet flats pass both gates, with their numbers."""
    gates = by_name(calibration_gates(flat_diagnostics(tmp_path, level=0.3, noise=0.01, count=40)))

    assert gates["flat_level"].status is GateStatus.PASSED
    assert gates["flat_noise"].status is GateStatus.PASSED
    assert gates["flat_noise"].measured_value is not None


def test_flat_gates_are_not_checked_when_the_stack_used_no_flats() -> None:
    """No flats means nothing to check; that is not a pass."""
    gates = by_name(calibration_gates({"calibration_applied": {"dark": True}}))

    assert gates["flat_level"].status is GateStatus.NOT_CHECKED
    assert gates["flat_noise"].status is GateStatus.NOT_CHECKED


def test_unreadable_flats_fail_the_level_gate_and_leave_noise_unchecked(tmp_path: Path) -> None:
    """A flat set that cannot be read fails; its noise cannot be judged."""
    bad = tmp_path / "bad.fits"
    bad.write_text("not a fits file")
    assessment = flat_calibration.assess_flats([str(bad)])
    gates = by_name(
        calibration_gates({
            "flat_calibration": assessment.as_diagnostics(),
            "calibration_applied": {"flat": True},
        })
    )

    assert gates["flat_level"].status is GateStatus.FAILED
    assert gates["flat_noise"].status is GateStatus.NOT_CHECKED


# ------------------------------------------------- calibration metadata


def test_calibration_metadata_gate_fails_on_a_mismatch() -> None:
    """A recorded mismatch between lights and darks fails the gate."""
    gate = by_name(
        calibration_gates({
            "calibration_applied": {"dark": True},
            "calibration_mismatch_flags": ["dark gain differs"],
        })
    )["calibration_metadata"]

    assert gate.status is GateStatus.FAILED
    assert gate.detail == "1 calibration metadata mismatch(es): dark gain differs"


def test_calibration_metadata_gate_passes_when_calibration_matched() -> None:
    """Applied calibration with no mismatch is a pass."""
    gate = by_name(calibration_gates({"calibration_applied": {"dark": True, "bias": True}}))[
        "calibration_metadata"
    ]

    assert gate.status is GateStatus.PASSED


def test_calibration_metadata_gate_is_not_checked_without_calibration() -> None:
    """With no calibration frame applied there was nothing to compare."""
    gate = by_name(calibration_gates({"calibration_applied": {"dark": False, "flat": False, "bias": False}}))[
        "calibration_metadata"
    ]

    assert gate.status is GateStatus.NOT_CHECKED


# ------------------------------------------------------- output gates


def make_metrics(is_spectral: bool = False) -> StackingPipelineQualityMetrics:
    """Build metrics with nothing measured.

    Returns
    -------
    metrics : `StackingPipelineQualityMetrics`
        Default metrics for a ten-frame stack.
    """
    return StackingPipelineQualityMetrics(is_spectral=is_spectral, frames_submitted=10, frames_stacked=10)


def output_gates(metrics: StackingPipelineQualityMetrics, applied: bool = True) -> dict[str, GateResult]:
    """Build the output gates keyed by name.

    Returns
    -------
    gates : `dict` [`str`, `GateResult`]
        The six output gates.
    """
    return by_name(aoq.output_quality_gates(metrics, applied))


def test_every_output_gate_is_not_checked_when_nothing_was_measured() -> None:
    """A stack with no measurements has no verdicts; none may read as passed.

    Negative pixels are the one exception: no Siril warning is the
    measurement.
    """
    gates = output_gates(make_metrics())

    unmeasured = {name for name, gate in gates.items() if gate.status is GateStatus.NOT_CHECKED}
    assert unmeasured == {
        aoq.REJECTED_GATE_NAME,
        aoq.SHARPNESS_GATE_NAME,
        aoq.SPECTRAL_REGISTRATION_GATE_NAME,
        aoq.SATURATION_GATE_NAME,
        aoq.ZERO_FRACTION_GATE_NAME,
    }
    assert GateStatus.FAILED not in {gate.status for gate in gates.values()}


def test_rejected_fraction_gate_goes_red_and_green() -> None:
    """A 30% rejection fails; 5% passes."""
    metrics = make_metrics()
    metrics.rejected_pixel_fraction = 0.30
    metrics.rejected_fraction_flagged = is_rejected_fraction_significant(0.30)
    assert output_gates(metrics)[aoq.REJECTED_GATE_NAME].status is GateStatus.FAILED

    metrics.rejected_pixel_fraction = 0.05
    metrics.rejected_fraction_flagged = is_rejected_fraction_significant(0.05)
    assert output_gates(metrics)[aoq.REJECTED_GATE_NAME].status is GateStatus.PASSED


def test_sharpness_gate_goes_red_on_a_misregistered_stack() -> None:
    """A stack 1.5 times wider than its inputs predict fails; 1.0 passes."""
    metrics = make_metrics()
    metrics.expected_stack_fwhm_px = 3.0
    metrics.stacked_fwhm_px = 4.5
    metrics.fwhm_degraded = is_stacked_fwhm_degraded(4.5, 3.0)
    failed = output_gates(metrics)[aoq.SHARPNESS_GATE_NAME]
    assert failed.status is GateStatus.FAILED
    assert failed.measured_value == pytest.approx(1.5)

    metrics.stacked_fwhm_px = 3.0
    metrics.fwhm_degraded = is_stacked_fwhm_degraded(3.0, 3.0)
    assert output_gates(metrics)[aoq.SHARPNESS_GATE_NAME].status is GateStatus.PASSED


def test_sharpness_gate_is_not_checked_for_spectra_and_single_frames() -> None:
    """Star width does not apply to spectra or to a single frame."""
    spectral = make_metrics(is_spectral=True)
    assert output_gates(spectral)[aoq.SHARPNESS_GATE_NAME].status is GateStatus.NOT_CHECKED

    single = make_metrics()
    single.stacked_fwhm_px = 3.0
    single.expected_stack_fwhm_px = 3.0
    assert output_gates(single, applied=False)[aoq.SHARPNESS_GATE_NAME].status is GateStatus.NOT_CHECKED


def test_saturation_gate_goes_red_and_green() -> None:
    """One percent saturated fails; none passes."""
    metrics = make_metrics()
    metrics.saturated_pixel_fraction = 0.01
    metrics.saturation_flagged = is_saturation_significant(0.01)
    assert output_gates(metrics)[aoq.SATURATION_GATE_NAME].status is GateStatus.FAILED

    metrics.saturated_pixel_fraction = 0.0
    metrics.saturation_flagged = is_saturation_significant(0.0)
    assert output_gates(metrics)[aoq.SATURATION_GATE_NAME].status is GateStatus.PASSED


def test_zero_fraction_gate_goes_red_on_a_blank_stack_and_skips_spectra() -> None:
    """A 99% zero image fails and a 6% zero image passes."""
    image = make_metrics()
    image.zero_pixel_fraction = 0.99
    image.zero_fraction_flagged = is_zero_fraction_significant(0.99)
    assert output_gates(image)[aoq.ZERO_FRACTION_GATE_NAME].status is GateStatus.FAILED

    image.zero_pixel_fraction = 0.06
    image.zero_fraction_flagged = is_zero_fraction_significant(0.06)
    assert output_gates(image)[aoq.ZERO_FRACTION_GATE_NAME].status is GateStatus.PASSED


def test_spectral_zero_fraction_gate_uses_its_own_limit() -> None:
    """Spectra are judged at 5% zero pixels, not the 50% of images.

    Library data: clean spectra read at most 0.4%, Sirius 32%, Alnath 35%.
    """
    for clean_fraction in (0.0, 0.0028, 0.004):
        spectrum = make_metrics(is_spectral=True)
        spectrum.zero_pixel_fraction = clean_fraction
        spectrum.zero_fraction_flagged = is_zero_fraction_significant(
            clean_fraction, zero_fraction_threshold(True)
        )
        assert output_gates(spectrum)[aoq.ZERO_FRACTION_GATE_NAME].status is GateStatus.PASSED

    for bad_fraction in (0.3235, 0.3503):
        spectrum = make_metrics(is_spectral=True)
        spectrum.zero_pixel_fraction = bad_fraction
        spectrum.zero_fraction_flagged = is_zero_fraction_significant(
            bad_fraction, zero_fraction_threshold(True)
        )
        gate = output_gates(spectrum)[aoq.ZERO_FRACTION_GATE_NAME]
        assert gate.status is GateStatus.FAILED
        assert gate.limit == SPECTRAL_ZERO_FRACTION_FLAG_THRESHOLD

    # An image stack keeps the 50% limit: 35% zero is not a blank image.
    assert zero_fraction_threshold(False) == pytest.approx(DEFAULT_ZERO_FRACTION_FLAG_THRESHOLD)
    assert DEFAULT_ZERO_FRACTION_FLAG_THRESHOLD == pytest.approx(0.5)
    assert not is_zero_fraction_significant(0.35, zero_fraction_threshold(False))


def test_spectral_zero_fraction_gate_is_not_checked_when_unmeasured() -> None:
    """A spectral stack whose file could not be read is not a pass."""
    spectrum = make_metrics(is_spectral=True)
    assert output_gates(spectrum)[aoq.ZERO_FRACTION_GATE_NAME].status is GateStatus.NOT_CHECKED


def _zero_fraction_entries(*pairs: tuple[float, float]) -> dict:
    """Build diagnostics holding measured raw-frame zero fractions.

    Returns
    -------
    diagnostics : `dict`
        Diagnostics with one entry per (exposure, zero fraction) pair.
    """
    return {
        "exposure_group_frame_zero_fractions": [
            {"exposure_seconds": exposure, "frame_zero_fraction": fraction} for exposure, fraction in pairs
        ]
    }


def test_raw_frame_clipping_gate_passes_when_no_group_is_clipped() -> None:
    """Clean groups pass, with the largest zero share as the value."""
    gate = raw_frame_clipping_gate(_zero_fraction_entries((2.0, 0.01), (5.0, 0.0)))
    assert gate.status is GateStatus.PASSED
    assert gate.measured_value == pytest.approx(0.01)
    assert gate.limit == CLIPPED_FRAME_ZERO_FRACTION


def test_raw_frame_clipping_gate_fails_softly_when_longer_groups_can_replace_the_pixels() -> None:
    """Some groups clipped: fails, and says the longer groups fill in."""
    gate = raw_frame_clipping_gate(_zero_fraction_entries((0.5, 0.71), (5.0, 0.0)))
    assert gate.status is GateStatus.FAILED
    assert gate.measured_value == pytest.approx(0.71)
    assert "longer groups replace the faint pixels" in gate.detail
    assert "0.5 s exposure group" in gate.detail
    assert "5 s exposure group" not in gate.detail.replace("0.5 s", "")


def test_raw_frame_clipping_gate_fails_hard_when_every_group_is_clipped() -> None:
    """The Alnath case: nothing can replace the faint pixels."""
    gate = raw_frame_clipping_gate(_zero_fraction_entries((0.1, 0.92), (0.25, 0.85), (1.02, 0.34)))
    assert gate.name == RAW_FRAME_CLIPPING_GATE_NAME
    assert gate.status is GateStatus.FAILED
    assert "nothing can replace the faint pixels" in gate.detail
    assert gate.measured_value == pytest.approx(0.92)


def test_raw_frame_clipping_gate_is_not_checked_without_a_measurement() -> None:
    """No measured list (or an empty one) is not a pass."""
    assert raw_frame_clipping_gate({}).name == RAW_FRAME_CLIPPING_GATE_NAME
    assert raw_frame_clipping_gate({}).status is GateStatus.NOT_CHECKED
    assert (
        raw_frame_clipping_gate({"exposure_group_frame_zero_fractions": []}).status is GateStatus.NOT_CHECKED
    )


def test_a_summary_with_clipped_raw_frames_is_flagged_by_the_gate() -> None:
    """The stage records the failed gate and its sentence as a flag reason."""
    from astrometricslib.pipelines.stacking import stage

    frames = [make_frame(index, "100", 100.0) for index in range(10)]
    summary = stage._build_stack_quality_summary(
        target=type("TargetStub", (), {"id": "Alnath", "frames": frames})(),
        is_spectral=True,
        frames_submitted=10,
        target_frames=frames,
        excluded_frames=[],
        diagnostics=_zero_fraction_entries((0.1, 0.92)),
        background_split=None,
        stacked_path=None,
        gate_results=[],
    )

    assert summary.gate(RAW_FRAME_CLIPPING_GATE_NAME).status is GateStatus.FAILED
    assert summary.flagged
    assert any("clipped at zero" in reason for reason in summary.flag_reasons)


def test_negative_pixel_gate_goes_red_on_siril_warning() -> None:
    """A Siril warning of 99% negative pixels fails; no warning passes."""
    metrics = make_metrics()
    metrics.negative_pixel_max_percent = 99
    metrics.negative_pixels_flagged = is_negative_pixel_percent_significant(99)
    assert output_gates(metrics)[aoq.NEGATIVE_PIXELS_GATE_NAME].status is GateStatus.FAILED

    clean = make_metrics()
    assert output_gates(clean)[aoq.NEGATIVE_PIXELS_GATE_NAME].status is GateStatus.PASSED


def test_spectral_registration_gate_goes_red_and_is_not_checked_when_it_did_not_run() -> None:
    """Flagged frames fail; a check that never ran is not a pass."""
    metrics = make_metrics(is_spectral=True)
    # The check did not run (its lists did not line up): no flags, no marker.
    assert output_gates(metrics)[aoq.SPECTRAL_REGISTRATION_GATE_NAME].status is GateStatus.NOT_CHECKED

    metrics.spectral_registration_checked = True
    assert output_gates(metrics)[aoq.SPECTRAL_REGISTRATION_GATE_NAME].status is GateStatus.PASSED

    metrics.spectral_registration_flags = [ExcludedFrame(path="a.fits", reason="drifted")]
    assert output_gates(metrics)[aoq.SPECTRAL_REGISTRATION_GATE_NAME].status is GateStatus.FAILED


def test_the_real_builder_records_every_stacking_gate() -> None:
    """A finished summary carries all gates, unmeasured ones as not checked."""
    from astrometricslib.pipelines.stacking import stage

    frames = [make_frame(index, "100", 100.0) for index in range(10)]
    summary = stage._build_stack_quality_summary(
        target=type("TargetStub", (), {"id": "M 52", "frames": frames})(),
        is_spectral=False,
        frames_submitted=10,
        target_frames=frames,
        excluded_frames=[],
        diagnostics={},
        background_split=None,
        stacked_path=None,
        gate_results=[],
    )

    recorded = {gate.name for gate in summary.gates}
    assert recorded == {
        "flat_level",
        "flat_noise",
        "calibration_metadata",
        "calibration_frame_count",
        "exposure_group_linearity",
        "exposure_groups_combined",
        aoq.REJECTED_GATE_NAME,
        aoq.SHARPNESS_GATE_NAME,
        aoq.SPECTRAL_REGISTRATION_GATE_NAME,
        aoq.SATURATION_GATE_NAME,
        aoq.ZERO_FRACTION_GATE_NAME,
        aoq.NEGATIVE_PIXELS_GATE_NAME,
        RAW_FRAME_CLIPPING_GATE_NAME,
    }
    assert summary.flagged is False
