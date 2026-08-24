r"""Fill in a missing FOCALLEN on frames captured before a given date.

Frames must be grouped by optic before stacking, since optics of
different focal length image at different scales and a blended stack has
no single pixel scale. That grouping needs each frame's focal length,
and a frame whose header never recorded one cannot be grouped at all --
it is simply omitted, which on this library would silently discard 602
frames and erase three targets outright, including a comet that cannot
be re-imaged.

Rather than have the library guess, this fills the gap deliberately and
auditably. The rule is supplied by the observer, not inferred: "frames
before DATE were shot at FOCAL_MM". That is a fact about an equipment
history, which no library can know.

Only frames with no FOCALLEN at all are touched -- an existing value is
never overwritten, so a wrong date cannot destroy good metadata. Every
frame written records a HISTORY card marking the value as inferred, so
a reader can always tell a backfilled focal length from a captured one.

Check first, then apply::

    python -m astrometricslib.scripts.backfill_focal_length \\
        --before 2024-01-01 --focal-length 300 --dry-run

    python -m astrometricslib.scripts.backfill_focal_length \\
        --before 2024-01-01 --focal-length 300 --apply
"""

import argparse
import datetime
import logging
import sys
from typing import Any

from astrometricslib import Astrometrics

logger = logging.getLogger(__name__)

# Marker written alongside every backfilled value. Present so the
# provenance survives in the file itself rather than only in this
# script's output, and so a later run can recognise its own work.
BACKFILL_HISTORY_PREFIX = "FOCALLEN inferred by backfill_focal_length"


def _build_argument_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser for this script.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        Parser covering the cutoff date, the focal length, and whether
        to write.
    """
    parser = argparse.ArgumentParser(
        prog="backfill_focal_length",
        description="Write a FOCALLEN into frames captured before a date that have none.",
    )
    parser.add_argument(
        "--before",
        required=True,
        metavar="YYYY-MM-DD",
        help="Frames captured strictly before this date are backfilled.",
    )
    parser.add_argument(
        "--focal-length",
        required=True,
        type=float,
        metavar="MM",
        help="Focal length in millimetres to write into those frames.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write the headers. Without this the script only reports what it would do.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicitly request a preview. This is already the default.",
    )
    parser.add_argument(
        "--target",
        action="append",
        dest="target_ids",
        metavar="TARGET_ID",
        help="Restrict to this target; repeatable. Defaults to every target.",
    )
    return parser


def find_frames_missing_focal_length(targets: list, cutoff_date: datetime.date) -> list[dict[str, Any]]:
    """Find frames captured before `cutoff_date` that record no FOCALLEN.

    Reads each frame's header rather than trusting the catalog, since
    the catalog's own copy may predate the field being recorded.

    Parameters
    ----------
    targets : `list`
        Targets whose frames are inspected.
    cutoff_date : `datetime.date`
        Frames captured strictly before this date are eligible.

    Returns
    -------
    candidates : `list` [`dict`]
        One entry per eligible frame, with ``path``, ``target_id`` and
        ``captured`` (`datetime.date`).
    """
    from astropy.io import fits

    candidates: list[dict[str, Any]] = []
    for target in targets:
        for frame in target.frames or []:
            if (frame.role or "LIGHT") != "LIGHT":
                continue
            if not frame.timestamp:
                # Without a capture time the rule cannot be applied, and
                # guessing is exactly what this script exists to avoid.
                continue
            captured = datetime.datetime.fromtimestamp(frame.timestamp).date()
            if captured >= cutoff_date:
                continue
            try:
                if fits.getheader(frame.path).get("FOCALLEN") is not None:
                    continue
            except Exception as header_error:
                logger.debug("Skipping unreadable frame %s: %s", frame.path, header_error)
                continue
            candidates.append({
                "path": frame.path,
                "target_id": target.id,
                "captured": captured,
            })
    return candidates


def apply_focal_length(path: str, focal_length_mm: float) -> bool:
    """Write a FOCALLEN into one frame, unless it already has one.

    The existing-value check is repeated here rather than trusted from
    the caller's scan, so this function is safe to call directly and
    cannot overwrite a real measurement through a stale candidate list.

    Parameters
    ----------
    path : `str`
        FITS file to update, modified in place.
    focal_length_mm : `float`
        Value to write.

    Returns
    -------
    written : `bool`
        `True` if the header was updated, `False` if the frame already
        had a focal length or could not be written.
    """
    from astropy.io import fits

    try:
        with fits.open(path, mode="update") as hdul:
            header = hdul[0].header
            if header.get("FOCALLEN") is not None:
                return False
            header["FOCALLEN"] = (
                focal_length_mm,
                "Focal length [mm] (inferred, see HISTORY)",
            )
            header.add_history(
                f"{BACKFILL_HISTORY_PREFIX} as {focal_length_mm:g}mm on {datetime.date.today().isoformat()}"
            )
            hdul.flush()
        return True
    except Exception as write_error:
        logger.warning("Could not write FOCALLEN into %s: %s", path, write_error)
        return False


def run_backfill(argv: list[str] | None = None) -> int:
    """Report or apply the focal-length backfill.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments; taken from `sys.argv` when omitted.

    Returns
    -------
    exit_code : `int`
        ``0`` on success, ``1`` when the date could not be parsed, and
        ``2`` when there was nothing to do.
    """
    arguments = _build_argument_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    try:
        cutoff_date = datetime.date.fromisoformat(arguments.before)
    except ValueError:
        print(f"Could not read '{arguments.before}' as a date; expected YYYY-MM-DD.")
        return 1

    astrometrics = Astrometrics()
    targets = astrometrics.targets.list()
    if arguments.target_ids:
        wanted = {target_id.strip().casefold() for target_id in arguments.target_ids}
        targets = [target for target in targets if target.id.strip().casefold() in wanted]

    candidates = find_frames_missing_focal_length(targets, cutoff_date)
    if not candidates:
        print(f"No frames captured before {cutoff_date} are missing a FOCALLEN.")
        return 2

    by_target: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        by_target.setdefault(candidate["target_id"], []).append(candidate)

    print(f"{len(candidates)} frame(s) captured before {cutoff_date} have no FOCALLEN:")
    for target_id, target_candidates in sorted(by_target.items()):
        dates = [candidate["captured"] for candidate in target_candidates]
        print(f"  {target_id:16s} {len(target_candidates):5d} frames   {min(dates)} .. {max(dates)}")

    if not arguments.apply:
        print(
            f"\nDry run: nothing was written. Re-run with --apply to set "
            f"FOCALLEN={arguments.focal_length:g} on these frames."
        )
        return 0

    print(f"\nWriting FOCALLEN={arguments.focal_length:g} into {len(candidates)} frame(s)...")
    written = 0
    for candidate in candidates:
        if apply_focal_length(candidate["path"], arguments.focal_length):
            written += 1
    print(f"Updated {written} of {len(candidates)} frame(s).")
    print("Re-run the batch script (or a reindex) so the catalog picks the new values up.")
    return 0


if __name__ == "__main__":
    sys.exit(run_backfill())
