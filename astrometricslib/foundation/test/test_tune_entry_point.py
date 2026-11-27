"""Tests for `tune`, the one tuning entry point, and its package registry.

`astrometricslib.parameters.tune(astrometrics, wayfinder)` runs the tuners of
every registered library (#175, #206). These tests run it with stand-in
tuners, so they also cover its tuner, package and setup checks.
"""

from types import SimpleNamespace
from typing import Any

import pytest

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


def _astrometrics(active: bool = True) -> Any:
    """Build a stand-in `Astrometrics` with a stand-in config.

    Parameters
    ----------
    active : `bool`, optional
        Whether the config has an active setup.

    Returns
    -------
    astrometrics : `SimpleNamespace`
        An object with a ``config``.
    """
    return SimpleNamespace(config=SimpleNamespace(get_observatory_setups=lambda: _Setups(active)))


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
    others = [SimpleNamespace(name="w.only")]
    monkeypatch.setattr(tuning, "TUNER_PACKAGES", ["astrometricslib", "wayfindinglib"])
    monkeypatch.setattr(
        tuning,
        "collect_tuners",
        lambda packages: [
            *(tuners if "astrometricslib" in packages else []),
            *(others if "wayfindinglib" in packages else []),
        ],
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


def test_runs_every_registered_tuner_on_the_active_setup_without_saving(calls: list[dict[str, Any]]) -> None:
    """Every registered tuner runs, nothing is written, the library goes on."""
    astrometrics = _astrometrics()
    assert tuning.tune(astrometrics) == "report"
    assert calls[0]["tuners"] == ["a.first", "a.second", "w.only"]
    assert calls[0]["setup"] == "Scope on Camera"
    assert calls[0]["write"] is False
    assert calls[0]["services"] == {"astrometrics": astrometrics}


def test_the_wayfinder_is_passed_on_and_packages_narrow_the_tuners(calls: list[dict[str, Any]]) -> None:
    """A `Wayfinder` is a service; ``packages`` picks a library's tuners."""
    astrometrics = _astrometrics()
    tuning.tune(astrometrics, "wayfinder", packages=("wayfindinglib",))
    assert calls[0]["tuners"] == ["w.only"]
    assert calls[0]["services"] == {"astrometrics": astrometrics, "wayfinder": "wayfinder"}


def test_only_and_save_are_passed_on(calls: list[dict[str, Any]]) -> None:
    """`only` picks tuners by name and `save` writes."""
    tuning.tune(_astrometrics(), setup="Scope on Camera", only=["a.second"], save=True)
    assert calls[0]["tuners"] == ["a.second"]
    assert calls[0]["write"] is True


def test_an_unknown_tuner_or_package_is_refused(calls: list[dict[str, Any]]) -> None:
    """A name that is no tuner, or a package not registered, raises first."""
    with pytest.raises(InvalidArgumentError, match=r"a\.missing"):
        tuning.tune(_astrometrics(), only=["a.missing"])
    with pytest.raises(InvalidArgumentError, match="elsewhere"):
        tuning.tune(_astrometrics(), packages=("elsewhere",))
    assert calls == []


def test_a_missing_setup_is_not_found(calls: list[dict[str, Any]]) -> None:
    """An unknown setup name, or no active setup, raises `NotFoundError`."""
    with pytest.raises(NotFoundError):
        tuning.tune(_astrometrics(), setup="Other setup")
    with pytest.raises(NotFoundError):
        tuning.tune(_astrometrics(active=False))


def test_registering_a_package_twice_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The registry keeps each package once, in the order it registered."""
    monkeypatch.setattr(tuning, "TUNER_PACKAGES", ["astrometricslib"])
    tuning.register_tuner_package("wayfindinglib")
    tuning.register_tuner_package("wayfindinglib")
    assert tuning.TUNER_PACKAGES == ["astrometricslib", "wayfindinglib"]


def test_the_core_is_the_public_tune() -> None:
    """`astrometricslib.parameters.tune` is the core the wrappers share."""
    import astrometricslib.parameters as parameters

    assert parameters.tune is tuning.tune
    assert parameters.register_tuner_package is tuning.register_tuner_package
