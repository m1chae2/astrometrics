"""Purpose: Test `predict_stack_weights` against the stacker's own rules.

Description: The prediction must agree with `choose_stack_weight`,
`fold_exposure_lengths`, `wfwhm_weights` and `group_frame_weights`, because
those are what a real stack uses. These tests check each mode and that the
exposure-group weights equal what `group_frame_weights` gives directly.
"""

from unittest.mock import MagicMock

from astrometricslib.api.processing import ProcessingPipelines
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.pipelines.stacking.post_processing.stack_membership import (
    group_frame_weights,
    wfwhm_weights,
)
from astrometricslib.pipelines.stacking.processing.exposure_groups import fold_exposure_lengths
from astrometricslib.pipelines.stacking.stack_weight_prediction import predict_stack_weights


def test_equal_exposures_give_equal_weights() -> None:
    """Frames of one length are weighted equally and stay in one group."""
    prediction = predict_stack_weights([10.0] * 6)
    assert prediction.mode == "equal"
    assert prediction.weights == [1.0] * 6
    assert prediction.folded_exposure_lengths == [10.0] * 6
    assert prediction.reasons == []


def test_a_short_list_of_other_lengths_folds_and_the_stacker_weights_by_noise() -> None:
    """Two long frames fold into the 10 s group; noise weights follow."""
    exposures = [10.0] * 10 + [60.0] * 2
    variances = [2.0] * 10 + [8.0] * 2
    prediction = predict_stack_weights(exposures, rate_variance=variances)
    assert prediction.folded_exposure_lengths == fold_exposure_lengths(exposures)
    assert set(prediction.folded_exposure_lengths) == {10.0}
    assert prediction.mode == "noise"
    assert prediction.weights == [0.5] * 10 + [0.125] * 2


def test_noise_weights_are_unknown_without_variances() -> None:
    """Missing or zero variances give no weights and a reason."""
    exposures = [10.0] * 10 + [60.0] * 2
    assert predict_stack_weights(exposures).weights is None
    zero = predict_stack_weights(exposures, rate_variance=[0.0] * 12)
    assert zero.weights is None
    assert zero.reasons


def test_a_configured_mode_wins_and_star_width_weights_match_the_stacker() -> None:
    """A configured ``wfwhm`` uses the stacker's star-width weights."""
    widths = [2.0, 2.5, 3.0, 3.5, 4.0, 4.5]
    prediction = predict_stack_weights([10.0] * 6, "wfwhm", star_width_px=widths)
    assert prediction.mode == "wfwhm"
    assert prediction.weights == wfwhm_weights(widths)
    missing = predict_stack_weights([10.0] * 6, "wfwhm", star_width_px=[2.0, None, 3.0, 3.0, 3.0, 3.0])
    assert missing.weights is None
    assert missing.reasons


def test_spectral_and_unknown_modes() -> None:
    """A spectral stack keeps its configured weight; unknown modes: none."""
    exposures = [10.0] * 10 + [60.0] * 2
    assert predict_stack_weights(exposures, is_spectral=True).mode == "equal"
    other = predict_stack_weights([10.0] * 6, "nbstack")
    assert other.mode == "nbstack"
    assert other.weights is None
    assert other.reasons


def test_an_unreadable_exposure_keeps_one_group() -> None:
    """A frame with no exposure length leaves every frame in one group.

    The readable lengths differ, so the stacker weights by noise.
    """
    prediction = predict_stack_weights([10.0, None, 20.0])
    assert prediction.folded_exposure_lengths == [None, None, None]
    assert prediction.mode == "noise"
    assert prediction.weights is None


def test_exposure_group_weights_equal_the_stackers_group_rule() -> None:
    """Two groups give the weights `group_frame_weights` gives for them."""
    exposures = [10.0] * 5 + [30.0] * 5
    widths = [2.0, 2.2, 2.4, 2.6, 2.8, 3.0, 3.2, 3.4, 3.6, 3.8]
    noises = {10.0: 4.0, 30.0: 9.0}
    prediction = predict_stack_weights(exposures, star_width_px=widths, group_frame_noise_adu=noises)
    assert prediction.mode == "group"
    assert prediction.folded_exposure_lengths == [10.0] * 5 + [30.0] * 5
    expected = group_frame_weights(
        [wfwhm_weights(widths[:5]), wfwhm_weights(widths[5:])], [10.0, 30.0], [5, 5], [4.0, 9.0]
    )
    assert prediction.weights == [*expected.weights[0], *expected.weights[1]]


def test_exposure_group_weights_are_unknown_when_inputs_are_missing() -> None:
    """No frame noise, no star widths, or a folded group each give a reason."""
    exposures = [10.0] * 5 + [30.0] * 5
    widths = [2.0] * 10
    assert predict_stack_weights(exposures, star_width_px=widths).weights is None
    assert predict_stack_weights(exposures, group_frame_noise_adu={10.0: 4.0, 30.0: 9.0}).weights is None
    folded = predict_stack_weights(
        [10.0] * 5 + [30.0] * 5 + [12.0],
        star_width_px=[2.0] * 11,
        group_frame_noise_adu={10.0: 4.0, 30.0: 9.0},
    )
    assert folded.mode == "group"
    assert folded.weights is None
    assert folded.reasons


def test_the_method_on_processing_pipelines_delegates() -> None:
    """The method on `ProcessingPipelines` returns the same prediction."""
    pipelines = ProcessingPipelines(AppConfiguration(), MagicMock())
    prediction = pipelines.predict_stack_weights([5.0] * 6)
    assert prediction == predict_stack_weights([5.0] * 6)


def test_the_group_minimum_is_forwarded() -> None:
    """A lower minimum lets a short length stand as its own group."""
    exposures = [10.0] * 6 + [30.0] * 3
    assert predict_stack_weights(exposures).mode == "noise"
    lowered = predict_stack_weights(exposures, minimum_frames_per_group=2)
    assert lowered.mode == "group"
    assert lowered.folded_exposure_lengths == fold_exposure_lengths(exposures, 2)
