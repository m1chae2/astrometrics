"""Purpose: Time the app's own stack job and analysis for one target.

Description: The display flow measures how the screens respond. This script
measures the heavy work: it asks the throwaway backend, through the same RPC
calls the Image Processing display uses, to stack one target's frames and
then to analyze it, waits for each job to finish, and records for each stage
the wall time and what the backend and the programs it started used.

The steps, in order:

1. ``target:get_files`` lists the target's frames. Only the frames of the
   chosen camera are used, so the input is the same every run (the plan is
   written to ``processing-plan.json``). With ``--dry-run`` the script stops
   here, after it has checked that every frame file can be found.
2. ``processing:stack`` starts the stacking job (the "Process" button). The
   script polls ``processing:get_job`` until the job ends. The job log
   (``processing:job_log_tail``) holds one line ``Stage N of M: ...`` at the
   start of each stage, with a time to the millisecond. Those lines cut the
   job into stages.
3. The analysis steps run one after another, each a background job like the
   buttons in the display: ``photometry`` (``analysis:analyze_image`` on the
   new stack, the "Analyze Target" button), ``frame_measurements``
   (``quality:record_frame_measurements``) and ``stack_comparison``
   (``quality:measure_stack``). The last two start a job without telling the
   job's number, so the script waits until every job that is new for the
   target has ended.

Resources: a `ProcessTreeTracker` follows the backend and every program under
it, with a sample every half second while the script waits. A stage's CPU
time, peak memory and disk bytes are read from the samples between the
stage's start and end (see ``resource_usage.py`` for what is counted and what
can be missed). The NVIDIA card's utilization and memory come from the
sampler's file, one row every 2 seconds, so a stage shorter than that may
have no row (``None``).

Safety: it talks only to the throwaway backend (the port is refused if it is
5000) and reads ``/proc``. It never writes to the library itself; the
backend's job writes to the throwaway library or, in real-snapshot mode, to
the overlay.

Usage::

    .venv/bin/python build/tpm/processing_run.py --backend-port 47123 \
        --backend-pid 123 --target "M 31" --camera "ZWO ASI 533MM Pro" \
        --out results/<run> [--nvidia-samples file] [--dry-run]
"""

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend_rpc import call_rpc
from resource_usage import BACKEND_GROUP, ProcessTreeTracker, window_usage

#: Seconds between two looks at a running job (and two resource samples).
POLL_SECONDS = 0.5
#: Seconds the stacking job may take before the script gives up.
STACK_TIMEOUT_SECONDS = 6 * 3600
#: Seconds one analysis step may take.
STEP_TIMEOUT_SECONDS = 3 * 3600
#: Seconds to wait for a job to show up in the job list after a step starts.
JOB_APPEARS_SECONDS = 20.0
#: Job states that mean the job is over.
TERMINAL_STATUSES = ("completed", "failed", "cancelled")
#: The analysis steps in the order they run by default.
DEFAULT_ANALYSIS_STEPS = ("photometry", "frame_measurements", "stack_comparison")
#: The stage keys of a stacking job in order. Used only to name stage numbers.
STAGE_KEYS = (
    "choose_frames",
    "screen_frames",
    "prepare",
    "stacking",
    "summarize",
    "quality",
    "preview",
)
STAGE_LINE = re.compile(
    r"^Stage (?P<index>\d+) of (?P<total>\d+): (?P<skipped>skipped, )?(?P<text>.*?)…?\.?$"
)


def parse_log_time(text: str) -> float:
    """Turn a job log time into seconds since the epoch.

    Parameters
    ----------
    text : `str`
        The time as the log keeps it, local time such as
        ``2026-11-06 16:45:12.123``.

    Returns
    -------
    seconds : `float`
        Seconds since the epoch.
    """
    return datetime.fromisoformat(text).timestamp()


def stages_from_log(lines: list[str], keys: tuple[str, ...] | None = None) -> list[dict]:
    """Find the stage starts in a job's log.

    Parameters
    ----------
    lines : `list` [`str`]
        The log lines from ``processing:job_log_tail``, each
        ``<time> - <level> - <message>``.
    keys : `tuple` [`str`], optional
        The stage keys in order; `STAGE_KEYS` by default.

    Returns
    -------
    stages : `list` [`dict`]
        One entry per ``Stage N of M`` line, in order: ``index`` (1 for the
        first), ``total``, ``key`` (the stage's name, or ``stage_N`` when the
        number of stages is not the number of known keys), ``text``,
        ``skipped`` and ``start`` (seconds since the epoch).
    """
    names = keys if keys is not None else STAGE_KEYS
    stages = []
    for line in lines:
        parts = line.split(" - ", 2)
        if len(parts) != 3:
            continue
        match = STAGE_LINE.match(parts[2].strip())
        if not match:
            continue
        index, total = int(match["index"]), int(match["total"])
        known = total == len(names) and 1 <= index <= len(names)
        stages.append({
            "index": index,
            "total": total,
            "key": names[index - 1] if known else f"stage_{index}",
            "text": match["text"].strip(),
            "skipped": bool(match["skipped"]),
            "start": parse_log_time(parts[0].strip()),
        })
    return stages


def nvidia_window(rows: list[dict], start: float, end: float) -> dict | None:
    """Summarize the NVIDIA samples inside a time window.

    Parameters
    ----------
    rows : `list` [`dict`]
        Rows with ``t``, ``utilization_percent`` and ``memory_mb`` (from
        ``setup_fingerprint.py nvidia-sample``).
    start, end : `float`
        The window, seconds since the epoch.

    Returns
    -------
    summary : `dict` or `None`
        ``samples``, ``util_mean``, ``util_peak`` and ``memory_mb_peak``;
        `None`
        if no row falls in the window (the card is read every 2 seconds).
    """
    inside = [row for row in rows if start <= row["t"] <= end]
    if not inside:
        return None
    utilization = [row["utilization_percent"] for row in inside]
    return {
        "samples": len(inside),
        "util_mean": sum(utilization) / len(utilization),
        "util_peak": max(utilization),
        "memory_mb_peak": max(row["memory_mb"] for row in inside),
    }


def aggregate_stages(
    stages: list[dict], job_end: float, samples: list[dict], nvidia_rows: list[dict]
) -> list[dict]:
    """Give each stage its time and its resource use.

    A stage runs from its own start line to the next stage's start line; the
    last one runs to the end of the job.

    Parameters
    ----------
    stages : `list` [`dict`]
        The result of `stages_from_log`.
    job_end : `float`
        When the job ended, seconds since the epoch.
    samples : `list` [`dict`]
        Samples from `ProcessTreeTracker.poll`.
    nvidia_rows : `list` [`dict`]
        The NVIDIA sampler's rows (may be empty).

    Returns
    -------
    result : `list` [`dict`]
        Each stage with ``end``, ``seconds`` and ``usage`` (from
        ``resource_usage.window_usage``) and ``nvidia`` (`nvidia_window`).
    """
    result = []
    for position, stage in enumerate(stages):
        end = stages[position + 1]["start"] if position + 1 < len(stages) else job_end
        end = max(end, stage["start"])
        result.append({
            **stage,
            "end": end,
            "seconds": end - stage["start"],
            "usage": window_usage(samples, stage["start"], end, BACKEND_GROUP),
            "nvidia": nvidia_window(nvidia_rows, stage["start"], end),
        })
    return result


def choose_frames(files: list[dict], camera: str) -> list[dict]:
    """Pick the imaging frames of one camera from a target's file list.

    Parameters
    ----------
    files : `list` [`dict`]
        The ``files`` of ``target:get_files``: ``path``, ``camera``,
        ``filter``, ``exposure``, ``date``.
    camera : `str`
        The camera's name (a part of it is enough, any case).

    Returns
    -------
    chosen : `list` [`dict`]
        The frames whose camera name contains `camera` and whose filter is not
        a spectroscopy filter, ordered by path so the order is the same every
        run.
    """
    wanted = camera.lower()
    chosen = [
        frame
        for frame in files
        if wanted in str(frame.get("camera") or "").lower()
        and str(frame.get("filter") or "").upper() not in ("SPEC", "SPECTROSCOPY", "STAR ANALYZER 200")
    ]
    return sorted(chosen, key=lambda frame: frame["path"])


def describe_plan(target: str, camera: str, files: list[dict], listing: dict) -> dict:
    """Describe the input of the stacking job.

    Parameters
    ----------
    target : `str`
        The target's name.
    camera : `str`
        The camera asked for.
    files : `list` [`dict`]
        The frames chosen by `choose_frames`.
    listing : `dict`
        The whole ``target:get_files`` answer (for the cameras present and the
        current stack).

    Returns
    -------
    plan : `dict`
        ``frame_count``, ``missing_files`` (how many cannot be found on disk),
        ``total_exposure_seconds``, ``first_date``, ``last_date``,
        ``cameras_in_target``, ``current_stack`` and ``paths_sha`` (a short
        hash of the sorted paths, so two runs can tell they had the same
        input).
    """
    import hashlib

    exposures = []
    for frame in files:
        try:
            exposures.append(float(frame.get("exposure") or 0.0))
        except TypeError, ValueError:
            exposures.append(0.0)
    dates = sorted(str(frame.get("date")) for frame in files if frame.get("date"))
    cameras = sorted({str(frame.get("camera")) for frame in listing.get("files", [])})
    digest = hashlib.sha256("\n".join(frame["path"] for frame in files).encode()).hexdigest()[:12]
    return {
        "target": target,
        "camera": camera,
        "frame_count": len(files),
        "missing_files": sum(1 for frame in files if not os.path.exists(frame["path"])),
        "total_exposure_seconds": sum(exposures),
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "cameras_in_target": cameras,
        "current_stack": listing.get("stackedImage"),
        "paths_sha": digest,
    }


def job_field(job: dict, snake: str, camel: str) -> object:
    """Read a job field whichever way the backend spelled it.

    Parameters
    ----------
    job : `dict`
        A job as the RPC returns it.
    snake, camel : `str`
        The two spellings of the field name.

    Returns
    -------
    value : `object`
        The value, or `None`.
    """
    return job.get(camel, job.get(snake))


class Recorder:
    """Poll the backend's process tree and keep every sample.

    Parameters
    ----------
    tracker : `ProcessTreeTracker`
        The tracker to poll.
    clock : `Callable`, optional
        Gives the time in seconds since the epoch.
    sleep : `Callable`, optional
        Waits for some seconds; tests pass a fake.
    """

    def __init__(
        self,
        tracker: ProcessTreeTracker,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Start with no samples."""
        self.tracker = tracker
        self.clock = clock
        self.sleep = sleep
        self.samples: list[dict] = []

    def poll(self) -> None:
        """Take one sample."""
        self.samples.append(self.tracker.poll())

    def wait_for(self, done: Callable[[], bool], timeout: float, interval: float = POLL_SECONDS) -> bool:
        """Sample every interval until a condition holds.

        Parameters
        ----------
        done : `Callable`
            Called after each sample; returns `True` when to stop.
        timeout : `float`
            Seconds to wait at most.
        interval : `float`, optional
            Seconds between samples.

        Returns
        -------
        finished : `bool`
            `True` if the condition held, `False` on timeout.
        """
        deadline = self.clock() + timeout
        while True:
            self.poll()
            if done():
                return True
            if self.clock() >= deadline:
                return False
            self.sleep(interval)


def run_stack(port: int, target: str, files: list[dict], recorder: Recorder, timeout: float) -> dict:
    """Run the stacking job and wait for it.

    Parameters
    ----------
    port : `int`
        The backend's port.
    target : `str`
        The target.
    files : `list` [`dict`]
        The frames to stack.
    recorder : `Recorder`
        Samples the backend while waiting.
    timeout : `float`
        Seconds to wait for the job.

    Returns
    -------
    record : `dict`
        ``job_id``, ``status``, ``start``, ``end``, ``seconds``, ``message``,
        ``log_lines`` and ``reply``.
    """
    start = recorder.clock()
    reply = call_rpc(
        port, "processing:stack", {"target_id": target, "image_files": [f["path"] for f in files]}
    )
    job_id = (reply or {}).get("jobId")
    record: dict = {"reply": reply, "job_id": job_id, "start": start}
    if not job_id or (reply or {}).get("status") != "started":
        record.update(
            status="not started", end=recorder.clock(), seconds=0.0, message=str(reply), log_lines=[]
        )
        return record
    state: dict = {}

    def finished() -> bool:
        """Read the job and say whether it is over.

        Returns
        -------
        over : `bool`
            `True` once the job's status is final.
        """
        state["job"] = call_rpc(port, "processing:get_job", {"job_id": job_id}) or {}
        return state["job"].get("status") in TERMINAL_STATUSES

    over = recorder.wait_for(finished, timeout)
    job = state.get("job", {})
    seen_end = recorder.clock()
    completed = job_field(job, "completed_at", "completedAt")
    end = seen_end
    if completed:
        try:
            end = min(seen_end, parse_log_time(str(completed)))
        except ValueError:
            end = seen_end
    log_lines = call_rpc(port, "processing:job_log_tail", {"job_id": job_id, "lines": 100000}) or []
    record.update(
        status=job.get("status") if over else "timeout",
        end=end,
        seconds=end - start,
        message=job.get("message"),
        log_lines=log_lines,
        log_file=job_field(job, "log_file_path", "logFilePath"),
    )
    return record


def analysis_request(step: str, target: str, stack_path: str | None) -> tuple[str, dict]:
    """Say which RPC call runs an analysis step.

    Parameters
    ----------
    step : `str`
        ``photometry``, ``frame_measurements`` or ``stack_comparison``.
    target : `str`
        The target.
    stack_path : `str` or `None`
        The target's new stack (the picture "Analyze Target" sends).

    Returns
    -------
    request : `tuple` [`str`, `dict`]
        The RPC method and its parameters.

    Raises
    ------
    ValueError
        If the step is unknown, or ``photometry`` has no stack to analyze.
    """
    if step == "photometry":
        if not stack_path:
            raise ValueError("photometry needs a stack to analyze, but the target has none")
        return "analysis:analyze_image", {
            "target_id": target,
            "image_files": [stack_path],
            "type": "photometry",
        }
    if step == "frame_measurements":
        return "quality:record_frame_measurements", {"target_id": target}
    if step == "stack_comparison":
        return "quality:measure_stack", {"target_id": target, "kind": "imaging"}
    raise ValueError(f"unknown analysis step '{step}'")


def run_analysis_step(
    port: int, target: str, step: str, stack_path: str | None, recorder: Recorder, timeout: float
) -> dict:
    """Run one analysis step and wait until its jobs have ended.

    Parameters
    ----------
    port : `int`
        The backend's port.
    target : `str`
        The target.
    step : `str`
        The step (see `analysis_request`).
    stack_path : `str` or `None`
        The target's stack.
    recorder : `Recorder`
        Samples the backend while waiting.
    timeout : `float`
        Seconds to wait.

    Returns
    -------
    record : `dict`
        ``step``, ``rpc``, ``start``, ``end``, ``seconds``, ``status``
        (``ok``, ``failed``, ``no job`` or ``timeout``), ``jobs`` (id, type and
        status of each new job) and ``reply``.
    """
    method, params = analysis_request(step, target, stack_path)
    known = {
        job["id"] for job in call_rpc(port, "processing:list_jobs", {"target_id": target, "limit": 200}) or []
    }
    start = recorder.clock()
    reply = call_rpc(port, method, params)
    state: dict = {"new": []}

    def finished() -> bool:
        """Say whether every new job for the target has ended.

        Returns
        -------
        over : `bool`
            `True` once at least one new job exists and none is active, or
            no job appeared within `JOB_APPEARS_SECONDS`.
        """
        jobs = call_rpc(port, "processing:list_jobs", {"target_id": target, "limit": 200}) or []
        state["new"] = [job for job in jobs if job["id"] not in known]
        if not state["new"]:
            return recorder.clock() - start > JOB_APPEARS_SECONDS
        return all(job.get("status") in TERMINAL_STATUSES for job in state["new"])

    over = recorder.wait_for(finished, timeout)
    end = recorder.clock()
    jobs = [
        {"id": job["id"], "type": job_field(job, "job_type", "jobType"), "status": job.get("status")}
        for job in state["new"]
    ]
    if not over:
        status = "timeout"
    elif not jobs:
        status = "no job"
    elif all(job["status"] == "completed" for job in jobs):
        status = "ok"
    else:
        status = "failed"
    return {
        "step": step,
        "rpc": method,
        "start": start,
        "end": end,
        "seconds": end - start,
        "status": status,
        "jobs": jobs,
        "reply": reply,
    }


def read_nvidia_rows(path: Path | None) -> list[dict]:
    """Read the NVIDIA sampler's file.

    Parameters
    ----------
    path : `pathlib.Path` or `None`
        The JSON-lines file, or `None`.

    Returns
    -------
    rows : `list` [`dict`]
        The rows, empty if there is no file.
    """
    if path is None or not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def build_report(
    plan: dict, stack: dict, analysis: list[dict], recorder: Recorder, nvidia_rows: list[dict]
) -> dict:
    """Put the whole run into one record.

    Parameters
    ----------
    plan : `dict`
        The result of `describe_plan`.
    stack : `dict`
        The result of `run_stack`.
    analysis : `list` [`dict`]
        The results of `run_analysis_step`.
    recorder : `Recorder`
        Holds the samples.
    nvidia_rows : `list` [`dict`]
        The NVIDIA rows.

    Returns
    -------
    report : `dict`
        What ``processing-metrics.json`` holds: ``plan``, ``stack`` (with its
        ``stages``), ``analysis`` (each with ``usage`` and ``nvidia``),
        ``total_seconds`` (stack job plus analysis steps), ``failed_jobs``.
    """
    samples = recorder.samples
    stages = aggregate_stages(stages_from_log(stack.get("log_lines", [])), stack["end"], samples, nvidia_rows)
    stack_record = {
        key: stack.get(key) for key in ("job_id", "status", "start", "end", "seconds", "message", "log_file")
    }
    stack_record["stages"] = stages
    stack_record["usage"] = window_usage(samples, stack["start"], stack["end"], BACKEND_GROUP)
    stack_record["nvidia"] = nvidia_window(nvidia_rows, stack["start"], stack["end"])
    steps = []
    for item in analysis:
        steps.append({
            **{key: value for key, value in item.items() if key != "reply"},
            "usage": window_usage(samples, item["start"], item["end"], BACKEND_GROUP),
            "nvidia": nvidia_window(nvidia_rows, item["start"], item["end"]),
        })
    failed = int(stack.get("status") != "completed") + sum(
        1 for item in analysis if item["status"] not in ("ok", "no job")
    )
    return {
        "plan": plan,
        "stack": stack_record,
        "analysis": steps,
        "total_seconds": float(stack.get("seconds") or 0.0) + sum(item["seconds"] for item in analysis),
        "failed_jobs": failed,
        "sample_count": len(samples),
    }


def main() -> int:
    """Plan, run and record the processing scenario.

    Returns
    -------
    status : `int`
        0 on success, 1 if a job failed or timed out, 2 for a usage or safety
        error, 3 if the target has no frames of the camera.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backend-port", type=int, required=True)
    parser.add_argument("--backend-pid", type=int, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--camera", required=True)
    parser.add_argument("--out", required=True, help="the results folder")
    parser.add_argument("--nvidia-samples")
    parser.add_argument("--analysis-steps", default=",".join(DEFAULT_ANALYSIS_STEPS))
    parser.add_argument("--dry-run", action="store_true", help="find the frames and stop")
    parser.add_argument("--stack-timeout", type=float, default=STACK_TIMEOUT_SECONDS)
    parser.add_argument("--step-timeout", type=float, default=STEP_TIMEOUT_SECONDS)
    arguments = parser.parse_args()
    out = Path(arguments.out)
    port = arguments.backend_port
    listing = call_rpc(port, "target:get_files", {"target_id": arguments.target})
    files = choose_frames((listing or {}).get("files", []), arguments.camera)
    plan = describe_plan(arguments.target, arguments.camera, files, listing or {})
    (out / "processing-plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    print(
        f"Plan: {plan['frame_count']} frames of {arguments.camera} for {arguments.target}, "
        f"{plan['total_exposure_seconds'] / 3600:.2f} h of exposure, "
        f"{plan['missing_files']} file(s) not found, "
        f"input hash {plan['paths_sha']}"
    )
    if not files:
        print(
            f"ERROR: {arguments.target} has no frames of '{arguments.camera}' "
            f"(cameras in the target: {plan['cameras_in_target']}).",
            file=sys.stderr,
        )
        return 3
    if plan["missing_files"]:
        print(f"ERROR: {plan['missing_files']} frame file(s) cannot be found on disk.", file=sys.stderr)
        return 3
    if arguments.dry_run:
        print("Dry run: no job started.")
        return 0
    recorder = Recorder(ProcessTreeTracker({BACKEND_GROUP: arguments.backend_pid}))
    nvidia_path = Path(arguments.nvidia_samples) if arguments.nvidia_samples else None
    print(f"Stacking {plan['frame_count']} frames through processing:stack ...")
    stack = run_stack(port, arguments.target, files, recorder, arguments.stack_timeout)
    print(f"  stack job {stack.get('job_id')}: {stack['status']} in {stack['seconds']:.1f} s")
    analysis = []
    if stack["status"] == "completed":
        stack_path = (call_rpc(port, "target:get_files", {"target_id": arguments.target}) or {}).get(
            "stackedImage"
        )
        for step in [item for item in arguments.analysis_steps.split(",") if item]:
            print(f"Analysis step {step} ...")
            record = run_analysis_step(
                port, arguments.target, step, stack_path, recorder, arguments.step_timeout
            )
            analysis.append(record)
            print(f"  {step}: {record['status']} in {record['seconds']:.1f} s")
    else:
        print("The stack job did not complete; the analysis steps are skipped.", file=sys.stderr)
    report = build_report(plan, stack, analysis, recorder, read_nvidia_rows(nvidia_path))
    (out / "processing-metrics.json").write_text(json.dumps(report, indent=2) + "\n")
    (out / "processing-job.log").write_text("\n".join(stack.get("log_lines", [])) + "\n")
    for stage in report["stack"]["stages"]:
        print(
            f"  stage {stage['index']} {stage['key']:<14} {stage['seconds']:8.1f} s"
            f"{'  (skipped)' if stage['skipped'] else ''}"
        )
    return 1 if report["failed_jobs"] else 0


if __name__ == "__main__":
    sys.exit(main())
