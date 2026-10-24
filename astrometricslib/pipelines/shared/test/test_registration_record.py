"""Tests for the registration record kept beside a stack.

The stacking run writes the record and the astrometry run reads it, so the
tests write one, read it back and then break it in the ways a file on disk can
be broken.
"""

from pathlib import Path

from astrometricslib.pipelines.shared.registration_record import (
    REGISTRATION_RECORD_SUFFIX,
    RegistrationRecord,
    build_registration_record,
    read_registration_record,
    registration_record_path,
    write_registration_record,
)

IDENTITY = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]


def _diagnostics(count: int = 3) -> dict:
    """Build the registration results of a run.

    Parameters
    ----------
    count : `int`, optional
        How many frames.

    Returns
    -------
    diagnostics : `dict`
        The three keys the record reads.
    """
    return {
        "symlinked_light_paths": [f"/frames/f{index}.fits" for index in range(count)],
        "registration_included": [True] * (count - 1) + [False],
        "registration_frames": [{"homography": IDENTITY} for _ in range(count)],
    }


def test_the_record_is_named_after_the_stack() -> None:
    """The record sits beside the stack with the agreed suffix."""
    assert registration_record_path("/stacks/M_57_L_Stacked.fits") == (
        "/stacks/M_57_L_Stacked" + REGISTRATION_RECORD_SUFFIX
    )


def test_a_record_written_beside_a_stack_is_read_back_unchanged(tmp_path: Path) -> None:
    """Paths, transforms, flags, the crop and the reference all survive."""
    record = build_registration_record(_diagnostics(), (12, 34), "/frames/f1.fits", is_spectral=True)
    assert record is not None
    stack = str(tmp_path / "M99_L_Stacked.fits")

    write_registration_record(stack, record)

    assert read_registration_record(stack) == record
    assert record.included == (True, True, False)
    assert record.crop_offset == (12, 34)
    assert record.is_spectral


def test_lists_that_differ_in_length_give_no_record() -> None:
    """A position must name the same frame in every list."""
    diagnostics = _diagnostics()
    diagnostics["registration_frames"] = diagnostics["registration_frames"][:2]
    assert build_registration_record(diagnostics, (0, 0), None) is None
    assert build_registration_record({}, (0, 0), None) is None


def test_missing_inclusion_flags_mean_every_frame_was_included() -> None:
    """A run that reported no flags is read as all included."""
    diagnostics = _diagnostics()
    diagnostics["registration_included"] = []
    record = build_registration_record(diagnostics, (0, 0), None)
    assert record is not None
    assert record.included == (True, True, True)


def test_a_missing_damaged_or_foreign_file_reads_as_no_record(tmp_path: Path) -> None:
    """Every way the file can be wrong gives `None`, never an error."""
    stack = str(tmp_path / "M99_L_Stacked.fits")
    path = Path(registration_record_path(stack))
    assert read_registration_record(stack) is None
    path.write_text("{not json", encoding="utf-8")
    assert read_registration_record(stack) is None
    path.write_text('{"version": 99}', encoding="utf-8")
    assert read_registration_record(stack) is None
    path.write_text(
        '{"version": 1, "light_paths": ["a"], "homographies": [[1.0]], "included": [true],'
        ' "crop_offset": [0, 0], "reference_frame": null, "is_spectral": false}',
        encoding="utf-8",
    )
    assert read_registration_record(stack) is None


def test_a_write_to_a_missing_folder_is_logged_not_raised(tmp_path: Path) -> None:
    """The record is a by-product: a failed write never fails the stack."""
    record = RegistrationRecord(("a",), (tuple(IDENTITY),), (True,), (0, 0), None)
    write_registration_record(str(tmp_path / "no_such_folder" / "stack.fits"), record)
