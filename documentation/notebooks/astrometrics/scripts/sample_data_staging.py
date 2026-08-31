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
"""

import shutil
from pathlib import Path

from astrometricslib import get_configuration


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
    source_dir = project_root / "documentation" / "notebooks" / "astrometrics" / "sample_data" / "M 13"
    working_dir = project_root / "libraryIndex" / "sample_data_working" / "M 13"
    working_dir.mkdir(parents=True, exist_ok=True)

    for source_path in source_dir.glob("*.fits"):
        destination_path = working_dir / source_path.name
        if not destination_path.exists():
            shutil.copy2(source_path, destination_path)

    return working_dir
