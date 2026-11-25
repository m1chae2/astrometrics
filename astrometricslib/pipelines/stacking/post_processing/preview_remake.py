"""Purpose: Make a stack's preview picture again without restacking.

Description: The preview (see `stack_preview`) is made from a finished stack
by steps that depend on settings: whether GraXpert, Cosmic Clarity and the
star toning run, and how strongly Cosmic Clarity denoises. Changing one of
those settings does not change the stack, but the stacking stage keeps an
unchanged stack and its old picture, so the only way to a new picture used to
be a full restack, which takes minutes and moves the current stack aside.

This module runs only the preview step on the stack that is already there,
followed by the color picture of a monochrome stack when the target has a
color stack (see `color_from_other_stack`). A
run can override the denoise and star-toning settings without writing them to
the configuration. By default the old pictures are copied into a
``_previous_preview`` folder first, so the new and old can be compared, and a
failed run puts them back. The stack file is never opened for writing.

A target stacked with several cameras or optics keeps one imaging stack for
each (``stacks_by_configuration``). A run can name one of those stacks; its
own pictures are made again, and the target's main stack and its
processed-image pointer stay as they were.
"""

import logging
import os
import shutil
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError, ProcessingError
from astrometricslib.foundation.run_abandonment import RunAbandonedError
from astrometricslib.models.processing_results import PreviewRemakeResult, PreviewSettingsRecord
from astrometricslib.pipelines.shared.stack_preview_path import (
    background_model_path_for,
    color_preview_path_for,
    color_preview_record_path_for,
    preview_path_for,
    preview_settings_path_for,
    processed_fits_path_for,
)
from astrometricslib.pipelines.stacking.post_processing.background_gradient import (
    BackgroundGradientOutcome,
    record_background_gradient,
)
from astrometricslib.pipelines.stacking.post_processing.color_from_other_stack import (
    make_color_preview_for_target,
)
from astrometricslib.pipelines.stacking.post_processing.masked_stretch import anchor_for_target
from astrometricslib.pipelines.stacking.post_processing.preview_notices import record_preview_step_notices
from astrometricslib.pipelines.stacking.post_processing.stack_preview import (
    PreviewSettings,
    SkippedPreviewStep,
    read_preview_settings,
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
    for picture in (
        preview_path_for(stacked_path),
        processed_fits_path_for(stacked_path),
        preview_settings_path_for(stacked_path),
        background_model_path_for(stacked_path),
        color_preview_path_for(stacked_path),
        color_preview_record_path_for(stacked_path),
    ):
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


def _read_settings_record(stacked_path: str) -> PreviewSettingsRecord | None:
    """Read the record of what made the new picture.

    Parameters
    ----------
    stacked_path : `str`
        Path of the stack.

    Returns
    -------
    record : `PreviewSettingsRecord` or `None`
        The record, or `None` if it is missing or does not have the expected
        fields. `None` means unknown.
    """
    record = read_preview_settings(stacked_path)
    if record is None:
        return None
    try:
        return PreviewSettingsRecord.model_validate(record)
    except ValidationError:
        logger.warning("The preview settings record of '%s' is not readable.", stacked_path)
        return None


def remake_stack_preview(
    target: Any,
    spectral: bool,
    settings: PreviewSettings,
    keep_previous: bool = True,
    job_id: str | None = None,
    measured_at: datetime | None = None,
    configuration: Any | None = None,
    stack_path: str | None = None,
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
    job_id : `str`, optional
        The job the remake runs in. A step the preview skips is stored as a
        notice row with this id, so the job lists it as a warning.
    measured_at : `datetime`, optional
        When the remake started, with a time zone. Without it no notice row
        is stored (pipeline code does not read the clock).
    configuration : `AppConfiguration`, optional
        Where the quality store lives. The process-wide one if omitted.
    stack_path : `str`, optional
        The stack to make the picture of. It must be the target's main stack
        or one of its stacks by camera and optic. Left out, the main stack.
        Another configuration's stack gets its own pictures; the target's
        processed-image pointer is changed only for the stack it shows.

    Returns
    -------
    result : `PreviewRemakeResult`
        Where the picture went, the steps that ran, whether the viewer shows
        it, where the old pictures went, and whether the stack file was left
        unchanged.

    Raises
    ------
    InvalidArgumentError
        If ``stack_path`` is not one of the target's stacks of this kind.
    NotFoundError
        If the target has no such stack, or its file is missing.
    ProcessingError
        If no preview was made. The old pictures are put back first.
    RunAbandonedError
        If the job was cancelled. The old pictures are put back first.
    """
    stacking = target.spectral_stacking if spectral else target.stacking
    kind = "spectral stack" if spectral else "stack"
    stacked_path = stacking.stacked_image
    if stack_path and stack_path != stacked_path:
        # Spectral stacks are kept only for the main configuration.
        known = (
            set()
            if spectral
            else {
                recorded.stacked_image
                for recorded in stacking.stacks_by_configuration.values()
                if recorded.stacked_image
            }
        )
        if stack_path not in known:
            raise InvalidArgumentError(
                f"'{stack_path}' is not a {kind} of '{target.id}'.",
                details={"target": target.id, "stack_path": stack_path},
            )
        stacked_path = stack_path
    if not stacked_path:
        raise NotFoundError(f"Target '{target.id}' has no {kind}.", details={"target": target.id})
    if not os.path.isfile(stacked_path):
        raise NotFoundError(
            f"The {kind} file of '{target.id}' is missing: {stacked_path}.", details={"path": stacked_path}
        )

    modified_before = os.path.getmtime(stacked_path)
    kept = _keep_old_pictures(stacked_path) if keep_previous else []
    steps: list[str] = []
    skipped_steps: list[SkippedPreviewStep] = []
    # A spectral stack has no sky gradient worth measuring.
    background_outcomes: list[BackgroundGradientOutcome] | None = None if spectral else []
    try:
        preview_path = write_stack_preview(
            stacked_path,
            settings,
            steps,
            skipped_steps,
            background_outcomes,
            # The masked stretch is for imaging stacks of a nebula or a galaxy.
            object_category=None if spectral else target.object_category,
            target_anchor=None if spectral else anchor_for_target(target),
        )
    except RunAbandonedError:
        # The user cancelled the remake: the old picture goes back.
        if kept:
            _put_old_pictures_back(stacked_path, kept)
        raise
    color_outcome = None
    if preview_path is not None:
        # A monochrome stack also gets its color picture again (see
        # `color_from_other_stack`). It never fails the remake. A color stack
        # is skipped, and only the stack the target shows changes the
        # target's color-picture pointer.
        color_outcome = make_color_preview_for_target(target, stacked_path, spectral, skipped_steps)
    record_preview_step_notices(
        skipped_steps, target.id, os.path.basename(stacked_path), job_id, measured_at, configuration
    )
    if background_outcomes is not None:
        record_background_gradient(
            background_outcomes, target.id, os.path.basename(stacked_path), job_id, measured_at, configuration
        )
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
        preview_settings=_read_settings_record(stacked_path),
        color_preview_path=color_outcome.path if color_outcome is not None and color_outcome.made else None,
        color_preview_note=color_outcome.summary() if color_outcome is not None else None,
    )
