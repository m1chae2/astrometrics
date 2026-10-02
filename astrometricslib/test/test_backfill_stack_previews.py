"""Tests for the choices the preview backfill script makes.

Only the selection and planning code is tested here. Making a preview runs
Siril and the AI tools, which `test_stack_preview.py` covers with stand-ins.
"""

import os
from pathlib import Path
from types import SimpleNamespace

from astrometricslib.scripts.backfill_stack_previews import (
    preview_is_current,
    select_targets,
    stacks_of,
)


def _target(name: str, cameras: list[str], stacked: str = "", spectral: str = "") -> SimpleNamespace:
    """Make a target stand-in.

    Returns
    -------
    target : `types.SimpleNamespace`
        A target with the given name, frames from the given cameras, and
        the given stack paths.
    """
    return SimpleNamespace(
        id=name,
        frames=[SimpleNamespace(camera=camera) for camera in cameras],
        stacking=SimpleNamespace(stacked_image=stacked),
        spectral_stacking=SimpleNamespace(stacked_image=spectral),
    )


def test_targets_are_selected_by_name_and_by_camera() -> None:
    """Verify each filter narrows the list and the filters combine."""
    targets = [
        _target("M 27", ["ZWO ASI 533MM Pro"]),
        _target("M 31", ["Nikon DSLR DSC D5300"]),
        _target("M 57", ["Nikon DSLR DSC D5300", "ZWO ASI 533MM Pro"]),
    ]

    assert [t.id for t in select_targets(targets, ["M 31"], None)] == ["M 31"]
    assert [t.id for t in select_targets(targets, None, "asi 533mm")] == ["M 27", "M 57"]
    assert [t.id for t in select_targets(targets, ["M 57"], "asi 533mm")] == ["M 57"]


def test_only_stacks_that_exist_are_listed(tmp_path: Path) -> None:
    """Verify missing, blank and unwanted stacks are left out."""
    imaging = tmp_path / "M_27_L_Stacked.fits"
    imaging.write_bytes(b"x")
    target = _target("M 27", [], stacked=str(imaging), spectral=str(tmp_path / "gone.fits"))

    assert stacks_of(target, "imaging") == [(False, str(imaging))]
    assert stacks_of(target, "spectral") == []
    assert stacks_of(target, "both") == [(False, str(imaging))]


def test_a_preview_is_current_only_if_it_is_not_older_than_the_stack(tmp_path: Path) -> None:
    """Verify the freshness check."""
    stack = tmp_path / "S_Stacked.fits"
    stack.write_bytes(b"x")
    preview = tmp_path / "S_Stacked_preview.jpg"
    assert not preview_is_current(str(stack))

    preview.write_bytes(b"j")
    os.utime(preview, (stack.stat().st_mtime + 5, stack.stat().st_mtime + 5))
    assert preview_is_current(str(stack))

    os.utime(preview, (stack.stat().st_mtime - 5, stack.stat().st_mtime - 5))
    assert not preview_is_current(str(stack))
