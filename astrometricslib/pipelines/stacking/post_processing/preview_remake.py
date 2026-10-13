"""Purpose: Make a stack's preview picture again without restacking.

Description: The preview (see `stack_preview`) is made from a finished stack
by steps that depend on settings: whether GraXpert, Cosmic Clarity and the
star toning run, and how strongly Cosmic Clarity denoises. Changing one of
those settings does not change the stack, but the stacking stage keeps an
unchanged stack and its old picture, so the only way to a new picture used to
be a full restack, which takes minutes and moves the current stack aside.

This module runs only the preview step on the stack that is already there. A
run can override the denoise and star-toning settings without writing them to
the configuration. By default the old pictures are copied into a
``_previous_preview`` folder first, so the new and old can be compared, and a
failed run puts them back. The stack file is never opened for writing.
"""

import logging
import os
import shutil
from typing import Any

from astrometricslib.foundation.errors import NotFoundError, ProcessingError
from astrometricslib.models.processing_results import PreviewRemakeResult
from astrometricslib.pipelines.shared.stack_preview_path import preview_path_for, processed_fits_path_for
from astrometricslib.pipelines.stacking.post_processing.stack_preview import (
    PreviewSettings,
    record_preview_as_processed_image,
    write_stack_preview,
)

logger = logging.getLogger(__name__)

PREVIOUS_PICTURES_FOLDER = "_previous_preview"
"""The folder, beside the stack, that holds the pictures a remake replaced.
One earlier set is kept: a second remake overwrites it."""


def _keep_old_pictures(stacked_path: str) -> list[str]:
    """Copy the stack's current pictures into the previous-pictures folder.

    Parameters
    ----------
    stacked_path : `str`
        Path of the stack.

    Returns
    -------
    kept : `list` [`str`]
        The paths of the copies made. Empty if there were no pictures.
    """
    folder = os.path.join(os.path.dirname(stacked_path), PREVIOUS_PICTURES_FOLDER)
    kept = []
    for picture in (preview_path_for(stacked_path), processed_fits_path_for(stacked_path)):
        if os.path.isfile(picture):
            os.makedirs(folder, exist_ok=True)
            copy = os.path.join(folder, os.path.basename(picture))
            shutil.copy2(picture, copy)
            kept.append(copy)
    return kept


def _put_old_pictures_back(stacked_path: str, kept: list[str]) -> None:
    """Restore pictures that a failed run removed.

    Parameters
    ----------
    stacked_path : `str`
        Path of the stack.
    kept : `list` [`str`]
        The copies made by `_keep_old_pictures`.
    """
    folder = os.path.dirname(stacked_path)
    for copy in kept:
        shutil.copy2(copy, os.path.join(folder, os.path.basename(copy)))


def remake_stack_preview(
    target: Any,
    spectral: bool,
    settings: PreviewSettings,
    keep_previous: bool = True,
) -> PreviewRemakeResult:
    """Make the preview of a target's current stack again.

    Parameters
    ----------
    target : `Target`
        The target that owns the stack. Its processed-image pointer is
        updated; the caller saves it.
    spectral : `bool`
        Use the spectral stack rather than the imaging stack.
    settings : `PreviewSettings`
        Choices for this run that replace the saved settings.
    keep_previous : `bool`, optional
        Copy the current pictures aside first. On by default.

    Returns
    -------
    result : `PreviewRemakeResult`
        Where the picture went, the steps that ran, whether the viewer shows
        it, where the old pictures went, and whether the stack file was left
        unchanged.

    Raises
    ------
    NotFoundError
        If the target has no such stack, or its file is missing.
    ProcessingError
        If no preview was made. The old pictures are put back first.
    """
    stacking = target.spectral_stacking if spectral else target.stacking
    stacked_path = stacking.stacked_image
    kind = "spectral stack" if spectral else "stack"
    if not stacked_path:
        raise NotFoundError(f"Target '{target.id}' has no {kind}.", details={"target": target.id})
    if not os.path.isfile(stacked_path):
        raise NotFoundError(
            f"The {kind} file of '{target.id}' is missing: {stacked_path}.", details={"path": stacked_path}
        )

    modified_before = os.path.getmtime(stacked_path)
    kept = _keep_old_pictures(stacked_path) if keep_previous else []
    steps: list[str] = []
    preview_path = write_stack_preview(stacked_path, settings, steps)
    if preview_path is None:
        if kept:
            _put_old_pictures_back(stacked_path, kept)
        raise ProcessingError(
            "No preview was made (see the job log). "
            + ("The old pictures were put back." if kept else "The old pictures were removed."),
            details={"target": target.id, "steps_run": steps},
        )
    processed_path = processed_fits_path_for(stacked_path)
    shown = record_preview_as_processed_image(
        target, spectral, stacked_path, preview_path, keep_attached=True
    )
    return PreviewRemakeResult(
        target_id=target.id,
        stack_path=stacked_path,
        preview_path=preview_path,
        processed_fits_path=processed_path if os.path.isfile(processed_path) else None,
        steps_run=steps,
        shown_in_viewer=shown,
        previous_pictures=kept,
        stack_file_unchanged=os.path.getmtime(stacked_path) == modified_before,
    )
