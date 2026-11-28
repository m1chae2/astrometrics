"""Tests that the wayfinding tuners reach the core tuning entry point (#175).

The wayfinding tuned values are worked out by the core's one tuning entry
point, `astrometricslib.parameters.tune(astrometrics, wayfinder)`, which
runs both libraries' tuners. Importing `wayfindinglib` registers its tuners
there.
"""

import importlib
from types import SimpleNamespace

import pytest

import astrometricslib.parameters as parameters
from astrometricslib.exceptions import InvalidArgumentError


def test_importing_wayfindinglib_registers_its_tuners() -> None:
    """`wayfindinglib` adds itself to the core's tuner registry on import.

    An unregistered package is refused before any tuner is looked for, so a
    refusal that names the tuner shows the package is registered.
    """
    importlib.import_module("wayfindinglib")
    astrometrics = SimpleNamespace(config=None)
    with pytest.raises(InvalidArgumentError, match="No tuner named"):
        parameters.tune(astrometrics, packages=("wayfindinglib",), only=["no.such.tuner"])
