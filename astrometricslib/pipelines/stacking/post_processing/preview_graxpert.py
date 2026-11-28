"""Removes the sky's brightness gradient from the stack preview's linear copy.

The first preview step (see `stack_preview`) removes the gradient in one of
two ways (see `sky_plane`): a fitted plane for a sky that tilts by less than
the limit, or GraXpert's AI background extraction for a stronger gradient.
`remove_background` chooses and runs one, and `flatten_background` runs the
GraXpert program itself, holding the shared Siril slot. GraXpert is given a
stack in the 0 to 1 range and its picture is scaled back, so the picture is
in the stack's units (`flatten_in_stack_units`). The stack's own sky bins,
measured while the method is chosen, are the stored background measurement.
"""

import os
from typing import Any

import numpy as np

from astrometricslib._internal.drivers.fits_access import read_data, read_header, write_image
from astrometricslib._internal.drivers.siril.core.lock import siril_process_lock
from astrometricslib.foundation.run_abandonment import run_registered
from astrometricslib.foundation.system import split_program_command
from astrometricslib.pipelines.stacking.post_processing.background_gradient import BackgroundGradientOutcome
from astrometricslib.pipelines.stacking.post_processing.preview_steps import SkippedPreviewStep, next_source
from astrometricslib.pipelines.stacking.post_processing.sky_plane import (
    choose_background_method_for_file,
    subtract_sky_plane_in_file,
)

# The longest GraXpert may run, in seconds. On the M 13 stack (3008 x 3008
# pixels) the extraction took 7 seconds including start-up, with the AI model
# already on disk. A first run also downloads the AI model, which took 34
# seconds. Five minutes covers both and a much larger stack, and still stops
# a hung process. The wait for a free Siril slot is not counted.
GRAXPERT_TIMEOUT_SECONDS = 300

# GraXpert leaves an image whose brightest pixel is 1.0 or less as it is, and
# rescales one with a brighter pixel to run from 0 to 1 (measured 2026-11-28
# on NGC 2244 and M 81: output median 0.0018 for an input median of 0.046,
# which is (median - minimum) / (maximum - minimum)). The rescale changes the
# picture's level, so a stack with a pixel
# above this value is divided by its brightest pixel before GraXpert and the
# result is multiplied back (`flatten_in_stack_units`).
GRAXPERT_UNIT_LIMIT = 1.0


def scale_into_unit_range(input_path: str, scaled_path: str) -> float:
    """Divide a stack by its brightest pixel when it has a pixel above 1.0.

    Parameters
    ----------
    input_path : `str`
        The FITS image. It is not changed.
    scaled_path : `str`
        Where to write the divided copy. Nothing is written when no pixel is
        above `GRAXPERT_UNIT_LIMIT`.

    Returns
    -------
    factor : `float`
        The number the copy was divided by: the largest finite pixel. 1.0 when
        no copy was made (nothing above the limit, no finite pixel, or no
        pixels).
    """
    try:
        data = read_data(input_path)
    except OSError, ValueError:
        # Not readable as an image here: GraXpert gets the file as it is and
        # reports the problem itself.
        return 1.0
    if data is None or not np.isfinite(data).any():
        return 1.0
    factor = float(np.nanmax(np.where(np.isfinite(data), data, -np.inf)))
    if factor <= GRAXPERT_UNIT_LIMIT:
        return 1.0
    write_image(
        scaled_path, (np.asarray(data, dtype=np.float64) / factor).astype(np.float32), read_header(input_path)
    )
    return factor


def multiply_image_in_file(path: str, factor: float) -> None:
    """Multiply the pixels of a FITS image by a number, in the same file.

    Parameters
    ----------
    path : `str`
        The FITS image, replaced by the multiplied one with its header.
    factor : `float`
        The number to multiply by.

    Raises
    ------
    ValueError
        If the file holds no pixels.
    """
    data = read_data(path)
    if data is None:
        raise ValueError(f"'{path}' holds no pixels")
    write_image(path, (np.asarray(data, dtype=np.float64) * factor).astype(np.float32), read_header(path))


def flatten_background(graxpert_executable: str, input_path: str, output_stem: str) -> bool:
    """Remove the sky gradient from an image with GraXpert's AI model.

    Waits for a free Siril slot first, as every Siril run does. GraXpert uses
    the graphics card when it can and the processor otherwise, so a machine
    without CUDA still works. GraXpert's own model of the background is not
    asked for: nothing uses it. The sky gradient that is stored is measured on
    the stack's own sky bins (see `sky_plane` and `background_gradient`).

    Parameters
    ----------
    graxpert_executable : `str`
        The command that starts GraXpert, as in the configuration.
    input_path : `str`
        The FITS image to flatten. It is not changed.
    output_stem : `str`
        Where to write the result, without an extension. GraXpert adds
        ``.fits``.

    Returns
    -------
    succeeded : `bool`
        `True` if GraXpert exited without an error code.
    """
    arguments = [
        *split_program_command(graxpert_executable),
        "-cli",
        "-cmd",
        "background-extraction",
        "-gpu",
        "true",
        "-correction",
        "Subtraction",
        "-smoothing",
        "0.0",
        "-output",
        output_stem,
        input_path,
    ]
    with siril_process_lock():
        completed = run_registered(arguments, GRAXPERT_TIMEOUT_SECONDS)
    return completed.returncode == 0


def flatten_in_stack_units(graxpert_executable: str, input_path: str, output_path: str) -> bool:
    """Run GraXpert so that its picture comes back in the stack's units.

    A stack with a pixel above 1.0 is divided by its brightest pixel first
    (`scale_into_unit_range`), so GraXpert does not rescale it, and its
    picture is multiplied by the same number afterwards. The copy GraXpert
    reads is deleted.

    Parameters
    ----------
    graxpert_executable : `str`
        The command that starts GraXpert.
    input_path : `str`
        The FITS image to flatten. It is not changed.
    output_path : `str`
        Where to write the result, with its ``.fits`` ending.

    Returns
    -------
    succeeded : `bool`
        `True` if GraXpert succeeded and its picture was written in the
        stack's units.
    """
    output_stem = os.path.splitext(output_path)[0]
    scaled_path = f"{output_stem}_unit_range_input.fits"
    try:
        factor = scale_into_unit_range(input_path, scaled_path)
        succeeded = flatten_background(
            graxpert_executable, scaled_path if factor > 1.0 else input_path, output_stem
        )
        if succeeded and factor > 1.0:
            multiply_image_in_file(output_path, factor)
        return succeeded
    finally:
        if os.path.isfile(scaled_path):
            os.remove(scaled_path)


def _flatten_with_graxpert(
    scratch: str,
    name: str,
    graxpert_executable: str,
    steps: list[str],
    skipped_steps: list[SkippedPreviewStep] | None,
) -> str:
    """Run GraXpert on the scratch copy.

    Parameters
    ----------
    scratch : `str`
        The scratch folder.
    name : `str`
        The file in the scratch folder GraXpert starts from.
    graxpert_executable : `str`
        The command that starts GraXpert.
    steps : `list` [`str`]
        The step sentences; "GraXpert done" or "GraXpert skipped after a
        failure" is added.
    skipped_steps : `list` [`SkippedPreviewStep`] or `None`
        Receives the step if it failed.

    Returns
    -------
    file_name : `str`
        The flattened file's name, or `name` if GraXpert failed.
    """
    flattened_name = next_source(
        "GraXpert",
        name,
        "flat.fits",
        scratch,
        lambda input_path, output_path: flatten_in_stack_units(graxpert_executable, input_path, output_path),
        check_result=True,
        skipped_steps=skipped_steps,
    )
    steps.append("GraXpert " + ("done" if flattened_name != name else "skipped after a failure"))
    return flattened_name


def remove_background(
    scratch: str,
    name: str,
    graxpert_executable: str,
    steps: list[str],
    skipped_steps: list[SkippedPreviewStep] | None,
    background_outcomes: list[BackgroundGradientOutcome] | None,
) -> tuple[str, dict[str, Any]]:
    """Remove the sky gradient: a fitted plane for a flat sky, else GraXpert.

    The stack's sky tilt is measured first (see `sky_plane`). Below the limit
    a plane is subtracted, which cannot make a ring around a large nebula.
    At or above it, or when the tilt cannot be measured, GraXpert runs as it
    always did. If the plane cannot be written, GraXpert runs too.

    Parameters
    ----------
    scratch : `str`
        The scratch folder.
    name : `str`
        The file in the scratch folder to start from.
    graxpert_executable : `str`
        The command that starts GraXpert.
    steps : `list` [`str`]
        The step sentences; the one for this step is added.
    skipped_steps : `list` [`SkippedPreviewStep`] or `None`
        Receives a step that failed.
    background_outcomes : `list` [`BackgroundGradientOutcome`] or `None`
        Receives the measurement of the stack's own sky bins, or the reason
        there is none.

    Returns
    -------
    file_name : `str`
        The file to carry forward.
    record : `dict` [`str`, `Any`]
        What was decided and measured, for the preview record
        (`BackgroundDecision.record`). Its ``method`` is what actually ran:
        ``plane``, ``graxpert`` or ``none`` (every method failed).
    """
    decision = choose_background_method_for_file(os.path.join(scratch, name))
    if background_outcomes is not None:
        # The stack's own sky bins are the stored measurement, whichever
        # method runs.
        background_outcomes.append(decision.sky_background)
    record = decision.record()
    if decision.method == "plane":
        plane_name = next_source(
            "Background plane fit",
            name,
            "flat.fits",
            scratch,
            lambda input_path, output_path: subtract_sky_plane_in_file(
                input_path, output_path, decision.plane_coefficients, decision.radial_coefficients
            ),
            check_result=True,
            skipped_steps=skipped_steps,
        )
        if plane_name != name:
            steps.append(decision.step_sentence())
            return plane_name, record
        record["method"] = "graxpert"
        record["reason"] = f"{record['reason']}; the plane could not be subtracted, so GraXpert runs"
    flattened_name = _flatten_with_graxpert(scratch, name, graxpert_executable, steps, skipped_steps)
    if flattened_name == name:
        record["method"] = "none"
        record["reason"] = f"{record['reason']}; GraXpert failed"
    return flattened_name, record
