"""Tests for the color steps of the stack preview.

The tests read the plate-solution and catalog checks that decide whether
Siril's photometric color calibration can run, and the rule that decides
whether Siril's green removal runs. The calibration script itself is tested
with the Siril driver (``test_siril_preview_scripts.py``). No program or
network service is used.
"""

from contextlib import nullcontext
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib._internal.drivers import outside_services
from astrometricslib.pipelines.stacking.post_processing import (
    preview_color,
)
from astrometricslib.pipelines.stacking.post_processing.preview_color import (
    has_plate_solution,
    online_catalog_reachable,
)


def test_a_header_with_a_projection_and_a_reference_position_counts_as_solved(tmp_path: Path) -> None:
    """Verify the plate solution check reads the header only."""
    solved, unsolved, broken = tmp_path / "s.fits", tmp_path / "u.fits", tmp_path / "b.fits"
    header = fits.Header()
    header["CTYPE1"], header["CRVAL1"] = "RA---TAN", 202.4
    fits.writeto(solved, np.zeros((4, 4), dtype=np.float32), header)
    fits.writeto(unsolved, np.zeros((4, 4), dtype=np.float32))
    broken.write_bytes(b"not fits")

    assert has_plate_solution(str(solved)) is True
    assert has_plate_solution(str(unsolved)) is False
    assert has_plate_solution(str(broken)) is False


def _fake_connection(monkeypatch: pytest.MonkeyPatch, error: Exception | None) -> list[str]:
    """Replace the connection the check opens; record each host it is sent to.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Test patcher.
    error : `Exception` or `None`
        What the connection raises, or `None` for a connection that works.

    Returns
    -------
    attempts : `list` [`str`]
        The host of each connection tried.
    """
    attempts: list[str] = []

    def connect(address: tuple[str, int], timeout: float) -> nullcontext:
        """Record the attempt, then fail or hand back a closable stand-in.

        Returns
        -------
        connection : `contextlib.nullcontext`
            A stand-in that closes with nothing to do.
        """
        attempts.append(address[0])
        if error is not None:
            raise error
        return nullcontext()

    monkeypatch.setattr(outside_services.clients.socket, "create_connection", connect)
    return attempts


def test_the_catalog_server_check_goes_through_the_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the check is counted as a live request of its gateway service."""
    attempts = _fake_connection(monkeypatch, None)

    events: list[outside_services.ServiceEvent] = []
    with outside_services.record_service_events(events):
        assert online_catalog_reachable() is True

    assert attempts == ["tapvizier.u-strasbg.fr"]
    assert any(event.service == "Siril Gaia catalog" and event.kind == "live" for event in events)


def test_a_timeout_pauses_the_server_and_the_next_check_makes_no_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify one timeout pauses the host, so the next preview skips."""
    attempts = _fake_connection(monkeypatch, TimeoutError("timed out"))

    assert online_catalog_reachable() is False
    assert online_catalog_reachable() is False

    assert attempts == ["tapvizier.u-strasbg.fr"]


@pytest.mark.parametrize(
    ("is_colour", "switch_on", "calibrated", "expected"),
    [
        (True, True, False, True),
        (True, True, True, False),
        (True, False, False, False),
        (False, True, False, False),
    ],
)
def test_decide_remove_green_covers_every_case(
    is_colour: bool, switch_on: bool, calibrated: bool, expected: bool
) -> None:
    """Verify the rule: colour, switch on, and no calibration."""
    run_step, reason = preview_color.decide_remove_green(is_colour, switch_on, calibrated)

    assert run_step is expected
    assert reason
