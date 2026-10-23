"""Purpose: Stack exactly the frames given into a scratch folder only.

Description: The noise-against-frame-count and each-night's-contribution
measurements (plan decisions 17 and 6.5) compare the library stack with stacks
of some of its frames. Those smaller stacks must never replace the library
stack. `stage.stack_frames` cannot make them, because it always records the
stack on the target, moves frames it finds bad, keeps the previous stack, makes
the preview picture and writes the record that lets the next run skip. This
module stacks a list of frames with the target's normal settings and does
nothing else:

- The frames are copied, so the caller's records are never changed.
- No frame is moved or measured on the target. The frame quarantine step is
  left out, because the frames of a subset are frames the library stack already
  kept.
- The stack goes to a path the caller names, in a subset folder
  (``<target's stacks folder>/_subsets/<label>/``). It is never the library
  stack's path, the previous-stack copy, the preview or ``_inputs.json``.
- The target and the target catalog are not given to it, so it cannot change
  them.

A person can delete the ``_subsets`` folder at any time. The next run makes
what it needs again.
"""

import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from astrometricslib.drivers.driver_set import Drivers
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.target import FrameRecord
from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral
from astrometricslib.pipelines.stacking.post_processing.stack_membership import (
    registration_reference_from_diagnostics,
)

logger = logging.getLogger(__name__)

__all__ = [
    "MINIMUM_SUBSET_FRAMES",
    "SUBSET_FOLDER_NAME",
    "SubsetStackOutcome",
    "check_subset_label",
    "stack_frame_subset",
    "subset_stack_path",
]

# The folder, inside the target's stacks folder, that holds subset stacks.
SUBSET_FOLDER_NAME = "_subsets"

# The fewest frames a subset stack is made from. Sigma-clipping rejection
# compares each pixel with the spread of the same pixel in other frames; with
# three frames one bad value is a third of the sample, and the spread is itself
# barely known. Four is the smallest count where one frame can be rejected and
# the rest still give a spread. A design choice; no data set decided it.
MINIMUM_SUBSET_FRAMES = 4

# A label is a file-name piece: letters, digits, dot, underscore and hyphen,
# and it may not start with a dot (so it never names a hidden file or ``..``).
_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*$")


@dataclass(frozen=True)
class SubsetStackOutcome:
    """What stacking a subset produced.

    Parameters
    ----------
    stack_path : `str` or `None`
        The subset stack, or `None` when the stacker made none.
    diagnostics : `dict`
        What the stacking run reported.
    stack_weight : `str` or `None`
        The Siril weighting mode the subset used.
    crop_offset : `tuple` [`int`, `int`]
        The (columns, rows) trimmed from the stack's left and top edges.
    registration_reference_frame : `str` or `None`
        The frame Siril lined the others up with, when it can be named.
    is_spectral : `bool`
        Whether the frames are spectroscopy frames.
    """

    stack_path: str | None
    diagnostics: dict[str, Any]
    stack_weight: str | None
    crop_offset: tuple[int, int]
    registration_reference_frame: str | None
    is_spectral: bool


def check_subset_label(label: str) -> None:
    """Refuse a label that is not safe to use in a file path.

    Parameters
    ----------
    label : `str`
        The subset's label.

    Raises
    ------
    InvalidArgumentError
        If the label is empty, starts with a dot, or holds anything but
        letters, digits, dots, underscores and hyphens.
    """
    if not _LABEL_PATTERN.match(label):
        raise InvalidArgumentError(
            f"The subset label {label!r} must hold only letters, digits, dots, underscores and "
            "hyphens, and may not start with a dot."
        )


def subset_stack_path(library_folder: str, label: str, file_name: str) -> str:
    """Give the path of a subset stack.

    Parameters
    ----------
    library_folder : `str`
        The target's folder of stacks (the folder of its library stack).
    label : `str`
        The subset's label. It names the subset's own folder, so two subsets
        never share a file.
    file_name : `str`
        The stack's file name.

    Returns
    -------
    path : `str`
        ``<library_folder>/_subsets/<label>/<file_name>``.
    """
    check_subset_label(label)
    return os.path.join(library_folder, SUBSET_FOLDER_NAME, label, os.path.basename(file_name))


def stack_frame_subset(
    target_id: str,
    frames: list[FrameRecord],
    output_path: str,
    *,
    configuration: Any,
    drivers: Drivers | None = None,
    job_id: str | None = None,
    log_file: str | None = None,
) -> SubsetStackOutcome:
    """Stack the given frames into ``output_path`` and change nothing else.

    The stack uses the target's normal settings: the configured rejection and
    weighting, the automatic weighting of frames with different exposure
    lengths, exposure groups, and the trim of noisy edges when turned on.

    Parameters
    ----------
    target_id : `str`
        The target's id. It names Siril's work folder, together with the
        output file.
    frames : `list` [`FrameRecord`]
        The frames to stack, all of one camera. They are copied.
    output_path : `str`
        The full path of the subset stack. Its folder is made if needed. The
        caller makes sure it is not the library stack's path.
    configuration : `AppConfiguration`
        The settings to read (the weighting mode and the edge trim).
    drivers : `Drivers`, optional
        The stacking program to use. Left out, it is Siril.
    job_id : `str`, optional
        The tracked job this stack runs under, for the stacker's progress.
    log_file : `str`, optional
        Where to write Siril's log.

    Returns
    -------
    outcome : `SubsetStackOutcome`
        The subset stack's path (`None` when none was made) and what the run
        reported.

    Raises
    ------
    InvalidArgumentError
        If there are fewer than `MINIMUM_SUBSET_FRAMES` frames, or the output
        path is not absolute.
    """
    from astrometricslib.pipelines.stacking.pre_processing.camera_selection import ensure_single_camera
    from astrometricslib.pipelines.stacking.pre_processing.exposure_weighting import choose_stack_weight
    from astrometricslib.pipelines.stacking.stack_runner import run_stack

    if len(frames) < MINIMUM_SUBSET_FRAMES:
        raise InvalidArgumentError(
            f"A subset stack needs at least {MINIMUM_SUBSET_FRAMES} frames, but {len(frames)} were given."
        )
    if not os.path.isabs(output_path):
        raise InvalidArgumentError("The subset stack's output path must be absolute.")
    frames_to_stack = [frame.model_copy(deep=True) for frame in frames]
    ensure_single_camera(frames_to_stack)
    is_spectral = any(frame_is_spectral(frame) for frame in frames_to_stack)
    stack_weight = choose_stack_weight(
        [float(frame.exposure) for frame in frames_to_stack if frame.exposure],
        configuration.get_stack_weight(),
        is_spectral=is_spectral,
    )
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    engine = (drivers or Drivers()).stacking_or_default()
    stacked_path, diagnostics = run_stack(
        engine,
        frames_to_stack,
        target_id,
        output_path,
        log_file,
        is_spectral,
        job_id=job_id,
        stack_weight=stack_weight,
    )
    crop_offset = (0, 0)
    if stacked_path and not is_spectral and configuration.get_trim_noisy_stack_edges_enabled():
        from astrometricslib.pipelines.stacking.post_processing.stack_crop import crop_stack_edges

        crop_box = crop_stack_edges(stacked_path)
        if crop_box is not None:
            crop_offset = (crop_box.x0, crop_box.y0)
    reference = registration_reference_from_diagnostics(diagnostics) if stacked_path else None
    return SubsetStackOutcome(
        stack_path=stacked_path,
        diagnostics=diagnostics,
        stack_weight=stack_weight,
        crop_offset=crop_offset,
        registration_reference_frame=None if reference is None else reference[0],
        is_spectral=is_spectral,
    )
