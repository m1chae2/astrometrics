"""Stage the bundled M 13 sample FITS files into a local working copy.

The tutorial notebooks register these files directly under a `Target`.
Several pipelines cache a solved WCS by writing it back into the FITS
file they solved (`astrometricslib`'s `session_identification.py` and
`pipelines/astrometry/runner.py`) -- a sound design for a user's own
capture library, where re-solving the same reference frame on every
future run would waste real time. But
`documentation/notebooks/astrometrics/sample_data/` is checked into
git as read-only reference data with a documented provenance table, so
registering it directly would let a real tutorial run silently rewrite
the repository's own tracked files.

Staging a copy under `libraryIndex/`, the directory astrometricslib
already treats as local and disposable (see `.gitignore`), keeps the
checked-in original untouched while still letting the WCS cache do its
job across repeated runs of the same notebook.

`drop_stale_sample_data_frames` cleans up the one-time transitional
case: a library that already ran an earlier version of these tutorials,
before this staging existed, and so still carries frame records
pointing at the checked-in original rather than a staged copy.
"""

import shutil
from pathlib import Path
from typing import Any

from astrometricslib import get_configuration


def _checked_in_sample_data_dir() -> Path:
    """Return the read-only, git-tracked sample data directory.

    Returns
    -------
    sample_data_dir : `Path`
        `documentation/notebooks/astrometrics/sample_data/`.
    """
    return (
        get_configuration().get_project_root()
        / "documentation"
        / "notebooks"
        / "astrometrics"
        / "sample_data"
    )


def stage_m13_sample_data() -> Path:
    """Copy the bundled M 13 sample FITS files into a local working directory.

    Each file is copied only if it is not already present at the
    destination, so a WCS solved and cached into a working copy by an
    earlier notebook run is still there on the next one.

    Returns
    -------
    working_dir : `Path`
        The local, writable directory holding the working copies,
        under the same filenames as
        `documentation/notebooks/astrometrics/sample_data/M 13/`.
    """
    project_root = get_configuration().get_project_root()
    source_dir = _checked_in_sample_data_dir() / "M 13"
    working_dir = project_root / "libraryIndex" / "sample_data_working" / "M 13"
    working_dir.mkdir(parents=True, exist_ok=True)

    for source_path in source_dir.glob("*.fits"):
        destination_path = working_dir / source_path.name
        if not destination_path.exists():
            shutil.copy2(source_path, destination_path)

    return working_dir


def drop_stale_sample_data_frames(target: Any) -> int:
    """Remove frame records left over from before sample data was staged.

    Earlier versions of these tutorials registered sample frames by
    their real path under
    `documentation/notebooks/astrometrics/sample_data/`, rather than a
    staged copy. A library that already ran one of those earlier
    versions carries frame records pointing at that checked-in
    location -- and if one of them is ever picked as a session's
    reference frame, `astrometricslib`'s WCS-solve caching writes back
    into it, right back into the repository's own tracked file, which
    staging the sample data was meant to prevent. Call this once,
    right after `stage_m13_sample_data`, so any such record left over
    from before this fix existed gets cleared out before new,
    correctly-staged ones are added.

    Frames outside `documentation/notebooks/astrometrics/sample_data/`
    are left untouched -- a target reused for a real capture library
    alongside these tutorials keeps its own frames.

    Parameters
    ----------
    target : `Target`
        The target to clean up.

    Returns
    -------
    dropped_count : `int`
        How many stale frame records were removed.
    """
    checked_in_dir = _checked_in_sample_data_dir()
    kept = [frame for frame in target.frames if not Path(frame.path).is_relative_to(checked_in_dir)]
    dropped_count = len(target.frames) - len(kept)
    if dropped_count:
        target.frames = kept
        target.recalculate_total_exposure()
    return dropped_count
