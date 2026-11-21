"""Purpose: Tests for the AI tools that start a job in the app.

Description: Each job tool must start its work through the same backend RPC
method as the app's own button, so the job is the app's job: it shows in
the job list and the app can cancel it. These tests replace the HTTP call
with canned replies and check which method each tool calls and with which
parameters, that a started job's reply says how to follow it, that a tool
fails clearly when the backend is not running, and that only the backend
server serves these tools.
"""

import asyncio
from typing import Any

import pytest

from astrometricslib.exceptions import ExternalServiceError, InvalidArgumentError, NotFoundError
from backend.public_interface import RPC_METHODS
from mcp_servers.astrometrics_core import definition as astrometrics_definition
from mcp_servers.backend import app_jobs, definition
from mcp_servers.common.app_job_tools import APP_JOB_TOOLS
from mcp_servers.common.profile import find_withheld_tools, load_manifest
from mcp_servers.wayfinding_core import definition as wayfinding_definition


def fake_backend(monkeypatch: pytest.MonkeyPatch, replies: dict[str, Any]) -> list[dict]:
    """Answer each RPC method with its canned result.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Pytest's monkeypatch fixture.
    replies : `dict` [`str`, `Any`]
        RPC method -> the result its handler returns.

    Returns
    -------
    requests : `list` [`dict`]
        Every JSON-RPC request the tools sent, in order.
    """
    requests: list[dict] = []

    async def fake_post(endpoint: str, payload: dict | None = None, timeout: float = 120.0) -> dict:
        """Record the request and answer with the method's canned result.

        Returns
        -------
        reply : `dict`
            A JSON-RPC success reply wrapped as the RPC route does.
        """
        await asyncio.sleep(0)
        assert endpoint == "/api/rpc"
        requests.append(payload or {})
        result = replies[(payload or {})["method"]]
        return {"jsonrpc": "2.0", "id": "mcp-proxy", "result": {"status": "success", "data": result}}

    monkeypatch.setattr(definition, "post_to_backend", fake_post)
    return requests


def test_only_the_backend_server_serves_the_job_tools() -> None:
    """The backend server offers every job tool; the library servers none."""
    backend_tools = definition.build_registry().tools
    assert set(APP_JOB_TOOLS) <= set(backend_tools)
    library_tools = set(astrometrics_definition.build_registry().tools)
    library_tools |= set(wayfinding_definition.build_registry().tools)
    assert not set(APP_JOB_TOOLS) & library_tools
    manifest = load_manifest(definition.MANIFEST_PATH)
    assert not find_withheld_tools(sorted(APP_JOB_TOOLS), manifest)


def test_each_job_tool_names_a_declared_rpc_method() -> None:
    """The method each tool calls is one the backend declares."""
    assert set(APP_JOB_TOOLS.values()) <= set(RPC_METHODS)


@pytest.mark.anyio
async def test_stack_starts_the_stack_buttons_job_with_the_frame_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The frame choice goes to ``processing:stack``; left-out ones do not."""
    requests = fake_backend(
        monkeypatch, {"processing:stack": {"status": "started", "jobId": "job-7", "logFile": "/logs/x.log"}}
    )

    reply = await app_jobs.tool_processing_stack("M 13", kind="imaging", filter_name="L", first_file="013")

    assert requests == [
        {
            "jsonrpc": "2.0",
            "method": "processing:stack",
            "params": {"target_id": "M 13", "kind": "imaging", "filter_name": "L", "first_file": "013"},
            "id": "mcp-proxy",
        }
    ]
    assert reply["jobId"] == "job-7"
    assert "jobs_query" in reply["follow"]


@pytest.mark.anyio
async def test_stack_sends_exact_frames_as_the_buttons_frame_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """``frames`` becomes ``image_files``, the Stack button's frame list."""
    requests = fake_backend(monkeypatch, {"processing:stack": {"status": "started", "jobId": "job-8"}})

    await app_jobs.tool_processing_stack("M 13", frames=["/lights/a.fits", "/lights/b.fits"], force=True)

    assert requests[0]["params"] == {
        "target_id": "M 13",
        "image_files": ["/lights/a.fits", "/lights/b.fits"],
        "force": True,
    }


@pytest.mark.anyio
async def test_a_stack_plan_comes_back_as_the_backend_gave_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """A plan starts no job, so it carries no hint to follow one."""
    plan = {"status": "plan", "plan": {"frames_selected": 12, "first_file": "a", "last_file": "b"}}
    requests = fake_backend(monkeypatch, {"processing:stack": plan})

    reply = await app_jobs.tool_processing_stack("M 13", plan_only=True)

    assert requests[0]["params"] == {"target_id": "M 13", "plan_only": True}
    assert reply == plan


@pytest.mark.anyio
async def test_identify_stack_starts_the_astrometry_buttons_job(monkeypatch: pytest.MonkeyPatch) -> None:
    """A given stack goes straight to ``analysis:identify_stack``."""
    requests = fake_backend(monkeypatch, {"analysis:identify_stack": {"status": "started", "jobId": "job-9"}})

    reply = await app_jobs.tool_processing_identify_stack("M 13", stack_path="/stacks/M_13.fits")

    assert [request["method"] for request in requests] == ["analysis:identify_stack"]
    assert requests[0]["params"] == {"target_id": "M 13", "stack_path": "/stacks/M_13.fits"}
    assert reply["jobId"] == "job-9"
    assert "processing_get_stack_identification" in reply["follow"]


@pytest.mark.anyio
async def test_identify_stack_uses_the_targets_stack_when_none_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a path the tool reads the target's imaging stack first."""
    requests = fake_backend(
        monkeypatch,
        {
            "target:get": {"id": "M 13", "stacking": {"stackedImage": "/stacks/M_13_L.fits"}},
            "analysis:identify_stack": {"status": "started", "jobId": "job-10"},
        },
    )

    await app_jobs.tool_processing_identify_stack("M 13")

    assert [request["method"] for request in requests] == ["target:get", "analysis:identify_stack"]
    assert requests[1]["params"]["stack_path"] == "/stacks/M_13_L.fits"


@pytest.mark.anyio
async def test_identify_stack_refuses_a_target_with_no_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    """No stack and no path is a clear error, and no job starts."""
    requests = fake_backend(monkeypatch, {"target:get": {"id": "M 13", "stacking": {"stackedImage": ""}}})

    with pytest.raises(NotFoundError, match="no imaging stack"):
        await app_jobs.tool_processing_identify_stack("M 13")

    assert [request["method"] for request in requests] == ["target:get"]


@pytest.mark.anyio
async def test_remake_preview_starts_the_apps_preview_job(monkeypatch: pytest.MonkeyPatch) -> None:
    """The run's overrides go to ``processing:remake_preview`` unchanged."""
    requests = fake_backend(
        monkeypatch, {"processing:remake_preview": {"status": "running", "job_id": "job-11"}}
    )

    reply = await app_jobs.tool_processing_remake_preview("M 13", denoise=False, starnet=True)

    assert requests[0]["method"] == "processing:remake_preview"
    assert requests[0]["params"] == {"target_id": "M 13", "denoise": False, "starnet": True}
    assert reply["job_id"] == "job-11"


@pytest.mark.anyio
async def test_frame_sync_starts_the_sync_buttons_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real run calls ``telescope:sync`` and says how to follow it."""
    requests = fake_backend(monkeypatch, {"telescope:sync": {"started": True, "target_id": "M 13"}})

    reply = await app_jobs.tool_observatory_remote_sync_frames("M 13")

    assert requests[0]["method"] == "telescope:sync"
    assert requests[0]["params"] == {"object_id": "M 13", "dry_run": False}
    assert "remote_sync" in reply["follow"]


@pytest.mark.anyio
async def test_frame_sync_needs_a_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """Copying every folder is not offered, so an empty target is refused."""
    requests = fake_backend(monkeypatch, {})

    with pytest.raises(InvalidArgumentError):
        await app_jobs.tool_observatory_remote_sync_frames("  ")

    assert requests == []


@pytest.mark.anyio
async def test_log_sync_dry_run_reaches_the_apps_log_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    """``dry_run`` goes to ``telescope:sync_logs``."""
    requests = fake_backend(monkeypatch, {"telescope:sync_logs": {"status": "plan", "plan": {}}})

    reply = await app_jobs.tool_observatory_remote_sync_logs(dry_run=True)

    assert requests[0]["method"] == "telescope:sync_logs"
    assert requests[0]["params"] == {"dry_run": True}
    assert reply["status"] == "plan"


@pytest.mark.anyio
async def test_a_job_tool_fails_clearly_when_the_backend_is_not_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing runs in the MCP process: the error says the backend is away."""

    async def no_backend(endpoint: str, payload: dict | None = None, timeout: float = 120.0) -> dict:
        """Fail as `post_to_backend` does when nothing listens.

        Raises
        ------
        ExternalServiceError
            Always.
        """
        await asyncio.sleep(0)
        raise ExternalServiceError("Could not connect to the backend at http://127.0.0.1:5000.")

    monkeypatch.setattr(definition, "post_to_backend", no_backend)

    with pytest.raises(ExternalServiceError, match="Could not connect to the backend"):
        await app_jobs.tool_processing_stack("M 13")
