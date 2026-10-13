"""Purpose: Tests for the equipment section rename script."""

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "rename_equipment_sections", Path(__file__).parent.parent / "rename_equipment_sections.py"
)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_old_headers_are_renamed_and_current_ones_are_kept() -> None:
    """Old headers change. Current headers and keys stay."""
    text = "[Telescope]\nfocal_length_mm = 400\n[Camera.Main]\nx = 1\n[Observatory.Camera]\ny = 2\n"
    new_text, count = _MODULE.rename_sections(text)
    assert count == 2
    assert new_text == (
        "[Observatory.Telescope]\nfocal_length_mm = 400\n"
        "[Observatory.Camera.Main]\nx = 1\n[Observatory.Camera]\ny = 2\n"
    )
