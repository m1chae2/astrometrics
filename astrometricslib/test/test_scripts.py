"""Verification suite running demonstration and utility scripts.

Ensures that scripts are continually tested against API changes.
"""

import os
import subprocess
import sys

import pytest


def run_script_as_subprocess(module_name: str) -> None:
    """Run a script module as a subprocess against the real environment.

    Executes the module with real config and database. Appends
    ``--headless`` if the script supports it.
    """
    env = dict(os.environ)
    env["ASTROMETRICS_TESTING"] = "1"
    # Suppresses SirilInterface.launch_siril_gui(), which otherwise
    # pops an interactive GUI window after a successful stack -- see
    # its "HEADLESS" check in astrometricslib/drivers/siril_interface.py.
    env["HEADLESS"] = "1"

    # 1. Determine if the script accepts --headless
    help_result = subprocess.run(
        [sys.executable, "-m", module_name, "--help"], capture_output=True, text=True, env=env
    )
    args = []
    if "--headless" in help_result.stdout or "--headless" in help_result.stderr:
        args.append("--headless")

    result = subprocess.run(
        [sys.executable, "-m", module_name, *args], capture_output=True, text=True, env=env
    )
    assert result.returncode == 0, (
        f"Script {module_name} failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


# NOTE: astrometricslib/scripts/reset_stellar_object_library.py is
# deliberately NOT exercised here. `run_script_as_subprocess` runs against
# the real config and database (see its docstring), and that script deletes
# every row in the stellar catalog -- so covering it meant a plain
# `pytest astrometricslib` silently destroyed the user's real stellar
# catalog. The script itself is fine and still shipped; it just cannot be
# smoke-tested against production data. Restoring coverage for it requires
# pointing the subprocess at a throwaway library path first (the conftest
# sandbox does not reach subprocesses, which is why this went unnoticed).


@pytest.mark.slow
def test_script_reindex_all_targets():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Run astrometricslib/scripts/reindex_all_targets script."""
    run_script_as_subprocess("astrometricslib.scripts.reindex_all_targets")


@pytest.mark.slow
def test_script_stellar_catalog_audit():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Run astrometricslib/scripts/stellar_catalog_audit script."""
    run_script_as_subprocess("astrometricslib.scripts.stellar_catalog_audit")
