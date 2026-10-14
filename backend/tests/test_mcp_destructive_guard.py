"""Purpose: Tests for the guard that stops MCP tools deleting real data.

Description: The backend MCP's ``backend_call_rpc`` and
``electron_run_python`` tools must refuse the obvious ways to delete
catalog records, while ordinary reads and calculations pass.
"""

import pytest

from backend.services.infrastructure.destructive_guard import destructive_code_reason, destructive_rpc_reason


@pytest.mark.parametrize(
    "method", ["target:delete", "astronomy:delete", "images:delete_images", "x:Remove_all"]
)
def test_delete_style_rpc_methods_are_refused(method: str) -> None:
    """Methods whose action starts with a delete word are refused."""
    assert destructive_rpc_reason(method) is not None


@pytest.mark.parametrize("method", ["target:list", "images:last", "target:get", "astronomy:save"])
def test_ordinary_rpc_methods_are_allowed(method: str) -> None:
    """Reads and saves pass."""
    assert destructive_rpc_reason(method) is None


@pytest.mark.parametrize(
    "code",
    [
        "astrometrics.targets.delete('M 52')",
        "import shutil; shutil.rmtree('/x')",
        "from pathlib import Path; Path('a').unlink()",
        "callBackend('target:delete', {})",
        "import os; os.system('ls')",
    ],
)
def test_code_that_deletes_is_refused(code: str) -> None:
    """Delete-like calls, shell calls and delete RPC names are refused."""
    assert destructive_code_reason(code) is not None


@pytest.mark.parametrize(
    "code", ["x = 1 + 2", "astrometrics.targets.list()", "print('hello world')", "def broken(:"]
)
def test_harmless_code_passes(code: str) -> None:
    """Ordinary code, and code with a syntax error, is not refused."""
    assert destructive_code_reason(code) is None
