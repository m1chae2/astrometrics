"""Purpose: Tests for the backend MCP server's terminal_get_recent_runs tool.

Description: The tool lets an AI read what the person's recent Command
Console runs printed and drew. These tests replace the backend with a fake
that answers ``terminal:get_recent_runs``. They check the text and figure
paths, the images (and that a wide figure is shrunk), the limit bounds, that
only the read-only method is called, and that the profile serves the tool.
"""

import asyncio
import base64
import io
import json
from pathlib import Path
from typing import Any

import pytest
from mcp.types import ImageContent, TextContent
from PIL import Image

from astrometricslib.drivers import scratch_directory
from astrometricslib.exceptions import InvalidArgumentError
from mcp_servers.backend import definition
from mcp_servers.common.tool_registry import result_content


class FakeRunsBackend:
    """Stand in for the backend's recent-runs method.

    Attributes
    ----------
    runs : `list` [`dict` [`str`, `Any`]]
        The runs to answer with.
    calls : `list` [`tuple` [`str`, `Any`]]
        Every RPC call, in order.
    """

    def __init__(self, runs: list[dict[str, Any]]) -> None:
        """Start with the runs to report.

        Parameters
        ----------
        runs : `list` [`dict` [`str`, `Any`]]
            The runs, newest first.
        """
        self.runs = runs
        self.calls: list[tuple[str, Any]] = []

    async def __call__(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Answer one RPC call.

        Returns
        -------
        runs : `list` [`dict` [`str`, `Any`]]
            A copy of the runs, cut to the limit.
        """
        await asyncio.sleep(0)
        self.calls.append((method, params))
        assert params is not None
        return [{**run} for run in self.runs[: params["limit"]]]


def _write_png(width: int, height: int) -> Path:
    """Write a small PNG in the scratch folder.

    Returns
    -------
    path : `Path`
        The new file. The caller deletes it.
    """
    path = Path(scratch_directory()) / f"test_recent_runs_{width}x{height}.png"
    Image.new("RGB", (width, height), "white").save(path)
    return path


def _run(plot_paths: list[str]) -> dict[str, Any]:
    """Build one run record like the backend's.

    Returns
    -------
    run : `dict` [`str`, `Any`]
        A run with the given figure files.
    """
    return {
        "ran_at": "2026-11-15T12:00:00+00:00",
        "label": "demo.py",
        "source": "editor",
        "source_code": "print(1)",
        "status": "success",
        "stdout": "1\n",
        "stderr": "",
        "plot_paths": plot_paths,
        "execution_time_ms": 5,
    }


def test_returns_text_and_images(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each figure becomes an image after the text, and wide ones shrink."""
    small = _write_png(300, 200)
    wide = _write_png(2400, 1200)
    try:
        backend = FakeRunsBackend([_run([str(small), str(wide)])])
        monkeypatch.setattr(definition, "execute_rpc", backend)
        content = result_content(asyncio.run(definition.tool_terminal_get_recent_runs(limit=3)))
        assert backend.calls == [("terminal:get_recent_runs", {"limit": 3})]
        assert isinstance(content[0], TextContent)
        body = json.loads(content[0].text)
        assert body["runs"][0]["stdout"] == "1\n"
        assert body["runs"][0]["plot_paths"] == [str(small), str(wide)]
        assert body["runs"][0]["plot_images"] == {"attached": 2, "problems": []}
        images = content[1:]
        assert len(images) == 2
        assert all(isinstance(image, ImageContent) for image in images)
        sizes = [Image.open(io.BytesIO(base64.b64decode(image.data))).size for image in images]
        assert sizes == [(300, 200), (1200, 600)]
    finally:
        small.unlink(missing_ok=True)
        wide.unlink(missing_ok=True)


def test_without_plots_returns_paths_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """include_plots false returns only the text, with the paths."""
    backend = FakeRunsBackend([_run(["/somewhere/figure.png"])])
    monkeypatch.setattr(definition, "execute_rpc", backend)
    content = asyncio.run(definition.tool_terminal_get_recent_runs(include_plots=False))
    assert len(content) == 1
    assert backend.calls == [("terminal:get_recent_runs", {"limit": 1})]
    assert json.loads(content[0].text)["runs"][0]["plot_paths"] == ["/somewhere/figure.png"]


def test_missing_and_outside_files_are_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A gone file, or one outside the scratch folder, gives a note."""
    outside = tmp_path / "other.png"
    Image.new("RGB", (10, 10)).save(outside)
    gone = Path(scratch_directory()) / "test_recent_runs_gone.png"
    backend = FakeRunsBackend([_run([str(outside), str(gone)])])
    monkeypatch.setattr(definition, "execute_rpc", backend)
    content = asyncio.run(definition.tool_terminal_get_recent_runs())
    assert len(content) == 1
    images_info = json.loads(content[0].text)["runs"][0]["plot_images"]
    assert images_info["attached"] == 0
    assert len(images_info["problems"]) == 2


@pytest.mark.parametrize("bad_limit", [0, 21, -3, True])
def test_limit_bounds_are_checked_before_any_call(monkeypatch: pytest.MonkeyPatch, bad_limit: int) -> None:
    """A limit outside 1 to 20 is refused and the backend is not called."""
    backend = FakeRunsBackend([])
    monkeypatch.setattr(definition, "execute_rpc", backend)
    with pytest.raises(InvalidArgumentError):
        asyncio.run(definition.tool_terminal_get_recent_runs(limit=bad_limit))
    assert backend.calls == []


def test_profile_serves_the_tool_as_observe() -> None:
    """The profile offers the tool, and the manifest calls it a read."""
    registry = definition.build_registry()
    registry.apply_profile(definition.MANIFEST_PATH)
    assert "terminal_get_recent_runs" in registry.tools
    manifest = json.loads(definition.MANIFEST_PATH.read_text())
    assert manifest["tools"]["terminal_get_recent_runs"]["tool_class"] == "observe"
