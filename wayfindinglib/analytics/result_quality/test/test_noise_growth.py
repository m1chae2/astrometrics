"""Purpose: Tests for the noise-growth forward model.

Description: Checks the stack variance for equal weights, noise weights and
star-width weights on made-up frames, the known penalty of equal weights, the
moonlit case, mode identification, the take-N-more search, the stacker
constant it copies, and the noise-floor fit.
"""

import importlib
import math

import numpy as np
import pytest

from wayfindinglib.analytics.result_quality import noise_growth as ng
from wayfindinglib.analytics.result_quality.noise_growth import StackFrame, WeightingMode
from wayfindinglib.models.quality.statements import Confidence


def _frame(
    index: int = 0, exposure: float = 60.0, sky: float = 1.0, read: float = 0.0, width: float | None = None
) -> StackFrame:
    """Build one stack frame.

    Parameters
    ----------
    index : `int`
        Frame number.
    exposure, sky, read : `float`
        Exposure, sky rate and read noise.
    width : `float` or `None`
        Star width.

    Returns
    -------
    frame : `StackFrame`
        The frame.
    """
    return StackFrame(
        frame_id=f"f{index}",
        exposure_seconds=exposure,
        sky_rate_e_per_s_per_px=sky,
        read_noise_e=read,
        star_width=width,
    )


def test_equal_frames_give_variance_over_n() -> None:
    """N identical frames have 1/N of one frame's variance in every mode."""
    frames = [_frame(i, width=3.0) for i in range(8)]
    one = frames[0].rate_variance
    for mode in (WeightingMode.EQUAL, WeightingMode.NOISE, WeightingMode.WFWHM):
        assert ng.forecast_stack_noise(frames, mode).variance == pytest.approx(one / 8)


def test_equal_weights_penalty_matches_the_stacker_formula() -> None:
    """With no read noise.

    Equal weights cost sqrt(mean(1/t) mean(t)) in noise over noise weights.
    """
    times = [30.0, 60.0, 120.0, 120.0]
    frames = [_frame(i, exposure=t) for i, t in enumerate(times)]
    equal = ng.forecast_stack_noise(frames, WeightingMode.EQUAL).noise
    weighted = ng.forecast_stack_noise(frames, WeightingMode.NOISE).noise
    expected = math.sqrt(np.mean(1 / np.array(times)) * np.mean(times))
    assert equal / weighted == pytest.approx(expected)


def test_moonlit_frames_cost_more_with_equal_weights_than_noise_weights() -> None:
    """Noise weights down-weight moonlit frames.

    Equal weights give a smaller improvement.
    """
    dark = [_frame(i, sky=0.5) for i in range(10)]
    moonlit = [_frame(10 + i, sky=3.0) for i in range(10)]
    equal = ng.forecast_stack_noise(dark + moonlit, WeightingMode.EQUAL).noise
    noise = ng.forecast_stack_noise(dark + moonlit, WeightingMode.NOISE).noise
    dark_only = ng.forecast_stack_noise(dark, WeightingMode.EQUAL).noise
    assert noise < equal
    gain_equal = 1 - equal / dark_only
    gain_noise = 1 - noise / dark_only
    assert gain_noise > gain_equal


def test_wfwhm_weights_follow_the_siril_formula_and_zero_the_blurriest_frame() -> None:
    """The weights are (1/f^2 - 1/fmax^2) / (1/fmin^2 - 1/fmax^2)."""
    frames = [_frame(0, width=2.0), _frame(1, width=3.0), _frame(2, width=4.0)]
    weights = ng.frame_weights(frames, WeightingMode.WFWHM)
    assert weights is not None
    assert weights[0] == pytest.approx(1.0)
    assert weights[2] == pytest.approx(0.0)
    assert weights[1] == pytest.approx((1 / 9 - 1 / 16) / (1 / 4 - 1 / 16))


def test_wfwhm_weights_are_recomputed_over_old_and_new_frames() -> None:
    """Adding a sharper frame changes the old frames' weights."""
    old = [_frame(0, width=3.0), _frame(1, width=4.0)]
    before = ng.frame_weights(old, WeightingMode.WFWHM)
    after = ng.frame_weights([*old, _frame(2, width=2.0)], WeightingMode.WFWHM)
    assert before is not None and after is not None
    assert before[0] == pytest.approx(1.0)
    assert after[0] < 1.0


def test_wfwhm_without_widths_and_unknown_mode_give_reasons() -> None:
    """A missing width or an unrecorded mode gives no forecast and a reason."""
    assert ng.forecast_stack_noise([_frame(0)], WeightingMode.WFWHM).reason
    unknown = ng.forecast_stack_noise([_frame(0)], WeightingMode.UNKNOWN)
    assert unknown.noise is None
    assert "not recorded" in (unknown.reason or "")
    assert ng.forecast_stack_noise([], WeightingMode.EQUAL).noise is None


def test_predict_weighting_mode_follows_the_stacker_rule() -> None:
    """Equal exposures stay equal.

    Mixed exposures switch to noise; a configured mode wins.
    """
    assert ng.predict_weighting_mode(None, [60.0] * 10) == WeightingMode.EQUAL
    assert ng.predict_weighting_mode(None, [30.0, 60.0, 120.0]) == WeightingMode.NOISE
    assert ng.predict_weighting_mode("wfwhm", [60.0] * 3) == WeightingMode.WFWHM
    assert ng.predict_weighting_mode("nbstars", [60.0] * 3) == WeightingMode.UNKNOWN


def test_copied_stacker_threshold_matches_astrometricslib() -> None:
    """The copy of MINIMUM_PENALTY_TO_WEIGHT equals the stacker's value."""
    module = importlib.import_module("astrometricslib.pipelines.stacking.pre_processing.exposure_weighting")
    assert module.MINIMUM_PENALTY_TO_WEIGHT == ng.MINIMUM_PENALTY_TO_WEIGHT


def test_identify_weighting_mode_from_recorded_weights() -> None:
    """Recorded weights are matched to a mode; missing rows give 'unknown'."""
    frames = [_frame(0, width=2.0), _frame(1, width=3.0), _frame(2, width=4.0), _frame(3, width=2.5)]
    recorded = ng.frame_weights(frames, WeightingMode.WFWHM)
    assert ng.identify_weighting_mode(frames, recorded).mode == WeightingMode.WFWHM
    assert ng.identify_weighting_mode(frames, [1.0] * 4).mode == WeightingMode.EQUAL
    unknown = ng.identify_weighting_mode(frames, [1.0, None, 1.0, 1.0])
    assert unknown.mode == WeightingMode.UNKNOWN


def test_take_more_for_identical_frames_is_the_current_count() -> None:
    """Doubling identical frames cuts the noise to 0.707.

    So N equals the number taken.
    """
    existing = [_frame(i) for i in range(20)]
    result = ng.frames_to_cut_noise(existing, existing, WeightingMode.EQUAL)
    assert result.frames_needed == 20
    assert result.predicted_noise_ratio == pytest.approx(1 / math.sqrt(2))
    assert result.confidence == Confidence.DESIGN_ESTIMATE
    assert result.added_exposure_seconds == pytest.approx(20 * 60.0)


def test_take_more_counts_read_noise_limited_short_frames_for_less() -> None:
    """Short read-noise-limited frames need more frames than long ones."""
    existing = [_frame(i, exposure=60.0, sky=0.5, read=3.0) for i in range(20)]
    long_frames = ng.frames_to_cut_noise(existing, existing, WeightingMode.NOISE, new_exposure_seconds=120.0)
    short_frames = ng.frames_to_cut_noise(existing, existing, WeightingMode.NOISE, new_exposure_seconds=15.0)
    assert long_frames.frames_needed < 20 < short_frames.frames_needed
    assert short_frames.added_exposure_seconds > long_frames.added_exposure_seconds


def test_take_more_reports_missing_inputs() -> None:
    """No existing frames.

    No template or an unknown mode gives no answer and a reason.
    """
    frame = _frame(0)
    assert ng.frames_to_cut_noise([], [frame], WeightingMode.EQUAL).reason
    assert ng.frames_to_cut_noise([frame], [], WeightingMode.EQUAL).reason
    assert ng.frames_to_cut_noise([frame], [frame], WeightingMode.UNKNOWN).frames_needed is None


def test_aperture_noise_error_is_117_over_root_n() -> None:
    """The error is about 12 percent for 100 apertures."""
    assert ng.aperture_noise_relative_error(100) == pytest.approx(0.117)
    with pytest.raises(ValueError, match="2 apertures"):
        ng.aperture_noise_relative_error(1)


def _steps(
    floor_variance: float, alpha: float = 1.0, count: int = 5, apertures: int = 400, paired: bool = False
) -> list[ng.NoiseStep]:
    """Build stack steps that follow a known floor exactly.

    Parameters
    ----------
    floor_variance, alpha : `float`
        The true floor and scale.
    count : `int`
        Number of steps.
    apertures : `int`
        Apertures per step.
    paired : `bool`
        Whether errors are paired.

    Returns
    -------
    steps : `list` [`NoiseStep`]
        The steps.
    """
    return [
        ng.NoiseStep(
            label=f"s{i}",
            expected_variance=4.0 / (i + 1),
            measured_noise=math.sqrt(alpha * 4.0 / (i + 1) + floor_variance),
            apertures=apertures,
            paired=paired,
            relative_error=0.02 if paired else None,
        )
        for i in range(count)
    ]


def test_floor_is_found_when_present() -> None:
    """A floor of 1.0 in variance is detected and recovered."""
    fit = ng.fit_noise_floor(_steps(1.0))
    assert fit.floor_detected
    assert fit.floor_variance == pytest.approx(1.0, rel=1e-6)
    assert fit.alpha == pytest.approx(1.0, rel=1e-6)
    assert fit.floor_noise == pytest.approx(1.0, rel=1e-6)
    assert fit.error_kind == "unpaired"


def test_no_floor_when_noise_follows_the_model() -> None:
    """With no floor the constant is not above 0 by two standard errors."""
    fit = ng.fit_noise_floor(_steps(0.0))
    assert not fit.floor_detected
    assert fit.floor_noise is None


def test_floor_needs_enough_steps_and_says_so() -> None:
    """One step.

    Or two with a free read-noise term, gives 'not enough stack steps'.
    """
    assert "Not enough stack steps" in ng.fit_noise_floor(_steps(1.0, count=1)).reason
    assert (
        "Not enough stack steps" in ng.fit_noise_floor(_steps(1.0, count=2), read_noise_term_free=True).reason
    )


def test_free_read_noise_term_needs_the_read_variance() -> None:
    """The three-term fit asks for the read-noise part of each step."""
    assert "read-noise" in ng.fit_noise_floor(_steps(1.0), read_noise_term_free=True).reason


def test_paired_errors_are_reported_as_paired() -> None:
    """The fit says which kind of error it used."""
    assert ng.fit_noise_floor(_steps(1.0, paired=True)).error_kind == "paired"
    mixed = _steps(1.0, count=3) + _steps(1.0, count=2, paired=True)
    assert ng.fit_noise_floor(mixed).error_kind == "mixed"


def test_stop_signal_follows_the_floor() -> None:
    """A large floor stops the gain.

    No floor does not; no fit gives no signal.
    """
    floor_fit = ng.fit_noise_floor(_steps(1.0))
    assert ng.stop_signal(floor_fit, current_expected_variance=0.5).stopped is True
    no_floor = ng.fit_noise_floor(_steps(0.0))
    assert ng.stop_signal(no_floor, current_expected_variance=0.5).stopped is False
    too_few = ng.fit_noise_floor(_steps(1.0, count=1))
    assert ng.stop_signal(too_few, current_expected_variance=0.5).stopped is None
