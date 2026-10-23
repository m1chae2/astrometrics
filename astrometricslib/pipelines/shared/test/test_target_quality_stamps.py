"""Tests for saving only the frames' measurement stamps to the stored target.

The frame measurement job saves its stamps while it runs. The save must copy
the stamps onto the stored target and leave everything else as stored, so a
long run never overwrites a change made elsewhere in the meantime.
"""

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.target_records import merge_quality_stamps, save_quality_stamps


def _frame(path: str, size: int | None = None, modified_ns: int | None = None, **fields: Any) -> FrameRecord:
    """Build a frame record with an optional stamp.

    Returns
    -------
    frame : `FrameRecord`
        The record.
    """
    frame = FrameRecord(path=path, **fields)
    frame.quality_measured_file_size = size
    frame.quality_measured_file_modified_ns = modified_ns
    return frame


def test_only_the_stamps_are_copied_onto_the_stored_target() -> None:
    """Everything else in the stored target stays as it was stored."""
    stored = Target(
        id="M 99",
        frames=[_frame("/a.fits", camera="Stored Camera"), _frame("/b.fits"), _frame("/new_elsewhere.fits")],
    )
    in_memory = Target(
        id="M 99",
        frames=[
            _frame("/a.fits", 10, 111, camera="Stale Camera"),
            _frame("/b.fits"),
            _frame("/gone.fits", 5, 6),
        ],
    )

    merged = merge_quality_stamps(stored, in_memory)

    assert merged is stored
    by_path = {frame.path: frame for frame in merged.frames}
    assert by_path["/a.fits"].quality_measured_file_size == 10
    assert by_path["/a.fits"].quality_measured_file_modified_ns == 111
    # Not overwritten by the in-memory copy's stale value.
    assert by_path["/a.fits"].camera == "Stored Camera"
    assert by_path["/b.fits"].quality_measured_file_size is None
    # A frame only the stored target holds is kept; one only memory holds
    # is not added.
    assert "/new_elsewhere.fits" in by_path
    assert "/gone.fits" not in by_path


def test_an_empty_stamp_in_memory_leaves_the_stored_stamp_alone() -> None:
    """A frame not measured in this run keeps the stamp it already has."""
    stored = Target(id="M 99", frames=[_frame("/a.fits", 7, 8)])
    in_memory = Target(id="M 99", frames=[_frame("/a.fits")])

    merged = merge_quality_stamps(stored, in_memory)

    assert merged.frames[0].quality_measured_file_size == 7


def test_with_nothing_stored_the_in_memory_target_is_the_result() -> None:
    """There is nothing to overwrite, so the target is saved as it is."""
    in_memory = Target(id="M 99", frames=[_frame("/a.fits", 1, 2)])

    assert merge_quality_stamps(None, in_memory) is in_memory


def test_the_save_goes_through_the_locked_merge_of_the_catalog() -> None:
    """The save hands the merge function to the catalog's own merge call."""
    calls: list[tuple[str, list, Callable]] = []
    catalog_access = SimpleNamespace(
        merge_and_record=lambda dataset, objects, merge: calls.append((dataset, objects, merge))
    )
    target = Target(id="M 99", frames=[_frame("/a.fits", 1, 2)])

    save_quality_stamps(SimpleNamespace(catalog_access=catalog_access), target)

    assert calls == [("target_catalog", [target], merge_quality_stamps)]
