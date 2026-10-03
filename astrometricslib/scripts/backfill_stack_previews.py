r"""Make preview pictures for stacks that already exist, and record them.

Stacking now saves a cleaned-up, stretched picture next to every new stack,
as a JPEG and as a FITS file (see
`stacking/post_processing/stack_preview.py`), and shows it as the target's
processed image. Stacks made earlier lack one or both. This script runs the
same step on them. It never restacks.

For each stack it:

1. Makes the pictures, unless a JPEG and a FITS that are both at least as new
   as the stack exist. ``--force`` makes them again, for example after a
   setting change.
2. Records the stretched FITS as the target's processed image, so the image
   viewer shows it. The JPEG is recorded when the FITS is missing. A picture
   a person attached to the target is replaced too (the attached file stays
   where it is), unless ``--keep-attached-pictures`` is given. The picture
   of a stack the target does not show is never replaced.

The stack files are never changed. Targets are saved once, at the end, and
only with ``--apply``.

Check first, then apply::

    python -m astrometricslib.scripts.backfill_stack_previews \
        --camera "ASI 533MM" --dry-run

    python -m astrometricslib.scripts.backfill_stack_previews \
        --targets "M 27" "M 57" --apply
"""

import argparse
import logging
import os
import sys
from typing import Any

from astrometricslib import Astrometrics
from astrometricslib.pipelines.shared.stack_preview_path import preview_path_for, processed_fits_path_for
from astrometricslib.pipelines.stacking.post_processing.stack_preview import (
    record_preview_as_processed_image,
    write_stack_preview,
)

logger = logging.getLogger(__name__)


def _build_argument_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser for this script.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        The parser, with the selection and mode options.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--targets", nargs="+", metavar="NAME", help="Target names to process.")
    parser.add_argument(
        "--camera",
        metavar="TEXT",
        help="Process targets that have a frame whose camera name contains TEXT (case ignored).",
    )
    parser.add_argument(
        "--kind",
        choices=("imaging", "spectral", "both"),
        default="imaging",
        help="Which stack of each target to use. Default: imaging.",
    )
    parser.add_argument("--force", action="store_true", help="Remake previews that are already current.")
    parser.add_argument(
        "--keep-attached-pictures",
        action="store_true",
        help="Keep a processed image that a person attached, instead of replacing it.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="List what would happen and change nothing.")
    mode.add_argument("--apply", action="store_true", help="Make the previews and save the targets.")
    return parser


def select_targets(targets: list[Any], names: list[str] | None, camera: str | None) -> list[Any]:
    """Pick the targets to process.

    Parameters
    ----------
    targets : `list`
        All targets in the library.
    names : `list` [`str`] or `None`
        Target names to keep, or `None` for no name filter.
    camera : `str` or `None`
        Keep targets with a frame whose camera name contains this text, or
        `None` for no camera filter. Case is ignored.

    Returns
    -------
    selected : `list`
        The targets that pass every filter that was given.
    """
    selected = []
    for target in targets:
        if names is not None and target.id not in names:
            continue
        if camera is not None and not any(
            camera.lower() in (frame.camera or "").lower() for frame in target.frames
        ):
            continue
        selected.append(target)
    return selected


def stacks_of(target: Any, kind: str) -> list[tuple[bool, str]]:
    """List the stack files of a target that exist on disk.

    Parameters
    ----------
    target : `Target`
        The target.
    kind : `str`
        ``"imaging"``, ``"spectral"`` or ``"both"``.

    Returns
    -------
    stacks : `list` [`tuple` [`bool`, `str`]]
        For each stack that is recorded and exists, whether it is the
        spectroscopy stack and its path.
    """
    candidates = []
    if kind in ("imaging", "both"):
        candidates.append((False, target.stacking.stacked_image))
    if kind in ("spectral", "both"):
        candidates.append((True, target.spectral_stacking.stacked_image))
    return [(is_spectral, path) for is_spectral, path in candidates if path and os.path.isfile(path)]


def preview_is_current(stacked_path: str) -> bool:
    """Tell whether a stack already has both pictures, each as new as itself.

    Returns
    -------
    current : `bool`
        `True` if the preview JPEG and the stretched FITS both exist and
        neither is older than the stack.
    """
    stack_time = os.path.getmtime(stacked_path)
    pictures = (preview_path_for(stacked_path), processed_fits_path_for(stacked_path))
    return all(os.path.isfile(path) and os.path.getmtime(path) >= stack_time for path in pictures)


def main(argv: list[str] | None = None) -> int:
    """Run the backfill.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments. Defaults to ``sys.argv[1:]``.

    Returns
    -------
    exit_code : `int`
        0 if every selected stack ended with a preview, 1 otherwise.
    """
    arguments = _build_argument_parser().parse_args(argv)
    if arguments.targets is None and arguments.camera is None:
        print("Give --targets or --camera, so the script does not process the whole library by accident.")
        return 2
    logging.basicConfig(level=logging.WARNING)
    # Show the sky level chosen for each stack, as well as any warnings.
    logging.getLogger("astrometricslib.pipelines.stacking.post_processing.stack_preview").setLevel(
        logging.INFO
    )
    astrometrics = Astrometrics()
    everything = [astrometrics.targets.get(listed.id) for listed in astrometrics.targets.list()]
    selected = select_targets([t for t in everything if t], arguments.targets, arguments.camera)
    failures = 0
    for target in selected:
        for is_spectral, stacked_path in stacks_of(target, arguments.kind):
            label = f"{target.id} ({'spectral' if is_spectral else 'imaging'})"
            current = preview_is_current(stacked_path) and not arguments.force
            if arguments.dry_run:
                print(f"{label}: would {'keep the current preview' if current else 'make a preview'}")
                continue
            preview = preview_path_for(stacked_path) if current else write_stack_preview(stacked_path)
            if not preview:
                print(f"{label}: FAILED to make a preview")
                failures += 1
                continue
            recorded = record_preview_as_processed_image(
                target, is_spectral, stacked_path, preview, arguments.keep_attached_pictures
            )
            made = "kept" if current else "made"
            image = "set" if recorded else "left as it was"
            print(f"{label}: {made} preview; processed image {image}")
    if arguments.apply:
        astrometrics.targets.save()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
