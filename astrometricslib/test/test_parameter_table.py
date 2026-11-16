"""Purpose: Test the parameter table generator.

Description: ``build/documentation/generate_parameter_table.py`` reads the
parameter registries of ``astrometricslib`` and ``wayfindinglib`` and the
validation register, and writes a review table of every tuned parameter.
These tests run it on the real repository and check its output.
"""

import csv
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "build" / "documentation" / "generate_parameter_table.py"


@pytest.fixture(scope="module")
def generator() -> ModuleType:
    """Load the generator script as a module.

    Returns
    -------
    generator : `types.ModuleType`
        The loaded ``generate_parameter_table`` module.
    """
    spec = importlib.util.spec_from_file_location("generate_parameter_table", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def output_folder(generator: ModuleType, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Run the generator on the real repository.

    Parameters
    ----------
    generator : `types.ModuleType`
        The loaded generator.
    tmp_path_factory : `pytest.TempPathFactory`
        Makes the output folder.

    Returns
    -------
    out : `pathlib.Path`
        The folder the generator wrote to.
    """
    out = tmp_path_factory.mktemp("parameter_table")
    assert generator.main(["--out", str(out)]) == 0
    return out


@pytest.fixture(scope="module")
def rows(generator: ModuleType, output_folder: Path) -> list[dict[str, str]]:
    """Read the CSV the generator wrote.

    Parameters
    ----------
    generator : `types.ModuleType`
        The loaded generator, for its column list.
    output_folder : `pathlib.Path`
        The generator's output folder.

    Returns
    -------
    rows : `list` [`dict` [`str`, `str`]]
        One dictionary per CSV row.
    """
    with (output_folder / "parameter_table.csv").open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames or ()) == generator.COLUMNS
        return list(reader)


def test_writes_the_csv_and_every_markdown_page(generator: ModuleType, output_folder: Path) -> None:
    """The CSV, one page per register file and the equipment page exist."""
    assert (output_folder / "parameter_table.csv").is_file()
    assert (output_folder / "parameter_table_by_equipment.md").is_file()
    for register in generator.REGISTER_FILES:
        page = output_folder / f"parameter_table_{register}.md"
        assert page.is_file()
        assert "generate_parameter_table.py" in page.read_text(encoding="utf-8")


def test_has_declared_and_backlog_rows_from_every_register_file(
    generator: ModuleType, rows: list[dict[str, str]]
) -> None:
    """Declared parameters fill every register page; backlog rows may follow.

    The backlog (tuning values not declared yet) is empty once every value
    is declared, so only the declared rows are required.
    """
    row_types = {row["row_type"] for row in rows}
    assert "declared" in row_types
    assert row_types <= {"declared", "backlog"}
    declared = {row["register"] for row in rows if row["row_type"] == "declared"}
    assert set(generator.REGISTER_FILES) <= declared
    assert all(row["status"] for row in rows if row["row_type"] == "declared")


def test_known_parameter_has_default_location_and_entry(rows: list[dict[str, str]]) -> None:
    """The flat noise fraction shows 0.005, its file and line, and STK-05."""
    row = next(row for row in rows if row["parameter"] == "stacking.maximum_flat_noise_fraction")
    assert row["default"] == "0.005"
    assert row["constant"] == "MAXIMUM_FLAT_NOISE_FRACTION"
    assert row["location"].startswith(
        "astrometricslib/pipelines/stacking/pre_processing/flat_calibration.py:"
    )
    assert row["location"].rsplit(":", 1)[1].isdigit()
    assert row["register_entry"] == "STK-05"
    assert row["status"] in {"Measured", "Partly measured"}
    assert row["role"] == "judgment"


def test_equipment_page_has_a_section_for_each_kind_including_site(
    generator: ModuleType, output_folder: Path
) -> None:
    """The equipment page lists every equipment kind and the site."""
    text = (output_folder / "parameter_table_by_equipment.md").read_text(encoding="utf-8")
    assert "site" in generator.EQUIPMENT_KINDS
    for kind in generator.EQUIPMENT_KINDS:
        assert f"## {kind} (" in text
