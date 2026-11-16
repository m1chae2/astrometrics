"""Purpose: Keep plain numeric constants listed and declarations evidenced.

Description: Every tuned value is a ``TunedParameter`` declaration.
``build/documentation/parameter_inventory.py`` finds the plain numeric
constants and config defaults of ``astrometricslib`` and ``wayfindinglib``
that are left. These tests check that each one is in exactly one of the two
lists in ``build/documentation/parameter_inventory/`` (decided not tuning, or
tuning that is not declared yet), and that the lists stay tidy: no stale rows
and no duplicates. They also check that each declaration names a register
entry that exists.
"""

import importlib.util
import sys
import textwrap
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from astrometricslib.parameters import ParameterSource, collect_parameters

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


def test_every_plain_constant_is_listed(
    found: list[Any],
    not_tuning_rows: list[dict[str, str]],
    backlog_rows: list[dict[str, str]],
) -> None:
    """Check every plain numeric constant is on one of the two lists."""
    listed = set(_keys(not_tuning_rows)) | set(_keys(backlog_rows))
    missing = sorted({
        f"{candidate.file}:{candidate.line} {candidate.name} = {candidate.value}"
        for candidate in found
        if (candidate.file, candidate.name) not in listed
    })
    assert not missing, (
        "These plain numeric values are on neither parameter list. If one is a tuning value, declare it "
        "as a TunedParameter. If it cannot be declared yet (a dict or nested-tuple value), add it to "
        "build/documentation/parameter_inventory/tuning_not_registered.csv with a note. If it is not "
        "tuning, add it to build/documentation/parameter_inventory/not_tuning.csv with a reason:\n"
        + "\n".join(missing)
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


def test_find_parameters_reads_argument_defaults(tmp_path: Path) -> None:
    """Find number defaults of arguments; skip bool, None and the rest."""
    library = tmp_path / "wayfindinglib"
    library.mkdir()
    (library / "example.py").write_text(
        textwrap.dedent(
            '''
            """Example module."""

            LIMIT_SCALE = 2


            class Finder:
                """A finder."""

                def detect(self, fwhm: float = 4.0, flag: bool = True, name="a", size=None, *, bias=-1.5):
                    """Detect."""

                    def inner(count=3):
                        """Count."""


            async def run(limit=LIMIT_SCALE, timeout=10):
                """Run."""
            '''
        )
    )
    found = {
        candidate.name: candidate
        for candidate in inventory.find_parameters(tmp_path)
        if candidate.kind == "argument default"
    }
    assert set(found) == {
        "Finder.detect.fwhm",
        "Finder.detect.bias",
        "Finder.detect.inner.count",
        "run.timeout",
    }
    assert found["Finder.detect.fwhm"].value == "4.0"
    assert found["Finder.detect.bias"].value == "-1.5"
    assert found["run.timeout"].bare_name == "timeout"


def _load_generator() -> ModuleType:
    """Load the parameter table generator from its file.

    Returns
    -------
    module : `types.ModuleType`
        The loaded ``generate_parameter_table`` module.
    """
    path = REPO_ROOT / "build" / "documentation" / "generate_parameter_table.py"
    spec = importlib.util.spec_from_file_location("generate_parameter_table", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def declarations() -> list[Any]:
    """Collect every declared parameter of both libraries.

    Returns
    -------
    parameters : `list` [`TunedParameter`]
        The declarations of ``astrometricslib`` and ``wayfindinglib``.
    """
    return [
        parameter
        for library in ("astrometricslib", "wayfindinglib")
        for parameter in collect_parameters(library).all()
    ]


def test_every_declaration_names_an_existing_register_entry(declarations: list[Any]) -> None:
    """Each ``register_entry`` is an entry or design-estimate row ID."""
    index = _load_generator().read_register_index(REPO_ROOT)
    missing = sorted(
        f"{parameter.name} -> {parameter.register_entry}"
        for parameter in declarations
        if parameter.register_entry not in index
    )
    assert not missing, f"These declarations name a register entry that does not exist: {missing}"


def test_hardcoded_parameters_have_a_kind_and_no_equipment(declarations: list[Any]) -> None:
    """A hardcoded value has a kind and no equipment (the model checks it)."""
    broken = sorted(
        parameter.name
        for parameter in declarations
        if parameter.source is ParameterSource.HARDCODED and (parameter.kind is None or parameter.equipment)
    )
    assert not broken, f"These hardcoded parameters need a kind and no equipment: {broken}"


def test_collecting_and_reading_the_register_print_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    """Collecting the registries and reading the register print nothing."""
    generator = _load_generator()
    for library in ("astrometricslib", "wayfindinglib"):
        collect_parameters(library)
    generator.read_register_index(REPO_ROOT)
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
