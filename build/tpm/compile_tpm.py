"""Purpose: Reduce a TPM run to a list of TPMs and compare it to the baseline.

Description: A run leaves three kinds of raw files in its results folder:
``flow-metrics.json`` (from `flow.mjs`), ``backend-metrics.json`` (from
`rpc_timings.py`) and, if Electron was measured, a JSON-lines file of process
samples. This script reduces them to technical performance measures (TPMs):
one entry each with a name, unit, value and the number of samples `n`. It
writes ``tpm.json``, compares each value with ``tpm_baseline.json`` and prints
a table. A value is "within" its limit, on "watch" (past the watch limit) or
"breached" (past the breach limit). The limits are in the baseline file.

``planetarium-profile.json`` data (a traced Planetarium pan and zoom)
becomes more TPMs. The sky map draws on demand, so the zoom TPMs are
per wheel tick (latency to the first frame, settle time) and over the
frames of active animation only (see ``planetarium_profile.mjs``). The error
TPMs (console errors, failed requests, error toasts, desktop notifications and
backend ERROR log lines, all with a Confirmed threshold of 0) come from the
``errors`` block of ``flow-metrics.json``, ``electron-startup-errors.json``,
the ``desktop_notification`` lines of the Electron samples and ``backend.log``;
each offending message goes into ``info.errors`` and is printed when a limit is
breached. The graphics report (the WebGL renderer the
page used and, in Electron, what the main process says about the GPU) is
written to ``tpm.json`` as an ``info`` block, not as TPMs, so every run
says whether it drew on real hardware.

The baseline changes only when you pass ``--update-baseline``, so a slow run
can never quietly become the new normal.

The setup the run happened on (``setup-fingerprint.json`` from
``setup_fingerprint.py``, plus the graphics card the page really used and the
NVIDIA samples) goes into the ``info`` block and the table header. It is
reduced to a ``setup_key``. ``tpm_baseline.json`` holds one baseline per
key, and a run is compared only with the baseline of its own key.

Usage::

    .venv/bin/python build/tpm/compile_tpm.py <results folder> \
        [--electron metrics.jsonl] [--update-baseline] \
        [--mode synthetic|real-snapshot] \
        [--observatory-profile offline|simulated] \
        [--observatory-metrics observatory-metrics.json] \
        [--data-size data-size.json] [--overlay-writes overlay-writes.json] \
        [--setup setup-fingerprint.json] [--gpu-requested auto|intel|nvidia] \
        [--migrate-legacy-baseline] [--cold --cold-report cold-cache.json] \
        [--after-reboot] [--boot-age SECONDS]

``--cold`` says the run started with the file cache dropped
(``cold_cache.py``).
The key then differs from a warm run's, so cold and warm runs have separate
baselines, and the cold-start TPMs (``cold_only`` in the catalog) are held to
their limits. ``--after-reboot`` and ``--boot-age`` record how long the
computer had been up. Every TPM carries ``cold`` (true or false).

Only a synthetic run whose setup key has a baseline is compared. With another
key the statuses are ``unmatched`` ("different setup, not compared"). A
real-snapshot run is a trend, not a gate: its TPMs get the status ``trend``
and it can never update the baseline. ``--migrate-legacy-baseline`` files an
old single baseline (no ``setups`` block) under this run's key, once.
``scaling.py`` reads the ``tpm.json`` files to plot the trend.
"""

import argparse
import json
import re
import statistics
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from setup_fingerprint import classify_renderer, make_setup_key

BASELINE_PATH = Path(__file__).with_name("tpm_baseline.json")
CATALOG_PATH = Path(__file__).with_name("tpm_catalog.json")
#: Past this multiple of the baseline (plus the slack) a value is on watch.
DEFAULT_WATCH_RATIO = 1.25
#: Past this multiple of the baseline (plus the slack) a value is breached.
DEFAULT_BREACH_RATIO = 1.5
#: The three RPC methods whose p95 is a TPM of its own.
TRACKED_RPC_METHODS = ("history:night_quality", "target:get_targets", "processing:stack_summary")
#: TPMs where a higher value is better (the limits divide instead of multiply).
HIGHER_IS_BETTER = ("planetarium_fps_panning", "planetarium_fps_zooming", "fps_hw")


def load_catalog() -> dict:
    """Read the TPM catalog (definitions only, no measured values).

    Returns
    -------
    catalog : `dict`
        The parsed ``tpm_catalog.json``: ``displays``, ``tpms`` and
        ``lighthouse_budgets``.
    """
    return json.loads(CATALOG_PATH.read_text())


#: The displays, in lap order, taken from the catalog. Displays the catalog
#: marks as excluded (the prototypes) are not measured.
DISPLAYS = tuple(display["name"] for display in load_catalog()["displays"] if "excluded" not in display)


def percentile(values: list[float], fraction: float) -> float:
    """Give a percentile by linear interpolation.

    Parameters
    ----------
    values : `list` [`float`]
        The values. Must not be empty.
    fraction : `float`
        0.95 for the 95th percentile.

    Returns
    -------
    value : `float`
        The percentile.
    """
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def entry(name: str, unit: str, values: list[float], how: str = "median") -> dict[str, object]:
    """Make one TPM entry from its samples.

    Parameters
    ----------
    name : `str`
        The TPM's name.
    unit : `str`
        The unit of the value.
    values : `list` [`float`]
        The samples.
    how : `str`, optional
        ``median``, ``p95``, ``max``, ``last`` or ``sum``.

    Returns
    -------
    tpm : `dict`
        ``name``, ``unit``, ``value`` (`None` with no samples) and ``n``.
    """
    clean = [value for value in values if value is not None]
    if not clean:
        return {"name": name, "unit": unit, "value": None, "n": 0}
    reducers = {
        "median": statistics.median,
        "p95": lambda samples: percentile(samples, 0.95),
        "max": max,
        "last": lambda samples: samples[-1],
        "sum": sum,
    }
    return {"name": name, "unit": unit, "value": float(reducers[how](clean)), "n": len(clean)}


def electron_entries(path: Path | None) -> list[dict[str, object]]:
    """Reduce the Electron process samples to memory and CPU TPMs.

    Parameters
    ----------
    path : `pathlib.Path` or `None`
        The JSON-lines file, or `None` if Electron was not measured.

    Returns
    -------
    entries : `list` [`dict`]
        Peak memory and mean CPU for the main, renderer and GPU processes.
    """
    kinds = {"Browser": "main", "Tab": "renderer", "GPU": "gpu"}
    totals: dict[str, dict[float, list[float]]] = {
        kind: defaultdict(lambda: [0.0, 0.0]) for kind in kinds.values()
    }
    if path is not None and path.exists():
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            kind = kinds.get(row["type"])
            if kind is None:
                continue
            bucket = totals[kind][row["timestamp"]]
            bucket[0] += (row.get("workingSetKb") or 0.0) / 1024.0
            bucket[1] += row.get("cpuPercent") or 0.0
    entries = []
    for kind, samples in totals.items():
        memory = [bucket[0] for bucket in samples.values()]
        cpu = [bucket[1] for bucket in samples.values()]
        entries.append(entry(f"electron_{kind}_peak_memory_mb", "MB", memory, "max"))
        entries.append(
            entry(f"electron_{kind}_mean_cpu_percent", "%", [statistics.fmean(cpu)] if cpu else [], "median")
        )
    return entries


def electron_gpu_report(path: Path | None) -> dict | None:
    """Read the last graphics report line the Electron sampler writes.

    The sampler writes one at start-up, before the window has rendered, and
    another about twenty seconds later; the last one is the better one.

    Parameters
    ----------
    path : `pathlib.Path` or `None`
        The JSON-lines file, or `None` if Electron was not measured.

    Returns
    -------
    report : `dict` or `None`
        ``featureStatus`` and ``gpuInfo`` as Electron gave them, or `None`
        if there is no report.
    """
    if path is None or not path.exists():
        return None
    report = None
    for line in path.read_text().splitlines():
        if line.strip() and json.loads(line).get("type") == "gpu_report":
            row = json.loads(line)
            report = {"featureStatus": row.get("featureStatus"), "gpuInfo": row.get("gpuInfo")}
    return report


def profile_entries(flow: dict) -> list[dict[str, object]]:
    """Reduce the traced Planetarium pan and zoom to TPMs.

    Parameters
    ----------
    flow : `dict`
        The contents of ``flow-metrics.json``.

    Returns
    -------
    entries : `list` [`dict`]
        ``fps_hw`` (only when WebGL drew on a real graphics card, otherwise
        no value), the pan frame-time median and 95th percentile, the median
        star projection and upload time, the median overlay drawing time and
        the median GPU busy time per frame. For the zoom: the frame rate and
        frame times over the frames of active animation only (the sky map
        draws on demand, so idle gaps are not frames), the number of frames
        over 20 ms, the latency from each wheel tick to its first frame, the
        settle time from each tick to the last frame of its easing, the
        number of main-thread tasks over 50 ms, and the main-thread and GPU
        busy time per frame. After the zoom, the draws per second in the
        trace's idle window (nothing moving), which should be about 0.
    """
    profile = flow.get("planetariumProfile") or {}
    zoom = profile.get("zoom") or {}
    active = zoom.get("active") or {}
    ticks = zoom.get("ticks") or {}
    hardware = (flow.get("gpu") or {}).get("hardware") is True
    fps_values = [row["fps"] for row in flow["planetarium"]] if hardware else []
    slow = [active["slowFrameCount"]] if "slowFrameCount" in active else []
    long_tasks = [zoom["longTasks"]["count"]] if zoom.get("longTasks") else []
    return [
        entry("planetarium_fps_zooming", "fps", [active.get("fps")]),
        entry("fps_hw", "fps", fps_values),
        entry("pan_frame_ms_p50", "ms", [profile.get("frameMsP50")]),
        entry("pan_frame_ms_p95", "ms", [profile.get("frameMsP95")]),
        entry("star_project_ms_p50", "ms", [(profile.get("starProjectUpload") or {}).get("msP50")]),
        entry("overlay_draw_ms_p50", "ms", [(profile.get("overlayDraw") or {}).get("msP50")]),
        entry("gpu_ms_p50", "ms", [(profile.get("gpuProcess") or {}).get("msP50")]),
        entry("zoom_frame_ms_p50", "ms", [active.get("frameMsP50")]),
        entry("zoom_frame_ms_p95", "ms", [active.get("frameMsP95")]),
        entry("zoom_slow_frames", "frames", slow, "last"),
        entry("zoom_tick_latency_ms_p50", "ms", [ticks.get("latencyMsP50")]),
        entry("zoom_tick_latency_ms_p95", "ms", [ticks.get("latencyMsP95")]),
        entry("zoom_settle_ms_p50", "ms", [ticks.get("settleMsP50")]),
        entry("zoom_settle_ms_p95", "ms", [ticks.get("settleMsP95")]),
        entry("zoom_long_tasks", "tasks", long_tasks, "last"),
        entry("zoom_main_thread_ms_per_frame", "ms", [(zoom.get("mainThread") or {}).get("busyMsPerFrame")]),
        entry("zoom_gpu_ms_p50", "ms", [(zoom.get("gpuProcess") or {}).get("msP50")]),
        entry(
            "planetarium_idle_draws_per_second",
            "draws/s",
            [(profile.get("idle") or {}).get("drawsPerSecond")],
        ),
    ]


#: A log line at ERROR or CRITICAL level:
#: ``date time - logger - LEVEL - message``.
BACKEND_ERROR_LINE = re.compile(
    r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+ - (?P<logger>\S+) - (?:ERROR|CRITICAL) - (?P<text>.*)$"
)
#: An error line of the web server's own log, such as
#: ``ERROR:    Exception in ASGI application``.
SERVER_ERROR_LINE = re.compile(r"^(?:ERROR|CRITICAL):\s+(?P<text>.*)$")


def backend_error_lines(log_path: Path) -> list[dict[str, str]] | None:
    """Find the lines at ERROR level in the throwaway backend's log.

    Parameters
    ----------
    log_path : `pathlib.Path`
        ``backend.log``, which covers the backend from start to end of the run.

    Returns
    -------
    lines : `list` [`dict`] or `None`
        One record (``text`` and ``logger``) per ERROR or CRITICAL line, or
        `None` if there is no log. The lines after a traceback's first line
        are not counted.
    """
    if not log_path.exists():
        return None
    found = []
    for line in log_path.read_text(errors="replace").splitlines():
        match = BACKEND_ERROR_LINE.match(line)
        if match:
            found.append({"text": match["text"][:500], "logger": match["logger"]})
            continue
        match = SERVER_ERROR_LINE.match(line)
        if match:
            found.append({"text": match["text"][:500], "logger": "uvicorn"})
    return found


def notification_records(path: Path | None) -> list[dict] | None:
    """Read the native notifications the Electron main process counted.

    Parameters
    ----------
    path : `pathlib.Path` or `None`
        The Electron samples file, or `None` if Electron was not measured.

    Returns
    -------
    records : `list` [`dict`] or `None`
        The ``desktop_notification`` lines (``title``, ``body``), or `None`
        if Electron was not measured (a plain browser cannot send one).
    """
    if path is None or not path.exists():
        return None
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [{**row, "run": "electron"} for row in rows if row.get("type") == "desktop_notification"]


def collect_errors(
    flows: list[dict],
    startup: dict | None,
    notifications: list[dict] | None,
    backend_lines: list[dict] | None,
) -> dict[str, list[dict] | None]:
    """Gather every error the run saw, with its message, for the error TPMs.

    Parameters
    ----------
    flows : `list` [`dict`]
        The ``flow-metrics.json`` contents of the Chrome flow and, if it ran,
        the Electron flow. A flow written before the ``errors`` block existed
        adds nothing.
    startup : `dict` or `None`
        ``electron-startup-errors.json`` (errors from the app's own start-up
        and its idle time), if Electron ran.
    notifications : `list` [`dict`] or `None`
        The native notifications, or `None` if Electron did not run.
    backend_lines : `list` [`dict`] or `None`
        The backend ERROR lines, or `None` if there was no log.

    Returns
    -------
    errors : `dict`
        ``console_errors``, ``failed_requests``, ``error_toasts``,
        ``desktop_notifications`` and ``backend_error_log_lines``, each a list
        of records (`None` when that source was not available), and
        ``aborted_requests`` (requests the browser cancelled, kept for
        information only).
    """
    blocks = [flow["errors"] for flow in flows if flow.get("errors")]
    if startup:
        blocks.append(startup)
    measured = bool(blocks)
    toasts = [toast for block in blocks for toast in block.get("toasts", [])]
    console = [item for block in blocks for item in block.get("consoleErrors", [])]
    failed = [item for block in blocks for item in block.get("failedRequests", [])]
    loud = [toast for toast in toasts if toast.get("kind") in ("error", "warning")]
    return {
        "console_errors": console if measured else None,
        "failed_requests": failed if measured else None,
        "error_toasts": loud if measured else None,
        "desktop_notifications": notifications,
        "backend_error_log_lines": backend_lines,
        "aborted_requests": [item for block in blocks for item in block.get("abortedRequests", [])],
    }


def error_entries(errors: dict[str, list[dict] | None]) -> list[dict[str, object]]:
    """Make the error TPMs: each is a count that must be 0.

    Parameters
    ----------
    errors : `dict`
        The result of `collect_errors`.

    Returns
    -------
    entries : `list` [`dict`]
        ``console_errors``, ``failed_requests``, ``error_toasts``,
        ``desktop_notifications`` and ``backend_error_log_lines``; a source
        that was not available gives no value.
    """
    names = (
        "console_errors",
        "failed_requests",
        "error_toasts",
        "desktop_notifications",
        "backend_error_log_lines",
    )
    return [
        entry(name, "count", [] if errors[name] is None else [len(errors[name])], "last") for name in names
    ]


def describe_error(kind: str, record: dict) -> str:
    """Write one offending record as a line of text.

    Parameters
    ----------
    kind : `str`
        The error TPM's name.
    record : `dict`
        One record from `collect_errors`.

    Returns
    -------
    text : `str`
        A single line naming the message and where it came from.
    """
    if kind == "failed_requests":
        outcome = record.get("status") or record.get("failure")
        what = f"{outcome} {record.get('method', '')} {record.get('url')}"
    elif kind == "desktop_notifications":
        what = f"{record.get('title')}: {record.get('body')}"
    elif kind == "backend_error_log_lines":
        what = f"[{record.get('logger')}] {record.get('text')}"
    elif kind == "error_toasts":
        what = f"{record.get('kind')}: {record.get('text')} (source {record.get('source')})"
    else:
        what = f"{record.get('text')} ({record.get('url') or 'no URL'})"
    return f"  [{record.get('run', 'backend')}] {what}"


def print_offenders(beyond: list[str], errors: dict[str, list[dict] | None]) -> None:
    """Print every message behind a breached error TPM.

    Parameters
    ----------
    beyond : `list` [`str`]
        Names of the TPMs beyond a Confirmed threshold.
    errors : `dict`
        The result of `collect_errors`.
    """
    for name in beyond:
        records = errors.get(name)
        if not records:
            continue
        print(f"OFFENDERS for {name} ({len(records)}):")
        counts: dict[str, int] = defaultdict(int)
        for record in records:
            counts[describe_error(name, record)] += 1
        for text, count in counts.items():
            print(text if count == 1 else f"{text}   (x{count})")


def observatory_entries(metrics: dict) -> list[dict[str, object]]:
    """Make the TPMs of the simulated observatory profile.

    Parameters
    ----------
    metrics : `dict`
        The contents of ``observatory-metrics.json`` (from
        ``observatory_metrics.py``).

    Returns
    -------
    entries : `list` [`dict`]
        ``frames_auto_downloaded`` (frames the app copied into the library by
        itself), ``guide_events_received`` (guide steps the stand-in PHD2 sent
        to the app), ``frame_to_library_seconds`` (the median time from a frame
        appearing in the stand-in picture folder to its copy in the library)
        and
        ``guide_log_lines_pulled`` (lines of guide log the log sync fetched).
        The replay profile adds ``ekos_log_lines_pulled`` (lines of the Ekos
        analyze log the log sync fetched).
    """
    entries = [
        entry("frames_auto_downloaded", "frames", [metrics.get("frames_auto_downloaded")], "last"),
        entry("guide_events_received", "events", [metrics.get("guide_events_received")], "last"),
        entry("frame_to_library_seconds", "s", [metrics.get("frame_to_library_seconds")]),
        entry("guide_log_lines_pulled", "lines", [metrics.get("guide_log_lines_pulled")], "last"),
    ]
    if metrics.get("ekos_log_lines_pulled") is not None:
        entries.append(entry("ekos_log_lines_pulled", "lines", [metrics["ekos_log_lines_pulled"]], "last"))
    return entries


def build_tpms(flow: dict, backend: dict, electron_path: Path | None) -> list[dict[str, object]]:
    """Reduce all the raw measurements to the TPM list.

    Parameters
    ----------
    flow : `dict`
        The contents of ``flow-metrics.json``.
    backend : `dict`
        The contents of ``backend-metrics.json``.
    electron_path : `pathlib.Path` or `None`
        The Electron samples file, if any.

    Returns
    -------
    tpms : `list` [`dict`]
        The TPM entries.
    """
    laps = flow["laps"]
    tpms = [entry("time_to_app_ready", "ms", [flow["timeToReadyMs"]])]
    # The cold-start TPMs are first-lap values. They are always measured; only
    # a --cold run holds them to a
    # limit (see `annotate`), because only then do the first visits really
    # start from a cold cache.
    tpms.append(entry("time_to_app_ready_cold", "ms", [flow["timeToReadyMs"]]))
    first_lap = [row for row in flow["switches"] if row.get("lap", 1) == 1 and not row["timedOut"]]
    for display in DISPLAYS:
        tpms.append(
            entry(
                f"first_display_open[{display}]",
                "ms",
                [row["settledMs"] for row in first_lap if row["display"] == display][:1],
            )
        )
    for method in TRACKED_RPC_METHODS:
        first_call = [call["ms"] for call in flow["rpc"] if call["method"] == method][:1]
        tpms.append(entry(f"first_rpc[{method}]", "ms", first_call))

    switches = [row for row in flow["switches"] if not row["timedOut"]]
    tpms.append(entry("display_switch_median", "ms", [row["settledMs"] for row in switches]))
    tpms.append(entry("display_switch_p95", "ms", [row["settledMs"] for row in switches], "p95"))
    for display in DISPLAYS:
        tpms.append(
            entry(
                f"display_switch_median[{display}]",
                "ms",
                [row["settledMs"] for row in switches if row["display"] == display],
            )
        )

    for name in ("select_target", "open_night", "change_tab"):
        rows = [row for row in flow["interactions"] if row["name"] == name and not row["timedOut"]]
        tpms.append(entry(f"interaction_latency_{name}", "ms", [row["settledMs"] for row in rows]))

    # Lighthouse steps: the first is the load, then one per lap and
    # display, in the order they ran.
    steps = flow["lighthouseSteps"]
    tbt_by_display: dict[str, list[float]] = defaultdict(list)
    inp_values, cls_values = [], []
    for step, label in zip(steps[1:], flow["stepNames"], strict=False):
        tbt_by_display[label["display"]].append(step["totalBlockingTimeMs"])
        inp_values.append(step["interactionToNextPaintMs"])
        cls_values.append(step["cumulativeLayoutShift"])
    tpms.append(entry("total_blocking_time[Load app]", "ms", [steps[0]["totalBlockingTimeMs"]]))
    for display in DISPLAYS:
        tpms.append(entry(f"total_blocking_time[{display}]", "ms", tbt_by_display.get(display, [])))
    tpms.append(entry("inp_worst", "ms", inp_values, "max"))
    tpms.append(entry("cls_load", "score", [steps[0]["cumulativeLayoutShift"]]))
    tpms.append(entry("cls_worst_display_step", "score", cls_values, "max"))
    tpms.append(
        entry(
            "script_transfer_size_load",
            "KB",
            [(steps[0]["scriptBytes"] or 0) / 1024.0 if steps[0]["scriptBytes"] is not None else None],
        )
    )
    tpms.append(entry("request_count_load", "requests", [steps[0]["requestCount"]]))

    tpms.append(entry("planetarium_fps_panning", "fps", [row["fps"] for row in flow["planetarium"]]))
    tpms.extend(profile_entries(flow))

    heaps = [row["bytes"] / 1048576.0 for row in flow["heapAfterLap"]]
    tpms.append(entry("js_heap_after_last_lap", "MB", heaps, "last"))
    growth = [(heaps[-1] - heaps[0]) / (len(heaps) - 1)] if len(heaps) > 1 else []
    tpms.append(entry("js_heap_growth_per_lap", "MB", growth))

    tpms.extend(electron_entries(electron_path))

    tpms.append(entry("backend_peak_rss", "MB", [backend["backend_peak_rss_mb"]]))
    tpms.append(entry("backend_cpu_time", "s", [backend["backend_cpu_seconds"]]))

    counts: dict[str, int] = defaultdict(int)
    for call in flow["rpc"]:
        counts[call["phase"]] += 1
    tpms.append(entry("rpc_count_load", "calls", [counts["load"]]))
    for display in DISPLAYS:
        tpms.append(entry(f"rpc_count_per_visit[{display}]", "calls", [counts[display] / laps]))
    for method in TRACKED_RPC_METHODS:
        values = [call["ms"] for call in flow["rpc"] if call["method"] == method]
        tpms.append(entry(f"rpc_p95[{method}]", "ms", values, "p95"))
    idle = flow["idle"]
    tpms.append(entry("idle_rpc_per_minute", "calls/min", [idle["rpcCalls"] * 60.0 / idle["seconds"]]))
    return tpms


def _name_pattern(name: str) -> re.Pattern[str]:
    """Turn a catalog name such as ``rpc_p95[{rpc}]`` into a pattern.

    Parameters
    ----------
    name : `str`
        The catalog's flat name, with ``{field}`` placeholders.

    Returns
    -------
    pattern : `re.Pattern`
        A pattern with one named group per placeholder.
    """
    parts = re.split(r"\{(\w+)\}", name)
    text = "".join(f"(?P<{part}>.+)" if index % 2 else re.escape(part) for index, part in enumerate(parts))
    return re.compile(f"^{text}$")


def catalog_match(catalog: dict, name: str) -> tuple[dict, dict[str, str]] | None:
    """Find the catalog entry that defines a flat TPM name.

    Parameters
    ----------
    catalog : `dict`
        The parsed catalog.
    name : `str`
        A flat name as `entry` makes it, for example
        ``rpc_p95[target:get_targets]``.

    Returns
    -------
    match : `tuple` or `None`
        The catalog entry and the values of its placeholders (for example
        ``{"rpc": "target:get_targets"}``), or `None` if no entry
        defines the name.
    """
    for definition in catalog["tpms"]:
        found = _name_pattern(definition["name"]).match(name)
        if found:
            return definition, found.groupdict()
    return None


def resolve_limit(limit: object, context: dict[str, float | None]) -> float | None:
    """Turn a catalog threshold or objective into a number.

    Parameters
    ----------
    limit : `float`, `dict` or `None`
        A number in the TPM's unit, ``{"factor": f, "of": fact}`` (``f``
        times a fact about the run, such as ``exposure_seconds``) or `None`.
    context : `dict` [`str`, `float` or `None`]
        The run facts that a relative limit can name.

    Returns
    -------
    value : `float` or `None`
        The limit, or `None` if there is none or the run fact is unknown.
    """
    if isinstance(limit, dict):
        fact = context.get(limit["of"])
        return None if fact is None else float(limit["factor"]) * fact
    return None if limit is None else float(limit)


def annotate(tpms: list[dict], catalog: dict, context: dict[str, float | None], cold: bool = False) -> None:
    """Add the catalog's structured fields and limit report to each TPM.

    The flat ``name`` stays, so ``tpm_baseline.json`` keys still match.
    Each TPM gets ``tpm`` (the catalog id), ``statistic``, the scope fields
    (``display``, ``rpc``, ``process`` or ``interaction`` when the TPM has
    them) and a ``limit`` block: ``threshold``, ``objective``,
    ``threshold_status``, ``used_percent`` (how much of the threshold the
    value uses; over 100 is beyond it) and ``beyond`` (`True` or `False`,
    `None` with no limit or value).
    Only a Confirmed threshold decides a run: see `breached_by_threshold`.
    Every TPM also gets ``cold`` (whether the run was a ``--cold`` run). A
    catalog entry marked ``cold_only`` gets no limit in a warm run, because
    its limit is a limit for a cold start.

    Parameters
    ----------
    tpms : `list` [`dict`]
        The TPM entries, changed in place.
    catalog : `dict`
        The parsed catalog.
    context : `dict`
        The run facts for relative limits (see `resolve_limit`).
    cold : `bool`, optional
        The run started with a cold file cache.
    """
    for tpm in tpms:
        tpm["cold"] = cold
        match = catalog_match(catalog, tpm["name"])
        if match is None:
            tpm["tpm"] = None
            continue
        definition, scope = match
        tpm["tpm"] = definition["id"]
        tpm["statistic"] = definition["statistic"]
        tpm.update(scope)
        limited = cold or not definition.get("cold_only")
        threshold = resolve_limit(definition["threshold"], context) if limited else None
        objective = resolve_limit(definition["objective"], context) if limited else None
        value = tpm["value"]
        used = beyond = None
        if threshold is not None and value is not None:
            higher = definition["better"] == "higher"
            if higher:
                used = threshold / value * 100.0 if value else None
            elif threshold == 0:
                # A limit of zero (the error counts): any value above it
                # is beyond, and "used" has no meaning.
                used = 0.0 if value == 0 else None
            else:
                used = value / threshold * 100.0
            beyond = value < threshold if higher else value > threshold
        tpm["limit"] = {
            "threshold": threshold,
            "objective": objective,
            "threshold_status": definition["threshold_status"],
            "better": definition["better"],
            "used_percent": used,
            "beyond": beyond,
        }


def breached_by_threshold(tpms: list[dict]) -> list[str]:
    """List the TPMs beyond a Confirmed threshold.

    Only a Confirmed threshold can fail a run. TBR (proposed) and TBD
    (none) never do.

    Parameters
    ----------
    tpms : `list` [`dict`]
        The annotated TPM entries.

    Returns
    -------
    names : `list` [`str`]
        Names of the TPMs whose value is beyond a Confirmed threshold.
    """
    return [
        tpm["name"]
        for tpm in tpms
        if (tpm.get("limit") or {}).get("threshold_status") == "Confirmed" and tpm["limit"]["beyond"]
    ]


def format_number(value: float | None) -> str:
    """Format a number for the table.

    Parameters
    ----------
    value : `float` or `None`
        The number.

    Returns
    -------
    text : `str`
        ``-`` for `None`, three decimals under 10, else one.
    """
    if value is None:
        return "-"
    return f"{value:.3f}" if abs(value) < 10 else f"{value:.1f}"


def run_facts(results: Path, observatory_metrics: dict | None) -> dict[str, float | None]:
    """Collect the run facts that relative limits name.

    Parameters
    ----------
    results : `pathlib.Path`
        The results folder.
    observatory_metrics : `dict` or `None`
        The contents of ``observatory-metrics.json``, if the run had one.

    Returns
    -------
    facts : `dict` [`str`, `float` or `None`]
        ``exposure_seconds`` (the simulated exposure length, from the
        metrics or the exposure driver's statistics file) and
        ``frame_interval_seconds`` (`None`: not measured yet).
    """
    exposure = (observatory_metrics or {}).get("exposure_seconds")
    stats_file = results / "observatory-sim-stats.json"
    if exposure is None and stats_file.exists():
        exposure = json.loads(stats_file.read_text()).get("exposure_seconds")
    return {"exposure_seconds": exposure, "frame_interval_seconds": None}


def load_baseline() -> dict:
    """Read the baseline file, or an empty one if it does not exist.

    Returns
    -------
    baseline : `dict`
        The parsed file. A file in the old format (one ``tpms`` block, no
        ``setups``) is returned as it is; `baseline_for_key` handles it.
    """
    if BASELINE_PATH.exists():
        return json.loads(BASELINE_PATH.read_text())
    return {"schema": 2, "setups": {}}


def baseline_for_key(baseline: dict, setup_key: str, observatory_profile: str) -> tuple[dict | None, str]:
    """Pick the baseline that belongs to a setup key.

    Parameters
    ----------
    baseline : `dict`
        The parsed baseline file.
    setup_key : `str`
        The run's setup key.
    observatory_profile : `str`
        The run's observatory profile (used only for an old-format file).

    Returns
    -------
    entry : `dict` or `None`
        The setup's block with its ``tpms``, or `None` if there is none.
    note : `str`
        ``matched``, ``legacy`` (old file with no setup key, compared as
        before) or ``unmatched``.
    """
    setups = baseline.get("setups")
    if setups is None:
        same_profile = baseline.get("observatory_profile", "offline") == observatory_profile
        legacy_ok = bool(baseline.get("tpms")) and same_profile
        return (baseline, "legacy") if legacy_ok else (None, "unmatched")
    if setup_key in setups:
        return setups[setup_key], "matched"
    return None, "unmatched"


def migrate_legacy_baseline(baseline: dict, setup_key: str, summary: dict) -> dict:
    """Move an old single baseline under one setup key.

    Parameters
    ----------
    baseline : `dict`
        The parsed old-format file.
    setup_key : `str`
        The key of the setup the old baseline was taken on.
    summary : `dict`
        The setup facts to store next to it, for a reader.

    Returns
    -------
    migrated : `dict`
        The new-format file with one setup.
    """
    entry_block = {key: value for key, value in baseline.items() if key not in ("schema", "tpms")}
    entry_block["setup"] = summary
    entry_block["tpms"] = baseline.get("tpms", {})
    return {"schema": 2, "setups": {setup_key: entry_block}}


def nvidia_summary(path: Path) -> dict | None:
    """Reduce the NVIDIA samples to a peak, a mean and the power states seen.

    Parameters
    ----------
    path : `pathlib.Path`
        The JSON-lines file from ``setup_fingerprint.py nvidia-sample``.

    Returns
    -------
    summary : `dict` or `None`
        Sample count, peak and mean utilization and memory, and the sorted
        power states (``P0`` is the busiest, ``P8`` idle), or `None` if there
        are no samples.
    """
    if not path.exists():
        return None
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        return None
    utilization = [row["utilization_percent"] for row in rows]
    memory = [row["memory_mb"] for row in rows]
    return {
        "samples": len(rows),
        "utilization_percent_peak": max(utilization),
        "utilization_percent_mean": round(statistics.fmean(utilization), 2),
        "memory_mb_peak": max(memory),
        "memory_mb_mean": round(statistics.fmean(memory), 1),
        "pstates_seen": sorted({row["pstate"] for row in rows}),
    }


def judge(tpm: dict, limits: dict | None) -> str:
    """Compare one TPM with its baseline entry.

    Parameters
    ----------
    tpm : `dict`
        The TPM entry.
    limits : `dict` or `None`
        The baseline entry: ``baseline``, ``watch_ratio``,
        ``breach_ratio``, ``slack``.

    Returns
    -------
    status : `str`
        ``within``, ``watch``, ``breached``, ``new`` (no baseline value yet)
        or ``not measured``.
    """
    if tpm["value"] is None:
        return "not measured"
    if not limits or limits.get("baseline") is None:
        return "new"
    slack = limits.get("slack", 0.0)
    watch = limits["baseline"] * limits.get("watch_ratio", DEFAULT_WATCH_RATIO) + slack
    breach = limits["baseline"] * limits.get("breach_ratio", DEFAULT_BREACH_RATIO) + slack
    higher_is_better = limits.get("direction") == "higher_is_better"
    value = tpm["value"]
    if higher_is_better:
        watch = limits["baseline"] / limits.get("watch_ratio", DEFAULT_WATCH_RATIO) - slack
        breach = limits["baseline"] / limits.get("breach_ratio", DEFAULT_BREACH_RATIO) - slack
        return "within" if value >= watch else "watch" if value >= breach else "breached"
    return "within" if value <= watch else "watch" if value <= breach else "breached"


def default_slack(tpm: dict) -> float:
    """Pick an absolute allowance so tiny baselines do not flag noise.

    Parameters
    ----------
    tpm : `dict`
        The TPM entry.

    Returns
    -------
    slack : `float`
        The allowance, in the TPM's unit.
    """
    unit = tpm["unit"]
    return {
        "ms": 50.0,
        "score": 0.02,
        "MB": 5.0,
        "s": 0.5,
        "%": 3.0,
        "calls": 3.0,
        "calls/min": 2.0,
        "KB": 50.0,
        "requests": 5.0,
        "fps": 5.0,
        "draws/s": 0.5,
    }.get(unit, 0.0)


def print_setup(setup: dict) -> None:
    """Print the setup block that heads the table.

    Parameters
    ----------
    setup : `dict`
        The ``setup`` block of the ``info`` data.
    """
    cores = f"{setup.get('cpu_physical_cores')} cores / {setup.get('cpu_logical_cores')} threads"
    online = setup.get("ac_online")
    power = "AC" if online else "BATTERY" if online is False else "power unknown"
    print(f"Setup {setup['setup_key']}: {setup.get('cpu_model')} ({cores})")
    print(f"  {setup.get('ram_gb')} GB RAM, kernel {setup.get('kernel')}")
    print(
        f"  power: {power}, profile {setup.get('power_profile')}, governor {setup.get('cpu_governor')}"
        f"   docked: {'yes' if setup.get('docked') else 'no'}"
    )
    print(f"  displays: {', '.join(setup.get('displays') or []) or 'unknown'}")
    print(f"  GPUs: {'; '.join(setup.get('gpus_present') or []) or 'unknown'}")
    print(f"  PRIME: {setup.get('prime_mode')}   session: {setup.get('session_type')}")
    print(f"  GPU requested: {setup.get('gpu_requested')}   used: {setup['gpu_used']}")
    print(f"  WebGL renderer: {setup.get('gpu_renderer')}")
    nvidia = setup.get("nvidia")
    if nvidia:
        print(
            f"  NVIDIA during run: utilization peak {nvidia['utilization_percent_peak']:.0f}%"
            f" mean {nvidia['utilization_percent_mean']:.1f}%,"
            f" memory peak {nvidia['memory_mb_peak']:.0f} MB, pstates {','.join(nvidia['pstates_seen'])}"
        )
    if setup["baseline_match"] == "unmatched":
        print("  DIFFERENT SETUP: no baseline for this setup key, not compared (statuses: unmatched).")
    elif setup["baseline_match"] == "legacy":
        print("  Baseline file has no setup keys (old format): compared as before.")


def main() -> int:
    """Compile a run, compare it and print the table.

    Returns
    -------
    status : `int`
        0 if nothing is breached, 1 if any TPM is breached against its
        baseline or is beyond a Confirmed threshold. TBR and TBD limits
        never change the status.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results")
    parser.add_argument("--electron")
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--mode", default="synthetic", choices=("synthetic", "real-snapshot"))
    parser.add_argument("--observatory-profile", default="offline")
    parser.add_argument("--observatory-metrics")
    parser.add_argument("--data-size")
    parser.add_argument("--overlay-writes")
    parser.add_argument("--setup")
    parser.add_argument("--gpu-requested", default="auto")
    parser.add_argument("--migrate-legacy-baseline", action="store_true")
    parser.add_argument("--cold", action="store_true")
    parser.add_argument("--cold-report")
    parser.add_argument("--after-reboot", action="store_true")
    parser.add_argument("--boot-age", type=float)
    arguments = parser.parse_args()
    if arguments.update_baseline and arguments.mode != "synthetic":
        print("ERROR: only a synthetic run can update the baseline.", file=sys.stderr)
        return 2
    results = Path(arguments.results)
    flow = json.loads((results / "flow-metrics.json").read_text())
    electron_flow_file = results / "electron-flow" / "flow-metrics.json"
    profile_flow = json.loads(electron_flow_file.read_text()) if electron_flow_file.exists() else flow
    backend = json.loads((results / "backend-metrics.json").read_text())
    tpms = build_tpms(flow, backend, Path(arguments.electron) if arguments.electron else None)
    # The Planetarium profile comes from the Electron window when it ran.
    tpms = [
        tpm for tpm in tpms if tpm["name"] not in {item["name"] for item in profile_entries(profile_flow)}
    ]
    tpms.extend(profile_entries(profile_flow))
    electron_samples = Path(arguments.electron) if arguments.electron else None
    startup_file = results / "electron-startup-errors.json"
    errors = collect_errors(
        [flow, *([profile_flow] if electron_flow_file.exists() else [])],
        json.loads(startup_file.read_text()) if startup_file.exists() else None,
        notification_records(electron_samples),
        backend_error_lines(results / "backend.log"),
    )
    tpms = [tpm for tpm in tpms if tpm["name"] not in {item["name"] for item in error_entries(errors)}]
    tpms.extend(error_entries(errors))
    observatory_metrics = (
        json.loads(Path(arguments.observatory_metrics).read_text()) if arguments.observatory_metrics else None
    )
    if observatory_metrics is not None:
        tpms.extend(observatory_entries(observatory_metrics))
    fingerprint = json.loads(Path(arguments.setup).read_text()) if arguments.setup else {}
    cold_report = json.loads(Path(arguments.cold_report).read_text()) if arguments.cold_report else None
    cold_facts = {
        "cold_source": (cold_report or {}).get("cold_source") if arguments.cold else None,
        "boot_age_seconds": arguments.boot_age
        if arguments.boot_age is not None
        else (cold_report or {}).get("boot_age_seconds"),
    }
    context = run_facts(results, observatory_metrics)
    # The card that drew the profiled page (Electron if it ran).
    renderer = (profile_flow.get("gpu") or {}).get("renderer")
    gpu_used = classify_renderer(renderer)
    setup_key = make_setup_key(
        fingerprint, gpu_used, arguments.mode, arguments.observatory_profile, arguments.cold
    )
    baseline = load_baseline()
    if arguments.migrate_legacy_baseline and baseline.get("setups") is None and baseline.get("tpms"):
        baseline = migrate_legacy_baseline(baseline, setup_key, {**fingerprint, "gpu_used": gpu_used})
        BASELINE_PATH.write_text(json.dumps(baseline, indent=2) + "\n")
        print(f"Old baseline filed under setup key {setup_key} in {BASELINE_PATH}")
    setup_baseline, match = baseline_for_key(baseline, setup_key, arguments.observatory_profile)
    limits = (setup_baseline or {}).get("tpms", {})
    comparable = arguments.mode == "synthetic" and setup_baseline is not None
    for tpm in tpms:
        if arguments.mode != "synthetic":
            tpm["status"] = "trend"
        elif not comparable:
            tpm["status"] = "unmatched"
        else:
            tpm["status"] = judge(tpm, limits.get(tpm["name"]))
        if tpm["value"] is None:
            tpm["status"] = "not measured"
    annotate(tpms, load_catalog(), context, arguments.cold)
    if not comparable:
        limits = {}
    setup_info = {
        **fingerprint,
        "gpu_requested": arguments.gpu_requested,
        "gpu_used": gpu_used,
        "gpu_renderer": renderer,
        "gpu_source": "electron window WebGL" if electron_flow_file.exists() else "Chrome flow WebGL",
        "mode": arguments.mode,
        "observatory_profile": arguments.observatory_profile,
        "cold": arguments.cold,
        "cold_source": cold_facts["cold_source"],
        "boot_age_seconds": cold_facts["boot_age_seconds"],
        "setup_key": setup_key,
        "baseline_match": match if arguments.mode == "synthetic" else "trend",
        "nvidia": nvidia_summary(results / "nvidia-samples.jsonl"),
    }
    info = {
        "setup": setup_info,
        "data_size": json.loads(Path(arguments.data_size).read_text()) if arguments.data_size else None,
        "overlay_writes": json.loads(Path(arguments.overlay_writes).read_text())
        if arguments.overlay_writes
        else None,
        "profile_source": "electron" if electron_flow_file.exists() else "chrome",
        # The card the profiled page really drew on: the Electron window's
        # WebGL when Electron ran, else the Chrome flow's. This (not the main
        # process's start-up report) is where the setup key's gpu field
        # comes from.
        "gpu": {
            "requested": arguments.gpu_requested,
            "used": gpu_used,
            "renderer": renderer,
            "vendor": (profile_flow.get("gpu") or {}).get("vendor"),
            "hardware": (profile_flow.get("gpu") or {}).get("hardware"),
            "source": setup_info["gpu_source"],
        },
        "errors": errors,
        "cold_cache": cold_report,
        "webgl_electron_window": profile_flow.get("gpu"),
        "webgl": flow.get("gpu"),
        "electron_gpu": electron_gpu_report(Path(arguments.electron) if arguments.electron else None),
        "planetarium_profile": flow.get("planetariumProfile"),
        "observatory_metrics": observatory_metrics,
        "replay": (observatory_metrics or {}).get("replay"),
    }
    (results / "tpm.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "mode": arguments.mode,
                "observatory_profile": arguments.observatory_profile,
                "cold": arguments.cold,
                "cold_source": cold_facts["cold_source"],
                "setup_key": setup_key,
                "laps": flow["laps"],
                "info": info,
                "tpms": tpms,
            },
            indent=2,
        )
        + "\n"
    )
    webgl = info["webgl"] or {}
    verdict = {True: "hardware", False: "SOFTWARE", None: "unknown"}[webgl.get("hardware")]
    print(
        f"Mode: {arguments.mode}   observatory profile: {arguments.observatory_profile}   "
        f"cold: {arguments.cold}   cold source: {cold_facts['cold_source']}   "
        f"boot age: {cold_facts['boot_age_seconds']} s"
    )
    print_setup(setup_info)
    print(f"WebGL renderer (Chrome flow): {webgl.get('renderer')} ({verdict})")
    window = info["webgl_electron_window"] if electron_flow_file.exists() else None
    if window:
        window_verdict = {True: "hardware", False: "SOFTWARE", None: "unknown"}[window.get("hardware")]
        print(f"WebGL renderer (Electron window): {window.get('renderer')} ({window_verdict})")
    width = max(len(str(tpm["name"])) for tpm in tpms)
    print(
        f"{'TPM':<{width}}  {'value':>10} {'unit':<9} {'n':>3}  {'baseline':>10}  {'status':<12}"
        f" {'threshold':>10} {'objective':>10} {'used':>6}  limit status"
    )
    for tpm in tpms:
        base = limits.get(tpm["name"], {}).get("baseline")
        limit = tpm.get("limit") or {}
        used = limit.get("used_percent")
        used_text = "-" if used is None else f"{used:.0f}%"
        print(
            f"{tpm['name']:<{width}}  {format_number(tpm['value']):>10} {tpm['unit']:<9} {tpm['n']:>3}"
            f"  {format_number(base):>10}  {tpm['status']:<12}"
            f" {format_number(limit.get('threshold')):>10} {format_number(limit.get('objective')):>10}"
            f" {used_text:>6}  {limit.get('threshold_status', '-')}"
        )
    beyond = breached_by_threshold(tpms)
    if beyond:
        print(f"BEYOND A CONFIRMED THRESHOLD: {', '.join(beyond)}")
        print_offenders(beyond, errors)
    if arguments.update_baseline:
        limits = (baseline.get("setups") or {}).get(setup_key, {}).get("tpms", {})
        new_limits = {}
        for tpm in tpms:
            previous = limits.get(tpm["name"], {})
            new_limits[tpm["name"]] = {
                "unit": tpm["unit"],
                "baseline": tpm["value"],
                "watch_ratio": previous.get("watch_ratio", DEFAULT_WATCH_RATIO),
                "breach_ratio": previous.get("breach_ratio", DEFAULT_BREACH_RATIO),
                "slack": previous.get("slack", default_slack(tpm)),
                **(
                    {"direction": "higher_is_better"}
                    if tpm["name"] in HIGHER_IS_BETTER or previous.get("direction")
                    else {}
                ),
            }
        if baseline.get("setups") is None:
            baseline = {"schema": 2, "setups": {}}
        baseline["setups"][setup_key] = {
            "created": datetime.now(UTC).isoformat(),
            "observatory_profile": arguments.observatory_profile,
            "setup": {**fingerprint, "gpu_used": gpu_used},
            "status": "Design estimate: one run on one setup.",
            "tpms": new_limits,
        }
        BASELINE_PATH.write_text(json.dumps(baseline, indent=2) + "\n")
        print(f"Baseline for setup {setup_key} written to {BASELINE_PATH}")
    return 1 if beyond or any(tpm["status"] == "breached" for tpm in tpms) else 0


if __name__ == "__main__":
    sys.exit(main())
