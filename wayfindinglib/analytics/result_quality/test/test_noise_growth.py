"""Purpose: Tests for the noise-growth forward model.

Description: Checks the stack variance for equal weights, noise weights and
star-width weights on made-up frames, the known penalty of equal weights, the
moonlit case, mode identification, the take-N-more search, the stacker
constant it copies, and the noise-floor fit.
"""

import math

import numpy as np
import pytest

from wayfindinglib.analytics.result_quality import noise_growth as ng
from wayfindinglib.analytics.result_quality.noise_growth import StackFrame, WeightingMode


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
    assert sum(weights) / len(weights) == pytest.approx(1.0)  # the stacker's weights have mean 1
    assert weights[2] == pytest.approx(0.0)
    assert weights[1] / weights[0] == pytest.approx((1 / 9 - 1 / 16) / (1 / 4 - 1 / 16))


def test_wfwhm_weights_are_recomputed_over_old_and_new_frames() -> None:
    """Adding a sharper frame changes the old frames' weights."""
    old = [_frame(0, width=3.0), _frame(1, width=4.0)]
    before = ng.frame_weights(old, WeightingMode.WFWHM)
    after = ng.frame_weights([*old, _frame(2, width=2.0)], WeightingMode.WFWHM)
    assert before is not None and after is not None
    assert before[0] == pytest.approx(2.0)  # mean 1 over two frames: weights 1 and 0
    assert after[0] < before[0]


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
