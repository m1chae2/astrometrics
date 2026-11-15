r"""Purpose: Run the parameter tuners of both libraries for one setup.

Description: Issue #175 Phase D. Each tuned parameter is derived from this
observatory's own data by a `Tuner` (``astrometricslib.parameters``). This
script collects the tuners of ``astrometricslib`` and ``wayfindinglib``,
runs them for a setup (the active one by default), checks each value
against its allowed range and minimum sample, and prints what would be set
or moved. With ``--write`` it backs up the config and saves the values,
each with where it came from.

It reads the declarations of both libraries, so it lives here rather than in
``astrometricslib`` (which must not import ``wayfindinglib``).

    .venv/bin/python build/config/tune_parameters.py
    .venv/bin/python build/config/tune_parameters.py \
        --setup "ASI533 on Apertura" --only spectroscopy.usable_range --write
"""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from astrometricslib import Astrometrics, get_configuration  # ruff: ignore[module-import-not-at-top-of-file]
from astrometricslib.parameters import (  # ruff: ignore[module-import-not-at-top-of-file]
    collect_tuners,
    run_tuning,
)
from wayfindinglib import Wayfinder  # ruff: ignore[module-import-not-at-top-of-file]


def main(argv: list[str] | None = None) -> int:
    """Run the tuners and print the report.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments; defaults to ``sys.argv``.

    Returns
    -------
    exit_code : `int`
        0 on success, 1 when the setup is not in the config.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--setup", help="The setup to tune; the active setup when left out.")
    parser.add_argument("--only", action="append", default=[], help="Run only this tuner (repeatable).")
    parser.add_argument("--write", action="store_true", help="Save the values to the config.")
    parser.add_argument("--list", action="store_true", help="List the tuners and their methods, then stop.")
    args = parser.parse_args(argv)

    tuners = collect_tuners(("astrometricslib", "wayfindinglib"))
    if args.only:
        tuners = [tuner for tuner in tuners if tuner.name in set(args.only)]
    if args.list:
        for tuner in tuners:
            names = ", ".join(parameter.name for parameter in tuner.parameters)
            print(f"{tuner.name}: {tuner.method}\n  sets: {names}")
        return 0

    configuration = get_configuration()
    setups = configuration.get_observatory_setups()
    setup = setups.setup_named(args.setup) if args.setup else setups.active_setup()
    if setup is None:
        print(f"No setup named {args.setup!r} in the config (or no active setup).")
        return 1

    astrometrics = Astrometrics(configuration)
    services = {"astrometrics": astrometrics, "wayfinder": Wayfinder(configuration, astrometrics)}
    report = run_tuning(
        tuners, setup, configuration, services=services, today=datetime.date.today(), write=args.write
    )
    print(f"Setup: {report.setup_name}")
    for notice in report.notices:
        print(f"  {notice.sentence()}")
    for line in report.rejected:
        print(f"  refused: {line}")
    for line in report.skipped:
        print(f"  skipped: {line}")
    print(f"{len(report.notices)} to set or move, {len(report.unchanged)} unchanged.")
    if report.written:
        print(f"Saved; the config was backed up to {report.backup_path}.")
    elif report.notices:
        print("Nothing written; add --write to save them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
