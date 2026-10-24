"""Purpose: Prove the per-night analyses read no science-library verdict.

Description: The capture, guiding and sky analyses, and the Control tasks that
feed them, must judge from stored measurements and their own limits. They
must not read a verdict that `astrometricslib` computed: a stacking summary's
exposure groups or recommended exposure, a quality summary, or a frame
record's `measurements` object. This test searches their source for those
names, so a new read fails here.
"""

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_FOLDERS = ("session_analysis", "tasks/control_tasks", "analytics")
_FORBIDDEN = (
    "quality_summary",
    "exposure_groups",
    "stacking_metrics",
    ".measurements",
    "registration_fwhm",
    "registration_roundness",
)
"""Names only a read of a science-library verdict or frame scan uses."""


def _sources() -> list[Path]:
    """List the source files under the checked folders (tests excluded).

    Returns
    -------
    paths : `list` [`Path`]
        Every non-test Python file.
    """
    return [
        path
        for folder in _FOLDERS
        for path in (_ROOT / folder).rglob("*.py")
        if "/test/" not in path.as_posix() and not path.name.startswith("test_")
    ]


@pytest.mark.parametrize("name", _FORBIDDEN)
def test_no_source_reads_a_science_library_verdict(name: str) -> None:
    """Verify the forbidden name appears in no checked source file."""
    code_lines = [
        f"{path.relative_to(_ROOT)}: {line.strip()}"
        for path in _sources()
        for line in path.read_text(encoding="utf-8").splitlines()
        if name in line and not line.strip().startswith(("#", '"', "`", "-"))
    ]

    assert code_lines == []
