"""Tests for `HalfStacks` and `OddEvenResult` of the odd–even difference run.

A fake stacker stands in for Siril. The tests check that `HalfStacks` stacks
each set under a stacking slot with the shared reference frame and without
the edge trim, that it names the problem when the halves were lined up with
another reference, and that it deletes only its own folders; and that an
`OddEvenResult` with only a reason turns into rows that all say why.
"""

import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib.models.quality.measurement import MeasurementStatus
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.stacking import odd_even_difference_run
from astrometricslib.pipelines.stacking.odd_even_difference_run import (
    HalfStacks,
    OddEvenResult,
    odd_even_difference_measurements,
)

SHARED = "/frames/lights/M 99/f4.fits"


class _Slot:
    """A stacking slot that counts how often it is entered."""

    def __init__(self, entries: list[int]) -> None:
        """Remember where to count.

        Parameters
        ----------
        entries : `list` [`int`]
            Gets one item for each entry.
        """
        self.entries = entries

    def __enter__(self) -> _Slot:
        """Count one entry.

        Returns
        -------
        slot : `_Slot`
            This slot.
        """
        self.entries.append(1)
        return self

    def __exit__(self, *arguments: Any) -> None:
        """Leave the slot.

        Parameters
        ----------
        *arguments : `Any`
            The exception, if any. Nothing is suppressed.
        """


def _half_stacks(tmp_path: Path, entries: list[int]) -> HalfStacks:
    """Make a `HalfStacks` for a library stack inside ``tmp_path``.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The library folder.
    entries : `list` [`int`]
        Counts the slot entries.

    Returns
    -------
    half_stacks : `HalfStacks`
        Odd and even labels, no job.
    """
    return HalfStacks(
        target_id="M 99",
        library_stack_path=str(tmp_path / "M99_L_Stacked.fits"),
        shared_path=SHARED,
        labels={"odd": "M99_L_Stacked.half-odd", "even": "M99_L_Stacked.half-even"},
        configuration=None,
        drivers=None,
        stacking_slot=lambda: _Slot(entries),
        job_id="job-7",
    )


def test_each_set_is_stacked_under_a_slot_with_the_shared_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stacker gets the shared frame, no trim, the job and the path."""
    calls: list[dict[str, Any]] = []

    def fake_stack(target_id: str, frames: list[Any], output_path: str, **keywords: Any) -> Any:
        """Record the call and report the shared frame as the reference.

        Parameters
        ----------
        target_id : `str`
            The target.
        frames : `list`
            The frames.
        output_path : `str`
            Where the stack goes.
        **keywords : `Any`
            The other arguments.

        Returns
        -------
        outcome : `SimpleNamespace`
            A stack outcome with a path and the shared reference.
        """
        calls.append({"target": target_id, "frames": frames, "output": output_path, **keywords})
        return SimpleNamespace(stack_path=output_path, registration_reference_frame=SHARED)

    monkeypatch.setattr(odd_even_difference_run, "stack_frame_subset", fake_stack)
    entries: list[int] = []
    half_stacks = _half_stacks(tmp_path, entries)

    outcome = half_stacks.stack("odd", ("a", "b"))

    assert entries == [1] and half_stacks.stacked["odd"] is outcome
    assert calls[0]["frames"] == ["a", "b"]
    assert calls[0]["output"] == half_stacks.output_path("odd")
    assert os.path.join("_subsets", "M99_L_Stacked.half-odd") in calls[0]["output"]
    assert calls[0]["trim_edges"] is False and calls[0]["job_id"] == "job-7"
    assert calls[0]["reference_frame_path"] == SHARED
    assert half_stacks.made_stack("odd") and half_stacks.lined_up_on_shared("odd")


def test_the_reference_problem_names_what_went_wrong(tmp_path: Path) -> None:
    """Unknown, different and shared references give three answers."""
    half_stacks = _half_stacks(tmp_path, [])
    half_stacks.stacked = {
        "odd": SimpleNamespace(stack_path="x", registration_reference_frame=SHARED),
        "even": SimpleNamespace(stack_path=None, registration_reference_frame=SHARED),
    }
    assert half_stacks.reference_problem() == ""
    assert half_stacks.made_stack("odd") and not half_stacks.made_stack("even")

    half_stacks.stacked["even"].registration_reference_frame = "/frames/lights/M 99/f6.fits"
    problem = half_stacks.reference_problem()
    assert (
        "f4.fits and f6.fits" in problem and "not both with the library stack's reference f4.fits" in problem
    )
    assert not half_stacks.lined_up_on_shared("even")

    half_stacks.stacked["even"].registration_reference_frame = None
    assert "Siril's reference frame is unknown" in half_stacks.reference_problem()


def test_only_the_named_half_folders_are_deleted(tmp_path: Path) -> None:
    """A neighbor folder in ``_subsets`` is left alone."""
    half_stacks = _half_stacks(tmp_path, [])
    subsets = tmp_path / "_subsets"
    for name in ("M99_L_Stacked.half-odd", "M99_L_Stacked.half-even", "other"):
        (subsets / name).mkdir(parents=True)
        (subsets / name / "file.fits").write_text("x")

    half_stacks.delete_folders()
    half_stacks.delete_folders()  # a missing folder is no error

    assert [path.name for path in subsets.iterdir()] == ["other"]


def test_a_result_with_only_a_reason_gives_rows_that_all_say_why() -> None:
    """An early exit has only a reason; no row holds a number."""
    context = PipelineRunContext(
        "M 99", "stacking", "1.3.0", "run-1", "run-1", datetime(2026, 10, 23, tzinfo=UTC)
    )
    rows = odd_even_difference_measurements(
        context, context.stack_subject("M99_L_Stacked.fits"), OddEvenResult(reason="a spectral stack")
    )
    assert rows
    assert all(row.status == MeasurementStatus.NOT_MEASURED for row in rows)
    assert all("a spectral stack" in row.reason for row in rows)
