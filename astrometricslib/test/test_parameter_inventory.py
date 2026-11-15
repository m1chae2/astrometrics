"""Purpose: Keep every numeric library constant either registered or judged.

Description: ``build/documentation/parameter_inventory.py`` finds the numeric
constants and config defaults of ``astrometricslib`` and ``wayfindinglib``.
These tests check that each one is named in the validation register or is in
exactly one of the two lists in ``build/documentation/parameter_inventory/``,
and that the lists stay tidy: no stale rows, no duplicates, and no row for a
value that the register now names.
"""

import importlib.util
import sys
import textwrap
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "build" / "documentation" / "parameter_inventory.py"


def _load_inventory() -> ModuleType:
    """Load the inventory module from its file.

    Returns
    -------
    module : `types.ModuleType`
        The loaded ``parameter_inventory`` module.
    """
    spec = importlib.util.spec_from_file_location("parameter_inventory", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["parameter_inventory"] = module
    spec.loader.exec_module(module)
    return module


inventory = _load_inventory()


@pytest.fixture(scope="module")
def found() -> list[Any]:
    """Find every parameter in the code.

    Returns
    -------
    found : `list`
        The candidates.
    """
    return inventory.find_parameters(REPO_ROOT)


@pytest.fixture(scope="module")
def registered(found: list[Any]) -> Callable[[str, str], bool]:
    """Build a check of whether the register names one parameter.

    Parameters
    ----------
    found : `list`
        The parameters in the code, used to tell shared names apart.

    Returns
    -------
    covers : `collections.abc.Callable`
        Takes a file and a qualified name; True when the register names it.
    """
    registration = inventory.registered_names(REPO_ROOT)
    name_counts = Counter(candidate.bare_name for candidate in found)
    return lambda file, name: registration.covers(file, name, name_counts)


@pytest.fixture(scope="module")
def not_tuning_rows() -> list[dict[str, str]]:
    """Read the not-tuning list.

    Returns
    -------
    rows : `list` [`dict` [`str`, `str`]]
        The rows.
    """
    return inventory.read_list(inventory.NOT_TUNING_FILE)


@pytest.fixture(scope="module")
def backlog_rows() -> list[dict[str, str]]:
    """Read the tuning-not-registered backlog.

    Returns
    -------
    rows : `list` [`dict` [`str`, `str`]]
        The rows.
    """
    return inventory.read_list(inventory.BACKLOG_FILE)


def _keys(rows: list[dict[str, str]]) -> list[tuple[str, str]]:
    """Build the (file, name) key of each row.

    Parameters
    ----------
    rows : `list` [`dict` [`str`, `str`]]
        List rows.

    Returns
    -------
    keys : `list` [`tuple` [`str`, `str`]]
        One key per row.
    """
    return [(row["file"], row["name"]) for row in rows]


def test_every_parameter_is_registered_or_listed(
    found: list[Any],
    registered: Callable[[str, str], bool],
    not_tuning_rows: list[dict[str, str]],
    backlog_rows: list[dict[str, str]],
) -> None:
    """Check every found parameter is registered or listed."""
    listed = set(_keys(not_tuning_rows)) | set(_keys(backlog_rows))
    missing = sorted({
        f"{candidate.file}:{candidate.line} {candidate.name} = {candidate.value}"
        for candidate in found
        if not registered(candidate.file, candidate.name) and (candidate.file, candidate.name) not in listed
    })
    assert not missing, (
        "These numeric values are not in the validation register or in the parameter lists. "
        "If one is a tuning value, register it in documentation/library_design/validation/. "
        "If it is not tuning, add it to build/documentation/parameter_inventory/not_tuning.csv "
        "with a reason:\n" + "\n".join(missing)
    )


def test_listed_parameters_exist_in_the_code(
    found: list[Any],
    not_tuning_rows: list[dict[str, str]],
    backlog_rows: list[dict[str, str]],
) -> None:
    """No row of either list names a parameter that is gone from the code."""
    existing = {(candidate.file, candidate.name) for candidate in found}
    stale = sorted(key for key in _keys(not_tuning_rows) + _keys(backlog_rows) if key not in existing)
    assert not stale, f"These list rows name parameters that no longer exist; delete or rename them: {stale}"


def test_no_parameter_is_in_both_lists_or_twice(
    not_tuning_rows: list[dict[str, str]],
    backlog_rows: list[dict[str, str]],
) -> None:
    """No parameter is in both lists, and no row is repeated within a list."""
    not_tuning_keys, backlog_keys = _keys(not_tuning_rows), _keys(backlog_rows)
    both = sorted(set(not_tuning_keys) & set(backlog_keys))
    assert not both, f"These parameters are in both lists: {both}"
    repeated = sorted(key for key, count in Counter(not_tuning_keys + backlog_keys).items() if count > 1)
    assert not repeated, f"These rows are duplicated: {repeated}"


def test_registered_backlog_rows_are_deleted(
    registered: Callable[[str, str], bool],
    backlog_rows: list[dict[str, str]],
) -> None:
    """A backlog row whose name the register now mentions must be deleted."""
    done = sorted((row["file"], row["name"]) for row in backlog_rows if registered(row["file"], row["name"]))
    assert not done, (
        "These values are now registered; delete their rows from "
        f"build/documentation/parameter_inventory/tuning_not_registered.csv: {done}"
    )


def test_registered_values_are_not_listed_as_not_tuning(
    registered: Callable[[str, str], bool],
    not_tuning_rows: list[dict[str, str]],
) -> None:
    """A value cannot be both registered and judged not tuning."""
    clash = sorted(
        (row["file"], row["name"]) for row in not_tuning_rows if registered(row["file"], row["name"])
    )
    assert not clash, (
        "These values are named in the register, so they are tuning; delete their rows from "
        f"build/documentation/parameter_inventory/not_tuning.csv: {clash}"
    )


def test_find_parameters_on_a_small_tree(tmp_path: Path) -> None:
    """Find constants and numeric config reads; skip other values."""
    library = tmp_path / "astrometricslib" / "pipelines"
    library.mkdir(parents=True)
    (library / "example.py").write_text(
        textwrap.dedent(
            '''
            """Example module."""

            # Largest allowed shift.
            MAXIMUM_SHIFT = 2.5
            LABEL = "not a number"
            lowercase = 3


            class Gate:
                """A gate."""

                LIMIT = 10


            def read(config):
                """Read two settings."""
                config.get_value("Processing", "threshold", fallback=0.5)
                config.get("Processing", "port", fallback="4400")
                config.get("Processing", "name", fallback="abc")
            '''
        )
    )
    skipped = tmp_path / "wayfindinglib" / "test"
    skipped.mkdir(parents=True)
    (skipped / "test_skipped.py").write_text("SKIPPED_CONSTANT = 1\n")
    names = {candidate.name: candidate for candidate in inventory.find_parameters(tmp_path)}
    assert set(names) == {"MAXIMUM_SHIFT", "Gate.LIMIT", "[Processing] threshold", "[Processing] port"}
    assert names["MAXIMUM_SHIFT"].file == "astrometricslib/pipelines/example.py"
    assert names["MAXIMUM_SHIFT"].value == "2.5"
    assert names["MAXIMUM_SHIFT"].comment == "Largest allowed shift."
    assert names["Gate.LIMIT"].bare_name == "LIMIT"
    assert names["[Processing] port"].bare_name == "port"


def test_registration_tells_shared_names_apart(tmp_path: Path) -> None:
    """A shared name written with its file registers only that constant."""
    register = tmp_path / "documentation" / "library_design" / "validation"
    register.mkdir(parents=True)
    (register / "example.md").write_text(
        "- code constant `READOUT_SECONDS` = 2 in `guide_camera.py`. Relation: derived from this entry.\n"
        "- config key `[Processing] threshold` (default 0.5). Relation: confirmed by this entry.\n"
        "- `SHARED_LIMIT` is mentioned with no file.\n"
        "Some prose mentions port without backticks.\n"
    )
    registration = inventory.registered_names(tmp_path)
    counts = Counter({"READOUT_SECONDS": 2, "threshold": 1, "SHARED_LIMIT": 2, "port": 1})
    assert registration.covers("wayfindinglib/drivers/guide_camera.py", "READOUT_SECONDS", counts)
    assert not registration.covers(
        "wayfindinglib/drivers/simulators/guiding_simulator.py", "READOUT_SECONDS", counts
    )
    assert registration.covers("astrometricslib/foundation/config.py", "[Processing] threshold", counts)
    assert not registration.covers("a/b.py", "SHARED_LIMIT", counts)
    assert registration.covers("a/b.py", "SHARED_LIMIT", Counter({"SHARED_LIMIT": 1}))
    assert not registration.covers("a/b.py", "port", counts)
