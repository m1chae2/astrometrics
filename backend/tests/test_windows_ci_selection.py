"""Purpose: Test which Python tests the Windows workflow runs, and when.

Description: The "windows CI" workflow (``.github/workflows/windows.yml``)
runs only the tests marked ``windows_sensitive`` on a pull request, and the
whole suite on a push to main, by hand and once a week. The root
``conftest.py`` adds the marker by reading each test file's text. These tests
run on Linux and check that the workflow keeps a whole-suite run, that a pull
request runs only the marked tests, and that the marker picks the tests of the
per-system modules but leaves out most of the suite.
"""

import os
import tomllib
import types
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "windows.yml"

PLATFORM_MODULE_TESTS = [
    "astrometricslib/foundation/test/test_system_windows.py",
    "astrometricslib/foundation/test/test_system_posix.py",
    "astrometricslib/foundation/test/test_scratch_paths.py",
    "astrometricslib/foundation/storage/test/test_process_locks.py",
    "astrometricslib/_internal/drivers/test/test_siril_windows.py",
    "astrometricslib/_internal/drivers/test/test_program_setup.py",
    "astrometricslib/_internal/drivers/test/test_file_staging.py",
    "backend/tests/test_startup_lifecycle.py",
    "mcp_servers/gaps/test/test_gap_database_path.py",
]
"""Tests of the per-system modules and of Windows-only code paths, which a
pull request must run on Windows."""

SEARCHED_FOLDERS = ["astrometricslib", "wayfindinglib", "backend/tests", "mcp_servers"]
"""Folders whose test files the share check counts."""


def _python_job_steps() -> list[dict]:
    """Read the steps of the workflow's Python job.

    Returns
    -------
    steps : `list` [`dict`]
        The job's steps, as YAML mappings.
    """
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return workflow["jobs"]["python"]["steps"]


def _pytest_steps() -> list[dict]:
    """Pick the Python job's steps that run pytest.

    Returns
    -------
    steps : `list` [`dict`]
        The steps whose command runs ``python -m pytest``.
    """
    return [step for step in _python_job_steps() if "python -m pytest" in step.get("run", "")]


def _workflow_triggers() -> dict:
    """Read what starts the workflow.

    PyYAML reads the key ``on`` as the value `True`, so both are tried.

    Returns
    -------
    triggers : `dict`
        The workflow's ``on`` mapping.
    """
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return workflow.get("on", workflow.get(True))


@pytest.fixture(scope="module")
def root_conftest(pytestconfig: pytest.Config) -> types.ModuleType:
    """Give the root ``conftest.py`` module pytest has already loaded.

    Taking the loaded module, instead of importing the file again, keeps its
    start-up work (the shared test folders) from running a second time.

    Returns
    -------
    module : `types.ModuleType`
        The root conftest module.
    """
    root_file = (PROJECT_ROOT / "conftest.py").resolve()
    for plugin in pytestconfig.pluginmanager.get_plugins():
        plugin_file = getattr(plugin, "__file__", None)
        if plugin_file and Path(plugin_file).resolve() == root_file:
            return plugin
    pytest.fail("The root conftest.py is not loaded.")


def test_the_whole_suite_runs_on_main_by_hand_and_weekly() -> None:
    """Check that a push to main, a manual or weekly start runs every test."""
    triggers = _workflow_triggers()
    assert "main" in triggers["push"]["branches"]
    assert "workflow_dispatch" in triggers
    assert triggers["schedule"] and all("cron" in entry for entry in triggers["schedule"])

    job_condition = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))["jobs"]["python"]["if"]
    assert "workflow_dispatch" in job_condition
    assert "schedule" in job_condition

    full_runs = [step for step in _pytest_steps() if step.get("if") == "github.event_name != 'pull_request'"]
    assert len(full_runs) == 1
    assert "windows_sensitive" not in full_runs[0]["run"]
    assert '-m "not trio"' in full_runs[0]["run"]


def test_a_pull_request_runs_only_the_windows_sensitive_tests() -> None:
    """Check that a pull request runs only the marked tests."""
    pull_request_runs = [
        step for step in _pytest_steps() if step.get("if") == "github.event_name == 'pull_request'"
    ]
    assert len(pull_request_runs) == 1
    assert '-m "windows_sensitive and not trio"' in pull_request_runs[0]["run"]
    assert all("if" in step for step in _pytest_steps()), "every pytest step must say which starts it runs on"


def test_the_marker_is_registered_in_pyproject() -> None:
    """Check that ``windows_sensitive`` is a registered pytest marker."""
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as pyproject_file:
        markers = tomllib.load(pyproject_file)["tool"]["pytest"]["ini_options"]["markers"]
    assert any(marker.startswith("windows_sensitive:") for marker in markers)


def test_the_marker_picks_the_platform_module_tests(root_conftest: types.ModuleType) -> None:
    """Check that every test of the per-system modules gets the marker."""
    missing = [
        path
        for path in PLATFORM_MODULE_TESTS
        if not root_conftest.windows_sensitive_reasons(PROJECT_ROOT / path)
    ]
    assert not missing, f"These Windows-risk tests would not run on a pull request: {missing}"


def test_the_extra_test_files_exist(root_conftest: types.ModuleType) -> None:
    """Check that every file on the extra list still exists."""
    missing = [
        path
        for path in root_conftest.WINDOWS_SENSITIVE_EXTRA_TEST_PATHS
        if not (PROJECT_ROOT / path).is_file()
    ]
    assert not missing, f"Listed in WINDOWS_SENSITIVE_EXTRA_TEST_PATHS but not found: {missing}"


def test_the_marker_leaves_out_most_tests(root_conftest: types.ModuleType) -> None:
    """Check that the selection is not empty and is well under the whole suite.

    A pattern that matched almost every file would make the pull-request
    run as slow as the whole suite again.
    """
    test_files = []
    for folder in SEARCHED_FOLDERS:
        for directory, subdirectories, file_names in os.walk(PROJECT_ROOT / folder):
            subdirectories[:] = [name for name in subdirectories if not name.startswith((".", "__"))]
            test_files += [
                Path(directory) / name
                for name in file_names
                if name.startswith("test_") and name.endswith(".py")
            ]
    selected = [path for path in test_files if root_conftest.windows_sensitive_reasons(path)]
    assert selected
    assert len(selected) < len(test_files) / 4, (
        f"{len(selected)} of {len(test_files)} test files are selected"
    )
