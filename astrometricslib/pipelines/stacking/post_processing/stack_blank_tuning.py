"""Tuner for the limits that flag a blank or over-subtracted stack.

A stack that had more subtracted than the sky it held is mostly exact zeros,
and Siril prints a warning about negative pixels after the dark is taken off.
`stack_quality.py` flags a stack above three limits (register entry STK-26):
the share of zero pixels in an imaging stack, the share in a spectral stack
(which is much lower for a clean one), and the percent of negative pixels.

The register entry places each limit in the gap between the clean stacks and
the broken ones of the library. This module does the same on the stored rows
of the setup's camera: it sorts the stored values, finds the widest empty
stretch between neighboring values, and puts the limit in the middle of it.
A library with no such gap (no broken stack, or no clean one) has no place
for a limit, so the tuner leaves the defaults alone.
"""

from collections.abc import Mapping, Sequence

from astrometricslib._internal.drivers.tuning_support import camera_rows, widest_gap
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
    STACKING_NEGATIVE_PIXEL_MAX_FRACTION,
    STACKING_ZERO_PIXEL_FRACTION,
)
from astrometricslib.pipelines.stacking.post_processing.stack_quality import (
    DEFAULT_NEGATIVE_PIXEL_FLAG_PERCENT,
    DEFAULT_ZERO_FRACTION_FLAG_THRESHOLD,
    SPECTRAL_ZERO_FRACTION_FLAG_THRESHOLD,
)

# The clean stacks and the broken ones must be at least this far apart, as a
# share of the pixels, for the gap between them to count as a gap. The evidence
# (STK-26) has gaps of 0.32 (spectral, 0.004 to 0.32) and 0.59 (imaging, 0.39
# to 0.98); the scatter of clean stacks is a few percent. A tenth keeps the
# scatter of the clean stacks from being read as a gap.
MINIMUM_BLANK_STACK_GAP = TunedParameter(
    name="stacking.blank_stack_minimum_gap",
    default=0.1,
    unit="fraction",
    description="Smallest share of pixels between clean and broken stacks for a limit to be placed there.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-26",
)

PERCENT_PER_FRACTION = 100


def limit_in_widest_gap(values: Sequence[float], minimum_gap: float) -> float | None:
    """Place a limit in the middle of the widest gap between stored values.

    Parameters
    ----------
    values : `Sequence` [`float`]
        The stored value of each stack, such as its share of zero pixels.
    minimum_gap : `float`
        The smallest gap that counts.

    Returns
    -------
    limit : `float` or `None`
        The middle of the widest gap, or `None` when no gap is that wide (the
        values do not split into a clean group and a broken one).
    """
    gap = widest_gap(values)
    if gap is None or gap[1] - gap[0] < minimum_gap:
        return None
    return (gap[0] + gap[1]) / 2


def negative_pixel_fractions(
    stacks: Sequence[tuple[str, str | None]], negative_by_stack: Mapping[tuple[str, str | None], float]
) -> list[float]:
    """Give every stack its share of negative pixels.

    Siril prints the warning only for a stack that has many negative pixels,
    so a stack with no stored row is a clean one.

    Parameters
    ----------
    stacks : `Sequence` [`tuple` [`str`, `str` or `None`]]
        Every stack, as its subject id and its subset label.
    negative_by_stack : `Mapping`
        The stored share of negative pixels, for the stacks that have one.

    Returns
    -------
    fractions : `list` [`float`]
        One share for each stack.
    """
    return [negative_by_stack.get(stack, 0.0) for stack in stacks]


def tune_blank_stack_limits(inputs: TuningInputs) -> TuningResult:
    """Derive the zero pixel and negative pixel limits.

    Parameters
    ----------
    inputs : `TuningInputs`
        What the tuner may read. Needs the ``astrometrics`` service.

    Returns
    -------
    result : `TuningResult`
        Each limit that the stored stacks of the camera can place, with the
        number of stacks the smallest of them rests on.

    Raises
    ------
    TuningSkippedError
        If no limit can be placed.
    """
    inputs.service("astrometrics")
    minimum_gap = resolve(MINIMUM_BLANK_STACK_GAP).value
    zero_rows = camera_rows(
        inputs.configuration, inputs.setup.camera_name, STACKING_ZERO_PIXEL_FRACTION, level=SubjectLevel.STACK
    )
    negative_rows = camera_rows(
        inputs.configuration,
        inputs.setup.camera_name,
        STACKING_NEGATIVE_PIXEL_MAX_FRACTION,
        level=SubjectLevel.STACK,
    )
    values: dict[str, float] = {}
    samples: list[int] = []
    for parameter, wants_spectral in (
        (DEFAULT_ZERO_FRACTION_FLAG_THRESHOLD, False),
        (SPECTRAL_ZERO_FRACTION_FLAG_THRESHOLD, True),
    ):
        fractions = [row.value for row, setting in zero_rows if setting.is_spectral == wants_spectral]
        limit = limit_in_widest_gap(fractions, minimum_gap) if fractions else None
        if limit is not None and len(fractions) >= (parameter.minimum_sample or 1):
            values[parameter.name] = limit
            samples.append(len(fractions))
    stacks = [(row.subject.subject_id, row.subject.variant) for row, _ in zero_rows]
    negative_by_stack = {(row.subject.subject_id, row.subject.variant): row.value for row, _ in negative_rows}
    percents = [
        fraction * PERCENT_PER_FRACTION for fraction in negative_pixel_fractions(stacks, negative_by_stack)
    ]
    percent_limit = limit_in_widest_gap(percents, minimum_gap * PERCENT_PER_FRACTION) if percents else None
    if percent_limit is not None and len(percents) >= (
        DEFAULT_NEGATIVE_PIXEL_FLAG_PERCENT.minimum_sample or 1
    ):
        values[DEFAULT_NEGATIVE_PIXEL_FLAG_PERCENT.name] = round(percent_limit)
        samples.append(len(percents))
    if not values:
        raise TuningSkippedError(
            f"the stored stacks of {inputs.setup.camera_name} do not split into clean and broken groups "
            "(no gap of the minimum width, or too few stacks)"
        )
    return TuningResult(
        values=values,
        sample=min(samples),
        source=(
            f"Middle of the widest gap between clean and broken stacks, from the stored zero and negative "
            f"pixel shares of {len(zero_rows)} stacks of {inputs.setup.camera_name}"
        ),
    )


BLANK_STACK_TUNER = Tuner(
    name="stacking.blank_stack_limits",
    parameters=(
        DEFAULT_ZERO_FRACTION_FLAG_THRESHOLD,
        SPECTRAL_ZERO_FRACTION_FLAG_THRESHOLD,
        DEFAULT_NEGATIVE_PIXEL_FLAG_PERCENT,
    ),
    method=(
        "Reads the stored zero-pixel share (imaging and spectral stacks apart) and the stored negative-pixel "
        "share of every stack of the setup's camera, and puts each limit in the middle of the widest gap "
        "between the clean and the broken stacks."
    ),
    tune=tune_blank_stack_limits,
)
