"""Purpose: Measure a stack file or a target's stack, as a job.

Description: `measure_stack` is the owner behind
``astrometrics.quality.stacks.measure``. It chooses the stack file (a path,
a target's main imaging stack, or another stack of the target), works out
whether it is a spectral stack, records a ``diagnostics`` job and calls
`stack_quality_report`. `read_aperture_record` reads the per-aperture noise
record the odd-even difference job wrote beside a stack. Nothing here writes
a file.
"""

import os
from collections.abc import Collection

from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib.foundation.arguments import check_include
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.quality.aperture_record import OddEvenApertureRecord
from astrometricslib.models.quality.quality_reports import StackQualityReport
from astrometricslib.models.target import Target
from astrometricslib.pipelines.stacking.post_processing.stack_quality_report import (
    STACK_QUALITY_SECTIONS,
    stack_path_of,
    stack_quality_report,
)

__all__ = ["STACK_FILE_SUFFIXES", "measure_stack", "read_aperture_record"]

STACK_FILE_SUFFIXES = (".fit", ".fits", ".fts")
"""The file name endings of a stack file."""


def _same_file(first: str | None, second: str) -> bool:
    """Say whether two paths name the same file.

    Returns
    -------
    same : `bool`
        `True` when ``first`` is given and names the file ``second`` names.
    """
    return bool(first) and os.path.normcase(os.path.realpath(str(first))) == os.path.normcase(
        os.path.realpath(second)
    )


def measure_stack(
    subject: Target | str,
    *,
    stack: str | os.PathLike[str] | None = None,
    include: Collection[str] | None = None,
    compare_to: str | None = None,
    drivers: Drivers | None = None,
) -> StackQualityReport:
    """Measure a stack, and optionally compare it with another stack.

    Parameters
    ----------
    subject : `Target` or `str`
        A target, or the path of a stack file (ending in ``.fit``,
        ``.fits`` or ``.fts``).
    stack : `str`, optional
        For a target: the path of one of its stacks, such as its spectral
        stack. The target's main imaging stack when left out.
    include : `Collection` [`str`], optional
        The sections to measure, from `STACK_QUALITY_SECTIONS`.
    compare_to : `str`, optional
        ``"previous"`` or the path of a stack to compare with.
    drivers : `Drivers`, optional
        The outside services, for any plate solve.

    Returns
    -------
    report : `StackQualityReport`
        The measurements asked for and the comparison.

    Raises
    ------
    InvalidArgumentError
        If a section is unknown, the subject is neither a `Target` nor a
        stack path, or ``stack`` is given with a path.
    """
    from astrometricslib.foundation.jobs.runner import registered_job

    sections = check_include(include, STACK_QUALITY_SECTIONS)
    if isinstance(subject, Target):
        target_id: str | None = subject.id
        if stack is None:
            stack_path = stack_path_of(subject, "imaging")
        else:
            stack_path = os.fspath(stack)
        is_spectral = _same_file(subject.spectral_stacking.stacked_image, stack_path)
    elif isinstance(subject, str | os.PathLike) and os.fspath(subject).lower().endswith(STACK_FILE_SUFFIXES):
        if stack is not None:
            raise InvalidArgumentError(
                "stack chooses one of a target's stacks; give it with a Target, not a path."
            )
        stack_path, target_id, is_spectral = os.fspath(subject), None, False
    else:
        raise InvalidArgumentError(
            "Give a Target (turn an id into one with targets.get(id)) or the path of a stack file."
        )
    with registered_job(job_type="diagnostics", target_id=target_id or stack_path):
        return stack_quality_report(stack_path, target_id, sections, compare_to, is_spectral, drivers)


def read_aperture_record(path: str) -> OddEvenApertureRecord | None:
    """Read the per-aperture noise record that a stack's job wrote.

    The owner of ``astrometrics.quality.stacks.get_aperture_record``.

    Parameters
    ----------
    path : `str`
        The record file.

    Returns
    -------
    record : `OddEvenApertureRecord` or `None`
        The file's content. `None` when the file is missing, cannot be read,
        is of another version or does not have the expected fields.
    """
    from pydantic import ValidationError

    from astrometricslib.pipelines.stacking.post_processing.odd_even_apertures import (
        load_odd_even_aperture_record,
    )

    data = load_odd_even_aperture_record(path)
    if data is None:
        return None
    try:
        return OddEvenApertureRecord.model_validate(data)
    except ValidationError:
        return None
