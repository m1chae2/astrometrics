#!/usr/bin/env python3
"""Purpose: Extra Siril checks of scale errors, search radius and repeats.

Description: On one ZWO frame and one Nikon frame the script runs Siril's own
solver with: a focal length 20% too long, 20% too short and 10% too long; a
position 2, 1 and 0.5 degrees off with the near search turned off
(``-radius=0``); a position about 20 degrees off with a 30 degree near
search; Siril's ``-downscale`` option; and the same command twice, to check
that a repeat gives the same solution. The results go to
``<study>/out/extras.json``.

Usage::

    extras.py --study <folder>
"""

import argparse
import json
from pathlib import Path

import run_solvers
from astropy.io import fits
from study_common import StudyFolder, add_study_argument, open_study

FRAME_IDS = ["m31_z_060", "m13_n_B001"]
SAVED_CARDS = ["CRVAL1", "CRVAL2", "CDELT1", "PC1_1", "PC1_2", "A_2_0"]


def run_one(study: StudyFolder, ident: str, line: str, tag: str) -> dict:
    """Run one ``platesolve`` command on a frame and return the key numbers.

    Parameters
    ----------
    study : `StudyFolder`
        The study folder.
    ident : `str`
        The frame's study id.
    line : `str`
        The ``platesolve`` command.
    tag : `str`
        The name of the case.

    Returns
    -------
    result : `dict`
        Success, times, a few solution cards, or Siril's error lines.
    """
    output_stem = study.work / f"x_{ident}_{tag}"
    script = run_solvers.siril_script(
        study, [f"load {study.frames / (ident + '.fits')}", line, f"save {output_stem}"]
    )
    output, _, wall, _, cpu = run_solvers.run_siril_script(study, script)
    script.unlink()
    result = {"id": ident, "tag": tag, "cmd": line, "ok": "Siril solve succeeded" in output}
    result.update({"wall_s": round(wall, 3), "cpu_s": cpu})
    solved_path = Path(f"{output_stem}.fits")
    if solved_path.exists():
        header = fits.getheader(solved_path)
        result["wcs"] = {key: header.get(key) for key in SAVED_CARDS}
        solved_path.unlink()
    if not result["ok"]:
        words = ["fail", "error", "cannot", "not", "no "]
        result["errors"] = [
            line
            for line in output.splitlines()
            if "log:" in line and any(word in line.lower() for word in words)
        ][-4:]
    (study.out / "logs" / f"siril_extra__{ident}__{tag}.log").write_text(output)
    print(json.dumps(result)[:500], flush=True)
    return result


def main() -> None:
    """Run the extra cases and save them."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_study_argument(parser)
    study = open_study(parser.parse_args().study)
    manifest = study.read_manifest()
    results = []
    for ident in FRAME_IDS:
        row = manifest[ident]
        ra, dec, focal, pixel = row["RA"], row["DEC"], float(row["FOCALLEN"]), row["XPIXSZ"]
        tail = f"-pixelsize={pixel} -force -noflip"
        cases = [
            (f"platesolve {ra},{dec} -focal={focal * 1.2:.1f} {tail}", "focal_plus20"),
            (f"platesolve {ra},{dec} -focal={focal * 0.8:.1f} {tail}", "focal_minus20"),
            (f"platesolve {ra},{dec} -focal={focal * 1.1:.1f} {tail}", "focal_plus10"),
            (f"platesolve {ra},{dec + 2} -focal={focal} {tail} -radius=0", "wrong2_no_near_search"),
            (f"platesolve {ra},{dec + 1} -focal={focal} {tail} -radius=0", "wrong1_no_near_search"),
            (f"platesolve {ra},{dec + 0.5} -focal={focal} {tail} -radius=0", "wrong0.5_no_near_search"),
            (f"platesolve {ra + 20},{dec + 15} -focal={focal} {tail} -radius=30", "far25_radius30"),
            (f"platesolve {ra},{dec} -focal={focal} {tail} -downscale", "downscale"),
        ]
        results += [run_one(study, ident, line, tag) for line, tag in cases]
        repeat = f"platesolve {ra},{dec} -focal={focal} {tail}"
        first = run_one(study, ident, repeat, "repeat_a")
        second = run_one(study, ident, repeat, "repeat_b")
        results += [first, second]
        print(ident, "repeat identical:", first.get("wcs") == second.get("wcs"))
    (study.out / "extras.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
