"""Purpose: Put a frame into a work folder without changing the original.

Description: A stacking or plate-solving run works in a scratch folder and
must not touch the library's frames. `stage_file` gives the frame a second
name in that folder. Each system has one fixed way to do it, with no
fallback, so a run never silently copies a library's worth of frames:

* Linux makes a symbolic link (`_staging_posix`). It is free and instant and
  reaches frames on any drive, such as a mounted network or cloud folder.
* Windows makes a hard link (`_staging_windows`): the same file under a
  second name, also free and instant, and it needs no special rights (a
  symbolic link needs Developer Mode). A hard link only works when the frames
  are on the same NTFS drive as the work folder; elsewhere the run stops with
  a `ConfigurationError` that says so.

This module imports the right one once, the way
`astrometricslib.foundation.system` does.
"""

import sys

if sys.platform == "win32":
    from astrometricslib._internal.drivers._staging_windows import stage_file
else:
    from astrometricslib._internal.drivers._staging_posix import stage_file

__all__ = ["stage_file"]
