"""Tests for the typical star and trail width tuners (#175, Phase E3).

The star width keeps one newest width per stack, only for the setup's optic
(by name, or by focal length within the tolerance). The trail width turns each
spectrum's median streak sigma into a FWHM and takes the median over spectra.
Both tuners skip when there is nothing to read.
"""

from types import SimpleNamespace

import pytest

from astrometricslib.foundation.observatory_setups import SetupConfig
from astrometricslib.foundation.tuning import TuningInputs, TuningSkippedError
from astrometricslib.pipelines.shared.basis_tuning import (
    median_trail_width_fwhm,
    stack_widths_of_optic,
    tune_typical_trail_width,
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
    widths = stack_widths_of_optic(rows, "Refractor", 405.0, 0.02)
    assert sorted(widths) == [2.5, 3.0]


def test_streak_sigmas_become_one_typical_fwhm() -> None:
    """The median over spectra of each median sigma, times 2.355."""
    assert median_trail_width_fwhm([[1.0, 2.0, 3.0], [2.0], [0.0, -1.0]]) == pytest.approx(2.355 * 2.0)
    assert median_trail_width_fwhm([[0.0]]) is None


def test_the_trail_tuner_skips_a_filter_that_is_not_the_grating() -> None:
    """An imaging filter has no streaks to read."""
    setup = SetupConfig(name="Mono on Refractor", camera_name="Mono Camera", optic_name="Refractor")
    with pytest.raises(TuningSkippedError):
        tune_typical_trail_width(TuningInputs(setup, "L", configuration=None))
