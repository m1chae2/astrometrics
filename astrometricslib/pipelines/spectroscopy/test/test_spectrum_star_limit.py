"""Tests for how many stars one spectroscopy analysis covers by default."""

from astrometricslib.pipelines.spectroscopy.runner import DEFAULT_SPECTRUM_STAR_LIMIT, spectrum_star_limit


def test_the_default_is_the_stopgap_limit() -> None:
    """A run that names no limit covers the default number of stars."""
    assert spectrum_star_limit({}) == DEFAULT_SPECTRUM_STAR_LIMIT == 10


def test_a_caller_can_set_a_limit_or_ask_for_every_star() -> None:
    """A number sets the count, and an explicit None asks for every star."""
    assert spectrum_star_limit({"limit": 25}) == 25
    assert spectrum_star_limit({"limit": None}) is None
