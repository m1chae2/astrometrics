"""Purpose: Print key TPMs next to the size of the data they were measured on.

Description: Every TPM run saves ``tpm.json`` with its mode (``synthetic`` or
``real-snapshot``), its observatory profile and a ``data_size`` block (targets,
light frames, stars, nights, guiding samples, database and library sizes).
This script reads every results folder, and prints one row per run, so you
can see how a TPM grows as the data grows. Plot the CSV (``--csv``) with any
tool. Runs in the same mode and profile are the ones to compare.

Usage::

    .venv/bin/python build/tpm/scaling.py [results root] [--csv scaling.csv]
"""

import argparse
import csv
import json
import sys
from pathlib import Path

#: Data-size columns: label and the key in ``info.data_size``.
DATA_COLUMNS = (
    ("targets", "targets"),
    ("frames", "light_frames"),
    ("stars", "stars"),
    ("nights", "observing_nights"),
    ("guide_samples", "guiding_samples"),
)
#: TPM columns, by TPM name.
TPM_COLUMNS = (
    "time_to_app_ready",
    "display_switch_median",
    "display_switch_p95",
    "rpc_p95[processing:stack_summary]",
    "rpc_p95[target:get_targets]",
    "rpc_p95[history:night_quality]",
    "backend_peak_rss",
    "js_heap_after_last_lap",
    "planetarium_fps_panning",
    "fps_hw",
    "pan_frame_ms_p95",
)


def load_rows(root: Path) -> list[dict[str, object]]:
    """Read every ``tpm.json`` under a results folder into flat rows.

    Parameters
    ----------
    root : `pathlib.Path`
        The results folder that holds one subfolder per run.

    Returns
    -------
    rows : `list` [`dict`]
        One row per run, oldest first: run name, mode, profile, the
        data-size columns, database and library sizes in GB, and the TPMs.
    """
    rows = []
    for file in sorted(root.glob("*/tpm.json")):
        run = json.loads(file.read_text())
        sizes = (run.get("info") or {}).get("data_size") or {}
        database_bytes = sum(value or 0 for value in (sizes.get("database_bytes") or {}).values())
        row: dict[str, object] = {
            "run": file.parent.name,
            "mode": run.get("mode", "synthetic"),
            "profile": run.get("observatory_profile", "offline"),
        }
        for label, key in DATA_COLUMNS:
            row[label] = sizes.get(key)
        row["db_gb"] = database_bytes / 1024**3 if sizes else None
        library = sizes.get("library_bytes")
        row["library_gb"] = library / 1024**3 if library else None
        values = {tpm["name"]: tpm["value"] for tpm in run["tpms"]}
        for name in TPM_COLUMNS:
            row[name] = values.get(name)
        rows.append(row)
    return rows


def format_cell(value: object) -> str:
    """Format one table cell.

    Parameters
    ----------
    value : `object`
        A number, text or `None`.

    Returns
    -------
    text : `str`
        ``-`` for `None`, otherwise a short form.
    """
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.1f}" if abs(value) >= 10 else f"{value:.2f}"
    return str(value)


def main() -> int:
    """Print the table and optionally write the CSV.

    Returns
    -------
    status : `int`
        0, or 1 if no run is found.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", nargs="?", default=str(Path(__file__).with_name("results")))
    parser.add_argument("--csv")
    arguments = parser.parse_args()
    rows = load_rows(Path(arguments.root))
    if not rows:
        print(f"No tpm.json files under {arguments.root}.", file=sys.stderr)
        return 1
    columns = list(rows[0])
    table = [[format_cell(row.get(column)) for column in columns] for row in rows]
    widths = [max(len(column), *(len(line[index]) for line in table)) for index, column in enumerate(columns)]
    print("  ".join(column.ljust(width) for column, width in zip(columns, widths, strict=True)))
    for line in table:
        print("  ".join(cell.ljust(width) for cell, width in zip(line, widths, strict=True)))
    if arguments.csv:
        with open(arguments.csv, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        print(f"CSV written to {arguments.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
