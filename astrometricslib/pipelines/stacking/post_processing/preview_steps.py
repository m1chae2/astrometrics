"""Runs one optional step of the stack preview and records what was skipped.

Each cleanup step of the preview (see `stack_preview`) works on a file in a
scratch folder and writes a new one. `next_source` runs a step and picks the
file the next step starts from: the new file if the step worked and, when
asked, its pixels are usable; otherwise the file from before the step. A step
that is skipped is kept as a `SkippedPreviewStep`, so the caller can store a
notice that the picture differs from its settings.
"""

import logging
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from astrometricslib._internal.drivers.image import AstrometricsImage
from astrometricslib.foundation.errors import NotFoundError, StorageError
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.run_abandonment import raise_if_abandoned
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)

logger = logging.getLogger(__name__)


# The share of an image's pixels that must be finite numbers for a cleanup
# step's result to be used. GraXpert can return not-a-number pixels for a
# stack with large empty regions. 0.9 allows for blank borders and still
# rejects an image that is mostly invalid. Not tuned beyond that.
_MINIMUM_FINITE_FRACTION = TunedParameter(
    name="stacking.minimum_finite_fraction",
    default=0.9,
    unit="fraction",
    description="Share of pixels that must be finite for a cleanup result to be used.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D56",
)


def _is_usable_image(path: str) -> bool:
    """Tell whether a FITS image is mostly valid numbers with some contrast.

    Parameters
    ----------
    path : `str`
        Path of the FITS image.

    Returns
    -------
    usable : `bool`
        `True` if at least `_MINIMUM_FINITE_FRACTION` of the pixels are
        finite and the finite pixels are not all one value. A file that
        cannot be read is not usable.
    """
    try:
        data = AstrometricsImage(path).data
    except OSError, ValueError, KeyError, NotFoundError, StorageError:
        return False
    finite = np.isfinite(data)
    if finite.mean() < resolve(_MINIMUM_FINITE_FRACTION).value:
        return False
    return bool(np.nanmax(data) > np.nanmin(data))


@dataclass(frozen=True)
class SkippedPreviewStep:
    """One preview step that was skipped or warned about, and why.

    The preview is made without a skipped step, so the picture differs from
    the one the settings describe. The caller stores a notice for it (see
    `astrometricslib.pipelines.stacking.post_processing.preview_notices`).

    Attributes
    ----------
    step_name : `str`
        The step, such as ``"GraXpert"``.
    reason : `str`
        The sentence that says what went wrong, such as
        ``"GraXpert did not finish"``.
    still_used : `bool`, optional
        `True` for a warning about a step that ran and whose result was kept,
        such as Siril's note that its color calibration is imprecise. The
        default `False` means the step's result was not used.
    """

    step_name: str
    reason: str
    still_used: bool = False


def next_source(
    step_name: str,
    current_name: str,
    new_name: str,
    scratch: str,
    run_step: Callable[[str, str], bool],
    check_result: bool = False,
    skipped_steps: list[SkippedPreviewStep] | None = None,
) -> str:
    """Run one optional cleanup step and choose the file to carry forward.

    Parameters
    ----------
    step_name : `str`
        The step's name, for the warning if it fails.
    current_name : `str`
        The file in the scratch folder that the step starts from.
    new_name : `str`
        The file the step should create in the scratch folder.
    scratch : `str`
        The scratch folder.
    run_step : `Callable` [[`str`, `str`], `bool`]
        Runs the step on a full input path and a full output path, and
        returns whether the program succeeded.
    check_result : `bool`, optional
        If `True`, the step's file is used only if `_is_usable_image` accepts
        it, so a program that exits without an error but writes invalid
        pixels is treated as a failure.
    skipped_steps : `list` [`SkippedPreviewStep`], optional
        If given, a step that is skipped is added to it, so the caller can
        store a notice that the preview differs from its settings.

    Returns
    -------
    file_name : `str`
        `new_name` if the step worked, otherwise `current_name`.
    """

    def skip(reason: str) -> str:
        """Log a skipped step, remember it, and keep the old file.

        Returns
        -------
        file_name : `str`
            `current_name`.
        """
        logger.warning("%s; the preview skips that step.", reason)
        if skipped_steps is not None:
            skipped_steps.append(SkippedPreviewStep(step_name, reason))
        return current_name

    # A cancelled job starts no further step; one that is running is stopped
    # by `RunAbandonment.abandon`, which ends its program.
    raise_if_abandoned()
    try:
        worked = run_step(os.path.join(scratch, current_name), os.path.join(scratch, new_name))
    except (OSError, subprocess.SubprocessError) as error:
        return skip(f"{step_name} could not run ({error})")
    result_path = os.path.join(scratch, new_name)
    if not worked or not os.path.isfile(result_path):
        return skip(f"{step_name} did not finish")
    if check_result and not _is_usable_image(result_path):
        return skip(f"{step_name} wrote an image with invalid pixels")
    logger.info("%s finished; the preview uses its result.", step_name)
    return new_name
