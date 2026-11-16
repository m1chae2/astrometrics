"""Tests for `ObservationReview.measure_tuned_parameters` (#175).

The call runs the wayfinding tuners for one setup, gives them the owning
`Wayfinder` and its `Astrometrics`, and saves nothing unless asked.
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
    """Replace the tuners and the tuning run with stand-ins that record calls.

    Returns
    -------
    calls : `list` [`dict`]
        One entry per tuning run.
    """
    seen: list[dict[str, Any]] = []
    tuners = [SimpleNamespace(name="wayfinding.first"), SimpleNamespace(name="wayfinding.second")]
    monkeypatch.setattr(parameters, "collect_tuners", lambda packages: tuners)

    def fake_run(tuners: list, setup: Any, configuration: Any, **keywords: Any) -> str:
        """Record what the run was given.

        Returns
        -------
        report : `str`
            A stand-in report.
        """
        seen.append({"tuners": [tuner.name for tuner in tuners], "setup": setup.name, **keywords})
        return "report"

    monkeypatch.setattr(parameters, "run_tuning", fake_run)
    return seen


def test_runs_every_tuner_without_saving_and_passes_both_services(calls: list[dict[str, Any]]) -> None:
    """All tuners run on the active setup with the owner and its library."""
    assert _review().measure_tuned_parameters() == "report"
    assert calls[0]["tuners"] == ["wayfinding.first", "wayfinding.second"]
    assert calls[0]["write"] is False
    assert calls[0]["services"]["astrometrics"] == "astrometrics"
    assert set(calls[0]["services"]) == {"wayfinder", "astrometrics"}


def test_only_and_save_are_passed_on(calls: list[dict[str, Any]]) -> None:
    """`only` picks tuners by name and `save` writes."""
    _review().measure_tuned_parameters("Scope on Camera", only=["wayfinding.second"], save=True)
    assert calls[0]["tuners"] == ["wayfinding.second"]
    assert calls[0]["write"] is True


def test_unknown_tuners_and_setups_are_refused(calls: list[dict[str, Any]]) -> None:
    """An unknown tuner or setup raises before anything runs."""
    with pytest.raises(InvalidArgumentError):
        _review().measure_tuned_parameters(only=["wayfinding.missing"])
    with pytest.raises(NotFoundError):
        _review().measure_tuned_parameters("Other setup")
    with pytest.raises(NotFoundError):
        _review(active=False).measure_tuned_parameters()
    assert calls == []
