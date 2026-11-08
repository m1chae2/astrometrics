"""Purpose: Refuse a real-data run when a copy points at the real library.

Description: A thin wrapper. The code lives in ``simulator/path_guard.py``,
which the simulator's library copy uses too. This file keeps the command
``copy_path_guard.py check <copy_config>`` that ``realrun.sh`` runs, and the
names that ``repoint_copy_paths.py``, ``build/tpm/real_snapshot.py`` and the
tests load by this file's path.
"""

import sys
from pathlib import Path

# The agent tools are not a package and may be loaded by file path, so put
# the repository root on the import path before importing the shared code.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from simulator.path_guard import (
    check,
    find_offending_paths,
    is_allowed,
    is_under,
    main,
    mirror_path,
    read_config_value,
    read_config_value_in_section,
    repoint,
    walk_strings,
)

__all__ = [
    "check",
    "find_offending_paths",
    "is_allowed",
    "is_under",
    "main",
    "mirror_path",
    "read_config_value",
    "read_config_value_in_section",
    "repoint",
    "walk_strings",
]

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
