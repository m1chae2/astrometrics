#!/usr/bin/env python3
"""Purpose: Point the stored paths of a COPY library database into the copy.

Description: Usage ``repoint_copy_paths.py <copy_config>`` with
ASTROMETRICS_SCRATCH set. It rewrites, inside the copy databases only, every
stored absolute path that points at the real stacks folder (any path with a
``/lights/`` folder) or the real data folder, so a run on the copy cannot
write to the real library. Paths under the frames folder are left alone. It
refuses when a database is not inside ASTROMETRICS_SCRATCH. Run
``realrun.sh`` afterwards; it checks the copy again.
"""

import importlib.util
import os
import sys
from pathlib import Path

# The guard sits beside this script; the agent tools are not a package, so
# load it by its file path.
_GUARD_SPEC = importlib.util.spec_from_file_location(
    "copy_path_guard", Path(__file__).with_name("copy_path_guard.py")
)
if _GUARD_SPEC is None or _GUARD_SPEC.loader is None:
    raise ImportError("copy_path_guard.py is missing beside this script")
_guard = importlib.util.module_from_spec(_GUARD_SPEC)
_GUARD_SPEC.loader.exec_module(_guard)
check = _guard.check
repoint = _guard.repoint


def main(arguments: list[str]) -> int:
    """Repoint the copy databases and report what is left.

    Parameters
    ----------
    arguments : `list` [`str`]
        ``[copy_config]``.

    Returns
    -------
    exit_code : `int`
        0 when the copy is clean afterwards, 1 when paths remain, 2 for a
        usage error or a refusal.
    """
    scratch = os.environ.get("ASTROMETRICS_SCRATCH")
    if len(arguments) != 1 or not scratch:
        print("usage: ASTROMETRICS_SCRATCH=... repoint_copy_paths.py <copy_config>", file=sys.stderr)
        return 2
    try:
        changed = repoint(arguments[0], scratch)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(f"rewrote {changed} stored path(s)")
    remaining = check(arguments[0], scratch)
    for database, table, label, trail, path in remaining:
        print(f"still outside the copy: {database} {table} {label} {trail}: {path}", file=sys.stderr)
    return 1 if remaining else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
