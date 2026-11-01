"""Tests for how many stars one spectroscopy analysis covers by default.

The user removed the top-10 default on 2026-11-01, with the cap on the wing
fit's individually fitted neighbors.
"""

from astrometricslib.pipelines.spectroscopy import runner
from astrometricslib.pipelines.spectroscopy.runner import spectrum_star_limit


def test_a_run_that_names_no_limit_covers_every_star() -> None:
    """With no ``limit`` option there is no cap; the old constant is gone."""
    assert spectrum_star_limit({}) is None
    assert not hasattr(runner, "DEFAULT_SPECTRUM_STAR_LIMIT")


def test_a_caller_can_still_set_a_limit() -> None:
    """A number caps the count, and an explicit None asks for every star."""
    assert spectrum_star_limit({"limit": 25}) == 25
    assert spectrum_star_limit({"limit": None}) is None
