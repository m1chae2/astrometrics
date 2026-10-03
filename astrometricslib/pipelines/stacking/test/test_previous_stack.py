"""Tests for keeping the stack that a restack replaced.

They use real files in a temporary folder. A stack is its FITS file plus the
companion files made with it; the module moves them together.
"""

from pathlib import Path

from astrometricslib.pipelines.shared.previous_stack_path import PREVIOUS_STACK_FOLDER_NAME
from astrometricslib.pipelines.stacking.post_processing import previous_stack

STACK_NAME = "M_27_L_Stacked.fits"
COMPANIONS = ("_RejMap.fits", "_Registration.seq", "_preview.jpg", "_processed.fits")


def write_stack(folder: Path, label: str, name: str = STACK_NAME) -> Path:
    """Write a stack and all its companion files, each holding `label`.

    Returns
    -------
    stack : `pathlib.Path`
        The stack's FITS file.
    """
    folder.mkdir(parents=True, exist_ok=True)
    stack = folder / name
    stack.write_text(label)
    stem = name.removesuffix(".fits")
    for suffix in COMPANIONS:
        (folder / f"{stem}{suffix}").write_text(label)
    return stack


def test_archive_moves_the_stack_and_its_companions_into_staging(tmp_path: Path) -> None:
    """Every file of the stack moves, and files of other stacks stay."""
    stack = write_stack(tmp_path, "old")
    other = write_stack(tmp_path, "other", name="M_27_SPEC_Stacked.fits")

    staging = previous_stack.archive_current_stack(str(stack))

    assert staging is not None
    assert sorted(p.name for p in Path(staging).iterdir()) == sorted(
        [STACK_NAME] + [STACK_NAME.removesuffix(".fits") + s for s in COMPANIONS]
    )
    assert not stack.exists()
    assert other.exists()


def test_archive_without_a_stack_does_nothing(tmp_path: Path) -> None:
    """A first stack has nothing to keep."""
    assert previous_stack.archive_current_stack(str(tmp_path / STACK_NAME)) is None
    assert not (tmp_path / PREVIOUS_STACK_FOLDER_NAME).exists()


def test_commit_replaces_the_older_previous_stack(tmp_path: Path) -> None:
    """After two restacks only the most recent old stack is kept."""
    stack = write_stack(tmp_path, "first")
    staging = previous_stack.archive_current_stack(str(stack))
    write_stack(tmp_path, "second")
    previous_stack.commit_archive(str(stack), staging)

    staging = previous_stack.archive_current_stack(str(stack))
    write_stack(tmp_path, "third")
    previous_stack.commit_archive(str(stack), staging)

    kept = previous_stack.previous_stack_path(str(stack))
    assert kept is not None
    assert Path(kept).read_text() == "second"
    assert stack.read_text() == "third"
    assert not (tmp_path / "_previous.staging").exists()


def test_rollback_restores_the_stack_and_keeps_the_older_previous(tmp_path: Path) -> None:
    """A failed restack leaves the stack and the previous one as they were."""
    stack = write_stack(tmp_path, "first")
    previous_stack.commit_archive(str(stack), previous_stack.archive_current_stack(str(stack)))
    write_stack(tmp_path, "second")
    staging = previous_stack.archive_current_stack(str(stack))
    (tmp_path / STACK_NAME).write_text("half-written")

    previous_stack.rollback_archive(str(stack), staging)

    assert stack.read_text() == "second"
    assert Path(previous_stack.previous_stack_path(str(stack))).read_text() == "first"
    assert not Path(staging).exists()


def test_discard_deletes_only_the_previous_stack(tmp_path: Path) -> None:
    """Discarding removes the `_previous` folder and reports its files."""
    stack = write_stack(tmp_path, "old")
    previous_stack.commit_archive(str(stack), previous_stack.archive_current_stack(str(stack)))
    write_stack(tmp_path, "new")

    removed = previous_stack.discard_previous_stack(str(stack))

    assert len(removed) == 1 + len(COMPANIONS)
    assert previous_stack.previous_stack_path(str(stack)) is None
    assert stack.read_text() == "new"
    assert previous_stack.discard_previous_stack(str(stack)) == []


def test_swap_exchanges_the_current_and_previous_stacks_and_can_be_undone(tmp_path: Path) -> None:
    """Swapping twice returns to where it started, and no file is lost."""
    stack = write_stack(tmp_path, "old")
    previous_stack.commit_archive(str(stack), previous_stack.archive_current_stack(str(stack)))
    write_stack(tmp_path, "new")

    restored = previous_stack.swap_with_previous_stack(str(stack))

    assert len(restored) == 1 + len(COMPANIONS)
    assert stack.read_text() == "old"
    assert Path(previous_stack.previous_stack_path(str(stack))).read_text() == "new"
    previous_stack.swap_with_previous_stack(str(stack))
    assert stack.read_text() == "new"
    assert Path(previous_stack.previous_stack_path(str(stack))).read_text() == "old"


def test_swap_without_a_previous_stack_moves_nothing(tmp_path: Path) -> None:
    """With nothing kept, the current stack is left alone."""
    stack = write_stack(tmp_path, "only")

    assert previous_stack.swap_with_previous_stack(str(stack)) == []
    assert stack.read_text() == "only"
