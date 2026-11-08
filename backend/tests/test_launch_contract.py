"""Purpose: Test the launcher contract: the observatory source choice file.

Description: The choice is a small JSON file in the settings folder. A missing
or damaged file means the real telescope, and a write never leaves a partly
written file.
"""

from pathlib import Path

from backend.public_interface import (
    CHOICE_FILE_NAME,
    NO_TELESCOPE,
    REAL,
    SIMULATED,
    SourceChoice,
    choice_file_path,
    normalize_source,
    read_choice,
    write_choice,
)


def test_a_missing_file_means_the_real_telescope(tmp_path: Path) -> None:
    """With no file the choice is real."""
    assert read_choice({"XDG_CONFIG_HOME": str(tmp_path)}) == SourceChoice(REAL)


def test_a_written_choice_is_read_back(tmp_path: Path) -> None:
    """The choice survives a write and a read, in the settings folder."""
    environment = {"XDG_CONFIG_HOME": str(tmp_path)}

    write_choice(SourceChoice(SIMULATED), environment)

    assert choice_file_path(environment) == tmp_path / "astrometrics" / CHOICE_FILE_NAME
    assert read_choice(environment).source == SIMULATED
    assert not list((tmp_path / "astrometrics").glob("*.tmp"))


def test_a_damaged_or_unknown_choice_means_real(tmp_path: Path) -> None:
    """Bad JSON and an unknown source both read as real."""
    environment = {"XDG_CONFIG_HOME": str(tmp_path)}
    path = choice_file_path(environment)
    path.parent.mkdir(parents=True)

    path.write_text("{not json", encoding="utf-8")
    assert read_choice(environment).source == REAL
    path.write_text('{"source": "replay"}', encoding="utf-8")
    assert read_choice(environment).source == REAL


def test_the_real_settings_variable_wins_over_a_moved_folder(tmp_path: Path) -> None:
    """A moved XDG_CONFIG_HOME leaves the choice in the real folder."""
    environment = {
        "XDG_CONFIG_HOME": str(tmp_path / "world"),
        "ASTROMETRICS_REAL_CONFIG_HOME": str(tmp_path / "real"),
    }

    write_choice(SourceChoice(SIMULATED), environment)

    assert (tmp_path / "real" / "astrometrics" / CHOICE_FILE_NAME).exists()
    assert not (tmp_path / "world").exists()


def test_a_saved_state_that_is_not_a_choice_means_the_real_telescope(tmp_path: Path) -> None:
    """A saved ``offline`` or ``no_telescope`` reads as the real telescope.

    Both are states, never choices the user can make.
    """
    environment = {"XDG_CONFIG_HOME": str(tmp_path)}
    path = choice_file_path(environment)
    path.parent.mkdir(parents=True)
    path.write_text('{"source": "offline"}', encoding="utf-8")

    assert read_choice(environment) == SourceChoice()
    assert normalize_source("offline") == NO_TELESCOPE
    assert normalize_source("no_telescope") == NO_TELESCOPE
    assert normalize_source(None) == REAL
    assert NO_TELESCOPE == "no_telescope"


def test_the_contract_and_the_public_interface_import_no_app_code() -> None:
    """A launcher imports the contract without starting the app or FastAPI."""
    import ast
    import sys

    allowed = set(sys.stdlib_module_names) | {"backend"}
    for name in ("launch_contract.py", "public_interface.py"):
        tree = ast.parse((Path(__file__).resolve().parents[1] / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            for module in modules:
                assert module.split(".")[0] in allowed, f"{name} imports {module}"
                if module.startswith("backend"):
                    assert module == "backend.launch_contract", f"{name} imports {module}"
