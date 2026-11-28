#!/usr/bin/env python3
"""Purpose: Join the study's runs, judgements and comparisons into tables.

Description: The script reads ``runs.jsonl``, ``judge.jsonl``,
``compare.jsonl`` and ``manifest.json`` from ``<study>/out/``, keeps the last
record of each solver, frame and mode, and writes one row per run to
``results.csv`` and ``results.json``. It then prints the summary numbers the
register entry quotes: solve rates by solver, mode and camera group; medians
with the smallest and largest value of the hinted solutions; times of the
other modes; and the Siril minus solve-field differences by group.

Usage::

    summarize.py --study <folder>
"""

import argparse
import csv
import json
import statistics
from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path

from study_common import add_study_argument, open_study

JUDGE_KEYS = [
    "center_ra",
    "center_dec",
    "scale_arcsec_px",
    "rotation_deg",
    "parity",
    "sip_order",
    "fwhm_px",
    "centroids",
    "gaia_stars",
    "ast01_n",
    "shift_ra_arcsec",
    "shift_dec_arcsec",
    "shift_px",
    "scatter_median_arcsec",
    "rms_arcsec",
    "refit_applied",
    "refit_reason",
    "refit_n",
    "refit_rms_before_arcsec",
    "refit_rms_after_arcsec",
    "refit_shift_arcsec",
    "before_fraction_of_fwhm",
    "gate_fraction_of_fwhm",
    "gate_pass",
]
COMPARE_KEYS = [
    "center_sep_arcsec",
    "scale_diff_percent",
    "rotation_diff_deg",
    "parity_same",
    "grid_max_diff_arcsec",
    "grid_max_diff_px",
]
SUMMARY_KEYS = [
    "wall_s",
    "cpu_s",
    "solver_fit_matched",
    "solver_fit_rms_arcsec",
    "sip_order",
    "shift_px",
    "scatter_median_arcsec",
    "refit_rms_before_arcsec",
    "refit_rms_after_arcsec",
    "refit_shift_arcsec",
    "before_fraction_of_fwhm",
    "gate_fraction_of_fwhm",
    "fwhm_px",
]
GROUPS = ["zwo", "nikon", "stack"]


def last_by(path: Path, key: Callable[[dict], tuple]) -> dict[tuple, dict]:
    """Read a JSON-lines file, keeping the last record per key.

    Parameters
    ----------
    path : `pathlib.Path`
        The JSON-lines file.
    key : callable
        Gives the key of a record.

    Returns
    -------
    records : `dict` [`tuple`, `dict`]
        The last record of each key.
    """
    records = {}
    with open(path) as handle:
        for line in handle:
            record = json.loads(line)
            records[key(record)] = record
    return records


def summary(values: Iterable, function: Callable = statistics.median) -> float | None:
    """Summarize values, ignoring missing ones.

    Parameters
    ----------
    values : iterable
        The values; None entries are skipped.
    function : callable
        The statistic, the median by default.

    Returns
    -------
    value : `float` or `None`
        The statistic rounded to 3 decimals, or None when no value is left.
    """
    kept = [value for value in values if value is not None]
    return round(function(kept), 3) if kept else None


def group(ident: str) -> str:
    """Return the camera group of a frame id.

    Parameters
    ----------
    ident : `str`
        The frame's study id.

    Returns
    -------
    group : `str`
        ``stack``, ``nikon`` or ``zwo``.
    """
    if "stack" in ident:
        return "stack"
    return "nikon" if "_n_" in ident else "zwo"


def build_rows(runs: dict, judgements: dict, comparisons: dict, manifest: dict) -> list[dict]:
    """Join each run with its frame, its judgement and its comparison.

    Parameters
    ----------
    runs, judgements : `dict`
        Last records keyed by (solver, id, mode).
    comparisons : `dict`
        Last comparison keyed by (id, mode).
    manifest : `dict`
        The manifest rows keyed by id.

    Returns
    -------
    rows : `list` [`dict`]
        One flat row per run.
    """
    rows = []
    for key, run in sorted(runs.items()):
        if run["mode"] == "timing":
            continue
        judgement = judgements.get(key, {})
        frame = manifest[run["id"]]
        row = {
            "id": run["id"],
            "note": frame["note"],
            "date_obs": frame["DATE-OBS"],
            "exptime": frame["EXPTIME"],
            "hdr_ra": frame["RA"],
            "hdr_dec": frame["DEC"],
            "hdr_objctra": frame["OBJCTRA"],
            "hdr_objctdec": frame["OBJCTDEC"],
            "hdr_focallen": frame["FOCALLEN"],
            "hdr_xpixsz": frame["XPIXSZ"],
            "bayer": frame["BAYERPAT"],
            "solver": run["solver"],
            "mode": run["mode"],
            "ok": run["ok"],
            "wall_s": run["wall_s"],
            "cpu_s": run["cpu_s"],
            "solver_fit_rms_arcsec": run.get("fit_rms_arcsec"),
            "solver_fit_matched": run.get("fit_matched"),
            "siril_sigx_px": run.get("sigx_px"),
            "siril_sigy_px": run.get("sigy_px"),
            "index": run.get("index"),
            "siril_moved_from_hint": run.get("moved_from_hint"),
        }
        row.update({name: judgement.get(name) for name in JUDGE_KEYS})
        comparison = comparisons.get((run["id"], run["mode"])) if run["solver"] == "siril" else None
        for name in COMPARE_KEYS:
            row["siril_minus_sf_" + name] = comparison.get(name) if comparison else None
        rows.append(row)
    return rows


def print_summary(rows: list[dict], comparisons: dict) -> None:
    """Print the grouped statistics the register entry quotes.

    Parameters
    ----------
    rows : `list` [`dict`]
        The joined rows.
    comparisons : `dict`
        Last comparison keyed by (id, mode).
    """
    print("== solve rates (solved, tried)")
    rates = defaultdict(lambda: [0, 0])
    for row in rows:
        rates[row["solver"], row["mode"], group(row["id"])][0] += bool(row["ok"])
        rates[row["solver"], row["mode"], group(row["id"])][1] += 1
    for key in sorted(rates):
        print(key, rates[key])
    print("== per solver and group, hint mode: median [smallest, largest]")
    for solver in ["solve-field", "siril"]:
        for name in GROUPS:
            selected = [
                r for r in rows if r["solver"] == solver and r["mode"] == "hint" and group(r["id"]) == name
            ]
            selected = [r for r in selected if r["ok"]]
            if not selected:
                continue
            numbers = {}
            for key in SUMMARY_KEYS:
                values = [r[key] for r in selected if r[key] is not None]
                numbers[key] = (
                    f"{summary(values)} [{summary(values, min)}, {summary(values, max)}]" if values else None
                )
            print(
                solver, name, len(selected), numbers, "gate_pass", sum(bool(r["gate_pass"]) for r in selected)
            )
    for solver in ["solve-field", "siril"]:
        for mode in ["blind", "wrong2", "wrong5"]:
            selected = [r for r in rows if r["solver"] == solver and r["mode"] == mode and r["ok"]]
            if selected:
                walls = [r["wall_s"] for r in selected]
                print(solver, mode, "wall median", summary(walls), "largest", summary(walls, max))
    print("== Siril minus solve-field (hint), by group: median of the size [largest]")
    for name in GROUPS:
        selected = [c for c in comparisons.values() if c["mode"] == "hint" and group(c["id"]) == name]
        if not selected:
            continue
        numbers = {}
        for key in COMPARE_KEYS:
            if key == "parity_same":
                continue
            sizes = [abs(c[key]) for c in selected]
            numbers[key] = f"{summary(sizes)} [{summary(sizes, max)}]"
        print(name, len(selected), numbers, "parity_same", all(c["parity_same"] for c in selected))


def main() -> None:
    """Write the tables and print the grouped statistics."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_study_argument(parser)
    study = open_study(parser.parse_args().study)
    runs = last_by(study.out / "runs.jsonl", lambda r: (r["solver"], r["id"], r["mode"]))
    judgements = last_by(study.out / "judge.jsonl", lambda r: (r["solver"], r["id"], r["mode"]))
    comparisons = last_by(study.out / "compare.jsonl", lambda r: (r["id"], r["mode"]))
    rows = build_rows(runs, judgements, comparisons, study.read_manifest())
    with open(study.out / "results.csv", "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    results = {"runs": rows, "comparisons": list(comparisons.values())}
    (study.out / "results.json").write_text(json.dumps(results, indent=1, default=str))
    print_summary(rows, comparisons)


if __name__ == "__main__":
    main()
