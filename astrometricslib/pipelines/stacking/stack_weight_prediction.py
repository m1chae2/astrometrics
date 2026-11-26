"""Purpose: Predict how the stacker would weight the frames of a stack.

Description: Planning code asks what a stack of some frames would do before
the frames are stacked: which weighting mode the stacker would choose, which
exposure group each frame would fall in, and what weight each frame would
carry. The answers come from the stacker's own rules (`choose_stack_weight`,
`plan_exposure_folding`, `wfwhm_weights` and `group_frame_weights`), so a
forecast and a real stack cannot disagree. This module asks all of them from
one set of frame facts and returns one `StackWeightPrediction`.
"""

import math
from collections.abc import Mapping, Sequence

from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.processing_results import StackWeightPrediction
from astrometricslib.pipelines.stacking.post_processing.stack_membership import (
    group_frame_weights,
    wfwhm_weights,
)
from astrometricslib.pipelines.stacking.pre_processing.exposure_weighting import (
    NOISE_WEIGHT_MODE,
    choose_stack_weight,
)
from astrometricslib.pipelines.stacking.processing.exposure_groups import (
    MINIMUM_FRAMES_PER_EXPOSURE_GROUP,
    ExposureFolding,
    exposure_group_key,
    plan_exposure_folding,
)

__all__ = ["predict_stack_weights"]

EQUAL_MODE = "equal"
"""The mode name for a stack that gives every frame the same weight."""

GROUP_MODE = "group"
"""The mode name for a stack built from several exposure groups."""

WFWHM_MODE = "wfwhm"
"""The mode name for star-width weights (Siril's ``-weight=wfwhm``)."""


def predict_stack_weights(
    exposure_seconds: Sequence[float | None],
    configured_weight: str | None = None,
    *,
    is_spectral: bool = False,
    star_width_px: Sequence[float | None] | None = None,
    rate_variance: Sequence[float | None] | None = None,
    group_frame_noise_adu: Mapping[float, float | None] | None = None,
    minimum_frames_per_group: int | None = None,
) -> StackWeightPrediction:
    """Predict the weighting mode, exposure groups and weights of a stack.

    Parameters
    ----------
    exposure_seconds : `Sequence` [`float` or `None`]
        The exposure length of each frame, in seconds. `None` marks a frame
        whose length cannot be read.
    configured_weight : `str` or `None`, optional
        The ``Processing.Siril.stack_weight`` setting. A configured mode
        wins over the stacker's own choice.
    is_spectral : `bool`, optional
        Whether the stack is spectral. Those keep their configured weight.
    star_width_px : `Sequence` [`float` or `None`], optional
        The weighted star width of each frame, in pixels. Needed for the
        ``wfwhm`` weights and for the weights inside each group.
    rate_variance : `Sequence` [`float` or `None`], optional
        The variance of each frame in rate units. Needed for the ``noise``
        weights, which are the inverse of this value.
    group_frame_noise_adu : `Mapping` [`float`, `float` or `None`], optional
        The noise of one raw frame of each exposure group, in counts, keyed
        by the group's length from ``folded_exposure_lengths``. Needed for
        the weights of an exposure-group stack.
    minimum_frames_per_group : `int`, optional
        The fewest frames an exposure length needs to be stacked on its own.
        Defaults to the stacker's value.

    Returns
    -------
    prediction : `StackWeightPrediction`
        The mode, the group each frame lands in, and the weight of each
        frame, or the reasons the weights are unknown.
    """
    if minimum_frames_per_group is None:
        minimum_frames_per_group = resolve(MINIMUM_FRAMES_PER_EXPOSURE_GROUP).value
    folding = plan_exposure_folding(list(exposure_seconds), minimum_frames_per_group)
    if any(reason is not None for reason in folding.left_out_reasons):
        return _predict_without_left_out_frames(
            folding,
            exposure_seconds,
            configured_weight,
            is_spectral=is_spectral,
            star_width_px=star_width_px,
            rate_variance=rate_variance,
            group_frame_noise_adu=group_frame_noise_adu,
            minimum_frames_per_group=minimum_frames_per_group,
        )
    folded = folding.group_lengths
    if len(set(folded)) > 1:
        mode = GROUP_MODE
    else:
        chosen = choose_stack_weight(
            [exposure for exposure in exposure_seconds if exposure is not None],
            configured_weight,
            is_spectral=is_spectral,
        )
        mode = EQUAL_MODE if chosen is None else chosen
    weights, reasons = _weights_for_mode(
        mode,
        exposure_seconds,
        folded,
        star_width_px=star_width_px,
        rate_variance=rate_variance,
        group_frame_noise_adu=group_frame_noise_adu,
    )
    return StackWeightPrediction(
        mode=mode,
        folded_exposure_lengths=folded,
        left_out_reasons=folding.left_out_reasons,
        weights=weights,
        reasons=reasons,
    )


def _predict_without_left_out_frames(
    folding: ExposureFolding,
    exposure_seconds: Sequence[float | None],
    configured_weight: str | None,
    *,
    is_spectral: bool,
    star_width_px: Sequence[float | None] | None,
    rate_variance: Sequence[float | None] | None,
    group_frame_noise_adu: Mapping[float, float | None] | None,
    minimum_frames_per_group: int,
) -> StackWeightPrediction:
    """Predict the stack of the frames the stacker would keep.

    The frames the stacker leaves out take no part in the stack: they are
    taken out, the rest are predicted, and the left-out frames come back with
    weight 0 and their reason.

    Parameters
    ----------
    folding : `ExposureFolding`
        The fold plan of all the frames, with at least one frame left out.
    exposure_seconds : `Sequence` [`float` or `None`]
        The exposure length of every frame.
    configured_weight : `str` or `None`
        The configured weight (see `predict_stack_weights`).
    is_spectral : `bool`
        Whether the stack is spectral.
    star_width_px : `Sequence` [`float` or `None`] or `None`
        The weighted star width of every frame.
    rate_variance : `Sequence` [`float` or `None`] or `None`
        The variance of every frame in rate units.
    group_frame_noise_adu : `Mapping` [`float`, `float` or `None`] or `None`
        The noise of one raw frame of each group.
    minimum_frames_per_group : `int`
        The fewest frames a length needs to be stacked on its own.

    Returns
    -------
    prediction : `StackWeightPrediction`
        One entry per frame, in the order of the input.
    """
    kept = [index for index, reason in enumerate(folding.left_out_reasons) if reason is None]

    def pick(values: Sequence[float | None] | None) -> list[float | None] | None:
        """Keep the entries of the frames that stay in the stack.

        Parameters
        ----------
        values : `Sequence` [`float` or `None`] or `None`
            One value per frame, or `None`.

        Returns
        -------
        kept_values : `list` [`float` or `None`] or `None`
            The values of the kept frames. A list of the wrong length is
            passed on as it is, for the prediction to refuse.
        """
        if values is None or len(values) != len(exposure_seconds):
            return None if values is None else list(values)
        return [values[index] for index in kept]

    inner = predict_stack_weights(
        [exposure_seconds[index] for index in kept],
        configured_weight,
        is_spectral=is_spectral,
        star_width_px=pick(star_width_px),
        rate_variance=pick(rate_variance),
        group_frame_noise_adu=group_frame_noise_adu,
        minimum_frames_per_group=minimum_frames_per_group,
    )
    lengths = list(folding.group_lengths)
    weights = None if inner.weights is None else [0.0] * len(exposure_seconds)
    for position, index in enumerate(kept):
        lengths[index] = inner.folded_exposure_lengths[position]
        if weights is not None and inner.weights is not None:
            weights[index] = inner.weights[position]
    return StackWeightPrediction(
        mode=inner.mode,
        folded_exposure_lengths=lengths,
        left_out_reasons=folding.left_out_reasons,
        weights=weights,
        reasons=inner.reasons,
    )


def _weights_for_mode(
    mode: str,
    exposure_seconds: Sequence[float | None],
    folded: Sequence[float | None],
    *,
    star_width_px: Sequence[float | None] | None,
    rate_variance: Sequence[float | None] | None,
    group_frame_noise_adu: Mapping[float, float | None] | None,
) -> tuple[list[float] | None, list[str]]:
    """Work out the frame weights of a mode, or why they are unknown.

    Parameters
    ----------
    mode : `str`
        The predicted mode.
    exposure_seconds : `Sequence` [`float` or `None`]
        The exposure length of each frame.
    folded : `Sequence` [`float` or `None`]
        The exposure group length of each frame.
    star_width_px : `Sequence` [`float` or `None`] or `None`
        The weighted star width of each frame, in pixels.
    rate_variance : `Sequence` [`float` or `None`] or `None`
        The variance of each frame in rate units.
    group_frame_noise_adu : `Mapping` [`float`, `float` or `None`] or `None`
        The noise of one raw frame of each exposure group, in counts.

    Returns
    -------
    weights : `list` [`float`] or `None`
        One weight per frame, or `None` when they are unknown.
    reasons : `list` [`str`]
        Why the weights are unknown. Empty when they are known.
    """
    count = len(exposure_seconds)
    if mode == EQUAL_MODE:
        return [1.0] * count, []
    if mode == WFWHM_MODE:
        if not count or star_width_px is None or len(star_width_px) != count or None in star_width_px:
            return None, ["The star-width weights need the star width of every frame."]
        return wfwhm_weights([float(width) for width in star_width_px if width is not None]), []
    if mode == NOISE_WEIGHT_MODE:
        if not count or rate_variance is None or len(rate_variance) != count:
            return None, ["The noise weights need the variance of every frame."]
        if any(variance is None or variance <= 0.0 for variance in rate_variance):
            return None, ["A frame has a variance of 0 or none, so its inverse is undefined."]
        return [1.0 / float(variance) for variance in rate_variance if variance is not None], []
    if mode == GROUP_MODE:
        return _group_weights(
            exposure_seconds, folded, star_width_px=star_width_px, group_frame_noise_adu=group_frame_noise_adu
        )
    return None, [f"The forecast does not know the weights of the {mode!r} mode."]


def _group_weights(
    exposure_seconds: Sequence[float | None],
    folded: Sequence[float | None],
    *,
    star_width_px: Sequence[float | None] | None,
    group_frame_noise_adu: Mapping[float, float | None] | None,
) -> tuple[list[float] | None, list[str]]:
    """Work out the raw weights of an exposure-group stack.

    Parameters
    ----------
    exposure_seconds : `Sequence` [`float` or `None`]
        The exposure length of each frame.
    folded : `Sequence` [`float` or `None`]
        The exposure group length of each frame. No entry is `None`.
    star_width_px : `Sequence` [`float` or `None`] or `None`
        The weighted star width of each frame, in pixels.
    group_frame_noise_adu : `Mapping` [`float`, `float` or `None`] or `None`
        The noise of one raw frame of each group, in counts.

    Returns
    -------
    weights : `list` [`float`] or `None`
        One raw weight per frame, in the order of the frames, or `None`.
    reasons : `list` [`str`]
        Why the weights are unknown. Empty when they are known.
    """
    lengths = sorted({length for length in folded if length is not None})
    members = [[index for index, length in enumerate(folded) if length == group] for group in lengths]
    widths_known = star_width_px is not None and len(star_width_px) == len(folded)
    in_group: list[list[float] | None] = []
    for indices in members:
        widths = (
            [star_width_px[index] for index in indices] if star_width_px is not None and widths_known else []
        )
        if widths_known and None not in widths:
            in_group.append(wfwhm_weights([float(width) for width in widths if width is not None]))
        else:
            in_group.append(None)
    noises = group_frame_noise_adu or {}
    result = group_frame_weights(
        in_group,
        lengths,
        [len(indices) for indices in members],
        [noises.get(length) for length in lengths],
        holds_folded_frames=[
            any(
                exposure_seconds[index] is None or exposure_group_key(exposure_seconds[index]) != length
                for index in indices
            )
            for length, indices in zip(lengths, members, strict=True)
        ],
        frame_exposures=[[exposure_seconds[index] for index in indices] for indices in members],
    )
    if any(group is None for group in result.weights):
        return None, [reason for reason in result.reasons if reason]
    weights = [math.nan] * len(folded)
    for indices, group in zip(members, result.weights, strict=True):
        for index, weight in zip(indices, group or (), strict=True):
            weights[index] = weight
    return weights, []
