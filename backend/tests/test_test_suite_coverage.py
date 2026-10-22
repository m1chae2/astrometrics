"""Purpose: Test that pytest and run_tests.sh both run every test file.

Description: A bare ``pytest`` run reads ``testpaths`` from ``pyproject.toml``.
CI runs ``build/linux/run_tests.sh``, which names its own folders. If a test
file lives outside both lists, it is silently never run. These tests read both
lists and check each project ``test_*.py`` file against them.
"""

import os
import re
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

SKIPPED_FOLDERS = {"node_modules", "__pycache__", "dist", "out"}
"""Folder names that never hold project tests."""


def _project_test_files() -> list[str]:
    """List the ``test_*.py`` files in the repository.

    Folders that hold no project code (virtual environments, caches, node
    packages and hidden folders) are skipped.

    Returns
    -------
    test_files : `list` [`str`]
        Forward-slash paths relative to the repository root.
    """
    test_files = []
    for directory, subdirectories, file_names in os.walk(PROJECT_ROOT):
        subdirectories[:] = [
            name for name in subdirectories if not name.startswith(".") and name not in SKIPPED_FOLDERS
        ]
        for file_name in file_names:
            if file_name.startswith("test_") and file_name.endswith(".py"):
                full_path = Path(directory) / file_name
                test_files.append(full_path.relative_to(PROJECT_ROOT).as_posix())
    return test_files


def _pytest_testpaths() -> list[str]:
    """Read ``testpaths`` from the pytest section of ``pyproject.toml``.

    Returns
    -------
    testpaths : `list` [`str`]
        Folders a bare ``pytest`` run searches.
    """
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as pyproject_file:
        settings = tomllib.load(pyproject_file)
    return settings["tool"]["pytest"]["ini_options"]["testpaths"]


def _run_tests_script_folders() -> list[str]:
    """Read the folders the suites in ``run_tests.sh`` run.

    Returns
    -------
    folders : `list` [`str`]
        Folder paths, without a trailing slash.
    """
    script = (PROJECT_ROOT / "build/linux/run_tests.sh").read_text()
    return re.findall(r'^\s+\w+\)\s+echo "([\w/]+?)/?[" ]', script, flags=re.MULTILINE)


def _is_under(path: str, folders: list[str]) -> bool:
    """Check whether a file path lies inside any of the folders.

    Parameters
    ----------
    path : `str`
        File path relative to the repository root.
    folders : `list` [`str`]
        Folder paths relative to the repository root.

    Returns
    -------
    inside : `bool`
        True when the path is under at least one folder.
    """
    return any(path.startswith(folder.rstrip("/") + "/") for folder in folders)


def test_every_test_file_is_under_a_pytest_testpath() -> None:
    """Check that a bare ``pytest`` run can find every test file."""
    testpaths = _pytest_testpaths()
    missed = [path for path in _project_test_files() if not _is_under(path, testpaths)]
    assert not missed, f"Test files outside testpaths {testpaths}: {missed}"


def test_every_test_file_is_under_a_run_tests_suite() -> None:
    """Check that ``run_tests.sh`` (and so CI) runs every test file."""
    folders = _run_tests_script_folders()
    assert folders, "Could not read any suite folders from run_tests.sh."
    missed = [path for path in _project_test_files() if not _is_under(path, folders)]
    assert not missed, f"Test files outside the run_tests.sh suites {folders}: {missed}"


def test_testpaths_match_run_tests_suites() -> None:
    """Check that ``testpaths`` and the ``run_tests.sh`` suites agree."""
    suite_folders = {folder.rstrip("/") for folder in _run_tests_script_folders()}
    assert suite_folders == {path.rstrip("/") for path in _pytest_testpaths()}
