"""Purpose: Tests for shutting observatory control down.

Description: `ObservatoryControl.close()` stops background readers, such as
the PHD2 event reader, when the program shuts down. These tests check that it
builds nothing, that it reaches the guiding driver once one exists, and that
the guiding driver is built only once when several threads ask at once.
"""

import threading
from unittest.mock import MagicMock

import pytest

from wayfindinglib.api.control.context import ControlContext


def _context() -> ControlContext:
    """Build a context over a stand-in configuration.

    Returns
    -------
    context : `ControlContext`
        A context whose drivers are all still unbuilt.
    """
    return ControlContext(config=MagicMock())


def test_close_builds_no_guiding_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    """A guiding driver nobody used is not built just to be closed."""
    context = _context()
    build = MagicMock()
    monkeypatch.setattr(context, "_build_guiding_driver", build)

    context.close()
    context.close()

    build.assert_not_called()


def test_close_closes_the_guiding_driver_once_it_exists() -> None:
    """close() hands the shutdown on to a guiding driver that was built."""
    context = _context()
    driver = MagicMock()
    context.guiding_driver = driver

    context.close()

    driver.close.assert_called_once_with()


def test_the_guiding_driver_is_built_once_under_concurrent_use(monkeypatch: pytest.MonkeyPatch) -> None:
    """Callers asking at the same moment all get the same, single driver."""
    context = _context()
    built: list[object] = []
    gate = threading.Event()

    def slow_build() -> object:
        """Build a driver slowly, so the callers overlap.

        Returns
        -------
        driver : `object`
            A new stand-in driver.
        """
        gate.wait(timeout=2)
        driver = object()
        built.append(driver)
        return driver

    monkeypatch.setattr(context, "_build_guiding_driver", slow_build)
    results: list[object] = []
    callers = [threading.Thread(target=lambda: results.append(context.guiding_driver)) for _ in range(6)]
    for caller in callers:
        caller.start()
    gate.set()
    for caller in callers:
        caller.join(timeout=5)

    assert len(built) == 1
    assert results == [built[0]] * 6
