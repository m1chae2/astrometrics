r"""Report stored telescope and ISO values that a re-scan would change.

Frame records keep a telescope name and an ISO. Older records were made with
rules that assumed this observatory's own equipment: the telescope was chosen
by looking for "Nikkor 300mm" in the file path, and every Nikon frame was
recorded at ISO 800. Records are now made from the config file (its optics and
setups) and from the image header, so a re-scan can give different answers.

This script only reports. It changes no record and no file. It shows, for every
combination, how many stored records would change, so the effect of a re-scan
can be checked first::

    python -m astrometricslib.scripts.report_equipment_disagreements

Restrict it to some targets with ``--target``.
"""

import argparse
import collections
import logging
import sys
from typing import Any

from astrometricslib import Astrometrics
from astrometricslib.drivers.fits_access import read_header
from astrometricslib.pipelines.shared.frame_optics import resolve_frame_telescope
from astrometricslib.utilities.iso_text import iso_or_gain_text, iso_or_gain_values_match
from astrometricslib.utilities.observatory_setups import ObservatorySetups

logger = logging.getLogger(__name__)


def _build_argument_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser for this script.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        Parser accepting an optional list of targets.
    """
    parser = argparse.ArgumentParser(
        prog="report_equipment_disagreements",
        description="Report stored telescope and ISO values that a re-scan would change. Changes nothing.",
    )
    parser.add_argument(
        "--target",
        action="append",
        dest="target_ids",
        metavar="TARGET_ID",
        help="Restrict to this target; repeatable. Defaults to every target.",
    )
    return parser


def find_telescope_disagreements(
    targets: list[Any], observatory_setups: ObservatorySetups
) -> collections.Counter[tuple[str, str, str]]:
    """Count frames whose stored telescope differs from the config's answer.

    Parameters
    ----------
    targets : `list`
        The targets whose frames are checked. Only light frames are used.
    observatory_setups : `ObservatorySetups`
        The optics and setups from the config.

    Returns
    -------
    disagreements : `collections.Counter`
        For each (camera, stored telescope, telescope the config gives), how
        many light frames differ. Frames that already agree are not counted.
    """
    disagreements: collections.Counter[tuple[str, str, str]] = collections.Counter()
    for target in targets:
        for frame in target.frames or []:
            if (frame.role or "LIGHT") != "LIGHT":
                continue
            resolved = resolve_frame_telescope(
                frame.camera, frame.focal_length_mm, frame.path, observatory_setups
            ).telescope_name
            if resolved != frame.telescope:
                disagreements[str(frame.camera), str(frame.telescope), resolved] += 1
    return disagreements


def find_iso_disagreements(targets: list[Any]) -> collections.Counter[tuple[str, str, str]]:
    """Count the frames whose stored ISO differs from the one in their header.

    Only frames whose header records an ``ISOSPEED`` or ``GAIN`` are checked,
    because for the others a re-scan uses the camera's configured default.
    ``800`` and ``800.0`` count as the same ISO, so a difference in how the
    number is written is not reported.

    Parameters
    ----------
    targets : `list`
        The targets whose frames are checked. Only light frames are used.

    Returns
    -------
    disagreements : `collections.Counter`
        For each (camera, stored ISO, ISO in the header), how many light
        frames differ. Unreadable files are skipped.
    """
    disagreements: collections.Counter[tuple[str, str, str]] = collections.Counter()
    for target in targets:
        for frame in target.frames or []:
            if (frame.role or "LIGHT") != "LIGHT":
                continue
            try:
                header = read_header(frame.path)
            except Exception as header_error:
                logger.debug("Skipping unreadable frame %s: %s", frame.path, header_error)
                continue
            header_value = iso_or_gain_text(header)
            if header_value is None:
                continue
            if not iso_or_gain_values_match(header_value, frame.iso):
                disagreements[str(frame.camera), str(frame.iso), header_value] += 1
    return disagreements


def _print_counter(title: str, columns: str, counter: collections.Counter[tuple[str, str, str]]) -> None:
    """Print one table of disagreement counts.

    Parameters
    ----------
    title : `str`
        The heading.
    columns : `str`
        Names of the three columns, for the header line.
    counter : `collections.Counter`
        The counts to print, most frequent first.
    """
    print(f"\n{title}")
    if not counter:
        print("  none: every stored value already matches.")
        return
    print(f"  {'frames':>7}  {columns}")
    for (first, second, third), count in counter.most_common():
        print(f"  {count:7d}  {first} | {second} -> {third}")
    print(f"  {sum(counter.values()):7d}  total")


def run_report(argv: list[str] | None = None) -> int:
    """Run the report from the command line.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments; taken from `sys.argv` when omitted.

    Returns
    -------
    exit_code : `int`
        Always ``0``: the script only reports.
    """
    arguments = _build_argument_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    astrometrics = Astrometrics()
    targets = astrometrics.targets.list()
    if arguments.target_ids:
        wanted = {target_id.strip().casefold() for target_id in arguments.target_ids}
        targets = [target for target in targets if target.id.strip().casefold() in wanted]

    observatory_setups = astrometrics.config.get_observatory_setups()
    if not observatory_setups.setups:
        print("The config lists no [Observatory.Setups], so every telescope below would become Unknown.")

    _print_counter(
        "Telescope: stored -> what the config's optics and setups give",
        "camera | stored telescope -> new telescope",
        find_telescope_disagreements(targets, observatory_setups),
    )
    _print_counter(
        "ISO: stored -> the value in the image header",
        "camera | stored ISO -> header ISO",
        find_iso_disagreements(targets),
    )
    print("\nNothing was changed.")
    return 0


if __name__ == "__main__":
    sys.exit(run_report())
