"""Purpose: Move a camera's gain and read noise into a setting-keyed entry.

Description: older config files hold one ``gain_e_per_adu`` and one
``read_noise_e`` per camera with no record of the gain setting, offset and
binning they were measured at. The pipelines now ignore those values. This
script rewrites a camera's section so the values become a ``sensor_noise``
entry for the setting you state. Read the value's source text in the config
to find that setting. For the ZWO ASI 533MM Pro the source text says gain 0,
offset 10, 1x1 binning, -10 C.

By default nothing is written: the script prints what it would change. Add
``--apply`` to write. The config file is first copied to a timestamped
``.bak`` file beside it. The script refuses to touch a camera whose section
already has a ``sensor_noise`` entry.

    python -m astrometricslib.scripts.migrate_camera_profiles \
        --camera "ZWO ASI 533MM Pro" --gain 0 --offset 10 --binning 1x1 \
        --temperature-c -10
    python -m astrometricslib.scripts.migrate_camera_profiles ... --apply
"""

import argparse
import sys

from astrometricslib.drivers.camera_profile_migration import MigrationSetting, migrate_camera_profiles
from astrometricslib.foundation.config import get_configuration
from astrometricslib.foundation.errors import ConfigurationError


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        The parser for this script's options.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--camera", required=True, help="The camera whose section to migrate.")
    parser.add_argument(
        "--gain", required=True, help="The gain setting (or ISO) the old values were measured at."
    )
    parser.add_argument("--offset", required=True, help="The offset the old values were measured at.")
    parser.add_argument("--binning", default="1x1", help="The binning, such as 1x1. Default 1x1.")
    parser.add_argument(
        "--temperature-c", type=float, default=None, help="The sensor temperature, in degrees C."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", help="Only print what would change. This is the default."
    )
    mode.add_argument("--apply", action="store_true", help="Write the change to the config file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the migration and print what changed.

    Parameters
    ----------
    argv : `list` [`str`], optional
        The command-line arguments. The process arguments are used when this
        is left out.

    Returns
    -------
    exit_code : `int`
        0 on success, 1 when the migration refused to run.
    """
    arguments = build_parser().parse_args(argv)
    setting = MigrationSetting(
        gain_setting=arguments.gain,
        offset=arguments.offset,
        binning=arguments.binning,
        temperature_c=arguments.temperature_c,
    )
    try:
        report = migrate_camera_profiles(
            get_configuration(), {arguments.camera: setting}, dry_run=not arguments.apply
        )
    except ConfigurationError as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 1
    for change in report.changes:
        print(f"[{change.section_name}] {'MIGRATE' if change.migrated else 'skip'}: {change.description}")
    if report.dry_run:
        print("Dry run: nothing was written. Add --apply to write.")
    elif report.backup_path is not None:
        print(f"Backup written to {report.backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
