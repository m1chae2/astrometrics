#!/usr/bin/env python3
"""Purpose: Run solve-field and Siril's own plate solver on the study frames.

Description: For each frame in the manifest and each hint mode, the script
runs one solver once. It saves the solved sky-map cards to
``<study>/out/wcs/<solver>/<id>__<mode>.hdr`` and appends one JSON line per
run to ``<study>/out/runs.jsonl`` (success, wall-clock and CPU time, the
solver's messages). Each run's full output goes to ``<study>/out/logs/``.

solve-field gets the command line the app's driver builds (one attempt, no
blind retry). Siril 1.4 runs headless (``siril-cli``) from its Flatpak, in
offline mode, with ``platesolve <ra,dec> -focal= -pixelsize= -force
-noflip`` and its local Gaia catalog. Siril always reads a private settings
file in the study folder (``-i``), made from a copy of the user's Siril
settings with ``update_default_scale=false``. Siril otherwise stores the
solved focal length and pixel size as the user's defaults after each solve.
The user's own settings file is only read, once, to make the copy.

Modes
-----
hint
    Header RA and Dec, and the header scale (``FOCALLEN``, ``XPIXSZ``).
blind
    No position and, for solve-field, no scale. Siril's own solver needs a
    position, so it gets a copy of the frame with no RA or Dec cards.
wrong2
    A position 2 degrees north of the header position, header scale.
wrong5
    A position 5 degrees north of the header position, header scale.

Usage::

    run_solvers.py --study <folder> <solve-field|siril> [ids...]
        [--modes hint,blind,wrong2,wrong5] [--extra "<platesolve options>"]
        [--tag <suffix>] [--siril-settings-source <Siril .ini, read only>]
"""

import argparse
import json
import os
import re
import resource
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from astropy.io import fits
from study_common import WCS_CARD_PATTERN, StudyFolder, add_study_argument, open_study, use_repository_code

use_repository_code()

# Hint offsets in degrees of declination, by mode.
OFFSETS_DEGREES = {"hint": 0.0, "wrong2": 2.0, "wrong5": 5.0}
# The app's scale window: 5% each side of the header scale
# (plate_solution_steps.py), relaxed by 0.8 and 1.2 in the driver.
SCALE_WINDOW_FRACTION = 0.05
# Arcseconds per radian divided by 1000, for a scale from micrometers and mm.
ARCSEC_PER_MICROMETER_PER_MM = 206.265
# The app's search radius around the hint, in degrees (star_identifier.py).
HINT_RADIUS_DEGREES = 2.0
SIRIL_FLATPAK = "org.siril.Siril"


def scale_window(row: dict) -> tuple[float, float]:
    """Return the pipeline's pixel-scale window from the header.

    Parameters
    ----------
    row : `dict`
        The frame's manifest row.

    Returns
    -------
    low, high : `float`
        The window in arcseconds per pixel, 5% each side of the header scale.
    """
    scale = ARCSEC_PER_MICROMETER_PER_MM * float(row["XPIXSZ"]) / float(row["FOCALLEN"])
    return scale * (1 - SCALE_WINDOW_FRACTION), scale * (1 + SCALE_WINDOW_FRACTION)


def hint_position(row: dict, mode: str) -> tuple[float, float] | None:
    """Return the position hint for a mode.

    Parameters
    ----------
    row : `dict`
        The frame's manifest row.
    mode : `str`
        One of ``hint``, ``blind``, ``wrong2`` and ``wrong5``.

    Returns
    -------
    position : `tuple` [`float`, `float`] or `None`
        Right ascension and declination in degrees, or None for ``blind``.
    """
    if mode == "blind":
        return None
    return float(row["RA"]), float(row["DEC"]) + OFFSETS_DEGREES[mode]


def save_wcs(study: StudyFolder, header: fits.Header, solver: str, ident: str, mode: str) -> None:
    """Save the plate-solution cards of a solved header.

    Parameters
    ----------
    study : `StudyFolder`
        The study folder.
    header : `astropy.io.fits.Header`
        The solver's output header.
    solver : `str`
        ``solve-field`` or ``siril``.
    ident : `str`
        The frame's study id.
    mode : `str`
        The hint mode (and tag) of the run.
    """
    folder = study.out / "wcs" / solver
    folder.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(WCS_CARD_PATTERN.pattern + r"|^NAXIS[12]$")
    cards = fits.Header([card for card in header.cards if pattern.match(card.keyword)])
    cards.tofile(folder / f"{ident}__{mode}.hdr", sep="\n", endcard=False, padding=False, overwrite=True)


def write_log(study: StudyFolder, name: str, text: str) -> None:
    """Save one run's full output.

    Parameters
    ----------
    study : `StudyFolder`
        The study folder.
    name : `str`
        The log file's name, without extension.
    text : `str`
        The output to save.
    """
    (study.out / "logs" / f"{name}.log").write_text(text)


def run_solve_field(study: StudyFolder, row: dict, mode: str, timeout: float = 300) -> dict:
    """Run solve-field once with the command line the app's driver builds.

    Parameters
    ----------
    study : `StudyFolder`
        The study folder.
    row : `dict`
        The frame's manifest row.
    mode : `str`
        The hint mode.
    timeout : `float`
        Seconds before the run is stopped.

    Returns
    -------
    record : `dict`
        Success, times, the index file that solved it and the solver's fit
        residual and matched-star count.
    """
    from astrometricslib._internal.drivers.astrometry_net_driver import read_corr_fit_statistics

    ident = row["id"]
    temporary = Path(tempfile.mkdtemp(dir=study.work, prefix="sf_"))
    configuration = temporary / "astrometry.cfg"
    configuration.write_text("add_path /usr/share/astrometry\nautoindex\ncpulimit 300")
    working = temporary / "input_image.fits"
    os.symlink(study.frames / f"{ident}.fits", working)
    command = [
        "solve-field",
        "--config",
        str(configuration),
        "--no-plots",
        "--overwrite",
        "--downsample",
        "2",
    ]
    command += ["--uniformize", "0", "--dir", str(temporary)]
    position = hint_position(row, mode)
    if position is not None:
        low, high = scale_window(row)
        command += [
            "--scale-units",
            "arcsecperpix",
            "--scale-low",
            str(low * 0.8),
            "--scale-high",
            str(high * 1.2),
        ]
        command += ["--ra", str(position[0]), "--dec", str(position[1]), "--radius", str(HINT_RADIUS_DEGREES)]
    command.append(str(working))
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.monotonic()
    try:
        process = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, start_new_session=True
        )
        return_code, output = process.returncode, process.stdout + process.stderr
    except subprocess.TimeoutExpired as error:
        partial = error.stdout or b""
        return_code = "timeout"
        output = partial.decode(errors="replace") if isinstance(partial, bytes) else str(partial)
    wall = time.monotonic() - started
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)
    solved_path = temporary / "input_image.new"
    record = {
        "solver": "solve-field",
        "id": ident,
        "mode": mode,
        "ok": solved_path.exists(),
        "rc": return_code,
        "wall_s": round(wall, 3),
        "cpu_s": round(cpu, 3),
        "cmd": " ".join(command),
    }
    index_names = re.findall(r"solved with index (\S+)", output)
    record["index"] = index_names[-1] if index_names else None
    if record["ok"]:
        save_wcs(study, fits.getheader(solved_path, 0), "solve-field", ident, mode)
        record["fit_rms_arcsec"], record["fit_matched"] = read_corr_fit_statistics(
            str(temporary / "input_image.corr")
        )
    else:
        record["tail"] = output[-600:]
    write_log(study, f"solve-field__{ident}__{mode}", output)
    shutil.rmtree(temporary)
    return record


def prepare_siril_settings(study: StudyFolder, source: str | None) -> Path:
    """Make or check Siril's private settings file in the study folder.

    Parameters
    ----------
    study : `StudyFolder`
        The study folder.
    source : `str` or `None`
        The user's Siril settings file, read once to make the private copy.
        None means the private copy must already exist.

    Returns
    -------
    path : `pathlib.Path`
        The private settings file, with ``update_default_scale=false``.

    Raises
    ------
    SystemExit
        When the source is the private file itself, the private file is
        missing, or it does not turn off ``update_default_scale``.
    """
    target = study.siril_settings
    if source is not None:
        source_path = Path(source).expanduser().resolve()
        if source_path == target.resolve():
            raise SystemExit(
                "The Siril settings source is the study's own private file; give the user's file."
            )
        lines = source_path.read_text().splitlines()
        lines = [line for line in lines if not line.startswith("update_default_scale=")]
        section = lines.index("[astrometry]")
        lines.insert(section + 1, "update_default_scale=false")
        target.write_text("\n".join(lines) + "\n")
    if not target.exists():
        raise SystemExit("No private Siril settings file yet; pass --siril-settings-source once.")
    if "update_default_scale=false" not in target.read_text().splitlines():
        raise SystemExit(f"{target} does not set update_default_scale=false; make it again.")
    return target


def siril_script(study: StudyFolder, lines: list[str]) -> Path:
    """Write a Siril script into the study's work folder.

    Parameters
    ----------
    study : `StudyFolder`
        The study folder.
    lines : `list` [`str`]
        The script's commands.

    Returns
    -------
    path : `pathlib.Path`
        The script file.
    """
    handle, name = tempfile.mkstemp(dir=study.work, suffix=".ssf")
    os.close(handle)
    path = Path(name)
    path.write_text("requires 1.4.0\n" + "\n".join(lines) + "\n")
    return path


def run_siril_script(
    study: StudyFolder, script: Path, timeout: float = 600
) -> tuple[str, object, float, float, float]:
    """Run a Siril script headless, offline, with the private settings file.

    Parameters
    ----------
    study : `StudyFolder`
        The study folder.
    script : `pathlib.Path`
        The Siril script.
    timeout : `float`
        Seconds before the run is stopped.

    Returns
    -------
    output : `str`
        Siril's output.
    return_code : `int` or `str`
        The exit code, or ``timeout``.
    wall : `float`
        Wall-clock seconds, Flatpak start included.
    sandbox_real : `float` or `None`
        Wall-clock seconds of ``siril-cli`` inside the sandbox.
    cpu : `float`
        CPU seconds (user and system) of ``siril-cli``.
    """
    settings = prepare_siril_settings(study, None)
    inner = f"time siril-cli -o -i {shlex.quote(str(settings))} -s {shlex.quote(str(script))}"
    command = ["flatpak", "run", "--command=bash", SIRIL_FLATPAK, "-c", inner]
    started = time.monotonic()
    try:
        process = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        output, return_code = process.stdout + process.stderr, process.returncode
    except subprocess.TimeoutExpired as error:
        output, return_code = str(error.stdout), "timeout"
    wall = time.monotonic() - started

    def seconds(name: str) -> float | None:
        """Read one line of the shell's ``time`` report.

        Parameters
        ----------
        name : `str`
            ``real``, ``user`` or ``sys``.

        Returns
        -------
        seconds : `float` or `None`
            The time, or None when the line is missing.
        """
        match = re.search(rf"^{name}\s+(\d+)m([\d.]+)s", output, re.MULTILINE)
        return int(match.group(1)) * 60 + float(match.group(2)) if match else None

    return output, return_code, wall, seconds("real"), (seconds("user") or 0) + (seconds("sys") or 0)


def platesolve_line(row: dict, mode: str, extra: str = "") -> str:
    """Build the Siril ``platesolve`` command for a mode.

    Parameters
    ----------
    row : `dict`
        The frame's manifest row.
    mode : `str`
        The hint mode.
    extra : `str`
        More ``platesolve`` options.

    Returns
    -------
    line : `str`
        The command.
    """
    position = hint_position(row, mode)
    coordinates = f"{position[0]:.6f},{position[1]:.6f} " if position else ""
    scale = f"-focal={row['FOCALLEN']} -pixelsize={row['XPIXSZ']}"
    return f"platesolve {coordinates}{scale} -force -noflip {extra}".strip()


def _read_siril_messages(output: str, record: dict) -> None:
    """Copy the numbers Siril prints about a solve into the run record.

    Parameters
    ----------
    output : `str`
        Siril's output.
    record : `dict`
        The run record, changed in place.
    """
    match = re.search(r"Total execution time: ([\d.]+) (ms|s)", output)
    if match:
        record["siril_exec_s"] = float(match.group(1)) / (1000 if match.group(2) == "ms" else 1)
    match = re.search(r"order: (\d+), nmatched: (\d+), sigx:([\d.]+), sigy:([\d.]+)", output)
    if match:
        record["sip_order"], record["fit_matched"] = int(match.group(1)), int(match.group(2))
        record["sigx_px"], record["sigy_px"] = float(match.group(3)), float(match.group(4))
    match = re.search(r"Was ([\d.]+) (arcmin|arcsec|deg)\w* from initial value", output)
    if match:
        record["moved_from_hint"] = f"{match.group(1)} {match.group(2)}"
    match = re.search(r"Fetched (\d+) stars from (.*)", output)
    if match:
        record["catalog_stars"], record["catalog"] = int(match.group(1)), match.group(2).strip()
    match = re.search(r"Using (\d+) detected stars", output)
    if match:
        record["detected"] = int(match.group(1))
    record["near_search"] = "near search" in output.lower()


def run_siril(study: StudyFolder, row: dict, mode: str, extra: str = "", tag: str | None = None) -> dict:
    """Run Siril's own solver on one frame in a fresh ``siril-cli``.

    Parameters
    ----------
    study : `StudyFolder`
        The study folder.
    row : `dict`
        The frame's manifest row.
    mode : `str`
        The hint mode.
    extra : `str`
        More ``platesolve`` options.
    tag : `str` or `None`
        The name the run is saved under; the mode when None.

    Returns
    -------
    record : `dict`
        Success, times and the numbers Siril reports about the solve.
    """
    ident = row["id"]
    tag = tag or mode
    source = study.frames / f"{ident}.fits"
    blind_copy = None
    if mode == "blind":
        blind_copy = study.work / f"blind_{ident}.fits"
        with fits.open(source, memmap=False) as hdul:
            header = hdul[0].header.copy()
            for key in ["RA", "DEC", "OBJCTRA", "OBJCTDEC"]:
                header.remove(key, ignore_missing=True, remove_all=True)
            fits.PrimaryHDU(hdul[0].data, header).writeto(blind_copy, overwrite=True)
        source = blind_copy
    output_stem = study.work / f"siril_{ident}_{tag}"
    command = platesolve_line(row, mode, extra)
    script = siril_script(study, [f"load {source}", command, f"save {output_stem}"])
    output, return_code, wall, sandbox_real, cpu = run_siril_script(study, script)
    solved_path = Path(f"{output_stem}.fits")
    record = {
        "solver": "siril",
        "id": ident,
        "mode": tag,
        "ok": "Siril solve succeeded" in output and solved_path.exists(),
        "rc": return_code,
        "wall_s": round(wall, 3),
        "sandbox_real_s": sandbox_real,
        "cpu_s": cpu,
        "cmd": command,
    }
    _read_siril_messages(output, record)
    if record["ok"]:
        save_wcs(study, fits.getheader(solved_path, 0), "siril", ident, tag)
    else:
        pattern = re.compile(r"error|fail|cannot|could not|not solve|abort", re.IGNORECASE)
        record["errors"] = [line for line in output.splitlines() if pattern.search(line)][-6:]
    write_log(study, f"siril__{ident}__{tag}", output)
    for path in [solved_path, script, blind_copy]:
        if path is not None and path.exists():
            path.unlink()
    return record


def main() -> None:
    """Run one solver over the chosen frames and modes."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_study_argument(parser)
    parser.add_argument("solver", choices=["solve-field", "siril"])
    parser.add_argument("ids", nargs="*", help="Frame ids from the manifest; all frames when none.")
    parser.add_argument("--modes", default="hint,blind,wrong2,wrong5")
    parser.add_argument("--extra", default="", help="More Siril platesolve options.")
    parser.add_argument("--tag", default="", help="A suffix for the saved name of Siril runs.")
    parser.add_argument(
        "--siril-settings-source", help="The user's Siril settings file; read once, never written."
    )
    arguments = parser.parse_args()
    study = open_study(arguments.study)
    if arguments.solver == "siril":
        prepare_siril_settings(study, arguments.siril_settings_source)
    rows = study.read_manifest()
    with open(study.out / "runs.jsonl", "a") as log:
        for ident in arguments.ids or list(rows):
            for mode in arguments.modes.split(","):
                if arguments.solver == "solve-field":
                    record = run_solve_field(study, rows[ident], mode)
                else:
                    record = run_siril(study, rows[ident], mode, arguments.extra, tag=mode + arguments.tag)
                log.write(json.dumps(record, default=str) + "\n")
                log.flush()
                keys = [
                    "solver",
                    "id",
                    "mode",
                    "ok",
                    "wall_s",
                    "cpu_s",
                    "fit_rms_arcsec",
                    "fit_matched",
                    "index",
                ]
                print(json.dumps({key: record.get(key) for key in keys}, default=str), flush=True)


if __name__ == "__main__":
    main()
