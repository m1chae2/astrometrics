#!/usr/bin/env python3
"""Purpose: Time ten plate solves in a row, as mount centering would run them.

Description: Mount centering solves one frame after each slew, so the time of
repeated solves matters more than one solve. The script times three cases on
ten M 31 frames, each with the header hint: (a) Siril, one ``siril-cli``
process with ten ``load`` and ``platesolve`` commands (one open session);
(b) Siril, ten separate ``siril-cli`` processes; (c) solve-field, ten
separate calls with the app's command line. The result goes to
``<study>/out/centering_timing.json``.

Usage::

    centering_timing.py --study <folder>
"""

import argparse
import json

import run_solvers
from study_common import add_study_argument, open_study

FRAME_IDS = [
    "m31_z_001_20s",
    "m31_z_002_30s",
    "m31_z_020",
    "m31_z_040",
    "m31_z_060",
    "m31_z_080",
    "m31_z_100",
    "m31_z_119",
    "m31_z_121_offfield",
    "m31_z_004_120s",
]


def main() -> None:
    """Run the three timing cases and save them."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_study_argument(parser)
    study = open_study(parser.parse_args().study)
    rows = study.read_manifest()
    result = {"frames": FRAME_IDS}
    lines = []
    for ident in FRAME_IDS:
        lines += [
            f"load {study.frames / (ident + '.fits')}",
            run_solvers.platesolve_line(rows[ident], "hint"),
        ]
    script = run_solvers.siril_script(study, lines)
    output, return_code, wall, _, cpu = run_solvers.run_siril_script(study, script)
    script.unlink()
    result["siril_one_session"] = {
        "wall_s": round(wall, 3),
        "cpu_s": cpu,
        "solved": output.count("Siril solve succeeded"),
        "rc": return_code,
    }
    separate = [run_solvers.run_siril(study, rows[ident], "hint", tag="timing") for ident in FRAME_IDS]
    result["siril_separate"] = {
        "wall_s": round(sum(r["wall_s"] for r in separate), 3),
        "cpu_s": round(sum(r["cpu_s"] for r in separate), 3),
        "solved": sum(r["ok"] for r in separate),
        "per_solve_wall_s": [r["wall_s"] for r in separate],
    }
    separate = [run_solvers.run_solve_field(study, rows[ident], "hint") for ident in FRAME_IDS]
    result["solve_field_separate"] = {
        "wall_s": round(sum(r["wall_s"] for r in separate), 3),
        "cpu_s": round(sum(r["cpu_s"] for r in separate), 3),
        "solved": sum(r["ok"] for r in separate),
        "per_solve_wall_s": [r["wall_s"] for r in separate],
    }
    (study.out / "centering_timing.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
