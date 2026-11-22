"""Tests that a Siril that stops answering gets no relaxed retry.

`_stack_one_batch` tries the relaxed star detection when the standard one
gives no stack. A run where Siril did not answer a command says nothing
about star detection, so it must stop with its own error instead.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib._internal.drivers.siril_interface.run_models import SIRIL_COMMUNICATION_ERROR_KEY
from astrometricslib.pipelines.stacking import stack_runner


class _FakeEngine:
    """A stacking engine that fails every run with the given diagnostics."""

    name = "fake"

    def __init__(self, diagnostics: dict[str, Any]) -> None:
        """Remember the diagnostics each run reports and start the call log."""
        self.diagnostics = diagnostics
        self.modes: list[str | None] = []

    def stack_batch(self, *args: Any, registration: str | None = None, **kwargs: Any) -> SimpleNamespace:
        """Record the star detection mode and return a failed run.

        Returns
        -------
        result : `SimpleNamespace`
            A run result with no stacked path.
        """
        self.modes.append(registration)
        return SimpleNamespace(
            stacked_path=None,
            diagnostics=dict(self.diagnostics),
            engine_name="fake",
            engine_version="0",
        )


def _run(engine: _FakeEngine) -> tuple[Any, dict[str, Any]]:
    """Run a one-frame spectral batch through `_stack_one_batch`.

    Returns
    -------
    result : `tuple`
        The stacked path and the diagnostics.
    """
    return stack_runner._stack_one_batch(engine, [{"path": "a.fits"}], "M 27", "out.fits", None, True)


def test_a_communication_timeout_does_not_try_the_relaxed_detection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Siril not answering stops the run after one attempt."""
    engine = _FakeEngine({
        SIRIL_COMMUNICATION_ERROR_KEY: "Siril did not answer the command 'stack' within 300 s."
    })
    path, diagnostics = _run(engine)

    assert path is None
    assert engine.modes == [stack_runner.SPECTRAL_STAR_DETECTION_ORDER[0]]
    assert SIRIL_COMMUNICATION_ERROR_KEY in diagnostics
    assert "did not answer" in caplog.text
    assert "trying" not in caplog.text


def test_a_plain_failure_still_tries_the_relaxed_detection() -> None:
    """A plain failure still gets the next detection mode."""
    engine = _FakeEngine({})
    _run(engine)

    assert engine.modes == list(stack_runner.SPECTRAL_STAR_DETECTION_ORDER)
