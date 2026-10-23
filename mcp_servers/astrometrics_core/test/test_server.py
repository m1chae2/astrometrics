"""Purpose: Tests for the astrometricslib-core MCP server's tool list.

Description: Checks that the server reflects the expected `Astrometrics`
methods with their documented arguments, never offers a delete method, that
its committed manifest covers every tool, that the investigator profile gets
only reading and safe processing tools, and that a picture comes back to the
client as an image.
"""

import asyncio
import base64
import json
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from PIL import Image

from astrometricslib import Visualization
from mcp_servers.astrometrics_core.definition import MANIFEST_PATH, build_registry
from mcp_servers.common.profile import load_manifest
from mcp_servers.common.tool_registry import MAXIMUM_REPLY_BYTES, ToolRegistry

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    """Run the asynchronous tests in this module on asyncio only.

    Returns
    -------
    backend : `str`
        ``"asyncio"``.
    """
    return "asyncio"


@pytest.fixture(scope="module")
def registry() -> ToolRegistry:
    """Build the server's registry once for the module.

    Returns
    -------
    registry : `ToolRegistry`
        Every tool, before a profile removes any.
    """
    return build_registry()


def test_the_main_sub_apis_are_reflected(registry: ToolRegistry) -> None:
    """Tools of the top-level and nested sub-APIs are registered."""
    names = set(registry.tools)
    for name in (
        "target_list",
        "target_create",
        "visualization_render_fits",
        "star_query",
        "quality_measure_stack",
        "quality_query",
        "quality_metrics",
        "calibration_query",
        "typegen_contract_validator",
    ):
        assert name in names, name


def test_delete_methods_are_never_offered_as_tools(registry: ToolRegistry) -> None:
    """Deleting is left to the app's own UI, so no delete tool is reflected."""
    names = set(registry.tools)
    assert not [name for name in names if "_delete" in name or name.startswith("delete")]
    assert "target_save" in names


async def test_a_reflected_tool_runs(registry: ToolRegistry) -> None:
    """Running a reflected tool returns text content."""
    reply = await registry.execute("target_list", {})
    assert reply
    assert reply[0].type == "text"


def test_frame_quality_has_the_documented_arguments(registry: ToolRegistry) -> None:
    """`quality_measure_frames` takes the documented, optional arguments."""
    schema = registry.tools["quality_measure_frames"]["tool_def"].input_schema
    assert set(schema["properties"]) == {
        "target",
        "folder_path",
        "filter_name",
        "first_file",
        "last_file",
        "since",
        "until",
        "trend_frames",
        "trend_threshold_percent",
        "kind",
        "include",
        "remeasure",
        "camera_id",
        "limit",
    }
    assert not schema.get("required")


def test_jobs_query_has_the_documented_arguments(registry: ToolRegistry) -> None:
    """`jobs_query` takes the documented arguments, none required."""
    schema = registry.tools["jobs_query"]["tool_def"].input_schema
    assert set(schema["properties"]) == {
        "job_id",
        "target_id",
        "job_type",
        "status",
        "active_only",
        "detail",
        "lines",
        "limit",
    }
    assert not schema.get("required")


def test_manifest_covers_every_registered_tool(registry: ToolRegistry) -> None:
    """Every registered tool has a manifest entry, and none is stale."""
    manifest = load_manifest(MANIFEST_PATH)
    assert manifest is not None
    registered = set(registry.tools)
    assert registered - set(manifest["tools"]) == set(), "Tools missing from the manifest"
    assert set(manifest["tools"]) - registered == set(), "Manifest entries with no tool"


def test_investigator_profile_offers_only_reading_tools(registry: ToolRegistry) -> None:
    """The manifest keeps writers, device commands and code out."""
    copy = ToolRegistry()
    copy.tools = dict(registry.tools)
    copy.apply_profile(MANIFEST_PATH, "investigator")
    manifest = load_manifest(MANIFEST_PATH)
    assert copy.tools
    for name in copy.tools:
        entry = manifest["tools"][name]
        assert entry["tool_class"] in ("observe", "compute", "ingest", "process"), name
        assert entry["disposition"] == "keep", name


def test_a_rendered_frame_reaches_the_client_as_an_image(tmp_path: Path) -> None:
    """`visualization_render_fits` replies with a PNG and its description."""
    generator = np.random.default_rng(1)
    data = generator.normal(1000.0, 20.0, (200, 300)).astype(np.float32)
    data[100, 150] = 50000.0
    frame = tmp_path / "frame.fits"
    fits.PrimaryHDU(data).writeto(frame)

    copy = ToolRegistry()
    copy.register("view", "View a frame.")(lambda: Visualization(None, None).render_fits(str(frame)))
    content = asyncio.run(copy.execute("view", {}))

    assert content[0].type == "image"
    assert content[0].mime_type == "image/png"
    assert Image.open(BytesIO(base64.b64decode(content[0].data))).size[0] > 0
    assert "brightness_range_shown" in json.loads(content[1].text)


def test_the_quality_root_replaces_the_diagnostics_tools(registry: ToolRegistry) -> None:
    """Quality tools are named ``quality_*``; none is ``diagnostics_*``."""
    names = set(registry.tools)
    assert not [name for name in names if name.startswith("diagnostics_")]
    assert {"quality_measure_frames", "quality_check_spectral_frames", "quality_measure_stack"} <= names


def test_the_stored_quality_query_has_the_documented_arguments(registry: ToolRegistry) -> None:
    """`quality_query` offers the stored-read filters, none required."""
    schema = registry.tools["quality_query"]["tool_def"].input_schema
    assert set(schema["properties"]) == {
        "metric",
        "level",
        "target_id",
        "observing_night_id",
        "camera",
        "telescope",
        "since",
        "row_ids",
        "latest_only",
        "variant",
        "library_only",
    }
    assert not schema.get("required")


async def test_quality_metrics_reply_is_json_with_levels_as_a_list(registry: ToolRegistry) -> None:
    """The summary reply is JSON under the size limit."""
    reply = await registry.execute("quality_metrics", {})
    assert len(reply[0].text.encode()) < MAXIMUM_REPLY_BYTES
    specs = json.loads(reply[0].text)
    assert specs
    for spec in specs:
        assert isinstance(spec["levels"], list)
        assert all(isinstance(level, str) for level in spec["levels"])
        assert "estimator" not in spec
        assert "meaning" not in spec
    one = json.loads((await registry.execute("quality_metrics", {"name": specs[0]["name"]}))[0].text)
    assert [spec["name"] for spec in one] == [specs[0]["name"]]


async def test_quality_metrics_full_detail_with_a_checkpoint_is_json(registry: ToolRegistry) -> None:
    """Full detail for one checkpoint is JSON with whole declarations."""
    reply = await registry.execute("quality_metrics", {"detail": "full", "checkpoint": "frame"})
    specs = json.loads(reply[0].text)
    assert specs
    for spec in specs:
        assert spec["checkpoint"] == "frame"
        assert isinstance(spec["decisions"], list)
        assert "estimator" in spec


def test_quality_metrics_offers_optional_arguments(registry: ToolRegistry) -> None:
    """`quality_metrics` takes `name`, `checkpoint` and `detail`."""
    schema = registry.tools["quality_metrics"]["tool_def"].input_schema
    assert set(schema["properties"]) == {"name", "checkpoint", "detail"}
    assert not schema.get("required")
