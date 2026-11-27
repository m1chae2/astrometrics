"""Purpose: Tests for the terminal_add_script tool of the backend MCP server.

Description: The tool lets an AI save a new script to the Command Console's
My scripts list, and never run it. These tests replace the backend with a
small fake that keeps scripts in memory. They check the saved text and its
header, that an existing name is refused, that a bad file name is refused
before any call, that no code-running RPC method is ever used, and that
the profile offers the tool and still withholds the code runner. The
description must state the console script convention.
"""

import asyncio
from datetime import date
from typing import Any

import pytest

from astrometricslib.exceptions import ConflictError, InvalidArgumentError
from mcp_servers.backend import definition


class FakeScriptBackend:
    """Stand in for the backend's user script methods.

    Attributes
    ----------
    scripts : `dict` [`str`, `str`]
        File name -> saved text.
    methods : `list` [`str`]
        Every RPC method the tool called, in order.
    """

    def __init__(self, scripts: dict[str, str] | None = None) -> None:
        """Start with some saved scripts.

        Parameters
        ----------
        scripts : `dict` [`str`, `str`], optional
            Scripts that already exist.
        """
        self.scripts = dict(scripts or {})
        self.methods: list[str] = []

    async def __call__(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Answer one RPC call.

        Returns
        -------
        data : `Any`
            The list or the save result.
        """
        await asyncio.sleep(0)
        self.methods.append(method)
        if method == "terminal:list_scripts":
            return [{"filename": name} for name in self.scripts]
        assert method == "terminal:save_script"
        assert params is not None
        self.scripts[params["filename"]] = params["content"]
        return {"filename": params["filename"], "size_bytes": len(params["content"]), "status": "saved"}


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> FakeScriptBackend:
    """Replace `execute_rpc` with the fake.

    Returns
    -------
    backend : `FakeScriptBackend`
        The fake, with no scripts.
    """
    fake = FakeScriptBackend()
    monkeypatch.setattr(definition, "execute_rpc", fake)
    return fake


@pytest.mark.anyio
async def test_a_new_script_is_saved_with_a_header(backend: FakeScriptBackend) -> None:
    """The saved text starts with the AI header and ends with the code."""
    result = await definition.tool_terminal_add_script("plot_it.py", "print(1)\n", "Plots it.\nTwo lines.")
    saved = backend.scripts["plot_it.py"]
    assert saved.startswith("# Added by an AI client")
    assert f"# Date added: {date.today().isoformat()}" in saved
    assert "# Description: Plots it.\n# Description: Two lines.\n" in saved
    assert saved.endswith("\n\nprint(1)\n")
    assert result["filename"] == "plot_it.py"
    assert "Command Console > Scripts > My scripts" in result["next_step"]
    assert "press Run" in result["next_step"]
    assert backend.methods == ["terminal:list_scripts", "terminal:save_script"]


@pytest.mark.anyio
async def test_a_description_cannot_add_code(backend: FakeScriptBackend) -> None:
    """Every line of the description stays a comment."""
    await definition.tool_terminal_add_script("a.py", "x = 1\n", "ok\nimport os")
    header = backend.scripts["a.py"].split("\n\n")[0]
    assert all(line.startswith("#") for line in header.splitlines())


@pytest.mark.anyio
async def test_an_existing_script_is_not_overwritten(backend: FakeScriptBackend) -> None:
    """The refusal names the file, and nothing is saved."""
    backend.scripts["mine.py"] = "keep me"
    with pytest.raises(ConflictError, match=r"mine\.py"):
        await definition.tool_terminal_add_script("MINE.py", "x = 1\n")
    assert backend.scripts == {"mine.py": "keep me"}
    assert "terminal:save_script" not in backend.methods


@pytest.mark.anyio
@pytest.mark.parametrize(
    "bad_name",
    ["../x.py", "sub/x.py", "x.txt", "x", ".py", "a b.py", "x.py.py", "/etc/x.py", ".hidden.py", ""],
)
async def test_a_bad_file_name_is_refused_before_any_call(backend: FakeScriptBackend, bad_name: str) -> None:
    """Only a plain ``.py`` name is accepted."""
    with pytest.raises(InvalidArgumentError):
        await definition.tool_terminal_add_script(bad_name, "x = 1\n")
    assert backend.methods == []


@pytest.mark.anyio
async def test_empty_content_is_refused(backend: FakeScriptBackend) -> None:
    """A blank script is not saved."""
    with pytest.raises(InvalidArgumentError):
        await definition.tool_terminal_add_script("blank.py", "  \n")
    assert backend.methods == []


def test_the_profile_offers_the_tool_and_withholds_the_code_runner() -> None:
    """The tool is served; the code runner and generic RPC call are not."""
    registry = definition.build_registry()
    registry.apply_profile(definition.MANIFEST_PATH)
    assert "terminal_add_script" in registry.tools
    assert "electron_run_python" in registry.withheld
    assert "backend_call_rpc" in registry.withheld


def test_the_description_states_the_script_convention() -> None:
    """The tool tells the AI how to write a script that can be promoted.

    The analysis goes in a function with explicit inputs, the console
    helpers only in driver lines, and ``selection`` gives library objects
    and 1-based pixels.
    """
    description = definition.build_registry().tools["terminal_add_script"]["tool_def"].description
    assert "function with explicit inputs" in description
    assert "driver lines" in description
    assert "the library's Target" in description and "x_px and y_px" in description
    assert "counted from 1" in description
