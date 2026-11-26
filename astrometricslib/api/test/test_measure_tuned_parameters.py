"""Tests for `QualityRecords.measure_tuned_parameters` (#175).

The call is a thin wrapper of the one tuning core,
`astrometricslib.parameters.tune`. These tests run through that core with
stand-in tuners, so they also cover its tuner and setup checks.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib.api import quality
from astrometricslib.api.quality import QualityRecords
from astrometricslib.foundation import tuning
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError


class _Setups:
    """Stand-in setups: one named setup, which is also the active one."""

    def __init__(self, active: bool = True) -> None:
        """Hold one setup.

        Parameters
        ----------
        active : `bool`, optional
            Whether an active setup exists.
        """
        self.setup = SimpleNamespace(name="Scope on Camera")
        self.active = active

    def setup_named(self, name: str) -> Any:
        """Return the setup with this name, if any.

        Returns
        -------
        setup : `Any` or `None`
            The stand-in setup or `None`.
        """
        return self.setup if name == self.setup.name else None

    def active_setup(self) -> Any:
        """Return the active setup, if any.

        Returns
        -------
        setup : `Any` or `None`
            The stand-in setup or `None`.
        """
        return self.setup if self.active else None


def _records(active: bool = True) -> QualityRecords:
    """Build quality records over a stand-in config and owner.

    Returns
    -------
    records : `QualityRecords`
        Records whose owner is the string ``"owner"``.
    """
    config = SimpleNamespace(get_observatory_setups=lambda: _Setups(active))
    return QualityRecords(config, storage=None, owner=lambda: "owner")  # type: ignore[arg-type]


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace the tuners and the tuning run with stand-ins that record calls.

    Returns
    -------
    calls : `list` [`dict`]
        One entry per tuning run.
    """
    seen: list[dict[str, Any]] = []
    tuners = [SimpleNamespace(name="a.first"), SimpleNamespace(name="a.second")]
    monkeypatch.setattr(
        tuning, "collect_tuners", lambda packages: tuners if tuple(packages) == ("astrometricslib",) else []
    )

    def fake_run(tuners: list, setup: Any, configuration: Any, **keywords: Any) -> str:
        """Record what the run was given.

        Returns
        -------
        report : `str`
            A stand-in report.
        """
        seen.append({"tuners": [tuner.name for tuner in tuners], "setup": setup.name, **keywords})
        return "report"

    monkeypatch.setattr(tuning, "run_tuning", fake_run)
    return seen


def test_runs_every_tuner_on_the_active_setup_without_saving(calls: list[dict[str, Any]]) -> None:
    """All tuners run, nothing is written, and the owner is passed on."""
    assert _records().measure_tuned_parameters() == "report"
    assert calls[0]["tuners"] == ["a.first", "a.second"]
    assert calls[0]["setup"] == "Scope on Camera"
    assert calls[0]["write"] is False
    assert calls[0]["services"] == {"astrometrics": "owner"}


def test_only_and_save_are_passed_on(calls: list[dict[str, Any]]) -> None:
    """`only` picks tuners by name and `save` writes."""
    _records().measure_tuned_parameters("Scope on Camera", only=["a.second"], save=True)
    assert calls[0]["tuners"] == ["a.second"]
    assert calls[0]["write"] is True


def test_an_unknown_tuner_is_refused(calls: list[dict[str, Any]]) -> None:
    """A name that is no tuner raises before anything runs."""
    with pytest.raises(InvalidArgumentError, match=r"a\.missing"):
        _records().measure_tuned_parameters(only=["a.missing"])
    assert calls == []


def test_a_missing_setup_is_not_found(calls: list[dict[str, Any]]) -> None:
    """An unknown setup name, or no active setup, raises `NotFoundError`."""
    with pytest.raises(NotFoundError):
        _records().measure_tuned_parameters("Other setup")
    with pytest.raises(NotFoundError):
        _records(active=False).measure_tuned_parameters()


def test_the_module_imports_the_report_model() -> None:
    """The return type is the public tuning report."""
    assert quality.TuningReport is tuning.TuningReport


def test_the_core_is_the_public_tune() -> None:
    """`astrometricslib.parameters.tune` is the core the wrappers share."""
    import astrometricslib.parameters as parameters

    assert parameters.tune is tuning.tune
