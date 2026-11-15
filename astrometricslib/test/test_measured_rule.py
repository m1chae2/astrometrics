"""Purpose: Hold the parameters to the rule that automatic values are measured.

Description: ``.claude/skills/documentation-style/CONVENTIONS.md`` Section 2
says a value the software acts on with no person checking (any role except
``recommendation``) needs a Measured register entry. Most automatic
parameters are not measured yet, so they are listed in
``build/documentation/parameter_inventory/automatic_not_measured.csv``. The
list only shrinks: a new automatic parameter must arrive with a Measured
entry (a design-estimate row is never Measured), and a row whose parameter
is now measured, advisory or gone must be deleted.

The parameters come from the two registries and the entry status from the
validation register, read by
``build/documentation/generate_parameter_table.py``.
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
ALLOW_LIST_COLUMNS = ["parameter", "register_entry", "role", "status"]


@pytest.fixture(scope="module")
def unmeasured_automatic() -> list[dict[str, str]]:
    """List the automatic parameters whose register entry is not Measured.

    Returns
    -------
    rows : `list` [`dict` [`str`, `str`]]
        One row per parameter name, with the columns of the allow-list.
    """
    spec = importlib.util.spec_from_file_location("generate_parameter_table", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    generator: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = generator
    spec.loader.exec_module(generator)
    return generator.automatic_not_measured(generator.build_rows(REPO_ROOT))


@pytest.fixture(scope="module")
def allowed() -> list[dict[str, str]]:
    """Read the allow-list of automatic parameters not measured yet.

    Returns
    -------
    rows : `list` [`dict` [`str`, `str`]]
        One row per parameter, in file order.
    """
    with ALLOW_LIST.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == ALLOW_LIST_COLUMNS
        return list(reader)


def test_every_unmeasured_automatic_parameter_is_on_the_allow_list(
    unmeasured_automatic: list[dict[str, str]], allowed: list[dict[str, str]]
) -> None:
    """A new automatic parameter must arrive with a Measured entry."""
    new = sorted({row["parameter"] for row in unmeasured_automatic} - {row["parameter"] for row in allowed})
    assert not new, (
        "These automatic parameters have no Measured register entry. Measure them before they act on data, "
        "or give them the role recommendation if a person reads them before anything happens "
        f"(.claude/skills/documentation-style/CONVENTIONS.md, Section 2): {new}"
    )


def test_the_allow_list_only_shrinks(
    unmeasured_automatic: list[dict[str, str]], allowed: list[dict[str, str]]
) -> None:
    """A measured or gone parameter leaves the list; no row repeats."""
    names = [row["parameter"] for row in allowed]
    done = sorted(set(names) - {row["parameter"] for row in unmeasured_automatic})
    assert not done, (
        "These parameters are now Measured, advisory or no longer declared; delete their rows from "
        f"build/documentation/parameter_inventory/automatic_not_measured.csv: {done}"
    )
    repeated = sorted({name for name in names if names.count(name) > 1})
    assert not repeated, f"These allow-list rows are repeated: {repeated}"
