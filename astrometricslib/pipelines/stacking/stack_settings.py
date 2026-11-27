"""Purpose: Keep the stacking choices a library and a subset stack share.

Description: A subset stack is only a fair stand-in for the library stack if
it is made with exactly the library stack's settings. Two choices are made
outside the stacking program, and both the library stack
(`stage.stack_frames`) and the subset stack (`subset_stack.stack_frame_subset`)
call the functions here for them, so there is one copy of each rule:

- the weighting mode, from the frames' exposure lengths and the setting;
- the trim of the noisy edges of a finished stack, from the setting.
"""

from collections.abc import Sequence
from typing import Any

__all__ = [
    "choose_weight_for_frames",
    "trim_noisy_stack_edges",
]


def choose_weight_for_frames(
    frames: Sequence[Any],
    requested_weight: str | None,
    configuration: Any,
    *,
    is_spectral: bool,
) -> str | None:
    """Choose the weighting mode for a stack of these frames.

    Parameters
    ----------
    frames : `Sequence`
        The frames to stack. Each has an ``exposure_s`` in seconds, which may
        be empty.
    requested_weight : `str` or `None`
        The mode the caller asked for. `None` means use the configured mode.
    configuration : `AppConfiguration`
        The settings, read for the configured weighting mode.
    is_spectral : `bool`
        Whether the stack is spectral.

    Returns
    -------
    weight : `str` or `None`
        The Siril ``-weight`` value to use, or `None` for equal weights (see
        `choose_stack_weight`).
    """
    from astrometricslib.pipelines.stacking.pre_processing.exposure_weighting import choose_stack_weight

    return choose_stack_weight(
        [float(frame.exposure_s) for frame in frames if frame.exposure_s],
        requested_weight if requested_weight is not None else configuration.get_stack_weight(),
        is_spectral=is_spectral,
    )


def trim_noisy_stack_edges(
    stacked_path: str | None,
    configuration: Any,
    *,
    is_spectral: bool,
    diagnostics: dict[str, Any] | None = None,
) -> tuple[int, int]:
    """Trim the thinly covered edges of a stack when the setting asks for it.

    Frames shift between nights, so the edges of a stack of several nights are
    covered by fewer frames and are noisier. The stack is trimmed only where
    fewer than half the frames cover it. The box the processed picture will
    keep (where at least 85% do) is put in ``diagnostics["processed_crop"]``.
    A stack whose frames all cover it is left as it is. A spectral stack is
    never trimmed.

    Parameters
    ----------
    stacked_path : `str` or `None`
        The finished stack, or `None` when the stacker made none.
    configuration : `AppConfiguration`
        The settings, read for the trim switch.
    is_spectral : `bool`
        Whether the stack is spectral.
    diagnostics : `dict`, optional
        What the stacking run reported, read for the frames' registration
        transforms. Without them the trim falls back to measured noise.

    Returns
    -------
    crop_offset : `tuple` [`int`, `int`]
        The (columns, rows) cut from the stack's left and top edges; (0, 0)
        when nothing was cut.
    """
    if not stacked_path or is_spectral or not configuration.get_trim_noisy_stack_edges_enabled():
        return (0, 0)
    from astrometricslib.pipelines.stacking.post_processing.stack_crop import crop_stack_edges

    crops = crop_stack_edges(stacked_path, diagnostics)
    if crops is None:
        return (0, 0)
    if diagnostics is not None and crops.processed is not None:
        # The registration record keeps this box. The preview crops its copy
        # of the stack to it before GraXpert and Cosmic Clarity run.
        processed = crops.processed
        diagnostics["processed_crop"] = [processed.x0, processed.x1, processed.y0, processed.y1]
    if crops.stack is None:
        return (0, 0)
    if diagnostics is not None:
        # The stage copies this into the stack's quality summary, where it
        # becomes the `stacking.notice_stack_cropped` row (only when the
        # stack itself was trimmed).
        diagnostics["stack_crop"] = {
            "reason": crops.stack.reason,
            "area_fraction": crops.stack.lost_area_fraction,
        }
    return (crops.stack.x0, crops.stack.y0)
