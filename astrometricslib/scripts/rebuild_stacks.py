r"""Find stacks that should be rebuilt, and rebuild them only when asked.

Two kinds of existing stack are out of date now that stacking calibrates each
exposure length with its own dark and no longer subtracts the bias twice:

* blank stacks, whose pixels are mostly exactly zero (15 of the 59 library
  stacks on 2026-09-21, nearly all from the Nikon D5300); and
* stacks of a session shot at several exposure lengths, which were stacked as
  one batch with the dark of the first frame's length.

    python -m astrometricslib.scripts.rebuild_stacks
    python -m astrometricslib.scripts.rebuild_stacks --target "M 31" \
        --apply --backup-dir ~/stack_backups

Without ``--apply`` the script only reads the library and prints what it
would rebuild. With ``--apply`` it copies each chosen target's existing stacks
to the backup folder first, then stacks the target's frames again. Name each
target explicitly: nothing is rebuilt in bulk.
"""

import argparse
import glob
import os
import shutil
import sys
from collections import Counter
from typing import Any

import numpy as np

from astrometricslib import Astrometrics
from astrometricslib.drivers.fits_access import read_data
from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral, group_frames_by_configuration
from astrometricslib.pipelines.stacking.exposure_groups import (
    MINIMUM_FRAMES_PER_EXPOSURE_GROUP,
    frame_exposure_seconds,
)
from astrometricslib.pipelines.stacking.stack_quality import is_zero_fraction_significant

# Every n-th pixel is read when counting zeros, so a large stack is checked
# quickly. One pixel in 36 is plenty to tell a stack that is 98% zero from one
# that is 1% zero.
ZERO_COUNT_PIXEL_STRIDE = 6


def stack_zero_fraction(path: str) -> float | None:
    """Measure how much of a stack is exactly zero.

    Parameters
    ----------
    path : `str`
        The stacked FITS file.

    Returns
    -------
    zero_fraction : `float` or `None`
        The share of sampled pixels equal to zero (the middle colour plane for
        a colour stack), or `None` if the file cannot be read.
    """
    try:
        data = np.asarray(read_data(path))
    except OSError:
        return None
    plane = data[data.shape[0] // 2] if data.ndim == 3 else data
    sample = plane[::ZERO_COUNT_PIXEL_STRIDE, ::ZERO_COUNT_PIXEL_STRIDE]
    return float(np.count_nonzero(sample == 0) / sample.size)


def blank_stack_reason(path: str, zero_fraction: float | None) -> str | None:
    """Say why a stack counts as blank, if it does.

    Spectral stacks are skipped: their sky is legitimately at or below zero.

    Parameters
    ----------
    path : `str`
        The stack's file name, used to recognise spectral stacks.
    zero_fraction : `float` or `None`
        The share of the stack's pixels that are zero.

    Returns
    -------
    reason : `str` or `None`
        A sentence saying the stack is mostly zero, or `None`.
    """
    if zero_fraction is None or "_SPEC_" in os.path.basename(path):
        return None
    if is_zero_fraction_significant(zero_fraction):
        return f"{zero_fraction:.0%} of its pixels are zero (calibration removed the sky)"
    return None


def mixed_exposure_counts(frames: list[Any]) -> dict[float, int]:
    """Find the exposure lengths a set of frames was shot at.

    Parameters
    ----------
    frames : `list`
        Frame records.

    Returns
    -------
    counts : `dict` [`float`, `int`]
        Frames per exposure length, or an empty dictionary unless at least two
        lengths each have `MINIMUM_FRAMES_PER_EXPOSURE_GROUP` frames (only
        those sessions are stacked as several groups).
    """
    counts = Counter(
        round(exposure, 3) for exposure in map(frame_exposure_seconds, frames) if exposure is not None
    )
    large = {
        exposure: count for exposure, count in counts.items() if count >= MINIMUM_FRAMES_PER_EXPOSURE_GROUP
    }
    return dict(sorted(large.items())) if len(large) > 1 else {}


def _stack_files(folder: str) -> list[str]:
    """List the stacked images in a target's library folder.

    Returns
    -------
    paths : `list` [`str`]
        The stacks, without rejection maps, star masks or group stacks.
    """
    excluded = ("RejMap", "starless", "starmask")
    return sorted(
        path
        for path in glob.glob(os.path.join(folder, "*_Stacked*.fits"))
        if not any(word in os.path.basename(path) for word in excluded)
    )


def find_candidates(astrometrics: Astrometrics, names: list[str] | None) -> list[dict[str, Any]]:
    """Look through the library for stacks that should be rebuilt.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The library to read.
    names : `list` [`str`] or `None`
        Only these targets, or every target when `None`.

    Returns
    -------
    candidates : `list` [`dict`]
        One entry per target with something to rebuild: its name, the reasons
        for each blank stack, and the exposure lengths of each mixed session.
    """
    frames_path = astrometrics.config.get_frames_path()
    candidates = []
    for target in astrometrics.targets.list():
        name = target.id
        if names and name not in names:
            continue
        folder = os.path.join(frames_path, "lights", name)
        blank = {}
        for path in _stack_files(folder):
            reason = blank_stack_reason(path, stack_zero_fraction(path))
            if reason:
                blank[os.path.basename(path)] = reason
        mixed = {}
        for key, frames in group_frames_by_configuration(target).items():
            usable = [frame for frame in frames if "_stacked" not in frame.path.lower()]
            for kind, kind_frames in (
                ("spectral", [f for f in usable if frame_is_spectral(f)]),
                ("images", [f for f in usable if not frame_is_spectral(f)]),
            ):
                counts = mixed_exposure_counts(kind_frames)
                if counts:
                    mixed[f"{key} ({kind})"] = counts
        if blank or mixed:
            candidates.append({"target": name, "blank_stacks": blank, "mixed_sessions": mixed})
    return candidates


def print_candidates(candidates: list[dict[str, Any]]) -> None:
    """Print what would be rebuilt."""
    if not candidates:
        print("Nothing to rebuild.")
        return
    for entry in candidates:
        print(f"\n{entry['target']}")
        for stack, reason in entry["blank_stacks"].items():
            print(f"  blank: {stack}: {reason}")
        for session, counts in entry["mixed_sessions"].items():
            lengths = ", ".join(f"{count} x {exposure:g} s" for exposure, count in counts.items())
            print(f"  mixed exposures: {session}: {lengths}")
    print(f"\n{len(candidates)} target(s). Nothing was changed; use --target NAME --apply to rebuild one.")


def rebuild_target(astrometrics: Astrometrics, name: str, backup_directory: str) -> None:
    """Back up a target's stacks and stack its frames again.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The library.
    name : `str`
        The target to rebuild.
    backup_directory : `str`
        Where the existing stacks are copied first.
    """
    from astrometricslib.pipelines.tasks import stack_frames_with_timeout

    target = astrometrics.targets.get(name)
    folder = os.path.join(astrometrics.config.get_frames_path(), "lights", name)
    destination = os.path.join(backup_directory, name.replace(" ", "_"))
    os.makedirs(destination, exist_ok=True)
    for path in glob.glob(os.path.join(folder, "*_Stacked*")):
        if os.path.isfile(path):
            shutil.copy2(path, destination)
    print(f"[{name}] Existing stacks copied to {destination}")
    for key, frames in group_frames_by_configuration(target).items():
        usable = [frame for frame in frames if "_stacked" not in frame.path.lower()]
        for kind_frames in (
            [f for f in usable if not frame_is_spectral(f)],
            [f for f in usable if frame_is_spectral(f)],
        ):
            if kind_frames:
                print(f"[{name}] Stacking {len(kind_frames)} frame(s) of {key}")
                stack_frames_with_timeout(target, kind_frames)
    astrometrics.targets.save()


def main(arguments: list[str] | None = None) -> int:
    """Run the script.

    Parameters
    ----------
    arguments : `list` [`str`], optional
        Command-line arguments; defaults to `sys.argv`.

    Returns
    -------
    status : `int`
        0 on success, 2 for a bad combination of options.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--target", action="append", help="Only this target (repeatable).")
    parser.add_argument("--apply", action="store_true", help="Rebuild the chosen targets.")
    parser.add_argument("--backup-dir", help="Where existing stacks are copied before a rebuild.")
    options = parser.parse_args(arguments)
    if options.apply and (not options.target or not options.backup_dir):
        print("--apply needs at least one --target and a --backup-dir.", file=sys.stderr)
        return 2
    astrometrics = Astrometrics()
    candidates = find_candidates(astrometrics, options.target)
    print_candidates(candidates)
    if options.apply:
        for entry in candidates:
            rebuild_target(astrometrics, entry["target"], os.path.expanduser(options.backup_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
