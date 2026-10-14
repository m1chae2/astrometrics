"""Purpose: Tests for the run-level wavelength-scale summary and gate.

Description: Each spectrum records its wavelength zero-point offset in its
pre-processing checkpoint. The run-level summary turns those into the RMS of
the offsets before and after correction and the correlation of the offset with
zero-order saturation. The ``wavelength_scale`` gate fails when the RMS after
correction is over one pixel of dispersion (11 A), is not checked when fewer
than three spectra had an offset, and passes otherwise. These tests build
checkpoints from known offsets, so every expected number is worked out by hand.
"""

import math
from types import SimpleNamespace

import pytest

from astrometricslib.models.gate_result import GateStatus
from astrometricslib.models.spectroscopy_quality import StageQualityCheckpoint
from astrometricslib.models.target import Target
from astrometricslib.models.wavelength_scale import WavelengthScaleSummary, WavelengthZeroPointRecord
from astrometricslib.pipelines.pipeline_base import PipelineRequest, Result
from astrometricslib.pipelines.shared.quality.saturation import DEFAULT_SATURATION_FLAG_THRESHOLD
from astrometricslib.pipelines.shared.star_recording import StarIdentificationBreakdown
from astrometricslib.pipelines.spectroscopy import batch, runner
from astrometricslib.pipelines.spectroscopy.post_processing import run_gates as rg
from astrometricslib.pipelines.spectroscopy.post_processing.wavelength_scale import (
    MINIMUM_SPECTRA_FOR_WAVELENGTH_SCALE,
    WAVELENGTH_SCALE_GATE_NAME,
    WAVELENGTH_SCALE_LIMIT_ANGSTROM,
    summarize_wavelength_scale,
    wavelength_scale_gate,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.assess_input_quality import (
    assess_input_quality,
    input_quality_checkpoint,
)
from astrometricslib.pipelines.spectroscopy.test.post_processing.test_run_gates import make_star
from astrometricslib.pipelines.spectroscopy.test.test_spectroscopy_batch_tasks import _make_session
from astrometricslib.utilities import parallel_batch


def checkpoint_for(
    offset: float | None,
    is_applied: bool = True,
    uncertainty: float = 1.0,
    saturation: float | None = 0.0,
    line_count: int = 3,
) -> StageQualityCheckpoint:
    """Build the pre-processing checkpoint of a spectrum with a known offset.

    Parameters
    ----------
    offset : `float` or `None`
        The spectrum's offset in Angstroms, or `None` when no line was found.
    is_applied : `bool`, optional
        Whether the offset was removed from the wavelengths.
    uncertainty : `float`, optional
        The offset's error in Angstroms.
    saturation : `float`, optional
        The zero-order saturated fraction, or `None` when not measured.
    line_count : `int`, optional
        The number of lines behind the offset.

    Returns
    -------
    checkpoint : `StageQualityCheckpoint`
        The checkpoint, built the way the pipeline builds it.
    """
    record = WavelengthZeroPointRecord(
        offset_angstrom=offset,
        uncertainty_angstrom=None if offset is None else uncertainty,
        line_count=0 if offset is None else line_count,
        is_applied=is_applied and offset is not None,
    )
    return input_quality_checkpoint(
        assess_input_quality(
            resolution_element_angstrom=40.0,
            is_resolution_measured=True,
            zero_order_saturated_pixel_fraction=saturation,
            valid_fraction=1.0,
            signal_to_noise=12.0,
        ),
        record,
    )


def rows_for(*offsets: float, is_applied: bool = True, uncertainty: float = 1.0) -> list[list]:
    """Build one row of checkpoints for each offset.

    Parameters
    ----------
    *offsets : `float`
        The offset of each spectrum, in Angstroms.
    is_applied : `bool`, optional
        Whether each offset was removed.
    uncertainty : `float`, optional
        The error of each offset.

    Returns
    -------
    rows : `list` [`list`]
        One list of checkpoints per spectrum.
    """
    return [[checkpoint_for(offset, is_applied, uncertainty)] for offset in offsets]


def summary_with_rms_after(rms_after: float, spectrum_count: int = 5) -> WavelengthScaleSummary:
    """Build a summary with a chosen RMS after correction.

    Returns
    -------
    summary : `WavelengthScaleSummary`
        A summary of ``spectrum_count`` spectra.
    """
    return WavelengthScaleSummary(
        spectrum_count=spectrum_count,
        applied_count=spectrum_count,
        rms_before_correction_angstrom=20.0,
        rms_after_correction_angstrom=rms_after,
    )


# ----------------------------------------------------------- the summary


def test_the_rms_before_correction_is_the_root_mean_square_of_the_offsets() -> None:
    """Offsets 30, -30, 10 and 0 (not applied) have an RMS of sqrt(475)."""
    summary = summarize_wavelength_scale(rows_for(30.0, -30.0, 10.0, 0.0, is_applied=False))

    assert summary is not None
    assert summary.spectrum_count == 4
    assert summary.rms_before_correction_angstrom == pytest.approx(math.sqrt((900 + 900 + 100 + 0) / 4))


def test_the_rms_after_correction_is_the_uncertainty_for_applied_offsets() -> None:
    """An applied offset leaves its error; an unapplied one stays whole."""
    rows = [
        [checkpoint_for(30.0, is_applied=True, uncertainty=2.0)],
        [checkpoint_for(-30.0, is_applied=True, uncertainty=2.0)],
        [checkpoint_for(8.0, is_applied=False)],
    ]

    summary = summarize_wavelength_scale(rows)

    assert summary is not None
    assert summary.applied_count == 2
    assert summary.rms_after_correction_angstrom == pytest.approx(math.sqrt((4 + 4 + 64) / 3))
    assert summary.rms_before_correction_angstrom == pytest.approx(math.sqrt((900 + 900 + 64) / 3))


def test_correction_lowers_the_scatter_when_it_works() -> None:
    """Large applied offsets give a large RMS before and a small one after."""
    summary = summarize_wavelength_scale(rows_for(25.0, -25.0, 30.0, -20.0, uncertainty=1.5))

    assert summary is not None
    assert summary.rms_before_correction_angstrom > 20.0
    assert summary.rms_after_correction_angstrom == pytest.approx(1.5)


def test_spectra_without_an_offset_are_left_out() -> None:
    """A spectrum with no line has no offset and does not count."""
    rows = [[checkpoint_for(None)], *rows_for(10.0, 10.0)]

    summary = summarize_wavelength_scale(rows)

    assert summary is not None
    assert summary.spectrum_count == 2


def test_there_is_no_summary_when_no_spectrum_has_an_offset() -> None:
    """With no offset anywhere the summary is `None`."""
    assert summarize_wavelength_scale([[checkpoint_for(None)], [checkpoint_for(None)]]) is None
    assert summarize_wavelength_scale([]) is None


def test_a_spectrum_without_a_pre_processing_checkpoint_is_skipped() -> None:
    """Old rows that lack the checkpoint do not break the summary."""
    old_row = [StageQualityCheckpoint(stage="processing")]

    summary = summarize_wavelength_scale([old_row, *rows_for(4.0)])

    assert summary is not None
    assert summary.spectrum_count == 1


def test_rows_as_plain_dictionaries_give_the_same_summary() -> None:
    """Rows as dictionaries (as batch workers send them) give the same."""
    rows = rows_for(12.0, -9.0, 3.0)
    as_dictionaries = [[checkpoint.model_dump() for checkpoint in row] for row in rows]

    assert summarize_wavelength_scale(as_dictionaries) == summarize_wavelength_scale(rows)


def test_the_offset_correlates_with_zero_order_saturation() -> None:
    """Offsets that grow with saturation give a correlation near 1."""
    saturations = [0.0, 0.01, 0.02, 0.05, 0.10]
    rows = [[checkpoint_for(100.0 * saturation + 1.0, saturation=saturation)] for saturation in saturations]

    summary = summarize_wavelength_scale(rows)

    assert summary is not None
    assert summary.correlation_spectrum_count == 5
    assert summary.offset_saturation_correlation == pytest.approx(1.0)


def test_the_correlation_uses_the_size_of_the_offset_not_its_sign() -> None:
    """Saturation can pull either way, so -50 and +50 both count as large."""
    rows = [
        [checkpoint_for(offset, saturation=saturation)]
        for offset, saturation in ((50.0, 0.1), (-50.0, 0.1), (1.0, 0.0), (-1.0, 0.0))
    ]

    summary = summarize_wavelength_scale(rows)

    assert summary is not None
    assert summary.offset_saturation_correlation == pytest.approx(1.0)


def test_the_correlation_is_none_when_it_is_undefined() -> None:
    """Fewer than three pairs, no saturation numbers, or no spread: `None`."""
    two_pairs = summarize_wavelength_scale(rows_for(5.0, 6.0))
    unmeasured = summarize_wavelength_scale([
        [checkpoint_for(offset, saturation=None)] for offset in (1, 2, 3)
    ])
    constant = summarize_wavelength_scale([
        [checkpoint_for(offset, saturation=0.0)] for offset in (1.0, 2.0, 3.0)
    ])

    assert two_pairs is not None
    assert two_pairs.offset_saturation_correlation is None
    assert unmeasured is not None
    assert unmeasured.offset_saturation_correlation is None
    assert unmeasured.correlation_spectrum_count == 0
    assert constant is not None
    assert constant.offset_saturation_correlation is None


def test_a_saturated_spectrum_flag_level_is_the_run_flag_level() -> None:
    """The correlation reads the saturated fraction the checkpoint holds."""
    checkpoint = checkpoint_for(5.0, saturation=DEFAULT_SATURATION_FLAG_THRESHOLD)
    saturation = next(entry for entry in checkpoint.metrics if entry.name == "zero_order_saturated_fraction")

    assert saturation.value == pytest.approx(DEFAULT_SATURATION_FLAG_THRESHOLD)


# -------------------------------------------------------------- the gate


def test_the_wavelength_scale_gate_goes_red_when_the_rms_after_correction_is_over_a_pixel() -> None:
    """An RMS of 15 A after correction is over the 11 A limit and fails."""
    gate = wavelength_scale_gate(summary_with_rms_after(15.0))

    assert gate.name == WAVELENGTH_SCALE_GATE_NAME
    assert gate.status is GateStatus.FAILED
    assert gate.measured_value == pytest.approx(15.0)
    assert gate.limit == pytest.approx(WAVELENGTH_SCALE_LIMIT_ANGSTROM)
    assert "designed" in (gate.limit_source or "")


def test_the_wavelength_scale_gate_is_not_checked_with_fewer_than_three_spectra() -> None:
    """No summary, or two spectra, leaves the gate not checked."""
    without_summary = wavelength_scale_gate(None)
    two_spectra = wavelength_scale_gate(summary_with_rms_after(50.0, spectrum_count=2))

    assert without_summary.name == WAVELENGTH_SCALE_GATE_NAME
    assert without_summary.status is GateStatus.NOT_CHECKED
    assert two_spectra.status is GateStatus.NOT_CHECKED
    assert MINIMUM_SPECTRA_FOR_WAVELENGTH_SCALE == 3


def test_the_wavelength_scale_gate_passes_at_and_below_the_limit() -> None:
    """Exactly 11 A is not over the limit; 3 spectra are enough."""
    at_limit = wavelength_scale_gate(summary_with_rms_after(WAVELENGTH_SCALE_LIMIT_ANGSTROM, 3))
    below = wavelength_scale_gate(summary_with_rms_after(2.0))

    assert at_limit.status is GateStatus.PASSED
    assert below.status is GateStatus.PASSED
    assert "3 spectra" in at_limit.detail


def test_the_designed_limit_is_one_pixel_of_dispersion() -> None:
    """The limit is 11 A, the dispersion of one pixel."""
    assert WAVELENGTH_SCALE_LIMIT_ANGSTROM == pytest.approx(11.0)


def test_uncorrected_spectra_with_large_offsets_fail_the_gate_end_to_end() -> None:
    """Big offsets that were only measured fail the gate."""
    summary = summarize_wavelength_scale(rows_for(30.0, -25.0, 40.0, is_applied=False))

    assert summary is not None
    assert wavelength_scale_gate(summary).status is GateStatus.FAILED


def test_corrected_spectra_with_large_offsets_pass_the_gate_end_to_end() -> None:
    """The same offsets, once removed, leave a small RMS and pass."""
    summary = summarize_wavelength_scale(rows_for(30.0, -25.0, 40.0, is_applied=True, uncertainty=1.5))

    assert summary is not None
    assert wavelength_scale_gate(summary).status is GateStatus.PASSED


def test_the_run_gates_carry_the_wavelength_scale_gate() -> None:
    """The run's gates list has the gate, not checked without a summary."""
    facts = rg.spectrum_facts([])
    facts["spectra"] = 1

    without_summary = {gate.name: gate for gate in rg.spectroscopy_run_gates(facts, [0.0], [])}
    failing = {
        gate.name: gate for gate in rg.spectroscopy_run_gates(facts, [0.0], [], summary_with_rms_after(30.0))
    }

    assert without_summary[WAVELENGTH_SCALE_GATE_NAME].status is GateStatus.NOT_CHECKED
    assert failing[WAVELENGTH_SCALE_GATE_NAME].status is GateStatus.FAILED


def test_the_batch_merge_records_the_summary_and_the_gate() -> None:
    """Checkpoint rows from every frame give one summary and a failing gate."""
    target = Target(id="WavelengthScaleBatchTarget")

    def frame_result(offsets: list[float]) -> dict:
        """Build one frame's worker result with a spectrum per offset.

        Returns
        -------
        result : `dict`
            The worker's result.
        """
        return {
            "status": "success",
            "stars_processed": len(offsets),
            "zero_order_saturation_fractions": [0.0],
            "spectrum_facts": {"spectra": len(offsets)},
            "stage_quality": [
                [checkpoint.model_dump() for checkpoint in row]
                for row in rows_for(*offsets, is_applied=False)
            ],
        }

    results = {"a.fits": frame_result([30.0, -28.0]), "b.fits": frame_result([35.0])}
    summary = parallel_batch.BatchRunSummary(succeeded=list(results), failed=[], results=results)
    session = _make_session("Target:2026-01-01:0.0:0", list(results))

    batch._attach_spectroscopy_quality_summary(target, summary, [(session, SimpleNamespace())])

    recorded = target.quality.spectroscopy
    scale = recorded.spectroscopy_metrics.wavelength_scale_summary
    assert scale is not None
    assert scale.spectrum_count == 3
    assert scale.rms_after_correction_angstrom == pytest.approx(math.sqrt((900 + 784 + 1225) / 3))
    assert recorded.gate(WAVELENGTH_SCALE_GATE_NAME).status is GateStatus.FAILED
    assert recorded.flagged is True


def test_the_real_validate_output_records_the_summary_and_the_gate() -> None:
    """The runner puts the summary and the gate on the quality summary."""
    stars = []
    for offset in (30.0, -25.0, 40.0):
        star = make_star()
        star.spectroscopy.stage_quality.append(checkpoint_for(offset, is_applied=False))
        stars.append(star)
    spectroscopy = SimpleNamespace(
        last_run_zero_order_saturation_fractions=[0.0],
        config=SimpleNamespace(camera=SimpleNamespace(name="ZWO ASI 533MM Pro")),
    )
    result = Result(
        stellar_objects=stars,
        payload={
            "star_id_breakdown": StarIdentificationBreakdown(
                catalog_matched=3, position_only=0, unresolved=0
            ),
            "spectroscopy": spectroscopy,
            "flagged_spectral_classifications": [],
        },
    )
    request = PipelineRequest(target=Target(id="Test Target"), catalog_access=None)

    summary = runner.SpectroscopyPipelineAdapter().validate_output(request, result)

    scale = summary.spectroscopy_metrics.wavelength_scale_summary
    assert scale is not None
    assert scale.spectrum_count == 3
    assert scale.applied_count == 0
    assert summary.gate(WAVELENGTH_SCALE_GATE_NAME).status is GateStatus.FAILED
    assert summary.flagged is True
