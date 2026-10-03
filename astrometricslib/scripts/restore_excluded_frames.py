r"""Move frames the stacking pipeline set aside back into their targets.

Before each stack, the pipeline moves frames with clouds or trailed stars
into a folder named `_excluded` next to them (see
`stacking/pre_processing/frame_quarantine.py`). Each such folder holds
`excluded_frames.json`, which says where every frame came from and what was
measured. This script lists those frames and, with ``--apply``, moves them
back and re-scans the target so it lists them again. It calls
`ProcessingPipelines.restore_excluded_frames`.

A restored frame joins the next stack, and the next stack may move it aside
again. To keep frames in the stack for good, set
``quarantine_bad_frames_enabled = "false"`` in the configuration first.

Check first, then apply::

    python -m astrometricslib.scripts.restore_excluded_frames

    python -m astrometricslib.scripts.restore_excluded_frames \
        --targets "M 52 - Bubble Nebula" --apply
"""

import argparse
import logging
import sys

from astrometricslib import Astrometrics

logger = logging.getLogger(__name__)


def _build_argument_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser for this script.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        The parser, with ``--targets`` and ``--apply``.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--targets",
        nargs="+",
        default=None,
        help="Target ids to look at. Default: every target.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Move the frames back and save the targets. Without it, only list them.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """List, and optionally restore, the frames set aside for each target.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments; default is the process's own.

    Returns
    -------
    exit_code : `int`
        0 on success.
    """
    arguments = _build_argument_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    astrometrics = Astrometrics()
    target_ids = arguments.targets or [target.id for target in astrometrics.targets.list()]

    total = 0
    touched = []
    for target_id in target_ids:
        target = astrometrics.targets.get(target_id)
        if target is None:
            logger.info("%s: no such target.", target_id)
            continue
        report = astrometrics.processing.restore_excluded_frames(target, apply=arguments.apply)
        total += len(report.frames)
        if report.frames:
            logger.info("%s: %d frame(s) set aside", target_id, len(report.frames))
        for frame in report.frames:
            logger.info("    %s  %s: %s", frame.file, frame.kind, frame.reason)
        if arguments.apply and report.restored_count:
            logger.info("    restored %d frame(s)", report.restored_count)
            touched.append(target_id)

    if arguments.apply:
        if touched:
            astrometrics.targets.save()
        logger.info("Restored frames for %d target(s).", len(touched))
    else:
        logger.info("%d frame(s) set aside in all. Nothing moved (use --apply to restore).", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
