"""Purpose: Tests for the model contract validator developer tool.

Description: A missing scan folder raises `NotFoundError` instead of
returning an error dictionary, a folder with models is scanned, and by
default the tool scans astrometricslib itself.
"""

from pathlib import Path

import pytest

from astrometricslib import NotFoundError
from mcp_servers.devtools.contract_validator import LIBRARY_ROOT, typegen_contract_validator


def test_a_missing_folder_raises_not_found(tmp_path: Path) -> None:
    """Scanning a folder that does not exist raises NotFoundError."""
    with pytest.raises(NotFoundError, match="does not exist"):
        typegen_contract_validator(str(tmp_path / "missing"))


def test_a_folder_with_a_model_is_scanned(tmp_path: Path) -> None:
    """A model with a plain field is found and passes."""
    (tmp_path / "models.py").write_text(
        "from pydantic import BaseModel\n\n\nclass Star(BaseModel):\n    name: str\n", encoding="utf-8"
    )

    report = typegen_contract_validator(str(tmp_path))

    assert report["total_pydantic_models_found"] == 1
    assert report["models"][0]["class"] == "Star"


def test_the_default_folder_is_the_library() -> None:
    """Without a folder, the tool scans the astrometricslib package."""
    assert (LIBRARY_ROOT / "__init__.py").is_file()
    assert LIBRARY_ROOT.name == "astrometricslib"
