"""Purpose: Unit tests for dynamic MCP astrometrics reflection engine.

Description: Verifies that the high-level interface generates valid
JSON Schemas and executes reflected tools. The equivalent coverage for
wayfindinglib's Wayfinder high-level interface lives in
`wayfindinglib/mcp/test/test_wayfinding_reflection.py` -- split out so
this suite does not require wayfindinglib to be installed.
"""

import asyncio
from pathlib import Path

import pytest

from astrometricslib.drivers.job_logging import background_job
from astrometricslib.mcp.reflection import (
    generate_tool_schema,
    parse_docstring_params,
    register_astrometrics_tools,
)
from astrometricslib.mcp.tool_registry import ToolRegistry
from astrometricslib.mcp.tool_registry import registry as astrometrics_registry

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Restrict anyio-marked tests in this module to the asyncio backend.

    Returns
    -------
    backend : `str`
        The anyio backend name to run these tests under.
    """
    return "asyncio"


def test_parse_docstring_params_extracts_descriptions():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify parsing of parameters from NumPy/Google style docstring."""
    sample_doc = """
    Perform standard FITS scaling and return raw PNG bytes.

    Parameters
    ----------
    path : `str`
        The absolute path of the target file.
    max_dimensions : `int`
        The maximum resolution bound.
    """
    parsed = parse_docstring_params(sample_doc)
    assert "path" in parsed
    assert "The absolute path of the target file." in parsed["path"]
    assert "max_dimensions" in parsed


def test_generate_tool_schema_builds_json_schema():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify JSON schema generation from a callable signature."""

    def dummy_func(target_id: str, count: int = 10, enable_flag: bool = True) -> str:
        """Process dummy target parameters and return target_id.

        Parameters
        ----------
        target_id : `str`
            Target name or identifier.
        count : `int`
            Number of iterations.
        enable_flag : `bool`
            Toggle feature flag.

        Returns
        -------
        result : `str`
            The target identifier string.
        """
        return target_id

    schema = generate_tool_schema(dummy_func)
    assert schema["type"] == "object"
    assert "target_id" in schema["properties"]
    assert schema["properties"]["target_id"]["type"] == "string"
    assert schema["properties"]["count"]["type"] == "integer"
    assert schema["properties"]["enable_flag"]["type"] == "boolean"
    assert schema["required"] == ["target_id"]


def test_astrometricslib_mcp_reflection_registers_tools():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify astrometricslib MCP server reflects astrometrics tools."""
    tool_defs = astrometrics_registry.get_tool_definitions()
    tool_names = {t.name for t in tool_defs}

    # Verify key reflected tools are present
    assert "target_list" in tool_names
    assert "target_create" in tool_names
    assert "visualization_render_fits" in tool_names
    assert "star_query" in tool_names

    # Verify nested sub-APIs (dotted branch_mapping keys) are reflected too
    assert "diagnostics_stack_quality" in tool_names
    assert "calibration_query" in tool_names


def test_delete_methods_are_never_offered_as_tools():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Deleting is left to the app's own UI, so no delete tool is reflected."""
    tool_names = {t.name for t in astrometrics_registry.get_tool_definitions()}

    assert not [name for name in tool_names if "_delete" in name or name.startswith("delete")]
    assert "target_save" in tool_names


async def test_astrometrics_reflected_tool_execution():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify executing a reflected tool via Astrometrics registry succeeds."""
    res = await astrometrics_registry.execute("target_list", {})
    assert len(res) > 0
    assert res[0].type == "text"


async def test_a_background_job_method_records_its_own_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `@background_job` method is called with register_job=True.

    The method records its own job, so the tool's reply carries that job's
    id next to the real result of a fast call.
    """
    from astrometricslib.drivers.job_logging import registered_job
    from astrometricslib.foundation import config as config_loader
    from astrometricslib.foundation.config import AppConfiguration

    library_path = tmp_path / "library"
    library_path.mkdir()
    configuration = AppConfiguration()
    configuration.update_config({"Image Library": {"path": str(library_path)}})
    monkeypatch.setattr(configuration, "get_logs_path", lambda: tmp_path)
    monkeypatch.setattr(config_loader, "get_configuration", lambda: configuration)
    received = {}

    class FakeApi:
        """A stand-in `Astrometrics`-like object with one marked method."""

        @background_job("unit_test_job", grace_period_seconds=2.0)
        def do_work(self, target: str, register_job: bool = False) -> dict:
            """Stand in for real pipeline work that records its own job.

            Returns
            -------
            result : `dict`
                A trivial, fixed result.
            """
            received.update(target=target, register_job=register_job)
            with registered_job(enabled=register_job, job_type="unit_test_job", target_id=target):
                return {"stackedImage": "Vega_Stacked.fits"}

    isolated_registry = ToolRegistry()
    register_astrometrics_tools(isolated_registry, FakeApi(), {"": "fake"})
    schema = next(t for t in isolated_registry.get_tool_definitions() if t.name == "fake_do_work").inputSchema

    result = await isolated_registry.execute("fake_do_work", {"target": "Vega"})

    assert "register_job" not in schema["properties"]
    assert received == {"target": "Vega", "register_job": True}
    assert '"stackedImage": "Vega_Stacked.fits"' in result[0].text
    assert '"jobId": null' not in result[0].text


async def test_a_sync_method_that_starts_its_own_event_loop_still_works():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A sync method bridging into `asyncio.run()` must not crash here.

    Regression test for a real incident: wayfindinglib's
    `ObservatoryControl` reuses this same reflection engine, and its
    hardware-facing methods stay synchronous by bridging into async
    INDI drivers via `hardware_operations._run_sync`'s own
    `asyncio.run(...)`. Calling such a method directly (as a plain
    Python call, not via a thread) from inside `execute_reflected`
    crashed with "asyncio.run() cannot be called from a running event
    loop", because this test itself (like the real MCP server) already
    runs inside one -- exactly the same conflict
    `backend/main_backend.py`'s periodic telemetry loop already hit and
    fixed calling the same hardware layer, by running it via
    `asyncio.to_thread` instead of a direct call.
    """

    class FakeObservatoryControl:
        """A stand-in with one method that starts its own event loop."""

        def get_telescope_status(self) -> dict:
            """Stand in for a hardware call bridged via `_run_sync`.

            Returns
            -------
            result : `dict`
                A trivial, fixed result, reached only if a nested
                `asyncio.run()` succeeds from a plain worker thread.
            """

            async def _coroutine() -> dict:
                await asyncio.sleep(0)  # stand in for a real async INDI call
                return {"trackingStatus": "Parked"}

            return asyncio.run(_coroutine())

    isolated_registry = ToolRegistry()
    register_astrometrics_tools(isolated_registry, FakeObservatoryControl(), {"": "fake"})

    result = await isolated_registry.execute("fake_get_telescope_status", {})

    assert len(result) == 1
    assert '"trackingStatus": "Parked"' in result[0].text


async def test_a_target_id_reaches_the_method_as_the_client_sent_it() -> None:
    """The tool does not look targets up; the library method does."""

    class FakeApi:
        """A stand-in with one method that takes a target."""

        def inspect_target(self, target: str) -> dict:
            """Report what the method received.

            Returns
            -------
            result : `dict`
                The received value and its type name.
            """
            return {"target": target, "type": type(target).__name__}

    isolated_registry = ToolRegistry()
    register_astrometrics_tools(isolated_registry, FakeApi(), {"": "fake"})

    result = await isolated_registry.execute("fake_inspect_target", {"target": "M 13"})

    assert '"target": "M 13"' in result[0].text
    assert '"type": "str"' in result[0].text
