"""Purpose: Hold the register to the rule that automatic values are measured.

Description: ``documentation/Documentation_Conventions.md`` Section 2 says a
setting the software acts on with no person checking (an automatic role, see
``validation/index.md`` Section 2.2) needs a Measured register entry. Most
automatic settings are not measured yet, so they are listed in
``build/documentation/parameter_inventory/automatic_not_measured.csv``. The
list only shrinks: a new automatic setting must arrive measured, and a row
whose setting is now measured, advisory or gone must be deleted.

The register rows come from the parameter table generator,
``build/documentation/generate_parameter_table.py``, so this test reads the
register the same way the review table does.
"""

import csv
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "build" / "documentation" / "generate_parameter_table.py"
ALLOW_LIST = REPO_ROOT / "build" / "documentation" / "parameter_inventory" / "automatic_not_measured.csv"


@pytest.fixture(scope="module")
def unmeasured_automatic() -> set[tuple[str, str, str]]:
    """List the automatic register settings that have no Measured entry.

    Returns
    -------
    keys : `set` [`tuple` [`str`, `str`, `str`]]
        One (register, entry or row ID, setting) key per automatic setting
        whose entry is not Measured. Design-estimate rows are never Measured.
    """
    spec = importlib.util.spec_from_file_location("generate_parameter_table", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    generator: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = generator
    spec.loader.exec_module(generator)
    rows, _ = generator.build_rows(REPO_ROOT)
    return {
        (row["register"], row["entry_id"], row["setting"])
        for row in rows
        if row["source"] != "backlog" and row["role"].startswith("automatic") and row["status"] != "Measured"
    }


@pytest.fixture(scope="module")
def allowed() -> list[tuple[str, str, str]]:
    """Read the allow-list of automatic settings not measured yet.

    Returns
    -------
    keys : `list` [`tuple` [`str`, `str`, `str`]]
        One (register, ID, setting) key per row, in file order.
    """
    with ALLOW_LIST.open(newline="", encoding="utf-8") as handle:
        return [(row["register"], row["id"], row["setting"]) for row in csv.DictReader(handle)]


def test_every_unmeasured_automatic_setting_is_on_the_allow_list(
    unmeasured_automatic: set[tuple[str, str, str]], allowed: list[tuple[str, str, str]]
) -> None:
    """A new automatic setting must arrive with a Measured entry."""
    new = sorted(unmeasured_automatic - set(allowed))
    assert not new, (
        "These automatic settings have no Measured register entry. Measure them before they act on data, "
        "or mark them advisory if a person reads them before anything happens "
        f"(Documentation_Conventions.md, Section 2): {new}"
    )


def test_the_allow_list_only_shrinks(
    unmeasured_automatic: set[tuple[str, str, str]], allowed: list[tuple[str, str, str]]
) -> None:
    """A row whose setting is now measured, advisory or gone is deleted."""
    done = sorted(set(allowed) - unmeasured_automatic)
    assert not done, (
        "These settings are now Measured, advisory or no longer in the register; delete their rows from "
        f"build/documentation/parameter_inventory/automatic_not_measured.csv: {done}"
    )
    repeated = sorted({key for key in allowed if allowed.count(key) > 1})
    assert not repeated, f"These allow-list rows are repeated: {repeated}"
