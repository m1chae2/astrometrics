"""Tests for the path sandbox in the MCP tool registry.

A tool that takes a path argument may only run on a path inside the
library, frames, or stacks folders. These tests check that the registry
allows a path inside those folders, refuses one outside them, and also
refuses the call when it cannot check the path at all.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrometricslib.foundation.paths import is_path_inside
from astrometricslib.mcp.tool_registry import ToolRegistry

SCHEMA = {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": []}


def _registry_with_probe(ran: list[str]) -> ToolRegistry:
    """Build a registry with one tool that records that it ran.

    Parameters
    ----------
    ran : `list` [`str`]
        List the tool appends to each time it runs.

    Returns
    -------
    registry : `ToolRegistry`
        A registry holding the tool ``probe``.
    """
    registry = ToolRegistry()

    @registry.register("probe", "Record a call.", SCHEMA)
    def probe(file_path: str = "") -> str:
        """Record that the tool ran.

        Parameters
        ----------
        file_path : `str`, optional
            The path argument the sandbox checks.

        Returns
        -------
        reply : `str`
            A short fixed reply.
        """
        ran.append(file_path)
        return "ran"

    return registry


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the configuration at three folders under a temporary directory.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        Pytest's temporary directory.
    monkeypatch : `pytest.MonkeyPatch`
        Pytest's monkeypatch fixture.

    Returns
    -------
    library : `pathlib.Path`
        The library folder, which is inside the sandbox.
    """
    library = tmp_path / "library"
    frames = tmp_path / "frames"
    stacks = tmp_path / "stacks"
    for folder in (library, frames, stacks):
        folder.mkdir()
    config = SimpleNamespace(
        get_library_path=lambda: library,
        get_frames_path=lambda: frames,
        get_stacks_path=lambda: stacks,
    )
    monkeypatch.setattr("astrometricslib.foundation.config.get_configuration", lambda: config)
    return library


def test_a_path_inside_the_sandbox_is_allowed(sandbox: Path) -> None:
    """The tool runs when its path is under the library folder."""
    ran: list[str] = []
    registry = _registry_with_probe(ran)
    reply = asyncio.run(registry.execute("probe", {"file_path": str(sandbox / "a.fits")}))
    assert reply[0].text == '"ran"'
    assert ran == [str(sandbox / "a.fits")]


def test_a_path_outside_the_sandbox_is_refused(sandbox: Path, tmp_path: Path) -> None:
    """The tool does not run when its path is outside every sandbox folder."""
    ran: list[str] = []
    registry = _registry_with_probe(ran)
    reply = asyncio.run(registry.execute("probe", {"file_path": str(tmp_path / "elsewhere.fits")}))
    assert "Security violation" in reply[0].text
    assert ran == []


def test_a_path_that_climbs_out_of_the_sandbox_is_refused(sandbox: Path) -> None:
    """A path that uses ``..`` to leave the library folder is refused."""
    ran: list[str] = []
    registry = _registry_with_probe(ran)
    reply = asyncio.run(registry.execute("probe", {"file_path": str(sandbox / ".." / "x.fits")}))
    assert "Security violation" in reply[0].text
    assert ran == []


def test_the_call_is_refused_when_the_configuration_is_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse a path tool when the sandbox folders cannot be found."""

    def broken() -> None:
        """Fail the way a missing configuration file does.

        Raises
        ------
        FileNotFoundError
            Always.
        """
        raise FileNotFoundError("no configuration")

    monkeypatch.setattr("astrometricslib.foundation.config.get_configuration", broken)
    ran: list[str] = []
    registry = _registry_with_probe(ran)
    reply = asyncio.run(registry.execute("probe", {"file_path": "/anywhere/a.fits"}))
    assert "Security violation" in reply[0].text
    assert "could not be checked" in reply[0].text
    assert ran == []


def test_the_configuration_is_not_needed_when_there_is_no_path_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run a tool with no path argument even if the configuration fails."""

    def broken() -> None:
        """Fail the way a missing configuration file does.

        Raises
        ------
        FileNotFoundError
            Always.
        """
        raise FileNotFoundError("no configuration")

    monkeypatch.setattr("astrometricslib.foundation.config.get_configuration", broken)
    ran: list[str] = []
    registry = _registry_with_probe(ran)
    reply = asyncio.run(registry.execute("probe", {}))
    assert reply[0].text == '"ran"'
    assert ran == [""]


def test_paths_that_cannot_be_compared_are_not_inside() -> None:
    """`_is_inside` returns `False` for a relative root instead of raising."""
    assert is_path_inside("/a/b", "/a/b/c")
    assert not is_path_inside("/a/b", "/a/bc")
    assert not is_path_inside("relative", "/a/b")
