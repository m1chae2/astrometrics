"""Tuner for the longest step that can still be a pointing change.

`step_summary` (in `registration_shifts.py`) leaves out a step between two
neighboring exposures when it is longer than `MAXIMUM_PLAUSIBLE_STEP_PX`: a
real dither or drift is a few pixels, while the debayered, differently sized
frames of a DSLR give "steps" of thousands of pixels (register entry STK-44).

The raw steps are not stored. Each stack stores the median step and the
spread of its steps, so this tuner works from those. For each stack with
measured step rows it works out the step that the stack itself would call a
jump (the median plus the jump sigmas times the robust spread, the same rule
`step_summary` uses). The longest of these across the library is the longest
step a real run of this setup has reached. The limit is that step times a
safety factor, so it stays above every real run and well below the
thousands of pixels of a broken transform.

What the stored rows cannot show is where the broken transforms start: no
real run reaches them, so the upper side of the limit rests on the safety
factor, and the allowed range of the parameter keeps it far below them.
"""

from collections.abc import Sequence

from astrometricslib._internal.drivers.tuning_support import MAD_TO_SIGMA, camera_rows
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.tuning import Tuner, TuningInputs, TuningResult, TuningSkippedError
from astrometricslib.models.quality.measurement import SubjectLevel
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.stacking.metrics import (
    STACKING_REGISTRATION_STEP_MAD_PX,
    STACKING_REGISTRATION_STEP_MEDIAN_PX,
)
from astrometricslib.pipelines.stacking.post_processing.registration_shifts import (
    MAXIMUM_PLAUSIBLE_STEP_PX,
    MINIMUM_STEP_SCALE_PX,
    STEP_JUMP_SIGMAS,
)

# How many times longer than the longest real step of the library the limit
# is. The survey of 2026-10-23 (STK-44) found a longest jump of about 10 px
# (M 27) and DSLR "steps" of 5,000 to 7,400 px. Five times the longest real
# step is 50 px there: wide enough that a real re-centering or flip stays
# counted, and a hundred times below the broken transforms.
STEP_LIMIT_SAFETY_FACTOR = TunedParameter(
    name="stacking.step_limit_safety_factor",
    default=5.0,
    unit="× longest real step",
    description="How many times the longest step of a real run the step limit is set to.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-44",
)


def jump_threshold_px(median_px: float, mad_px: float, jump_sigmas: float, minimum_scale_px: float) -> float:
    """Work out the step a stack would call a jump.

    Parameters
    ----------
    median_px : `float`
        The stack's median step.
    mad_px : `float`
        The median absolute deviation of its steps.
    jump_sigmas : `float`
        How many robust deviations above the median a step is a jump.
    minimum_scale_px : `float`
        The floor of the robust deviation, in pixels.

    Returns
    -------
    threshold_px : `float`
        The median plus ``jump_sigmas`` robust deviations (never less than the
        floor), as `step_summary` computes it.
    """
    return median_px + jump_sigmas * max(MAD_TO_SIGMA * mad_px, minimum_scale_px)


def step_limit_px(thresholds_px: Sequence[float], safety_factor: float) -> float:
    """Set the step limit above the longest real step.

    Parameters
    ----------
    thresholds_px : `Sequence` [`float`]
        The jump threshold of each stack.
    safety_factor : `float`
        How many times the longest threshold the limit is.

    Returns
    -------
    limit_px : `float`
        The longest threshold times the safety factor.
    """
    return safety_factor * max(thresholds_px)


def tune_registration_step_limit(inputs: TuningInputs) -> TuningResult:
    """Derive the longest plausible step from the stored step rows.

    Parameters
    ----------
    inputs : `TuningInputs`
        What the tuner may read. Needs the ``astrometrics`` service.

    Returns
    -------
    result : `TuningResult`
        The longest plausible step, resting on the stacks that have measured
        step rows.

    Raises
    ------
    TuningSkippedError
        If no stack of the camera has measured step rows.
    """
    inputs.service("astrometrics")
    medians = {
        (row.subject.subject_id, row.subject.variant): row.value
        for row, _ in camera_rows(
            inputs.configuration,
            inputs.setup.camera_name,
            STACKING_REGISTRATION_STEP_MEDIAN_PX,
            level=SubjectLevel.STACK,
        )
    }
    spreads = {
        (row.subject.subject_id, row.subject.variant): row.value
        for row, _ in camera_rows(
            inputs.configuration,
            inputs.setup.camera_name,
            STACKING_REGISTRATION_STEP_MAD_PX,
            level=SubjectLevel.STACK,
        )
    }
    jump_sigmas = resolve(STEP_JUMP_SIGMAS).value
    minimum_scale_px = resolve(MINIMUM_STEP_SCALE_PX).value
    thresholds = [
        jump_threshold_px(median, spreads[stack], jump_sigmas, minimum_scale_px)
        for stack, median in medians.items()
        if stack in spreads
    ]
    if not thresholds:
        raise TuningSkippedError(f"no stack of {inputs.setup.camera_name} has measured registration steps")
    safety_factor = resolve(STEP_LIMIT_SAFETY_FACTOR).value
    return TuningResult(
        values={MAXIMUM_PLAUSIBLE_STEP_PX.name: step_limit_px(thresholds, safety_factor)},
        sample=len(thresholds),
        source=(
            f"{safety_factor:g} times the longest jump threshold (median step plus {jump_sigmas:g} robust "
            f"deviations) of {len(thresholds)} stacks of {inputs.setup.camera_name} with measured steps"
        ),
    )


REGISTRATION_STEP_TUNER = Tuner(
    name="stacking.registration_step_limit",
    parameters=(MAXIMUM_PLAUSIBLE_STEP_PX,),
    method=(
        "Reads the stored median step and step spread of every stack of the setup's camera that has "
        "measured step rows, takes the longest step any of them would call a jump, and sets the limit a "
        "safety factor above it."
    ),
    tune=tune_registration_step_limit,
)
