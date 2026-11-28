"""Tests for the Siril driver's preview scripts and its short script run.

Siril is not started. The tests read the scripts the driver writes (stretch
and save, colour calibration with its plate solve, StarNet), and a stand-in
replaces the `run_registered` call that would run Siril.
"""

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrometricslib._internal.drivers.siril.astrometry.plate_solve import platesolve_command
from astrometricslib._internal.drivers.siril.core import script_run
from astrometricslib._internal.drivers.siril.core.program import find_local_gaia_catalog
from astrometricslib._internal.drivers.siril.core.script_run import last_siril_output, run_siril_script
from astrometricslib._internal.drivers.siril.preview.color_calibration import (
    build_pcc_script,
    reports_imprecise_solution,
)
from astrometricslib._internal.drivers.siril.preview.starnet import build_starnet_script
from astrometricslib._internal.drivers.siril.preview.stretch import (
    SkyStretch,
    build_bright_object_script,
    build_picture_script,
    build_preview_script,
    build_stretch_script,
)

# The stretch the preview asks for when Siril measures the sky itself.
CLIP = -2.8


def test_the_script_stretches_and_saves_without_touching_the_stack() -> None:
    """Verify the script saves a FITS and a JPEG under new names only."""
    commands = build_preview_script("M_13_L_Stacked.fits", "M_13_L_Stacked_preview", SkyStretch(0.21, CLIP))

    assert commands == [
        "requires 1.2.0",
        'load "M_13_L_Stacked.fits"',
        "autostretch -2.8 0.210",
        'save "processed"',
        "mirrorx",
        'savejpg "M_13_L_Stacked_preview" 90',
        "close",
    ]
    assert not any('M_13_L_Stacked"' in command for command in commands if command.startswith("save"))


def test_the_stretch_script_saves_a_fits_and_the_picture_script_does_not_stretch() -> None:
    """Verify the two halves of the denoise route."""
    stretch = build_stretch_script("flat.fits", "stretched", SkyStretch(0.15, CLIP))
    picture = build_picture_script("denoised.fits", "preview")

    assert stretch == [
        "requires 1.2.0",
        'load "flat.fits"',
        "autostretch -2.8 0.150",
        'save "stretched"',
        "close",
    ]
    assert picture[1:5] == ['load "denoised.fits"', 'save "processed"', "mirrorx", 'savejpg "preview" 90']
    assert not any(command.startswith("autostretch") for command in picture)


def test_a_measured_sky_is_written_as_an_mtf_command() -> None:
    """Verify the measured black point and midtones give `mtf ... 1`."""
    stretch = SkyStretch(0.2, CLIP, black_point=0.0097, midtones=0.123456789)

    assert stretch.command() == "mtf 0.009700000 0.123456789 1"
    assert SkyStretch(0.2, CLIP).command() == "autostretch -2.8 0.200"


def test_the_bright_object_script_clips_stretches_and_saves_a_jpeg() -> None:
    """The script applies Siril's mtf between two points, then saves a JPEG."""
    commands = build_bright_object_script("Moon.fits", "Moon_preview", 0.0002, 0.361, 0.509, 1.0)
    assert commands[1:6] == [
        'load "Moon.fits"',
        "mtf 0.000200 0.361000 0.509000",
        'save "processed"',
        "mirrorx",
        'savejpg "Moon_preview" 90',
    ]
    assert not any(command.startswith(("fmul", "autostretch")) for command in commands)


def test_a_stack_with_values_above_one_is_multiplied_down_before_the_stretch() -> None:
    """A scale below 1 adds an fmul command ahead of the mtf."""
    commands = build_bright_object_script("Moon.fits", "Moon_preview", 0.0005, 0.4, 1.0, 0.25)
    assert commands[2] == "fmul 0.250000000"
    assert commands[3].startswith("mtf ")


def test_green_removal_is_added_before_the_stretch_only_when_asked() -> None:
    """Verify `rmgreen` sits between load and stretch, and is not a default."""
    plain = build_preview_script("a.fits", "a_preview", SkyStretch(0.2, CLIP))
    with_green = build_preview_script("a.fits", "a_preview", SkyStretch(0.2, CLIP), remove_green=True)
    stretch_only = build_stretch_script("a.fits", "stretched", SkyStretch(0.2, CLIP), remove_green=True)

    assert "rmgreen" not in plain
    assert with_green[1:4] == ['load "a.fits"', "rmgreen", "autostretch -2.8 0.200"]
    assert stretch_only[1:4] == ['load "a.fits"', "rmgreen", "autostretch -2.8 0.200"]
    assert "rmgreen" not in build_stretch_script("a.fits", "stretched", SkyStretch(0.2, CLIP))


def test_the_linked_stretch_is_asked_for_only_when_wanted() -> None:
    """Verify the shared stretch adds `-linked`, and nothing else does."""
    separate = build_preview_script("a.fits", "a_preview", SkyStretch(0.2, CLIP))
    shared = build_preview_script("a.fits", "a_preview", SkyStretch(0.2, CLIP, linked=True))

    assert "autostretch -2.8 0.200" in separate
    assert "autostretch -linked -2.8 0.200" in shared
    assert "autostretch -linked -2.8 0.200" in build_stretch_script(
        "a.fits", "s", SkyStretch(0.2, CLIP, linked=True)
    )


def test_every_plate_solve_keeps_the_image_the_right_way_up() -> None:
    """Verify the one plate-solve command always carries `-noflip`."""
    assert platesolve_command("gaia") == "platesolve -catalog=gaia -noflip"
    assert platesolve_command("localgaia") == "platesolve -catalog=localgaia -noflip"


def test_the_pcc_script_solves_the_field_only_when_it_has_no_solution() -> None:
    """Verify `platesolve` comes before `pcc`, and only when asked for."""
    unsolved = build_pcc_script("a.fits", "colour", solve=True)
    solved = build_pcc_script("a.fits", "colour", solve=False)

    assert unsolved == [
        "requires 1.4.0",
        'load "a.fits"',
        "platesolve -catalog=gaia -noflip",
        "pcc -catalog=gaia",
        'save "colour"',
        "close",
    ]
    assert solved[1:4] == ['load "a.fits"', "pcc -catalog=gaia", 'save "colour"']


def test_the_pcc_script_reads_a_local_catalog_when_given_one() -> None:
    """Verify the script sets the catalog path and asks for `localgaia`."""
    commands = build_pcc_script("a.fits", "colour", solve=True, local_catalog="/data/cat.dat")

    assert commands[1:6] == [
        "set core.catalogue_gaia_astro=/data/cat.dat",
        'load "a.fits"',
        "platesolve -catalog=localgaia -noflip",
        "pcc -catalog=localgaia",
        'save "colour"',
    ]
    assert not any("-catalog=gaia" in command for command in commands)


def test_an_imprecise_colour_calibration_is_read_from_the_output() -> None:
    """Verify Siril's warning about a poor colour fit is found."""
    assert reports_imprecise_solution("log: PCC found an imprecise solution\n")
    assert not reports_imprecise_solution("log: PCC done\n")


def test_the_local_gaia_catalog_is_found_in_the_first_folder_that_has_it(tmp_path: Path) -> None:
    """Verify the search order and the answer when nothing is installed."""
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (second / "siril_cat_healpix8_astro.dat").write_bytes(b"x")

    assert find_local_gaia_catalog([str(first), str(second)]) == str(second / "siril_cat_healpix8_astro.dat")
    assert find_local_gaia_catalog([str(first)]) is None


def test_the_starnet_script_stretches_a_linear_image_only() -> None:
    """Verify `-stretch` is asked for a linear image and not for a picture."""
    assert build_starnet_script("colour.fits") == [
        "requires 1.2.0",
        'load "colour.fits"',
        "starnet -stretch",
        "close",
    ]
    assert build_starnet_script("picture.fits", stretch=False) == [
        "requires 1.2.0",
        'load "picture.fits"',
        "starnet",
        "close",
    ]


def _record_subprocess(monkeypatch: pytest.MonkeyPatch, settings_folder: Path, output: str = "") -> dict:
    """Replace `run_registered` and the lock inside the script runner.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        The test's patcher.
    settings_folder : `pathlib.Path`
        Where the private settings file is made.
    output : `str`, optional
        What the stand-in Siril prints.

    Returns
    -------
    captured : `dict`
        Filled in with ``arguments``, and ``script`` if one was passed, when
        the module runs a program.
    """
    captured: dict = {}

    def fake_run(arguments: list[str], _timeout_seconds: float) -> SimpleNamespace:
        """Record the command line and the script file.

        Returns
        -------
        completed : `types.SimpleNamespace`
            A successful result, shaped like `subprocess.CompletedProcess`.
        """
        captured["arguments"] = arguments
        captured["script"] = Path(arguments[-1]).read_text(encoding="utf-8")
        return SimpleNamespace(args=arguments, returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(script_run, "run_registered", fake_run)
    monkeypatch.setattr(script_run, "siril_process_lock", nullcontext)
    monkeypatch.setattr(
        script_run,
        "prepare_private_settings",
        lambda siril_executable: str(settings_folder / "siril_settings.ini"),
    )
    return captured


def test_a_flatpak_siril_gets_host_file_access_and_the_private_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a Flatpak Siril gets host files, the settings and the folder."""
    captured = _record_subprocess(monkeypatch, tmp_path / "settings")
    work = tmp_path / "work"
    work.mkdir()

    succeeded = run_siril_script(str(work), ["autostretch"], "flatpak run org.siril.Siril")

    assert succeeded
    assert captured["arguments"][:4] == ["flatpak", "run", "--filesystem=host", "org.siril.Siril"]
    assert captured["arguments"][4:6] == ["-i", str(tmp_path / "settings" / "siril_settings.ini")]
    assert captured["arguments"][6:8] == ["-d", str(work)]
    assert captured["script"] == "autostretch\n"
    assert list(work.iterdir()) == []


def test_offline_mode_is_asked_for_only_when_wanted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify `-o` is added for an offline run and not by default."""
    captured = _record_subprocess(monkeypatch, tmp_path)

    run_siril_script(str(tmp_path), ["noop"], "siril-cli")
    assert "-o" not in captured["arguments"]
    run_siril_script(str(tmp_path), ["noop"], "siril-cli", offline=True)
    assert captured["arguments"][:2] == ["siril-cli", "-o"]


def test_a_run_that_must_not_wait_skips_the_siril_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify only ``wait_for_slot=False`` runs without taking the lock."""
    _record_subprocess(monkeypatch, tmp_path)
    taken: list[bool] = []

    def recording_lock() -> nullcontext:
        """Note that the slot was taken.

        Returns
        -------
        scope : `contextlib.nullcontext`
            A scope that does nothing.
        """
        taken.append(True)
        return nullcontext()

    monkeypatch.setattr(script_run, "siril_process_lock", recording_lock)

    run_siril_script(str(tmp_path), ["noop"], "siril-cli")
    run_siril_script(str(tmp_path), ["noop"], "siril-cli", wait_for_slot=False)

    assert taken == [True]


def test_the_text_siril_printed_can_be_read_after_a_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify `last_siril_output` gives the output of the last script."""
    _record_subprocess(monkeypatch, tmp_path, output="log: hello from Siril\n")

    run_siril_script(str(tmp_path), ["noop"], "siril-cli")

    assert last_siril_output() == "log: hello from Siril\n"
    assert last_siril_output(include_errors=True) == "log: hello from Siril\n"
