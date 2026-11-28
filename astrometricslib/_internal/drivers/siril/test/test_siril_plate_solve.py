"""Tests for Siril's plate-solve operation (`siril.astrometry.plate_solve`).

Siril is not started in most tests: a stand-in takes the place of the short
script run, reads the script it is given and writes the solved image the way
Siril would. One test, marked ``golden``, solves a real M 13 frame with the
installed Siril and its Gaia catalog, and is skipped without them. Every test
uses the temporary Siril settings home of the folder's ``conftest.py``, so the
user's own Siril settings are never read or changed.
"""

import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib._internal.drivers.interfaces.plate_solve_driver import PlateSolveHeader
from astrometricslib._internal.drivers.siril.astrometry import plate_solve
from astrometricslib._internal.drivers.siril.astrometry.plate_solve import (
    FOCAL_LENGTH_RETRY_FACTORS,
    SolveInputs,
    parse_fit,
    platesolve_command,
    read_inputs,
    solve_image,
)
from astrometricslib._internal.drivers.siril.core import script_run
from astrometricslib._internal.drivers.siril.core.program import find_local_gaia_catalog

SIRIL_OUTPUT = (
    "log: Using 454 detected stars from image.\n"
    "order: 3, nmatched: 381, sigx:0.164167, sigy:0.182909\n"
    "log: Siril solve succeeded.\n"
)
FAILURE_OUTPUT = (
    "log: Plate solving failed: Siril near solver could not find a solution over the search radius\n"
)
SAMPLE_FRAME = (
    Path(__file__).resolve().parents[5]
    / "documentation/notebooks/astrometrics/sample_data/M 13/M_13_Light_Luminance_019.fits"
)


def write_frame(path: Path, **cards: Any) -> Path:
    """Write a small FITS frame with the cards a solve reads.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write the frame.
    **cards
        Header cards that replace or add to the defaults: a position, a focal
        length and a pixel size. A card set to `None` is left out.

    Returns
    -------
    path : `pathlib.Path`
        The frame.
    """
    header = fits.Header()
    values: dict[str, Any] = {"RA": 250.4197, "DEC": 36.44781, "FOCALLEN": 405.0, "XPIXSZ": 3.76}
    values.update(cards)
    for key, value in values.items():
        if value is not None:
            header[key] = value
    fits.PrimaryHDU(np.zeros((8, 8), dtype=np.float32), header).writeto(path)
    return path


class FakeSiril:
    """A stand-in for `run_siril_script` that solves like Siril.

    Attributes
    ----------
    calls : `list` [`dict`]
        What each run was given: the commands, the keyword arguments and the
        folder's contents when it started.
    """

    def __init__(self, outcomes: list[bool]) -> None:
        """Say which runs solve.

        Parameters
        ----------
        outcomes : `list` [`bool`]
            One entry per run, in order: `True` to solve, `False` to fail.
            The last entry repeats.
        """
        self.outcomes = outcomes
        self.calls: list[dict[str, Any]] = []

    def __call__(self, directory: str, commands: list[str], siril_executable: str, **options: Any) -> bool:
        """Act like one Siril run: write the solved file and print its log.

        Returns
        -------
        succeeded : `bool`
            Always `True`; Siril exits normally even when it fails to solve.
        """
        solves = self.outcomes[min(len(self.calls), len(self.outcomes) - 1)]
        self.calls.append({
            "commands": commands,
            "options": options,
            "executable": siril_executable,
            "files": sorted(path.name for path in Path(directory).iterdir()),
        })
        if solves:
            header = fits.Header()
            for key, value in {
                "CTYPE1": "RA---TAN",
                "CTYPE2": "DEC--TAN",
                "CRVAL1": 250.4,
                "CRVAL2": 36.5,
            }.items():
                header[key] = value
            fits.PrimaryHDU(np.zeros((8, 8), dtype=np.float32), header).writeto(
                Path(directory) / "solved.fits"
            )
        output = SIRIL_OUTPUT if solves else FAILURE_OUTPUT
        script_run._last_siril_output.set(output)
        script_run._last_siril_messages.set(output)
        return True

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Replace the real script run in the plate-solve module."""
        monkeypatch.setattr(plate_solve, "run_siril_script", self)


@pytest.fixture
def catalog(tmp_path: Path) -> str:
    """Make a stand-in Gaia catalog file.

    Returns
    -------
    path : `str`
        The file's path.
    """
    path = tmp_path / "siril_cat_healpix8_astro.dat"
    path.write_bytes(b"catalog")
    return str(path)


def test_platesolve_command_always_keeps_the_image_the_right_way_up() -> None:
    """Verify every command carries ``-noflip`` and the options asked for."""
    assert platesolve_command("localgaia") == "platesolve -catalog=localgaia -noflip"
    command = platesolve_command(
        "localgaia", ra_deg=250.4197, dec_deg=36.44781, focal_length_mm=405.0, pixel_size_um=3.76, force=True
    )
    assert command == (
        "platesolve 250.419700,36.447810 -catalog=localgaia -focal=405.000 -pixelsize=3.7600 -force -noflip"
    )


def test_inputs_come_from_the_hints_first_then_the_header() -> None:
    """Verify a hinted position wins over the header's position."""
    header = fits.Header({"RA": 10.0, "DEC": 20.0, "FOCALLEN": 300.0, "XPIXSZ": 3.9})
    assert read_inputs(header, {}) == SolveInputs(10.0, 20.0, 300.0, 3.9)
    assert read_inputs(header, {"center_ra": 11.5, "center_dec": -2.0}) == SolveInputs(11.5, -2.0, 300.0, 3.9)


def test_inputs_read_a_sexagesimal_position_and_a_binned_pixel_size() -> None:
    """Verify ``OBJCTRA`` and ``PIXSIZE1`` times the binning are read."""
    header = fits.Header({
        "OBJCTRA": "16 41 40.73",
        "OBJCTDEC": "+36 26 52.11",
        "FOCALLEN": 405.0,
        "PIXSIZE1": 3.76,
        "XBINNING": 2,
    })
    inputs = read_inputs(header, {})
    assert inputs is not None
    assert inputs.ra_deg == pytest.approx(250.4197, abs=1e-4)
    assert inputs.dec_deg == pytest.approx(36.4478, abs=1e-4)
    assert inputs.pixel_size_um == pytest.approx(7.52)


def test_inputs_work_out_the_focal_length_from_the_scale_when_the_header_has_none() -> None:
    """Verify the focal length follows from the scale hint and pixel size."""
    header = fits.Header({"RA": 10.0, "DEC": 20.0, "XPIXSZ": 3.76})
    inputs = read_inputs(header, {"scale_lower": 1.9, "scale_upper": 1.94})
    assert inputs is not None
    assert inputs.focal_length_mm == pytest.approx(206.265 * 3.76 / 1.92)
    assert read_inputs(header, {}) is None


@pytest.mark.parametrize("missing", ["RA", "XPIXSZ"])
def test_inputs_are_none_without_a_position_or_a_pixel_size(missing: str) -> None:
    """Verify no position (the blind case) or no pixel size gives none."""
    cards = {"RA": 10.0, "DEC": 20.0, "FOCALLEN": 300.0, "XPIXSZ": 3.9}
    del cards[missing]
    assert read_inputs(fits.Header(cards), {}) is None


def test_the_fit_is_read_from_siril_s_output() -> None:
    """Verify the fit residual combines the two spreads."""
    residual, matched = parse_fit(SIRIL_OUTPUT)
    assert residual == pytest.approx(np.hypot(0.164167, 0.182909))
    assert matched == 381
    assert parse_fit("nothing here") == (None, None)


def test_a_solve_runs_its_own_short_siril_and_returns_the_solution(
    tmp_path: Path, catalog: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the script, the run options and the result; the image stays."""
    frame = write_frame(tmp_path / "frame.fits")
    before = frame.read_bytes()
    fake = FakeSiril([True])
    fake.install(monkeypatch)

    solved = solve_image(str(frame), "siril-cli", {}, catalog_path=catalog).header

    assert isinstance(solved, PlateSolveHeader)
    assert solved["CRVAL1"] == pytest.approx(250.4)
    assert solved.matched_star_count == 381
    assert solved.fit_residual_rms_arcsec == pytest.approx(0.2457, abs=1e-3)
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["commands"] == [
        "requires 1.4.0",
        f"set core.catalogue_gaia_astro={catalog}",
        'load "input_image.fits"',
        "platesolve 250.419700,36.447810 -catalog=localgaia -focal=405.000 -pixelsize=3.7600 -force -noflip",
        'save "solved"',
        "close",
    ]
    # Its own process: no wait for a stack's slot, and never online.
    assert call["options"]["wait_for_slot"] is False
    assert call["options"]["offline"] is True
    assert call["executable"] == "siril-cli"
    assert call["files"] == ["input_image.fits"]
    assert frame.read_bytes() == before


def focal_lengths_tried(fake: FakeSiril) -> list[str]:
    """Read the focal length of each Siril run a fake was given.

    Parameters
    ----------
    fake : `FakeSiril`
        The stand-in that recorded the runs.

    Returns
    -------
    words : `list` [`str`]
        The ``-focal=`` word of each run's plate-solve command.
    """
    return [
        next(word for word in call["commands"][3].split() if word.startswith("-focal="))
        for call in fake.calls
    ]


def test_a_failed_solve_is_retried_with_a_shorter_focal_length(
    tmp_path: Path, catalog: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the second attempt uses 0.8 of the header's focal length."""
    frame = write_frame(tmp_path / "frame.fits", FOCALLEN=300.0)
    fake = FakeSiril([False, True])
    fake.install(monkeypatch)

    solved = solve_image(str(frame), "siril-cli", {}, catalog_path=catalog).header

    assert solved is not None
    assert focal_lengths_tried(fake) == ["-focal=300.000", "-focal=240.000"]


def test_a_header_focal_length_that_is_too_short_is_retried_longer(
    tmp_path: Path, catalog: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the third attempt, after 1.0 and 0.8, is 1.25 of the header's."""
    frame = write_frame(tmp_path / "frame.fits", FOCALLEN=300.0)
    fake = FakeSiril([False, False, True])
    fake.install(monkeypatch)

    assert solve_image(str(frame), "siril-cli", {}, catalog_path=catalog).header is not None
    assert focal_lengths_tried(fake) == ["-focal=300.000", "-focal=240.000", "-focal=375.000"]


def test_the_retries_go_shorter_and_longer_in_turn() -> None:
    """Verify the order: each shorter factor, then its longer twin."""
    assert FOCAL_LENGTH_RETRY_FACTORS.default == (0.8, 1.25, 0.64, 1.5625)


def test_every_attempt_failing_gives_no_solution(
    tmp_path: Path, catalog: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the header's focal length and each retry are tried once."""
    frame = write_frame(tmp_path / "frame.fits", FOCALLEN=300.0)
    fake = FakeSiril([False])
    fake.install(monkeypatch)

    outcome = solve_image(str(frame), "siril-cli", {}, catalog_path=catalog)

    assert outcome.header is None
    factors = FOCAL_LENGTH_RETRY_FACTORS.default
    assert len(fake.calls) == 1 + len(factors)
    assert "-focal=192.000" in fake.calls[3]["commands"][3]
    assert "-focal=468.750" in fake.calls[4]["commands"][3]


def test_a_failed_solve_carries_siril_s_own_reason(
    tmp_path: Path, catalog: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the outcome says why Siril failed, in Siril's words."""
    frame = write_frame(tmp_path / "frame.fits", FOCALLEN=300.0)
    fake = FakeSiril([False])
    fake.install(monkeypatch)

    outcome = solve_image(str(frame), "siril-cli", {}, catalog_path=catalog)

    assert outcome.failure_reason is not None
    assert "Plate solving failed" in outcome.failure_reason
    assert "300.0 mm" in outcome.failure_reason


def test_an_image_with_no_position_is_not_given_to_siril(
    tmp_path: Path, catalog: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a blind frame never starts Siril; the caller sends it online."""
    frame = write_frame(tmp_path / "frame.fits", RA=None, DEC=None)
    fake = FakeSiril([True])
    fake.install(monkeypatch)

    outcome = solve_image(str(frame), "siril-cli", {}, catalog_path=catalog)

    assert outcome.header is None and outcome.failure_reason is None
    assert fake.calls == []


def test_a_missing_catalog_gives_no_local_solution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify nothing runs when Siril's Gaia catalog is not installed."""
    frame = write_frame(tmp_path / "frame.fits")
    fake = FakeSiril([True])
    fake.install(monkeypatch)
    monkeypatch.setattr(plate_solve, "find_local_gaia_catalog", lambda: None)

    outcome = solve_image(str(frame), "siril-cli", {})

    assert outcome.header is None
    assert outcome.failure_reason is not None and "catalog" in outcome.failure_reason
    assert fake.calls == []


@pytest.mark.golden
def test_siril_solves_a_real_m13_frame(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the installed Siril solves a real M 13 frame.

    Skipped without Siril, its Gaia catalog or the sample frame. The solve
    uses the temporary settings home, so the user's own Siril settings are not
    read or changed. The scratch and data folders move into the test's own
    folder, which a Flatpak Siril can reach (not ``/tmp``).
    """
    if shutil.which("siril-cli"):
        siril_executable = "siril-cli"
    elif shutil.which("flatpak") and any(
        (folder / "app" / "org.siril.Siril").is_dir()
        for folder in (Path("/var/lib/flatpak"), Path.home() / ".local/share/flatpak")
    ):
        siril_executable = "flatpak run org.siril.Siril"
    else:
        pytest.skip("Siril is not installed")
    if find_local_gaia_catalog() is None:
        pytest.skip("Siril's Gaia astrometry catalog is not installed")
    if not SAMPLE_FRAME.is_file() or SAMPLE_FRAME.stat().st_size < 1_000_000:
        pytest.skip("the Git LFS sample frame is not checked out")
    if not tmp_path.resolve().is_relative_to(Path.home()):
        pytest.skip("a Flatpak Siril reaches the home folder only; use a pytest base folder there")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    frame = tmp_path / "frame.fits"
    shutil.copy(SAMPLE_FRAME, frame)

    solved = solve_image(str(frame), siril_executable, {}).header

    assert solved is not None
    assert solved["CRVAL1"] == pytest.approx(250.42, abs=0.1)
    assert solved["CRVAL2"] == pytest.approx(36.46, abs=0.1)
    assert solved.matched_star_count is not None and solved.matched_star_count > 100
    assert solved.fit_residual_rms_arcsec is not None and solved.fit_residual_rms_arcsec < 1.0
