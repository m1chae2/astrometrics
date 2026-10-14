"""Purpose: Unit tests for the stacking input-quality judgement.

Description: The builder counts the frames the pre-checks set aside, words a
background split, copies the flat assessment, and turns every problem into
one flag reason. A clean input has no reasons.
"""

import pytest

from astrometricslib.models.quality_summary import ExcludedFrame
from astrometricslib.pipelines.stacking.pre_processing.assess_input_quality import (
    BACKGROUND_EXCLUSION_REASON,
    GAIN_EXCLUSION_REASON,
    assess_input_quality,
    describe_background_splits,
)

SPLIT = {
    "exposure_seconds": 60.0,
    "low_group_count": 8,
    "low_group_median": 1000.0,
    "high_group_count": 2,
    "high_group_median": 4000.0,
    "gap_ratio": 4.0,
}


def test_a_clean_input_has_no_flag_reasons() -> None:
    """No exclusions, split or flat issue leaves the input unflagged."""
    quality = assess_input_quality(10, 10, [], None, {})
    assert not quality.is_flagged
    assert quality.flag_reasons == []
    assert quality.flat_frame_count is None


def test_excluded_frames_are_counted_by_reason() -> None:
    """Gain and background exclusions are counted separately."""
    excluded = [
        ExcludedFrame(path="a.fits", reason=GAIN_EXCLUSION_REASON),
        ExcludedFrame(path="b.fits", reason=BACKGROUND_EXCLUSION_REASON),
        ExcludedFrame(path="c.fits", reason=BACKGROUND_EXCLUSION_REASON),
    ]
    quality = assess_input_quality(10, 7, excluded, None, {})
    assert quality.frames_excluded_for_gain == 1
    assert quality.frames_excluded_for_background == 2
    assert quality.frames_accepted == 7


def test_a_background_split_is_described_and_flagged() -> None:
    """A split is worded with its exposure length and gap."""
    quality = assess_input_quality(10, 10, [], SPLIT, {})
    assert quality.background_split_detected
    assert quality.flag_reasons == [f"background split: {quality.background_split_detail}"]
    assert "60 s frames: 8 frame(s) at background~1000" in quality.background_split_detail


def test_a_split_without_an_exposure_length_has_no_prefix() -> None:
    """The single-dictionary shape names no exposure length."""
    split = {key: value for key, value in SPLIT.items() if key != "exposure_seconds"}
    assert describe_background_splits(split).startswith("8 frame(s)")
    assert describe_background_splits(None) is None
    assert describe_background_splits([]) is None


def test_flat_issues_and_mismatches_become_reasons() -> None:
    """The flat assessment is copied and each issue is a reason."""
    diagnostics = {
        "flat_calibration": {
            "frame_count": 1,
            "noise_fraction": 0.045,
            "smoothing_sigma_pixels": 2.5,
            "issues": ["master flat noise is 4.49% from 1 frame(s)"],
        },
        "calibration_mismatch_flags": ["dark gain differs"],
    }
    quality = assess_input_quality(10, 10, [], None, diagnostics)
    assert quality.flat_smoothing_sigma_px == pytest.approx(2.5)
    assert "1 calibration metadata mismatch(es): dark gain differs" in quality.flag_reasons
    assert "flat calibration: master flat noise is 4.49% from 1 frame(s)" in quality.flag_reasons


def test_a_short_master_becomes_a_reason_and_a_failed_gate() -> None:
    """A master from too few frames flags the input and fails its gate."""
    from astrometricslib.models.gate_result import GateStatus
    from astrometricslib.pipelines.stacking.pre_processing.assess_input_quality import (
        CALIBRATION_FRAME_COUNT_GATE_NAME,
        calibration_gates,
    )

    short_master = "flat master built from 1 frame(s), fewer than the minimum of 3: the master flat is noisy"
    diagnostics = {
        "calibration_applied": {"flat": True},
        "calibration_blocking_flags": [short_master],
    }

    quality = assess_input_quality(10, 10, [], None, diagnostics)
    gates = {gate.name: gate for gate in calibration_gates(diagnostics)}

    assert quality.is_flagged
    assert f"calibration frame count too low: {short_master}" in quality.flag_reasons
    assert gates[CALIBRATION_FRAME_COUNT_GATE_NAME].status is GateStatus.FAILED
    assert gates[CALIBRATION_FRAME_COUNT_GATE_NAME].detail in quality.flag_reasons


def test_the_frame_count_gate_passes_with_enough_frames_and_is_unchecked_without_masters() -> None:
    """No flag passes the gate; with no calibration it is not checked."""
    from astrometricslib.models.gate_result import GateStatus
    from astrometricslib.pipelines.stacking.pre_processing.assess_input_quality import (
        CALIBRATION_FRAME_COUNT_GATE_NAME,
        calibration_gates,
    )

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
    from astrometricslib.models.gate_result import GateStatus
    from astrometricslib.pipelines.stacking.pre_processing.assess_input_quality import (
        CALIBRATION_METADATA_GATE_NAME,
        calibration_gates,
    )

    diagnostics = {
        "calibration_applied": {"dark": False, "flat": False, "bias": False},
        "calibration_match_blocking_flags": [BINNING_BLOCK],
    }

    quality = assess_input_quality(10, 10, [], None, diagnostics)
    gates = {gate.name: gate for gate in calibration_gates(diagnostics)}

    assert gates[CALIBRATION_METADATA_GATE_NAME].status is GateStatus.FAILED
    assert BINNING_BLOCK in gates[CALIBRATION_METADATA_GATE_NAME].detail
    assert gates[CALIBRATION_METADATA_GATE_NAME].detail in quality.flag_reasons
    assert quality.is_flagged


def test_a_far_temperature_dark_fails_the_metadata_gate_with_its_sentence() -> None:
    """A dark applied from a far temperature fails the gate and is named."""
    from astrometricslib.models.gate_result import GateStatus
    from astrometricslib.pipelines.stacking.pre_processing.assess_input_quality import (
        CALIBRATION_METADATA_GATE_NAME,
        calibration_gates,
    )

    diagnostics = {"calibration_applied": {"dark": True}, "calibration_mismatch_flags": [TEMPERATURE_FLAG]}

    quality = assess_input_quality(10, 10, [], None, diagnostics)
    gates = {gate.name: gate for gate in calibration_gates(diagnostics)}

    assert gates[CALIBRATION_METADATA_GATE_NAME].status is GateStatus.FAILED
    assert TEMPERATURE_FLAG in gates[CALIBRATION_METADATA_GATE_NAME].detail
    assert quality.calibration_mismatch_flags == [TEMPERATURE_FLAG]
    assert gates[CALIBRATION_METADATA_GATE_NAME].detail in quality.flag_reasons


def test_the_metadata_gate_names_both_kinds_of_flag_together() -> None:
    """A soft and a blocking flag both appear in the gate and the reasons."""
    from astrometricslib.pipelines.stacking.pre_processing.assess_input_quality import (
        CALIBRATION_METADATA_GATE_NAME,
        calibration_gates,
    )

    diagnostics = {
        "calibration_applied": {"flat": True},
        "calibration_mismatch_flags": [TEMPERATURE_FLAG],
        "calibration_match_blocking_flags": [BINNING_BLOCK],
    }

    quality = assess_input_quality(10, 10, [], None, diagnostics)
    gates = {gate.name: gate for gate in calibration_gates(diagnostics)}
    detail = gates[CALIBRATION_METADATA_GATE_NAME].detail

    assert TEMPERATURE_FLAG in detail
    assert BINNING_BLOCK in detail
    assert len(quality.flag_reasons) == 2


def test_the_metadata_gate_passes_when_matched_and_is_unchecked_without_calibration() -> None:
    """No flag passes the gate; with nothing applied it is not checked."""
    from astrometricslib.models.gate_result import GateStatus
    from astrometricslib.pipelines.stacking.pre_processing.assess_input_quality import (
        CALIBRATION_METADATA_GATE_NAME,
        calibration_gates,
    )

    matched = {gate.name: gate for gate in calibration_gates({"calibration_applied": {"dark": True}})}
    nothing = {gate.name: gate for gate in calibration_gates({"calibration_applied": {"dark": False}})}

    assert matched[CALIBRATION_METADATA_GATE_NAME].status is GateStatus.PASSED
    assert nothing[CALIBRATION_METADATA_GATE_NAME].status is GateStatus.NOT_CHECKED
