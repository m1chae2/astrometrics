"""Tests for the color steps of the stack preview.

The tests read the photometric color calibration script, the plate-solution
and catalog checks that decide whether it can run, and the rule that decides
whether Siril's green removal runs. No program or network service is used.
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
    build_pcc_script,
    find_local_gaia_catalog,
    has_plate_solution,
    online_catalog_reachable,
)


def test_the_pcc_script_solves_the_field_only_when_it_has_no_solution() -> None:
    """Verify `platesolve` comes before `pcc`, and only when asked for."""
    unsolved = build_pcc_script("a.fits", "colour", solve=True)
    solved = build_pcc_script("a.fits", "colour", solve=False)

    assert unsolved[1:5] == [
        'load "a.fits"',
        "platesolve -catalog=gaia",
        "pcc -catalog=gaia",
        'save "colour"',
    ]
    assert solved[1:4] == ['load "a.fits"', "pcc -catalog=gaia", 'save "colour"']


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


def test_the_pcc_script_reads_a_local_catalog_when_given_one() -> None:
    """Verify the script sets the catalog path and asks for `localgaia`."""
    commands = build_pcc_script("a.fits", "colour", solve=True, local_catalog="/data/cat.dat")

    assert commands[1:6] == [
        "set core.catalogue_gaia_astro=/data/cat.dat",
        'load "a.fits"',
        "platesolve -catalog=localgaia",
        "pcc -catalog=localgaia",
        'save "colour"',
    ]
    assert not any("-catalog=gaia" in command for command in commands)


def test_the_local_gaia_catalog_is_found_in_the_first_folder_that_has_it(tmp_path: Path) -> None:
    """Verify the search order and the answer when nothing is installed."""
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (second / "siril_cat_healpix8_astro.dat").write_bytes(b"x")

    assert find_local_gaia_catalog([str(first), str(second)]) == str(second / "siril_cat_healpix8_astro.dat")
    assert find_local_gaia_catalog([str(first)]) is None


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
