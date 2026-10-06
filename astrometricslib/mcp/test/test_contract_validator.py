"""Tests for the model contract validator MCP tool.

A missing scan folder raises `NotFoundError` instead of returning an error
dictionary, and a folder with models is scanned.
"""

import asyncio
from pathlib import Path

import pytest

from astrometricslib.foundation.errors import NotFoundError
from astrometricslib.mcp.tools.contract_validator import typegen_contract_validator


def test_a_missing_folder_raises_not_found(tmp_path: Path) -> None:
    """Scanning a folder that does not exist raises NotFoundError."""
    with pytest.raises(NotFoundError, match="does not exist"):
        asyncio.run(typegen_contract_validator(str(tmp_path / "missing")))


def test_a_folder_with_a_model_is_scanned(tmp_path: Path) -> None:
    """A model with a plain field is found and passes."""
    (tmp_path / "models.py").write_text(
        "from pydantic import BaseModel\n\n\nclass Star(BaseModel):\n    name: str\n", encoding="utf-8"
    )

    report = asyncio.run(typegen_contract_validator(str(tmp_path)))

    assert report["total_pydantic_models_found"] == 1
    assert report["models"][0]["class"] == "Star"
