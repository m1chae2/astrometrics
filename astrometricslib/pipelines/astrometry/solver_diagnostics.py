"""Tools to check if the local mapping software is installed correctly.

Specific map files (called index files) must be downloaded to the
computer for the local solver to work. These functions check if they
exist.
"""

import os
from typing import Any


def check_local_solver_config(app_config) -> dict[str, Any]:  # ruff: ignore[missing-type-function-argument]
    """Check if the local solver has the map files it needs to run.

    Parameters
    ----------
    app_config : `AppConfiguration`
        The system settings (to determine where to look).

    Returns
    -------
    diagnostics : `dict`
        A report showing exactly where the check looked, whether the folder
        existed, and how many map files were found inside it.
    """
    # Assuming app_config provides access to the raw configuration if needed,
    # or specialized methods for processing settings.

    # In this context, we'll use the configuration service directly.
    index_path = app_config.get(
        "Processing.Astrometry.Local Solver", "index_path", fallback="astrometry/data"
    )

    # Resolve relative to project root
    project_root = app_config.get_project_root()
    abs_index_path = project_root / index_path

    results = {
        "config_index_path": str(index_path),
        "resolved_path": str(abs_index_path),
        "exists": abs_index_path.exists(),
        "is_directory": abs_index_path.is_dir() if abs_index_path.exists() else False,
        "index_files": [],
    }

    if results["is_directory"]:
        results["index_files"] = [f for f in os.listdir(abs_index_path) if f.endswith(".fits")]

    return results
