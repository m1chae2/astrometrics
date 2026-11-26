"""Tests for the GraXpert background removal of the stack preview.

GraXpert is not started. A stand-in replaces the `run_registered` call that
would run it, so the tests check the command line it would get.
"""

from contextlib import nullcontext

import pytest

from astrometricslib.pipelines.stacking.post_processing import preview_graxpert, stack_preview
from astrometricslib.pipelines.stacking.post_processing.preview_graxpert import flatten_background


def test_graxpert_is_asked_for_a_subtracted_ai_background(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the GraXpert command line asks for AI background subtraction."""
    captured: dict = {}

    def fake_run(arguments: list[str], _timeout_seconds: float) -> stack_preview.subprocess.CompletedProcess:
        """Record the command line.

        Returns
        -------
        completed : `subprocess.CompletedProcess`
            A successful result.
        """
        captured["arguments"] = arguments
        return stack_preview.subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(preview_graxpert, "run_registered", fake_run)
    monkeypatch.setattr(preview_graxpert, "siril_process_lock", nullcontext)

    assert flatten_background("/opt/graxpert-gpu.sh", "/work/stack.fits", "/work/flat")

    arguments = captured["arguments"]
    assert arguments[0] == "/opt/graxpert-gpu.sh"
    assert arguments[1:5] == ["-cli", "-cmd", "background-extraction", "-gpu"]
    assert arguments[arguments.index("-correction") + 1] == "Subtraction"
    assert arguments[-3:] == ["-output", "/work/flat", "/work/stack.fits"]
