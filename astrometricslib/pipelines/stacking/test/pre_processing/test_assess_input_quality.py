"""Purpose: Unit tests for the stacking input-quality counts and gates.

Description: The input checks store how many frames each pre-check set aside,
describe a background split, and build the calibration gates. A problem in
the inputs is reported by a failed gate (or a notice row), never by a flag on
the summary.
"""

from astrometricslib.models.quality.gate_result import GateStatus
from astrometricslib.models.quality.quality_summary import ExcludedFrame, StackingPipelineQualityMetrics
from astrometricslib.pipelines.stacking.pre_processing.assess_input_quality import (
    BACKGROUND_EXCLUSION_REASON,
    CALIBRATION_FRAME_COUNT_GATE_NAME,
    CALIBRATION_METADATA_GATE_NAME,
    GAIN_EXCLUSION_REASON,
    calibration_gates,
    describe_background_splits,
    record_input_counts,
)
from astrometricslib.pipelines.stacking.pre_processing.background_homogeneity import (
    background_homogeneity_gate,
)
from astrometricslib.pipelines.stacking.pre_processing.stack_calibration_health import (
    CALIBRATION_UNIQUE_COUNTS_KEY,
)

SPLIT = {
    "exposure_seconds": 60.0,
    "low_group_count": 8,
    "low_group_median": 1000.0,
    "high_group_count": 2,
    "high_group_median": 4000.0,
    "gap_ratio": 4.0,
}


def make_metrics() -> StackingPipelineQualityMetrics:
    """Build metrics with nothing recorded.

    Returns
    -------
    metrics : `StackingPipelineQualityMetrics`
        Default metrics for a ten-frame image stack.
    """
    return StackingPipelineQualityMetrics(is_spectral=False, frames_submitted=10, frames_stacked=10)


def failed_details(diagnostics: dict) -> list[str]:
    """List the sentences of the failed calibration gates.

    Parameters
    ----------
    diagnostics : `dict`
        What the stacking run reported.

    Returns
    -------
    details : `list` [`str`]
        The detail of each failed gate.
    """
    return [gate.detail for gate in calibration_gates(diagnostics) if gate.status is GateStatus.FAILED]


def test_a_clean_input_fails_no_gate_and_sets_aside_no_frame() -> None:
    """No exclusion, split or flat issue leaves every count zero."""
    metrics = make_metrics()
    record_input_counts(metrics, 10, [], {})
    assert failed_details({}) == []
    assert metrics.frames_accepted == 10
    assert metrics.frames_quarantined == 0
    assert metrics.flat_frame_count is None


def test_excluded_frames_are_counted_by_reason() -> None:
    """Gain, background and quarantine exclusions are counted separately."""
    excluded = [
        ExcludedFrame(path="a.fits", reason=GAIN_EXCLUSION_REASON),
        ExcludedFrame(path="b.fits", reason=BACKGROUND_EXCLUSION_REASON),
        ExcludedFrame(path="c.fits", reason=BACKGROUND_EXCLUSION_REASON),
        ExcludedFrame(path="d.fits", reason="moved to _excluded: clouds"),
    ]
    metrics = make_metrics()
    record_input_counts(metrics, 6, excluded, {CALIBRATION_UNIQUE_COUNTS_KEY: {"dark": 12, "bias": 40}})
    assert metrics.frames_excluded_for_gain == 1
    assert metrics.frames_excluded_for_background == 2
    assert metrics.frames_quarantined == 1
    assert metrics.frames_accepted == 6
    assert metrics.dark_unique_frame_count == 12
    assert metrics.bias_unique_frame_count == 40


def test_a_background_split_is_described_and_fails_the_background_gate() -> None:
    """A split is worded with its exposure length and gap, in the gate."""
    gate = background_homogeneity_gate([], [SPLIT])
    assert gate.status is GateStatus.FAILED
    assert gate.detail == f"background split: {describe_background_splits([SPLIT])}"
    assert "60 s frames: 8 frame(s) at background~1000" in gate.detail


def test_a_split_without_an_exposure_length_has_no_prefix() -> None:
    """The single-dictionary shape names no exposure length."""
    split = {key: value for key, value in SPLIT.items() if key != "exposure_seconds"}
    assert describe_background_splits(split).startswith("8 frame(s)")
    assert describe_background_splits(None) is None
    assert describe_background_splits([]) is None


def test_flat_issues_and_mismatches_fail_their_gates() -> None:
    """A noisy flat and a gain mismatch each fail a gate."""
    diagnostics = {
        "flat_calibration": {
            "frame_count": 1,
            "noise_fraction": 0.045,
            "smoothing_sigma_pixels": 2.5,
            "issues": ["master flat noise is 4.49% from 1 frame(s)"],
        },
        "calibration_applied": {"dark": True},
        "calibration_mismatch_flags": ["dark gain differs"],
    }
    details = failed_details(diagnostics)
    assert "1 calibration metadata mismatch(es): dark gain differs" in details
    assert "flat calibration: master flat noise is 4.49% from 1 frame(s)" in details


def test_a_short_master_fails_the_frame_count_gate() -> None:
    """A master from too few frames fails its gate and names the master."""
    short_master = "flat master built from 1 frame(s), fewer than the minimum of 3: the master flat is noisy"
    diagnostics = {
        "calibration_applied": {"flat": True},
        "calibration_blocking_flags": [short_master],
    }

    gates = {gate.name: gate for gate in calibration_gates(diagnostics)}

    assert gates[CALIBRATION_FRAME_COUNT_GATE_NAME].status is GateStatus.FAILED
    assert (
        f"calibration frame count too low: {short_master}" in gates[CALIBRATION_FRAME_COUNT_GATE_NAME].detail
    )


def test_the_frame_count_gate_passes_with_enough_frames_and_is_unchecked_without_masters() -> None:
    """No flag passes the gate; with no calibration it is not checked."""
    applied = {gate.name: gate for gate in calibration_gates({"calibration_applied": {"dark": True}})}
    none_applied = {gate.name: gate for gate in calibration_gates({"calibration_applied": {"dark": False}})}

    assert applied[CALIBRATION_FRAME_COUNT_GATE_NAME].status is GateStatus.PASSED
    assert none_applied[CALIBRATION_FRAME_COUNT_GATE_NAME].status is GateStatus.NOT_CHECKED


BINNING_BLOCK = (
    "No dark was applied to the 12 light frame(s) starting with light_001.fits: they were taken at "
    "binning 2x2, but the dark frames in the library were taken at binning 1x1, and frames binned "
    "differently cannot calibrate each other."
)
TEMPERATURE_FLAG = (
    "No dark frame was within 3 C of the 12 light frame(s) starting with light_001.fits, which were "
    "taken at -10.0 C, so the nearest dark master, taken at 5.0 C (3 dark frame(s) from 5.0 to 5.0 C), "
    "was applied, 15.0 C away."
)


def test_a_refused_calibration_fails_the_metadata_gate_and_names_the_reason() -> None:
    """A binning mismatch applied no dark, yet the gate fails and says why."""
    diagnostics = {
        "calibration_applied": {"dark": False, "flat": False, "bias": False},
        "calibration_match_blocking_flags": [BINNING_BLOCK],
    }

    gates = {gate.name: gate for gate in calibration_gates(diagnostics)}

    assert gates[CALIBRATION_METADATA_GATE_NAME].status is GateStatus.FAILED
    assert BINNING_BLOCK in gates[CALIBRATION_METADATA_GATE_NAME].detail


def test_a_far_temperature_dark_fails_the_metadata_gate_with_its_sentence() -> None:
    """A dark applied from a far temperature fails the gate and is named."""
    diagnostics = {"calibration_applied": {"dark": True}, "calibration_mismatch_flags": [TEMPERATURE_FLAG]}

    gates = {gate.name: gate for gate in calibration_gates(diagnostics)}

    assert gates[CALIBRATION_METADATA_GATE_NAME].status is GateStatus.FAILED
    assert TEMPERATURE_FLAG in gates[CALIBRATION_METADATA_GATE_NAME].detail


def test_the_metadata_gate_names_both_kinds_of_flag_together() -> None:
    """A soft and a blocking flag both appear in the one gate."""
    diagnostics = {
        "calibration_applied": {"flat": True},
        "calibration_mismatch_flags": [TEMPERATURE_FLAG],
        "calibration_match_blocking_flags": [BINNING_BLOCK],
    }

    gates = {gate.name: gate for gate in calibration_gates(diagnostics)}
    detail = gates[CALIBRATION_METADATA_GATE_NAME].detail

    assert TEMPERATURE_FLAG in detail
    assert BINNING_BLOCK in detail


def test_the_metadata_gate_passes_when_matched_and_is_unchecked_without_calibration() -> None:
    """No flag passes the gate; with nothing applied it is not checked."""
    matched = {gate.name: gate for gate in calibration_gates({"calibration_applied": {"dark": True}})}
    nothing = {gate.name: gate for gate in calibration_gates({"calibration_applied": {"dark": False}})}

    assert matched[CALIBRATION_METADATA_GATE_NAME].status is GateStatus.PASSED
    assert nothing[CALIBRATION_METADATA_GATE_NAME].status is GateStatus.NOT_CHECKED
