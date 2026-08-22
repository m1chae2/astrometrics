"""Purpose: Logic for verifying plate solver configuration and index files.

Provides diagnostic tools for both the backend and research notebooks.
"""

import os
from typing import Any


def check_local_solver_config(app_config) -> dict[str, Any]:  # ruff: ignore[missing-type-function-argument]
    """Verify the local astrometry solver configuration and index files.

    Parameters
    ----------
    app_config : `AppConfiguration`
        Application configuration providing access to the raw
        configuration values and the project root.

    Returns
    -------
    diagnostics : `dict`
        Dictionary containing the diagnostic results, with keys
        ``"config_index_path"``, ``"resolved_path"``, ``"exists"``,
        ``"is_directory"``, and ``"index_files"``.
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
