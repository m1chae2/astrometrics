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


BOTH_STACKS = {
    "files": [
        {"path": "/raw/L_1.fits", "filter": "L"},
        {"path": "/raw/S_1.fits", "filter": "SPEC"},
        {"path": "/raw/S_2.fits", "filter": "SPEC"},
    ],
    "stackedImage": "/stacks/M_13_L.fits",
    "stackedSpectralTarget": "/stacks/M_13_SPEC.fits",
}
"""A target's file list: an imaging stack, a spectral stack, raw spectrum
frames."""

STARTED = {"status": "started", "jobId": "job-12", "logFile": "/logs/analysis.log"}
"""The backend's answer when the analysis job starts."""


@pytest.mark.anyio
async def test_analyze_with_both_stacks_runs_photometry_and_spectroscopy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No stages and two stacks send both stacks, as the button does."""
    requests = fake_backend(monkeypatch, {"target:get_files": BOTH_STACKS, "analysis:analyze_image": STARTED})

    reply = await app_jobs.tool_processing_analyze("M 13")

    assert [request["method"] for request in requests] == ["target:get_files", "analysis:analyze_image"]
    assert requests[1]["params"] == {
        "target_id": "M 13",
        "image_files": ["/stacks/M_13_L.fits", "/stacks/M_13_SPEC.fits"],
        "stages": ["photometry", "spectroscopy"],
    }
    assert reply["jobId"] == "job-12"
    assert reply["stages"] == ["photometry", "spectroscopy"]
    assert reply["paths"] == ["/stacks/M_13_L.fits", "/stacks/M_13_SPEC.fits"]
    assert "jobs_query" in reply["follow"]


@pytest.mark.anyio
async def test_analyze_with_one_stack_runs_that_stacks_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a spectral stack gets spectroscopy, with the spectral filter."""
    only_spectral = {"files": [], "stackedImage": None, "stackedSpectralTarget": "/stacks/M_13_SPEC.fits"}
    requests = fake_backend(
        monkeypatch, {"target:get_files": only_spectral, "analysis:analyze_image": STARTED}
    )

    await app_jobs.tool_processing_analyze("M 13")

    assert requests[1]["params"] == {
        "target_id": "M 13",
        "image_files": ["/stacks/M_13_SPEC.fits"],
        "filter_name": "SPEC",
        "stages": ["spectroscopy"],
    }


@pytest.mark.anyio
async def test_analyze_with_no_stack_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """No stages and no stack is a clear error, and no job starts."""
    requests = fake_backend(
        monkeypatch, {"target:get_files": {"files": [], "stackedImage": None, "stackedSpectralTarget": None}}
    )

    with pytest.raises(NotFoundError, match="no stack to analyze"):
        await app_jobs.tool_processing_analyze("M 13")

    assert [request["method"] for request in requests] == ["target:get_files"]


@pytest.mark.anyio
async def test_analyze_with_stages_sends_each_stages_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    """Named stages send their own stacks; variation sends raw frames."""
    requests = fake_backend(monkeypatch, {"target:get_files": BOTH_STACKS, "analysis:analyze_image": STARTED})

    await app_jobs.tool_processing_analyze("M 13", stages=["astrometry"])
    await app_jobs.tool_processing_analyze("M 13", stages=["spectral_variation"])

    assert requests[1]["params"] == {
        "target_id": "M 13",
        "image_files": ["/stacks/M_13_L.fits"],
        "stages": ["astrometry"],
    }
    assert requests[3]["params"] == {
        "target_id": "M 13",
        "image_files": ["/raw/S_1.fits", "/raw/S_2.fits"],
        "filter_name": "SPEC",
        "stages": ["spectral_variation"],
    }


@pytest.mark.anyio
async def test_analyze_refuses_an_unknown_stage_before_any_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stage the app cannot run is refused and the backend is not called."""
    requests = fake_backend(monkeypatch, {})

    with pytest.raises(InvalidArgumentError, match="bogus"):
        await app_jobs.tool_processing_analyze("M 13", stages=["photometry", "bogus"])
    with pytest.raises(InvalidArgumentError, match="none given"):
        await app_jobs.tool_processing_analyze("M 13", stages=[])

    assert requests == []


@pytest.mark.anyio
async def test_analyze_refuses_a_stage_with_nothing_to_work_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spectroscopy on a target with no spectral stack is refused."""
    only_imaging = {"files": [], "stackedImage": "/stacks/M_13_L.fits", "stackedSpectralTarget": None}
    fake_backend(monkeypatch, {"target:get_files": only_imaging})

    with pytest.raises(NotFoundError, match="spectroscopy"):
        await app_jobs.tool_processing_analyze("M 13", stages=["spectroscopy"])


@pytest.mark.anyio
async def test_analyze_passes_an_already_running_answer_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """The already_running answer reaches the caller with the job's id."""
    running = {"status": "already_running", "jobId": "job-3", "logFile": "/logs/analysis.log"}
    fake_backend(monkeypatch, {"target:get_files": BOTH_STACKS, "analysis:analyze_image": running})

    reply = await app_jobs.tool_processing_analyze("M 13")

    assert reply["status"] == "already_running"
    assert reply["jobId"] == "job-3"


M_27_RECORD = {
    "id": "M 27",
    "stacking": {
        "stackedImage": "/stacks/M_27_ASI_405.fits",
        "stacksByConfiguration": {
            "asi@405": {
                "camera": "ZWO ASI 533MM Pro",
                "focalLengthMm": 405.0,
                "stackedImage": "/stacks/M_27_ASI_405.fits",
                "isPreferred": True,
            },
            "nikon@405": {
                "camera": "Nikon DSLR DSC D5300",
                "focalLengthMm": 405.0,
                "stackedImage": "/stacks/M_27_Nikon_405.fits",
            },
            "nikon@300": {
                "camera": "Nikon DSLR DSC D5300",
                "focalLengthMm": 300.0,
                "stackedImage": "/stacks/M_27_Nikon_300.fits",
            },
        },
    },
}
"""A target with one stack per camera and optic; the ASI533 one is main."""

M_27_FILES = {
    "files": [{"path": "/raw/S_1.fits", "filter": "SPEC"}],
    "stackedImage": "/stacks/M_27_ASI_405.fits",
    "stackedSpectralTarget": "/stacks/M_27_SPEC.fits",
}
"""M 27's file list: the main imaging stack and a spectral stack."""


@pytest.mark.anyio
async def test_analyze_a_configuration_sends_that_configurations_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A camera and optic send their own stack, camera and focal length."""
    requests = fake_backend(
        monkeypatch,
        {"target:get_files": M_27_FILES, "target:get": M_27_RECORD, "analysis:analyze_image": STARTED},
    )

    reply = await app_jobs.tool_processing_analyze("M 27", camera_id="D5300", focal_length_mm=300)

    assert requests[-1]["params"] == {
        "target_id": "M 27",
        "image_files": ["/stacks/M_27_Nikon_300.fits"],
        "stages": ["photometry"],
        "camera_id": "Nikon DSLR DSC D5300",
        "focal_length_mm": 300.0,
    }
    assert reply["configuration"] == "Nikon DSLR DSC D5300@300mm"


@pytest.mark.anyio
async def test_analyze_the_main_configuration_keeps_the_buttons_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Naming the main stack's camera and optic keeps both stacks."""
    requests = fake_backend(
        monkeypatch,
        {"target:get_files": M_27_FILES, "target:get": M_27_RECORD, "analysis:analyze_image": STARTED},
    )

    await app_jobs.tool_processing_analyze("M 27", camera_id="ASI 533", focal_length_mm=405)

    assert requests[-1]["params"]["image_files"] == ["/stacks/M_27_ASI_405.fits", "/stacks/M_27_SPEC.fits"]
    assert requests[-1]["params"]["stages"] == ["photometry", "spectroscopy"]


@pytest.mark.anyio
async def test_analyze_refuses_an_unknown_or_unclear_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown or ambiguous camera and optic list the configurations."""
    requests = fake_backend(monkeypatch, {"target:get_files": M_27_FILES, "target:get": M_27_RECORD})

    with pytest.raises(InvalidArgumentError, match=r"has no stack for .*Nikon DSLR DSC D5300@300mm"):
        await app_jobs.tool_processing_analyze("M 27", camera_id="Canon")
    with pytest.raises(InvalidArgumentError, match="more than one stack"):
        await app_jobs.tool_processing_analyze("M 27", camera_id="Nikon")
    with pytest.raises(InvalidArgumentError, match="only for the main configuration"):
        await app_jobs.tool_processing_analyze(
            "M 27", stages=["spectroscopy"], camera_id="Nikon", focal_length_mm=300
        )

    assert "analysis:analyze_image" not in [request["method"] for request in requests]


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
async def test_remake_preview_of_a_configuration_sends_that_configurations_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A camera and optic send their own stack to the remake."""
    requests = fake_backend(
        monkeypatch,
        {"target:get": M_27_RECORD, "processing:remake_preview": {"status": "running", "job_id": "job-12"}},
    )

    reply = await app_jobs.tool_processing_remake_preview("M 27", camera_id="D5300", focal_length_mm=300)

    assert requests[-1]["method"] == "processing:remake_preview"
    assert requests[-1]["params"] == {"target_id": "M 27", "stack_path": "/stacks/M_27_Nikon_300.fits"}
    assert reply["configuration"] == "Nikon DSLR DSC D5300@300mm"
    assert reply["job_id"] == "job-12"


@pytest.mark.anyio
async def test_remake_preview_refuses_an_unknown_unclear_or_spectral_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown or unclear cameras list the stacks; spectral is refused."""
    requests = fake_backend(monkeypatch, {"target:get": M_27_RECORD})

    with pytest.raises(InvalidArgumentError, match=r"has no stack for .*Nikon DSLR DSC D5300@300mm"):
        await app_jobs.tool_processing_remake_preview("M 27", camera_id="Canon")
    with pytest.raises(InvalidArgumentError, match="more than one stack"):
        await app_jobs.tool_processing_remake_preview("M 27", camera_id="Nikon")
    with pytest.raises(InvalidArgumentError, match="only for the main configuration"):
        await app_jobs.tool_processing_remake_preview("M 27", kind="spectral", camera_id="Nikon")

    assert "processing:remake_preview" not in [request["method"] for request in requests]


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


def active_job(job_id: str, job_type: str, target: str = "M 13", status: str = "running") -> dict:
    """Build one job summary as ``jobs:query`` returns it.

    Parameters
    ----------
    job_id : `str`
        The job id.
    job_type : `str`
        The job type.
    target : `str`, optional
        The target id.
    status : `str`, optional
        The job status.

    Returns
    -------
    job : `dict`
        The summary.
    """
    return {"id": job_id, "job_type": job_type, "target_id": target, "status": status}


@pytest.mark.anyio
async def test_cancel_stops_the_running_stacking_job(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one running job is a stack: ``processing:cancel`` runs."""
    requests = fake_backend(
        monkeypatch,
        {
            "jobs:query": {"jobs": [active_job("job-1", "stacking")]},
            "processing:cancel": {"status": "cancelled", "message": None},
        },
    )

    reply = await app_jobs.tool_processing_cancel("M 13")

    assert [request["method"] for request in requests] == ["jobs:query", "processing:cancel"]
    assert requests[1]["params"] == {"target_id": "M 13"}
    assert reply["cancelled"] is True
    assert reply["job_type"] == "stacking"
    assert reply["job_ids"] == ["job-1"]


@pytest.mark.anyio
async def test_cancel_stops_a_waiting_analysis_job(monkeypatch: pytest.MonkeyPatch) -> None:
    """A waiting analysis job goes to ``analysis:cancel``."""
    requests = fake_backend(
        monkeypatch,
        {
            "jobs:query": {"jobs": [active_job("job-2", "analysis", status="started")]},
            "analysis:cancel": {"cancelled": True, "own_process": True, "reason": None},
        },
    )

    reply = await app_jobs.tool_processing_cancel("M 13", job_type="analysis")

    assert requests[1]["method"] == "analysis:cancel"
    assert reply["cancelled"] is True
    assert reply["job_type"] == "analysis"


@pytest.mark.anyio
async def test_cancel_by_job_id_reads_the_jobs_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """A job id is looked up, and its own type picks the cancel call."""
    requests = fake_backend(
        monkeypatch,
        {
            "jobs:query": {"job": active_job("job-3", "analysis")},
            "analysis:cancel": {"cancelled": True, "own_process": True, "reason": None},
        },
    )

    reply = await app_jobs.tool_processing_cancel("M 13", job_id="job-3")

    assert requests[0]["params"] == {"job_id": "job-3"}
    assert requests[1]["method"] == "analysis:cancel"
    assert reply["job_ids"] == ["job-3"]


@pytest.mark.anyio
async def test_cancel_by_job_id_refuses_another_targets_job(monkeypatch: pytest.MonkeyPatch) -> None:
    """A job of another target is not cancelled."""
    fake_backend(monkeypatch, {"jobs:query": {"job": active_job("job-4", "stacking", target="M 57")}})

    with pytest.raises(InvalidArgumentError, match="belongs to"):
        await app_jobs.tool_processing_cancel("M 13", job_id="job-4")


@pytest.mark.anyio
async def test_cancel_says_so_when_nothing_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """No active job: nothing is cancelled and no cancel call is sent."""
    requests = fake_backend(monkeypatch, {"jobs:query": {"jobs": []}})

    reply = await app_jobs.tool_processing_cancel("M 13")

    assert [request["method"] for request in requests] == ["jobs:query"]
    assert reply["cancelled"] is False
    assert "No job is running" in reply["reason"]


@pytest.mark.anyio
async def test_cancel_refuses_when_two_kinds_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two kinds of job run and none was named: the call is refused."""
    fake_backend(
        monkeypatch,
        {"jobs:query": {"jobs": [active_job("job-1", "stacking"), active_job("job-2", "analysis")]}},
    )

    with pytest.raises(InvalidArgumentError, match="More than one kind"):
        await app_jobs.tool_processing_cancel("M 13")


@pytest.mark.anyio
async def test_cancel_sends_a_preview_job_to_the_apps_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    """A picture remake is cancelled through ``processing:cancel``."""
    requests = fake_backend(
        monkeypatch,
        {
            "jobs:query": {"jobs": [active_job("job-5", "preview")]},
            "processing:cancel": {"status": "cancelled", "message": None},
        },
    )

    reply = await app_jobs.tool_processing_cancel("M 13")

    assert [request["method"] for request in requests] == ["jobs:query", "processing:cancel"]
    assert reply["cancelled"] is True
    assert reply["job_type"] == "preview"


NEBULA = {
    "simbad_id": "NGC 7635",
    "object_type": "HII",
    "category": "nebula",
    "ra_deg": 350.2,
    "dec_deg": 61.2,
    "major_axis_arcmin": 15.0,
}
"""One object in the frame, as ``processing:list_bright_objects`` lists it."""


@pytest.mark.anyio
async def test_enhance_without_a_choice_lists_the_saved_choice_and_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reading changes nothing and returns the choice with the objects."""
    requests = fake_backend(
        monkeypatch,
        {
            "processing:list_bright_objects": {"objects": [NEBULA], "message": None},
            "target:get": {"preview_bright_object": {"mode": "off"}},
        },
    )

    reply = await app_jobs.tool_processing_enhance("M 52")

    assert reply == {"current": "nothing", "objects": [NEBULA], "message": None}
    assert "processing:set_bright_object" not in [request["method"] for request in requests]


@pytest.mark.anyio
async def test_enhance_without_objects_gives_the_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stack with no plate solution lists no objects and says why."""
    fake_backend(
        monkeypatch,
        {
            "processing:list_bright_objects": {"objects": [], "message": "Run Astrometry first."},
            "target:get": {"previewBrightObject": {"mode": "auto"}},
        },
    )

    reply = await app_jobs.tool_processing_enhance("M 52")

    assert reply["current"] == "target"
    assert reply["objects"] == []
    assert reply["message"] == "Run Astrometry first."


@pytest.mark.anyio
@pytest.mark.parametrize(("choice", "mode"), [("target", "auto"), ("Nothing", "off")])
async def test_enhance_saves_the_two_fixed_choices(
    monkeypatch: pytest.MonkeyPatch, choice: str, mode: str
) -> None:
    """Target and nothing are the backend's auto and off modes."""
    requests = fake_backend(
        monkeypatch,
        {
            "processing:list_bright_objects": {"objects": [], "message": "Run Astrometry first."},
            "processing:set_bright_object": {"mode": mode},
        },
    )

    reply = await app_jobs.tool_processing_enhance("M 52", choice)

    assert requests[-1]["params"] == {"target": "M 52", "mode": mode}
    assert "next stack" in reply["applies"]


@pytest.mark.anyio
async def test_enhance_saves_an_object_from_the_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """An object name, in any case or spacing, sends its listed position."""
    requests = fake_backend(
        monkeypatch,
        {
            "processing:list_bright_objects": {"objects": [NEBULA], "message": None},
            "processing:set_bright_object": {"mode": "object", "simbad_id": "NGC 7635"},
        },
    )

    await app_jobs.tool_processing_enhance("M 52", "ngc7635")

    assert requests[-1]["params"] == {
        "target": "M 52",
        "mode": "object",
        "simbad_id": "NGC 7635",
        "ra_deg": 350.2,
        "dec_deg": 61.2,
        "major_axis_arcmin": 15.0,
    }


@pytest.mark.anyio
async def test_enhance_refuses_an_object_not_in_the_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown name is refused with the list, and nothing is saved."""
    requests = fake_backend(
        monkeypatch, {"processing:list_bright_objects": {"objects": [NEBULA], "message": None}}
    )

    with pytest.raises(InvalidArgumentError, match="NGC 7635"):
        await app_jobs.tool_processing_enhance("M 52", "M 31")

    assert [request["method"] for request in requests] == ["processing:list_bright_objects"]
