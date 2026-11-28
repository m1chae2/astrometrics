"""Purpose: Tests for the RPC timing summary of the TPM harness.

Description: ``rpc_timings.py summarize`` turns the page's round trips and
the backend's ``RPC_TIMING`` log lines into ``backend-metrics.json``. These
tests write small stand-in files in a temporary folder and check that the
backend's run time and lane wait come out per method, that other log lines
are skipped, and that a log with no timing lines still gives a summary.

Run with ``.venv/bin/pytest build/tpm/test/test_rpc_timings.py -q``.
"""

import argparse
import json
import sys
from pathlib import Path

import pytest

TPM_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TPM_DIR))

import rpc_timings  # ruff: ignore[module-import-not-at-top-of-file]


def _timing_line(method: str, lane: str, wait_ms: float, run_ms: float, ok: bool = True) -> str:
    """Write one backend timing line the way the backend does.

    Returns
    -------
    line : `str`
        ``RPC_TIMING`` and the JSON object.
    """
    entry = {"method": method, "lane": lane, "wait_ms": wait_ms, "run_ms": run_ms, "ok": ok}
    return f"{rpc_timings.RPC_TIMING_MARKER} {json.dumps(entry)}"


def _summarize(tmp_path: Path, log_lines: list[str]) -> dict:
    """Run the summary on stand-in files and read what it wrote.

    Returns
    -------
    metrics : `dict`
        The contents of ``backend-metrics.json``.
    """
    flow = tmp_path / "flow-metrics.json"
    flow.write_text(json.dumps({"rpc": [{"method": "advisory:star_shapes", "ms": 40.0}]}))
    samples = tmp_path / "samples.jsonl"
    samples.write_text(
        json.dumps({"time": 1.0, "rss_kb": 1024.0, "peak_rss_kb": 2048.0, "cpu_seconds": 1.0})
        + "\n"
        + json.dumps({"time": 2.0, "rss_kb": 1024.0, "peak_rss_kb": 4096.0, "cpu_seconds": 3.0})
        + "\n"
    )
    log = tmp_path / "backend.log"
    log.write_text("\n".join(log_lines) + "\n")
    out = tmp_path / "backend-metrics.json"
    arguments = argparse.Namespace(flow=str(flow), samples=str(samples), log=str(log), out=str(out))
    assert rpc_timings.summarize_command(arguments) == 0
    return json.loads(out.read_text())


def test_backend_timings_are_summarized_per_method(tmp_path: Path) -> None:
    """Each method gets its backend run time and lane wait, p50 and p95."""
    metrics = _summarize(
        tmp_path,
        [
            "2026-11-27 INFO backend.startup Sky catalog warmed in 2.1s",
            _timing_line("advisory:star_shapes", "heavy", 100.0, 300.0),
            _timing_line("advisory:star_shapes", "heavy", 0.0, 100.0, ok=False),
            _timing_line("system:pulse", "light", 0.5, 1.0),
            "RPC_TIMING not json",
        ],
    )

    star_shapes = metrics["backend_methods"]["advisory:star_shapes"]
    assert star_shapes["count"] == 2
    assert star_shapes["lane"] == "heavy"
    assert star_shapes["failed"] == 1
    assert star_shapes["run_p50_ms"] == pytest.approx(200.0)
    assert star_shapes["run_p95_ms"] == pytest.approx(290.0)
    assert star_shapes["wait_p50_ms"] == pytest.approx(50.0)
    assert metrics["backend_methods"]["system:pulse"]["count"] == 1
    # The page's round trips are still reported beside them.
    assert metrics["methods"]["advisory:star_shapes"]["count"] == 1
    # A timing line is not counted as a failed call.
    assert metrics["backend_rpc_failures_logged"] == 0


def test_a_log_without_timing_lines_gives_an_empty_backend_summary(tmp_path: Path) -> None:
    """A backend that wrote no timing lines still gets a summary."""
    metrics = _summarize(
        tmp_path, ["2026-11-27 WARNING backend.routers.rpc_router RPC x failed (conflict): no"]
    )

    assert metrics["backend_methods"] == {}
    assert metrics["backend_rpc_failures_logged"] == 1


def test_the_marker_matches_the_backend() -> None:
    """The harness looks for the same marker the backend writes."""
    source = (TPM_DIR.parents[1] / "backend" / "services" / "infrastructure" / "rpc_timing.py").read_text()
    assert f'RPC_TIMING_MARKER = "{rpc_timings.RPC_TIMING_MARKER}"' in source
    assert "ASTROMETRICS_LOG_LEVEL=VERBOSE" in (TPM_DIR / "run_tpm.sh").read_text()


def test_a_timing_line_with_a_log_context_suffix_is_read() -> None:
    """A timing line keeps its JSON even when the log adds context after it."""
    line = (
        "2026-11-27 19:00:00,000 - backend.services.infrastructure.rpc_timing - INFO - "
        'RPC_TIMING {"method":"target:get","lane":"heavy","wait_ms":1.5,"run_ms":2.5,"ok":true}'
        " [request_id=abc method=target:get]"
    )

    assert rpc_timings.read_backend_timings(line) == [
        {"method": "target:get", "lane": "heavy", "wait_ms": 1.5, "run_ms": 2.5, "ok": True}
    ]
