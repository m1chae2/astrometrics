"""Purpose: Tests that the TPM catalog and the code that uses it agree.

Description: ``tpm_catalog.json`` defines every TPM. These tests fail when
the catalog and the code drift apart. They catch five kinds of drift:

- a TPM that ``compile_tpm.py`` produces with no catalog entry;
- a catalog entry that no code path produces (unless it says
  ``not_measured_yet``);
- a display in the catalog that the app does not have, or an app display
  that the catalog neither lists nor excludes;
- a ``budgets.json`` value that disagrees with the catalog;
- a limit that could fail a run without being Confirmed.

Run with ``.venv/bin/pytest build/tpm/test -q``.
"""

import json
import re
import sys
from pathlib import Path

import pytest

TPM_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = TPM_DIR.parents[1]
sys.path.insert(0, str(TPM_DIR))

import compile_tpm  # ruff: ignore[module-import-not-at-top-of-file]

STATUSES = {"TBD", "TBR", "Confirmed"}
REQUIRED_FIELDS = {
    "id",
    "name",
    "title",
    "why",
    "unit",
    "better",
    "scope",
    "method",
    "statistic",
    "threshold",
    "objective",
    "threshold_status",
    "source",
    "reference_setup",
}


@pytest.fixture(scope="module")
def catalog() -> dict:
    """Load the catalog once.

    Returns
    -------
    catalog : `dict`
        The parsed ``tpm_catalog.json``.
    """
    return compile_tpm.load_catalog()


def _fake_flow() -> dict:
    """Make a small ``flow-metrics.json`` that exercises every TPM code path.

    Returns
    -------
    flow : `dict`
        Raw flow measurements with every display, interaction and RPC method.
    """
    displays = compile_tpm.DISPLAYS
    step = {
        "totalBlockingTimeMs": 10.0,
        "interactionToNextPaintMs": 50.0,
        "cumulativeLayoutShift": 0.01,
        "scriptBytes": 2048,
        "requestCount": 10,
    }
    zoom = {
        "frameMsP50": 16.0,
        "frameMsP95": 20.0,
        "mainThread": {"busyMsPerFrame": 5.0},
        "gpuProcess": {"msP50": 4.0},
        "active": {"fps": 58.0, "frameMsP50": 16.0, "frameMsP95": 20.0, "slowFrameCount": 3},
        "ticks": {"latencyMsP50": 10.0, "latencyMsP95": 20.0, "settleMsP50": 90.0, "settleMsP95": 120.0},
        "longTasks": {"count": 1, "maxMs": 60.0},
    }
    return {
        "laps": 2,
        "timeToReadyMs": 1000.0,
        "switches": [{"display": name, "settledMs": 100.0, "timedOut": False} for name in displays],
        "interactions": [
            {"name": name, "settledMs": 50.0, "timedOut": False}
            for name in ("select_target", "open_night", "change_tab")
        ],
        "lighthouseSteps": [step] + [step] * len(displays),
        "stepNames": [{"display": name} for name in displays],
        "planetarium": [{"fps": 60.0}],
        "gpu": {"hardware": True},
        "planetariumProfile": {
            "frameMsP50": 16.0,
            "frameMsP95": 17.0,
            "starProjectUpload": {"msP50": 2.0},
            "overlayDraw": {"msP50": 2.0},
            "gpuProcess": {"msP50": 3.0},
            "zoom": zoom,
        },
        "heapAfterLap": [{"bytes": 1048576}, {"bytes": 2097152}],
        "rpc": [
            {"phase": phase, "method": method, "ms": 10.0}
            for phase in ("load", *displays)
            for method in compile_tpm.TRACKED_RPC_METHODS
        ],
        "idle": {"rpcCalls": 3, "seconds": 30.0},
    }


@pytest.fixture(scope="module")
def produced_names(tmp_path_factory: pytest.TempPathFactory) -> tuple:
    """Run every TPM code path on made-up data and collect the names.

    Parameters
    ----------
    tmp_path_factory : `pytest.TempPathFactory`
        Pytest's temporary folder maker, for the Electron sample file.

    Returns
    -------
    names_and_tpms : `tuple`
        The flat names the compiler produced, and the TPM entries.
    """
    samples = tmp_path_factory.mktemp("electron") / "metrics.jsonl"
    rows = [
        {"type": kind, "timestamp": 1.0, "workingSetKb": 1024.0, "cpuPercent": 1.0}
        for kind in ("Browser", "Tab", "GPU")
    ]
    samples.write_text("\n".join(json.dumps(row) for row in rows))
    backend = {"backend_peak_rss_mb": 100.0, "backend_cpu_seconds": 1.0}
    tpms = compile_tpm.build_tpms(_fake_flow(), backend, samples)
    tpms.extend(
        compile_tpm.observatory_entries({
            "frames_auto_downloaded": 1,
            "guide_events_received": 2,
            "frame_to_library_seconds": 3.0,
            "guide_log_lines_pulled": 4,
            "ekos_log_lines_pulled": 5,
        })
    )
    errors = compile_tpm.collect_errors([], {"consoleErrors": [], "failedRequests": [], "toasts": []}, [], [])
    tpms.extend(compile_tpm.error_entries(errors))
    return [tpm["name"] for tpm in tpms], tpms


def test_catalog_entries_are_complete(catalog: dict) -> None:
    """Every entry has every field, a unique id and a known status."""
    ids = [definition["id"] for definition in catalog["tpms"]]
    assert len(ids) == len(set(ids))
    for definition in catalog["tpms"]:
        assert REQUIRED_FIELDS <= set(definition), definition["id"]
        assert definition["threshold_status"] in STATUSES, definition["id"]
        assert definition["better"] in ("lower", "higher"), definition["id"]
        if definition["threshold"] is not None:
            assert definition["objective"] is not None, definition["id"]
        if definition["threshold_status"] != "TBD" and not definition.get("informational"):
            assert definition["threshold"] is not None, definition["id"]


def test_every_produced_tpm_has_a_catalog_entry(catalog: dict, produced_names: tuple) -> None:
    """Each name the compiler produces maps to an entry with the same unit."""
    tpms = produced_names[1]
    for tpm in tpms:
        match = compile_tpm.catalog_match(catalog, tpm["name"])
        assert match is not None, f"{tpm['name']} has no catalog entry"
        assert match[0]["unit"] == tpm["unit"], tpm["name"]


def test_every_catalog_entry_is_produced_or_marked(catalog: dict, produced_names: tuple) -> None:
    """Each entry is produced by a code path, or says ``not_measured_yet``."""
    names, _ = produced_names
    for definition in catalog["tpms"]:
        produced = any(compile_tpm.catalog_match({"tpms": [definition]}, name) for name in names)
        if definition.get("not_measured_yet"):
            assert not produced, f"{definition['id']} is produced now: remove not_measured_yet"
        else:
            assert produced, f"{definition['id']} is produced by no code path"


def test_displays_match_the_app(catalog: dict) -> None:
    """The catalog lists every display of the app, and only those."""
    source = (REPO_DIR / "ui" / "App.tsx").read_text()
    app_modes = re.findall(r"\{ mode: '([^']+)', id:", source)
    assert app_modes
    catalog_names = [display["name"] for display in catalog["displays"]]
    assert sorted(catalog_names) == sorted(app_modes)
    for display in catalog["displays"]:
        assert ("excluded" in display) != ("actions" in display), display["name"]


def test_flow_knows_every_action(catalog: dict) -> None:
    """Every catalog action is one that ``flow.mjs`` performs."""
    source = (TPM_DIR / "flow.mjs").read_text()
    for display in catalog["displays"]:
        for action in display.get("actions", []):
            assert f"'{action['action']}'" in source, action


def test_threshold_never_fails_unless_confirmed(catalog: dict) -> None:
    """A TBR or TBD limit never changes the verdict; a Confirmed one does."""
    flow_values = [{"name": "time_to_app_ready", "unit": "ms", "value": 9999.0, "n": 1}]
    compile_tpm.annotate(flow_values, catalog, {})
    assert flow_values[0]["limit"]["beyond"] is True
    assert compile_tpm.breached_by_threshold(flow_values) == []
    flow_values[0]["limit"]["threshold_status"] = "Confirmed"
    assert compile_tpm.breached_by_threshold(flow_values) == ["time_to_app_ready"]


def test_relative_threshold_uses_the_run_fact(catalog: dict) -> None:
    """The frame-to-library limit is the exposure length, if known."""
    tpms = [{"name": "frame_to_library_seconds", "unit": "s", "value": 15.0, "n": 1}]
    compile_tpm.annotate(tpms, catalog, {"exposure_seconds": 60.0})
    assert tpms[0]["limit"]["threshold"] == pytest.approx(60.0)
    assert tpms[0]["limit"]["objective"] == pytest.approx(15.0)
    compile_tpm.annotate(tpms, catalog, {"exposure_seconds": None})
    assert tpms[0]["limit"]["threshold"] is None


def test_budgets_json_agrees_with_catalog(catalog: dict) -> None:
    """``budgets.json`` holds exactly the catalog's Lighthouse budgets."""
    timings, sizes, counts = [], [], []
    for item in catalog["lighthouse_budgets"]:
        if item.get("tpm"):
            assert any(definition["id"] == item["tpm"] for definition in catalog["tpms"])
        if item["kind"] == "timing":
            timings.append({"metric": item["metric"], "budget": item["budget"]})
        else:
            row = {"resourceType": item["metric"], "budget": item["budget"]}
            (sizes if item["kind"] == "resourceSizes" else counts).append(row)
    expected = [{"path": "/*", "timings": timings, "resourceSizes": sizes, "resourceCounts": counts}]
    assert json.loads((TPM_DIR / "budgets.json").read_text()) == expected
