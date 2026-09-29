"""Purpose: Unit tests for dynamic MCP astrometrics reflection engine.

Description: Verifies that the high-level interface generates valid
JSON Schemas and executes reflected tools. The equivalent coverage for
wayfindinglib's Wayfinder high-level interface lives in
`wayfindinglib/mcp/test/test_wayfinding_reflection.py` -- split out so
this suite does not require wayfindinglib to be installed.
"""

import types

import pytest

from astrometricslib.drivers.job_logging import background_job
from astrometricslib.mcp.reflection import (
    _infer_background_job_target_id,
    _make_quality_snapshot_fn,
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
    assert "visualization_convert_fits_to_png" in tool_names
    assert "star_get_audit" in tool_names

    # Verify nested sub-APIs (dotted branch_mapping keys) are reflected too
    assert "diagnostics_measure_stack_fwhm" in tool_names
    assert "calibration_stats" in tool_names


async def test_astrometrics_reflected_tool_execution():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify executing a reflected tool via Astrometrics registry succeeds."""
    res = await astrometrics_registry.execute("target_list", {})
    assert len(res) > 0
    assert res[0].type == "text"


def _fake_target(target_id: str) -> types.SimpleNamespace:
    """Build a bare object with just the attributes a quality snapshot reads.

    Returns
    -------
    target : `types.SimpleNamespace`
        A stand-in for `astrometricslib.models.target.Target`.
    """
    return types.SimpleNamespace(
        id=target_id,
        stacking=types.SimpleNamespace(quality_summary=None),
        spectral_stacking=types.SimpleNamespace(quality_summary=None),
        quality=types.SimpleNamespace(astrometry=None, photometry=None, spectroscopy=None),
    )


def test_infer_background_job_target_id_prefers_a_resolved_target():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a single-target call's job is tracked under that target's id."""
    target_id = _infer_background_job_target_id({"target": _fake_target("Vega")})
    assert target_id == "Vega"


def test_infer_background_job_target_id_falls_back_to_a_batch_label():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an all-targets call gets a synthetic, camera-scoped label."""
    target_id = _infer_background_job_target_id({"camera_name": "ZWO ASI 533MM Pro"})
    assert target_id == "batch:ZWO ASI 533MM Pro"


def test_infer_background_job_target_id_defaults_when_neither_is_present():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a call identifying no target at all still gets a label."""
    assert _infer_background_job_target_id({}) == "unknown"


def test_quality_snapshot_fn_covers_a_single_resolved_target():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a single-target call snapshots exactly that target."""
    target = _fake_target("Vega")
    target.stacking.quality_summary = "stack-summary-v1"

    snapshot_fn = _make_quality_snapshot_fn(astrometrics_instance=None, kwargs={"target": target})

    assert snapshot_fn is not None
    assert snapshot_fn() == {
        "Vega": {
            "stack": "stack-summary-v1",
            "spectralStack": None,
            "astrometry": None,
            "photometry": None,
            "spectroscopy": None,
        }
    }


def test_quality_snapshot_fn_covers_every_target_in_a_batch_call():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a batch call snapshots every target it will touch.

    Uses `.list()`, not `.get()`, since each target in a real batch is
    processed in its own subprocess -- an in-memory cache in this process
    would not reflect that work by the time the "post" snapshot runs.
    """
    fake_targets = {"Vega": _fake_target("Vega"), "Albireo": _fake_target("Albireo")}
    fake_astrometrics = types.SimpleNamespace(
        targets=types.SimpleNamespace(list=lambda: list(fake_targets.values()))
    )

    snapshot_fn = _make_quality_snapshot_fn(fake_astrometrics, {"camera_name": "ZWO ASI 533MM Pro"})

    assert snapshot_fn is not None
    assert set(snapshot_fn().keys()) == {"Vega", "Albireo"}


def test_quality_snapshot_fn_is_none_when_no_target_can_be_identified():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a call with neither a target nor a camera gets no snapshot."""
    assert _make_quality_snapshot_fn(astrometrics_instance=None, kwargs={}) is None


async def test_a_background_job_marked_method_returns_its_result_without_blocking():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a `@background_job`-marked tool is dispatched as a job.

    A fast call should still get its real return value back, alongside a
    job id, rather than being called directly and blocking the dispatcher.
    """

    class FakeApi:
        """A stand-in `Astrometrics`-like object with one marked method."""

        @background_job("unit_test_job", grace_period_seconds=2.0)
        def do_work(self) -> dict:
            """Stand in for real pipeline work.

            Returns
            -------
            result : `dict`
                A trivial, fixed result.
            """
            return {"stackedImage": "Vega_Stacked.fits"}

    isolated_registry = ToolRegistry()
    register_astrometrics_tools(isolated_registry, FakeApi(), {"": "fake"})

    result = await isolated_registry.execute("fake_do_work", {})

    assert len(result) == 1
    assert '"stackedImage": "Vega_Stacked.fits"' in result[0].text
    assert '"jobId"' in result[0].text
