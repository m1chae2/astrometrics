"""Purpose: Test the parameter table generator.

Description: ``build/documentation/generate_parameter_table.py`` reads the
validation register and writes a review table of every tuned parameter. These
tests run it on the real register and check its output and its bullet parser.
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
    """Run the generator on the real register.

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


def test_writes_the_csv_and_every_markdown_page(generator: ModuleType, output_folder: Path) -> None:
    """The CSV, one page per register file and the equipment page exist."""
    assert (output_folder / "parameter_table.csv").is_file()
    assert (output_folder / "parameter_table_by_equipment.md").is_file()
    for register in generator.REGISTER_FILES:
        page = output_folder / f"parameter_table_{register}.md"
        assert page.is_file()
        assert "generate_parameter_table.py" in page.read_text(encoding="utf-8")


def test_has_rows_from_every_register_file(generator: ModuleType, output_folder: Path) -> None:
    """Each register file contributes at least one row."""
    with (output_folder / "parameter_table.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert set(rows[0]) == set(generator.COLUMNS)
    registers = {row["register"] for row in rows}
    assert set(generator.REGISTER_FILES) <= registers
    assert {"register entry", "design estimate"} <= {row["source"] for row in rows}


def test_known_setting_has_live_value_and_location(output_folder: Path) -> None:
    """``MAXIMUM_FLAT_NOISE_FRACTION`` in STK-05 shows 0.005 and its line."""
    with (output_folder / "parameter_table.csv").open(encoding="utf-8", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["setting"] == "MAXIMUM_FLAT_NOISE_FRACTION"]
    row = next(row for row in rows if row["entry_id"] == "STK-05")
    assert row["live_value"] == "0.005"
    assert "flat_calibration.py:" in row["code_location"]
    assert row["status"] in {"Measured", "Partly measured"}


def test_bullet_parser_reads_a_marked_line(generator: ModuleType) -> None:
    """The example line of ``validation/index.md`` Section 2.2 parses fully."""
    line = (
        "  - code constant `MAXIMUM_FLAT_NOISE_FRACTION` = 0.005 in `flat_calibration.py`. "
        "Relation: derived from this entry. Role: automatic (gives a verdict). "
        "Source: fixed in code. Equipment: camera, telescope. <!-- impl: a.b.C -->"
    )
    parsed = generator.parse_setting_bullet(line)
    assert parsed == {
        "setting": "MAXIMUM_FLAT_NOISE_FRACTION",
        "relation": "derived from",
        "impl_link": "a.b.C",
        "role": "automatic (gives a verdict)",
        "source_of_value": "fixed in code",
        "equipment": "camera, telescope",
    }
    unmarked = generator.parse_setting_bullet("  - `X_Y` in `a.py`. Relation: sanity check only.")
    assert unmarked["role"] == "" and unmarked["equipment"] == ""
