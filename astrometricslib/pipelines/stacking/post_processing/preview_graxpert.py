"""Removes the sky's brightness gradient from the stack preview's linear copy.

The first preview step (see `stack_preview`) removes the gradient in one of
two ways (see `sky_plane`): a fitted plane for a sky that tilts by less than
the limit, or GraXpert's AI background extraction for a stronger gradient.
`remove_background` chooses and runs one, and `flatten_background` runs the
GraXpert program itself, holding the shared Siril slot.
"""

import os
from typing import Any

from astrometricslib._internal.drivers.siril.core.lock import siril_process_lock
from astrometricslib.foundation.run_abandonment import run_registered
from astrometricslib.foundation.system import split_program_command
from astrometricslib.pipelines.stacking.post_processing.background_gradient import (
    BackgroundGradientOutcome,
    measure_background_model_file,
    not_measured_outcome,
)
from astrometricslib.pipelines.stacking.post_processing.preview_steps import SkippedPreviewStep, next_source
from astrometricslib.pipelines.stacking.post_processing.sky_plane import (
    choose_background_method_for_file,
    subtract_sky_plane_in_file,
)

# The longest GraXpert may run, in seconds. On the M 13 stack (3008 x 3008
# pixels) the extraction took 7 seconds including start-up, with the model
# already on disk. A first run also downloads the AI model, which took 34
# seconds. Five minutes covers both and a much larger stack, and still stops
# a hung process. The wait for a free Siril slot is not counted.
GRAXPERT_TIMEOUT_SECONDS = 300


def graxpert_background_model_path(output_stem: str) -> str:
    """Name the file where GraXpert saves its background model.

    Parameters
    ----------
    output_stem : `str`
        The output stem given to `flatten_background`.

    Returns
    -------
    model_path : `str`
        The stem with ``_background.fits`` added, as GraXpert names it when
        run with ``-bg``.
    """
    return f"{output_stem}_background.fits"


def flatten_background(graxpert_executable: str, input_path: str, output_stem: str) -> bool:
    """Remove the sky gradient from an image with GraXpert's AI model.

    Waits for a free Siril slot first, as every Siril run does. GraXpert uses
    the graphics card when it can and the processor otherwise, so a machine
    without CUDA still works. The ``-bg`` option makes it also save the
    background model it subtracted, at `graxpert_background_model_path`. It
    adds no run time (measured on the M 52 stack, 6.8 s with and without),
    and the model is the stack's own sky gradient, which
    `background_gradient` measures.

    Parameters
    ----------
    graxpert_executable : `str`
        The command that starts GraXpert, as in the configuration.
    input_path : `str`
        The FITS image to flatten. It is not changed.
    output_stem : `str`
        Where to write the result, without an extension. GraXpert adds
        ``.fits``, and ``_background.fits`` for the model.

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
        "-bg",
        "-output",
        output_stem,
        input_path,
    ]
    with siril_process_lock():
        completed = run_registered(arguments, GRAXPERT_TIMEOUT_SECONDS)
    return completed.returncode == 0


def _flatten_with_graxpert(
    scratch: str,
    name: str,
    graxpert_executable: str,
    steps: list[str],
    skipped_steps: list[SkippedPreviewStep] | None,
    background_outcomes: list[BackgroundGradientOutcome] | None,
) -> str:
    """Run GraXpert on the scratch copy and measure the model it wrote.

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
    background_outcomes : `list` [`BackgroundGradientOutcome`] or `None`
        Receives the measurement of GraXpert's model, or the reason there is
        none.

    Returns
    -------
    file_name : `str`
        The flattened file's name, or `name` if GraXpert failed.
    """
    measured_outcomes: list[BackgroundGradientOutcome] = []

    def flatten(input_path: str, output_path: str) -> bool:
        """Run GraXpert, which adds its own ``.fits`` to the output name.

        Returns
        -------
        succeeded : `bool`
            Whether GraXpert succeeded.
        """
        output_stem = os.path.splitext(output_path)[0]
        model_path = graxpert_background_model_path(output_stem)
        succeeded = flatten_background(graxpert_executable, input_path, output_stem)
        try:
            if background_outcomes is not None and succeeded:
                measured_outcomes.append(measure_background_model_file(model_path, input_path))
        finally:
            # The full-size model is about as big as the stack: only the
            # small binned map is kept.
            if os.path.isfile(model_path):
                os.remove(model_path)
        return succeeded

    flattened_name = next_source(
        "GraXpert", name, "flat.fits", scratch, flatten, check_result=True, skipped_steps=skipped_steps
    )
    if background_outcomes is not None:
        if flattened_name != name:
            background_outcomes.extend(
                measured_outcomes or [not_measured_outcome("GraXpert wrote no background model")]
            )
        else:
            background_outcomes.append(
                not_measured_outcome(
                    "GraXpert failed, so there is no model"
                    + (f" ({skipped_steps[-1].reason})" if skipped_steps else "")
                )
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
        Receives the measurement of GraXpert's model, or the reason none
        was made.

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
            if background_outcomes is not None:
                background_outcomes.append(
                    not_measured_outcome(
                        "the sky was flat enough for a plane fit, so GraXpert did not run and wrote no model"
                    )
                )
            return plane_name, record
        record["method"] = "graxpert"
        record["reason"] = f"{record['reason']}; the plane could not be subtracted, so GraXpert runs"
    flattened_name = _flatten_with_graxpert(
        scratch, name, graxpert_executable, steps, skipped_steps, background_outcomes
    )
    if flattened_name == name:
        record["method"] = "none"
        record["reason"] = f"{record['reason']}; GraXpert failed"
    return flattened_name, record
