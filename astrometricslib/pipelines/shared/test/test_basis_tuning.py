"""Tests for the typical star and trail width tuners (#175, Phase E3).

The star width keeps one newest width per stack, only for the setup's optic
(by name, or by focal length within the tolerance).
"""

from types import SimpleNamespace

from astrometricslib.pipelines.shared.basis_tuning import (
    widths_of_optic,
)


def _row(stack: str, width: float, telescope: str | None, focal_length_mm: float | None) -> tuple:
    """Build one stored stack-width row and its capture setting.

    Returns
    -------
    row : `tuple`
        ``(measurement, capture setting)``.
    """
    measurement = SimpleNamespace(subject=SimpleNamespace(subject_id=stack), value=width)
    setting = SimpleNamespace(telescope=telescope, focal_length_mm=focal_length_mm)
    return measurement, setting


def test_one_newest_width_per_stack_of_the_setup_optic() -> None:
    """Rows of another optic are left out, and a stack's newest row wins."""
    rows = [
        _row("a", 2.0, "Refractor", 405.0),
        _row("a", 2.5, "Refractor", 405.0),
        _row("b", 3.0, None, 406.0),
        _row("c", 9.0, "Lens", 300.0),
    ]
    widths = widths_of_optic(rows, "Refractor", 405.0, 0.02)
    assert sorted(widths) == [2.5, 3.0]
