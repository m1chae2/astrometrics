"""Tests for the Siril driver's group registration.

The code under test is `stacking.group_registration`.

Siril is not started: a stand-in takes the place of the short script run, reads
the folder the driver staged and writes what Siril would write (the solved
files and the lined-up files) and the log lines it would print. Every test
uses the temporary Siril settings home of the folder's ``conftest.py``, so the
user's own Siril settings are never read or changed.
"""

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib._internal.drivers.siril.core import script_run
from astrometricslib._internal.drivers.siril.core.output_parsing import parse_sequence_solve
from astrometricslib._internal.drivers.siril.stacking import group_registration
from astrometricslib._internal.drivers.siril.stacking.group_registration import (
    build_group_registration_commands,
    register_group_stacks,
)

# Siril's log for three stacks solved one after the other: the first fails with
# two stars, the second solves with a lower fit order, the third solves.
SEQUENCE_LOG = """\
log: Found 2 Gaussian profile stars in image, channel #0 (FWHM 2.822462)
log: Image s_00001 did not solve
log: Found 40 Gaussian profile stars in image, channel #0 (FWHM 3.1)
order: 1, nmatched: 9, sigx:0.390258, sigy:0.521904
Image s_00002.fits was a symlink, creating a new file to keep original untouched
log: Image s_00002.fits platesolved and updated
log: Found 158 Gaussian profile stars in image, channel #0 (FWHM 3.167741)
order: 3, nmatched: 142, sigx:0.163172, sigy:0.185289
log: Image s_00003.fits platesolved and updated
log: Sequence processing partially succeeded, with 1 images that failed.
"""


def test_the_log_gives_each_image_its_own_fit() -> None:
    """Verify a solved image gets the fit printed just before its line."""
    records = parse_sequence_solve(SEQUENCE_LOG)
    assert set(records) == {1, 2, 3}
    assert not records[1].solved
    assert records[1].stars_found == 2
    assert records[1].matched_star_count is None
    assert records[2].solved
    assert (records[2].stars_found, records[2].matched_star_count, records[2].fit_order) == (40, 9, 1)
    assert records[2].fit_residual_rms_arcsec == pytest.approx(np.hypot(0.390258, 0.521904))
    assert (records[3].stars_found, records[3].matched_star_count, records[3].fit_order) == (158, 142, 3)


def test_a_fit_does_not_carry_over_to_the_next_image() -> None:
    """Verify an image that fails after a solved one has no fit of its own."""
    log = (
        "log: Found 100 Gaussian profile stars in image\n"
        "order: 3, nmatched: 90, sigx:0.1, sigy:0.1\n"
        "log: Image s_00001.fits platesolved and updated\n"
        "log: Found 3 Gaussian profile stars in image\n"
        "log: Image s_00002 did not solve\n"
    )
    records = parse_sequence_solve(log)
    assert records[1].matched_star_count == 90
    assert records[2].matched_star_count is None
    assert records[2].stars_found == 3


def test_the_script_solves_each_stack_and_lines_up_the_solved_ones() -> None:
    """Verify the commands: one thread, the reference, the solve, the apply."""
    commands = build_group_registration_commands(2, "/data/catalog.dat", 3)
    assert commands == [
        "requires 1.4.0",
        "set core.catalogue_gaia_astro=/data/catalog.dat",
        "setext fits",
        "set32bits",
        "link s",
        "setcpu 1",
        "setref s 2",
        "seqplatesolve s -force -order=3 -catalog=localgaia -noflip",
        "seqapplyreg s -framing=current -interp=la -noclamp",
        "close",
    ]


@pytest.mark.parametrize(("reference", "count"), [(1, 1), (0, 2), (3, 2)])
def test_the_script_needs_two_stacks_and_a_reference_among_them(reference: int, count: int) -> None:
    """Verify one stack, or a reference outside the sequence, is refused."""
    with pytest.raises(ValueError, match="stacks"):
        build_group_registration_commands(reference, "/data/catalog.dat", count)


def write_stack(path: Path) -> str:
    """Write a small group stack.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write the stack.

    Returns
    -------
    path : `str`
        The stack's path.
    """
    fits.PrimaryHDU(np.ones((8, 8), dtype=np.float32), fits.Header({"RA": 10.0, "DEC": 20.0})).writeto(path)
    return str(path)


class FakeSiril:
    """A stand-in for `run_siril_script` that registers like Siril.

    Attributes
    ----------
    calls : `list` [`dict`]
        What each run was given.
    """

    def __init__(self, log: str, solved_numbers: list[int], applies: bool = True) -> None:
        """Say what the run prints and which stacks it writes.

        Parameters
        ----------
        log : `str`
            What the run prints.
        solved_numbers : `list` [`int`]
            The numbers of the stacks the run writes a solved file for.
        applies : `bool`, optional
            Whether the run goes on to write the lined-up files. Default
            `True`.
        """
        self.log = log
        self.solved_numbers = solved_numbers
        self.applies = applies
        self.calls: list[dict[str, Any]] = []

    def __call__(self, directory: str, commands: list[str], siril_executable: str, **options: Any) -> bool:
        """Act like one Siril run: write the output files and print the log.

        Returns
        -------
        succeeded : `bool`
            Always `True`.
        """
        self.calls.append({
            "commands": commands,
            "options": options,
            "files": sorted(p.name for p in Path(directory).iterdir()),
        })
        for number in self.solved_numbers:
            solved = fits.Header({"CTYPE1": "RA---TAN-SIP", "CRVAL1": 10.0 + number})
            fits.PrimaryHDU(np.ones((8, 8), dtype=np.float32), solved).writeto(
                Path(directory) / f"s_{number:05d}.fits"
            )
            if self.applies:
                aligned = fits.Header({"CTYPE1": "RA---TAN", "CRVAL1": 99.0})
                fits.PrimaryHDU(np.full((8, 8), number, dtype=np.float32), aligned).writeto(
                    Path(directory) / f"r_s_{number:05d}.fits"
                )
        script_run._last_siril_output.set(self.log)
        script_run._last_siril_messages.set(self.log)
        return True

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Replace the real script run in the registration module."""
        monkeypatch.setattr(group_registration, "run_siril_script", self)


@pytest.fixture
def stacks(tmp_path: Path) -> list[str]:
    """Write three group stacks.

    Returns
    -------
    paths : `list` [`str`]
        The stacks' paths.
    """
    return [write_stack(tmp_path / f"group_{number}.fits") for number in range(3)]


def test_a_stack_siril_cannot_solve_does_not_stop_the_others(
    stacks: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the unsolved stack gets a reason and the others their output."""
    fake = FakeSiril(SEQUENCE_LOG, [2, 3])
    fake.install(monkeypatch)
    groups = register_group_stacks(stacks, 2, "siril", catalog_path="/data/catalog.dat")
    assert [group.solved for group in groups] == [False, True, True]
    assert groups[0].stars_found == 2
    assert groups[0].failure_reason is not None
    assert "2 star(s)" in groups[0].failure_reason
    assert groups[1].matched_star_count == 9
    assert groups[1].fit_order == 1
    assert groups[2].matched_star_count == 142
    assert groups[2].aligned_image is not None
    assert float(groups[2].aligned_image[0, 0]) == pytest.approx(3.0)
    assert groups[2].solved_header is not None
    assert groups[2].aligned_header is not None
    assert groups[2].aligned_header["CRVAL1"] == pytest.approx(99.0)
    call = fake.calls[0]
    assert call["files"] == ["s_00001.fit", "s_00002.fit", "s_00003.fit"]
    assert "setref s 3" in call["commands"]
    assert call["options"]["offline"] is True


def test_the_stacks_are_only_linked_and_the_work_folder_is_removed(
    stacks: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify the stacks themselves are unchanged and no folder is left."""
    before = [Path(path).read_bytes() for path in stacks]
    seen: list[str] = []
    fake = FakeSiril(SEQUENCE_LOG, [2, 3])

    def run(directory: str, commands: list[str], siril_executable: str, **options: Any) -> bool:
        """Remember the folder and run the stand-in.

        Returns
        -------
        succeeded : `bool`
            What the stand-in returns.
        """
        seen.append(directory)
        return fake(directory, commands, siril_executable, **options)

    monkeypatch.setattr(group_registration, "run_siril_script", run)
    register_group_stacks(stacks, 2, "siril", catalog_path="/data/catalog.dat")
    assert [Path(path).read_bytes() for path in stacks] == before
    assert not Path(seen[0]).exists()


def test_only_the_reference_solving_gives_no_group_a_result(
    stacks: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a run that cannot apply names the cause in the solved group."""
    FakeSiril(SEQUENCE_LOG, [3], applies=False).install(monkeypatch)
    groups = register_group_stacks(stacks, 2, "siril", catalog_path="/data/catalog.dat")
    assert not any(group.solved for group in groups)
    assert groups[2].matched_star_count == 142
    assert groups[2].failure_reason is not None
    assert "too few" in groups[2].failure_reason


def test_a_missing_catalog_fails_every_stack(stacks: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify no catalog means no Siril run and one reason for every stack."""
    fake = FakeSiril(SEQUENCE_LOG, [])
    fake.install(monkeypatch)
    monkeypatch.setattr(group_registration, "find_local_gaia_catalog", lambda: None)
    groups = register_group_stacks(stacks, 2, "siril")
    assert fake.calls == []
    assert {group.failure_reason for group in groups} == {"Siril's Gaia astrometry catalog is not installed"}


def test_siril_that_cannot_start_fails_every_stack(
    stacks: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify an `OSError` from the run becomes a reason, not an exception."""

    def refuse(*args: Any, **options: Any) -> bool:
        """Fail like a missing program.

        Raises
        ------
        OSError
            Always.
        """
        raise OSError("no such program")

    monkeypatch.setattr(group_registration, "run_siril_script", refuse)
    groups = register_group_stacks(stacks, 0, "siril", catalog_path="/data/catalog.dat")
    assert not any(group.solved for group in groups)
    assert all("could not be started" in str(group.failure_reason) for group in groups)


def test_one_stack_has_nothing_to_line_up_with(tmp_path: Path) -> None:
    """Verify a single stack is reported, not run."""
    groups = register_group_stacks([write_stack(tmp_path / "only.fits")], 0, "siril", catalog_path="/x")
    assert len(groups) == 1
    assert not groups[0].solved
