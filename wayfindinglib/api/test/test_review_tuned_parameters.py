"""Tests for `ObservationReview.measure_tuned_parameters` (#175).

The call is a thin wrapper of `astrometricslib.parameters.tune`: it asks for
the wayfinding tuners, gives them the owning `Wayfinder` and its
`Astrometrics`, and saves nothing unless asked.
"""

from types import SimpleNamespace
from typing import Any

import pytest

import astrometricslib.parameters as parameters
from astrometricslib.exceptions import InvalidArgumentError, NotFoundError
from wayfindinglib.api.review import ObservationReview


def _review(active: bool = True) -> ObservationReview:
    """Build a review over a stand-in context and owner.

    Parameters
    ----------
    active : `bool`, optional
        Whether the config has an active setup.

    Returns
    -------
    review : `ObservationReview`
        A review whose owner is a stand-in `Wayfinder`.
    """
    setup = SimpleNamespace(name="Scope on Camera")
    setups = SimpleNamespace(
        setup_named=lambda name: setup if name == setup.name else None,
        active_setup=lambda: setup if active else None,
    )
    review = object.__new__(ObservationReview)
    review._context = SimpleNamespace(config=SimpleNamespace(get_observatory_setups=lambda: setups))
    owner = SimpleNamespace(astrometrics="astrometrics")
    review._owner_provider = lambda: owner
    return review


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace the one tuning entry point with a stand-in that records calls.

    Returns
    -------
    calls : `list` [`dict`]
        One entry per tuning run.
    """
    seen: list[dict[str, Any]] = []

    def fake_tune(packages: tuple[str, ...], configuration: Any, **keywords: Any) -> str:
        """Record what the entry point was given.

        Returns
        -------
        report : `str`
            A stand-in report.
        """
        seen.append({"packages": packages, "configuration": configuration, **keywords})
        return "report"

    monkeypatch.setattr(parameters, "tune", fake_tune)
    return seen


def test_runs_the_wayfinding_tuners_without_saving_and_passes_both_services(
    calls: list[dict[str, Any]],
) -> None:
    """The wrapper asks the core for the wayfinding tuners, both services."""
    assert _review().measure_tuned_parameters() == "report"
    assert calls[0]["packages"] == ("wayfindinglib",)
    assert calls[0]["setup"] is None
    assert calls[0]["only"] is None
    assert calls[0]["save"] is False
    assert calls[0]["services"]["astrometrics"] == "astrometrics"
    assert set(calls[0]["services"]) == {"wayfinder", "astrometrics"}


def test_setup_only_and_save_are_passed_on(calls: list[dict[str, Any]]) -> None:
    """The setup, `only` and `save` reach the tuning core unchanged."""
    _review().measure_tuned_parameters("Scope on Camera", only=["wayfinding.second"], save=True)
    assert calls[0]["setup"] == "Scope on Camera"
    assert calls[0]["only"] == ["wayfinding.second"]
    assert calls[0]["save"] is True


def test_unknown_tuners_and_setups_are_refused_by_the_core() -> None:
    """The core's checks apply: an unknown tuner or setup raises."""
    with pytest.raises(InvalidArgumentError):
        _review().measure_tuned_parameters(only=["wayfinding.missing"])
    with pytest.raises(NotFoundError):
        _review().measure_tuned_parameters("Other setup")
    with pytest.raises(NotFoundError):
        _review(active=False).measure_tuned_parameters()
