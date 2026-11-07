"""Purpose: Tests for the processing scenario of the TPM harness.

Description: Checks the pure parts of ``processing_run.py`` with made-up job
records: how stage lines in a job log cut a job into stages, how each stage
gets its time and its resource use, how the frames of one camera are chosen,
how the run waits for a job (with a fake backend and a fake clock), how the
processing TPMs come out of the record, and the write lock and the settings
edit the scenario depends on.

Run with ``.venv/bin/pytest build/tpm/test/test_processing_run.py -q``.
"""

import sys
import tomllib
from pathlib import Path

import pytest

TPM_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = TPM_DIR.parents[1]
sys.path.insert(0, str(TPM_DIR))

import compile_tpm  # ruff: ignore[module-import-not-at-top-of-file]
import processing_run  # ruff: ignore[module-import-not-at-top-of-file]
import processing_settings  # ruff: ignore[module-import-not-at-top-of-file]
import setup_fingerprint  # ruff: ignore[module-import-not-at-top-of-file]

KEYS = processing_run.STAGE_KEYS

JOB_LOG = [
    "2026-11-06 10:00:00.100 - INFO - Starting new processing task for M 31 (Job: j1)",
    "2026-11-06 10:00:00.200 - INFO - Stage 1 of 7: choosing the frames to stack…",
    "2026-11-06 10:00:00.700 - INFO - Stage 2 of 7: checking 40 frames before stacking…",
    "2026-11-06 10:00:10.700 - INFO - Checked 10 of 40 frames…",
    "2026-11-06 10:00:20.700 - INFO - Stage 3 of 7: preparing the stack settings…",
    "2026-11-06 10:00:21.700 - INFO - Stage 4 of 7: calibrating, aligning and stacking 40 frames in Siril…",
    "2026-11-06 10:01:21.700 - INFO - Stage 5 of 7: trimming the stack edges…",
    "2026-11-06 10:01:23.700 - INFO - Stage 6 of 7: skipped, Siril made no stack.",
    "2026-11-06 10:01:23.700 - INFO - Stage 7 of 7: skipped, Siril made no stack.",
]


def at(text: str) -> float:
    """Turn a log time into seconds since the epoch.

    Parameters
    ----------
    text : `str`
        A time such as ``2026-11-06 10:00:00.100``.

    Returns
    -------
    seconds : `float`
        Seconds since the epoch.
    """
    return processing_run.parse_log_time(text)


def make_samples(start: float, end: float) -> list[dict]:
    """Make steady fake samples (1 CPU s, 1 MB read, 2 MB written a second).

    Parameters
    ----------
    start, end : `float`
        The first and last sample time.

    Returns
    -------
    samples : `list` [`dict`]
        One sample a second; memory is 100 MB in the first half, 300 MB after.
    """
    samples = []
    for step in range(int(end - start) + 1):
        seconds = float(step)
        samples.append({
            "t": start + seconds,
            "groups": {
                "backend": {
                    "cpu_s": seconds,
                    "read_bytes": seconds * 2**20,
                    "write_bytes": 2 * seconds * 2**20,
                },
            },
            "trees": {
                "backend": {"cpu_s": seconds, "rss_kb": 102400.0 if seconds < (end - start) / 2 else 307200.0}
            },
        })
    return samples


def test_stages_come_from_the_log_lines() -> None:
    """Each 'Stage N of M' line starts a stage; other lines are ignored."""
    stages = processing_run.stages_from_log(JOB_LOG, KEYS)
    assert [stage["key"] for stage in stages] == list(KEYS)
    assert [stage["skipped"] for stage in stages] == [False] * 5 + [True, True]
    assert stages[1]["start"] - stages[0]["start"] == pytest.approx(0.5)
    assert stages[1]["text"].startswith("checking 40 frames")
    assert stages[5]["text"] == "Siril made no stack"


def test_stage_names_fall_back_to_numbers_for_another_stage_count() -> None:
    """A job with another number of stages gets numbered stage names."""
    stages = processing_run.stages_from_log(["2026-11-06 10:00:00.200 - INFO - Stage 1 of 9: x…"], KEYS)
    assert stages[0]["key"] == "stage_1"


def test_fallback_stage_keys_match_the_library() -> None:
    """The copy of the stage keys kept in the script is the library's list."""
    from astrometricslib.pipelines.stacking.stage_progress import STACK_STAGES

    assert tuple(stage.key for stage in STACK_STAGES) == processing_run.STAGE_KEYS


def test_each_stage_gets_its_time_and_its_resources() -> None:
    """A stage runs to the next stage line and gets its own resources."""
    stages = processing_run.stages_from_log(JOB_LOG, KEYS)
    job_start = at("2026-11-06 10:00:00.200")
    job_end = at("2026-11-06 10:01:23.700")
    samples = make_samples(job_start, job_end)
    nvidia = [
        {"t": at("2026-11-06 10:00:10.000"), "utilization_percent": 10.0, "memory_mb": 500.0},
        {"t": at("2026-11-06 10:00:15.000"), "utilization_percent": 30.0, "memory_mb": 900.0},
    ]
    result = processing_run.aggregate_stages(stages, job_end, samples, nvidia)
    by_key = {stage["key"]: stage for stage in result}
    assert by_key["choose_frames"]["seconds"] == pytest.approx(0.5)
    assert by_key["screen_frames"]["seconds"] == pytest.approx(20.0)
    assert by_key["stacking"]["seconds"] == pytest.approx(60.0)
    assert by_key["summarize"]["seconds"] == pytest.approx(2.0)
    assert by_key["preview"]["seconds"] == pytest.approx(0.0)
    assert sum(stage["seconds"] for stage in result) == pytest.approx(job_end - job_start)
    stacking = by_key["stacking"]["usage"]
    assert stacking["cpu_s"] == pytest.approx(60.0)
    assert stacking["read_mb"] == pytest.approx(60.0)
    assert stacking["write_mb"] == pytest.approx(120.0)
    assert stacking["peak_rss_mb"] == pytest.approx(300.0)
    assert by_key["screen_frames"]["nvidia"]["util_peak"] == pytest.approx(30.0)
    assert by_key["screen_frames"]["nvidia"]["memory_mb_peak"] == pytest.approx(900.0)
    assert by_key["stacking"]["nvidia"] is None  # no row inside the stage


def test_choose_frames_takes_one_camera_and_leaves_out_spectra() -> None:
    """The frames of the camera are chosen by name part, in path order."""
    files = [
        {"path": "/b.fits", "camera": "ZWO ASI 533MM Pro", "filter": "None"},
        {"path": "/a.fits", "camera": "ZWO ASI 533MM Pro", "filter": "L"},
        {"path": "/c.fits", "camera": "Nikon D5300", "filter": "None"},
        {"path": "/d.fits", "camera": "ZWO ASI 533MM Pro", "filter": "SPEC"},
    ]
    chosen = processing_run.choose_frames(files, "asi 533mm")
    assert [frame["path"] for frame in chosen] == ["/a.fits", "/b.fits"]


def test_plan_counts_frames_missing_files_and_hashes_the_input(tmp_path: Path) -> None:
    """The plan counts missing files and hashes the input paths."""
    present = tmp_path / "a.fits"
    present.write_text("x")
    files = [
        {"path": str(present), "camera": "C", "exposure": "30.0", "date": "2026-10-01 21:00:00"},
        {"path": str(tmp_path / "gone.fits"), "camera": "C", "exposure": "30", "date": "2026-10-02 21:00:00"},
    ]
    plan = processing_run.describe_plan("M 31", "C", files, {"files": files, "stackedImage": "/s.fits"})
    assert plan["frame_count"] == 2
    assert plan["missing_files"] == 1
    assert plan["total_exposure_seconds"] == pytest.approx(60.0)
    assert plan["first_date"] == "2026-10-01 21:00:00"
    assert plan["current_stack"] == "/s.fits"
    again = processing_run.describe_plan("M 31", "C", list(reversed(files)), {"files": files})
    assert len(plan["paths_sha"]) == 12
    assert again["paths_sha"] != plan["paths_sha"]  # the hash follows the order the caller gives


def test_analysis_request_builds_the_rpc_call() -> None:
    """Each step maps to the RPC method the display's button uses."""
    assert processing_run.analysis_request("photometry", "M 31", "/s.fits") == (
        "analysis:analyze_image",
        {"target_id": "M 31", "image_files": ["/s.fits"], "type": "photometry"},
    )
    assert processing_run.analysis_request("stack_comparison", "M 31", None)[0] == "quality:measure_stack"
    assert (
        processing_run.analysis_request("frame_measurements", "M 31", None)[0]
        == "quality:record_frame_measurements"
    )
    with pytest.raises(ValueError, match="needs a stack"):
        processing_run.analysis_request("photometry", "M 31", None)
    with pytest.raises(ValueError, match="unknown"):
        processing_run.analysis_request("nope", "M 31", None)


class FakeBackend:
    """A made-up backend that answers the RPC calls the run makes.

    Parameters
    ----------
    clock : `FakeClock`
        The clock the jobs finish by.
    """

    def __init__(self, clock: FakeClock) -> None:
        """Remember the clock and start with no jobs."""
        self.clock = clock
        self.calls: list[str] = []
        self.stack_done_at = 100.0
        self.step_jobs: dict[str, float] = {}
        self.started_steps: list[tuple[str, float]] = []

    def call(self, port: int, method: str, params: dict, timeout: float = 60.0) -> object:
        """Answer one call.

        Returns
        -------
        result : `object`
            What the real backend would give back for this method.
        """
        self.calls.append(method)
        now = self.clock.now
        if method == "processing:stack":
            return {"status": "started", "jobId": "stack-1"}
        if method == "processing:get_job":
            done = now >= self.stack_done_at
            return {
                "id": "stack-1",
                "status": "completed" if done else "running",
                "message": "Stack finished",
                "completedAt": "2026-11-06 10:01:23.700" if done else None,
            }
        if method == "processing:job_log_tail":
            return JOB_LOG
        if method == "processing:list_jobs":
            jobs = [{"id": "old", "status": "completed", "jobType": "stacking"}]
            for step, start in self.started_steps:
                finished = now >= start + self.step_jobs.get(step, 5.0)
                jobs.append({
                    "id": f"job-{step}",
                    "status": "completed" if finished else "running",
                    "jobType": step,
                })
            return jobs
        self.started_steps.append((method, now))
        return {"status": "started"}


class FakeClock:
    """A clock that only moves when the run sleeps."""

    def __init__(self) -> None:
        """Start at 0 seconds."""
        self.now = 0.0

    def time(self) -> float:
        """Give the fake time.

        Returns
        -------
        seconds : `float`
            Fake seconds.
        """
        return self.now

    def sleep(self, seconds: float) -> None:
        """Move the fake time forward.

        Parameters
        ----------
        seconds : `float`
            How far.
        """
        self.now += seconds


class FakeTracker:
    """A tracker that gives a flat sample at the fake time."""

    def __init__(self, clock: FakeClock) -> None:
        """Remember the clock."""
        self.clock = clock

    def poll(self) -> dict:
        """Give a sample.

        Returns
        -------
        sample : `dict`
            A sample with a growing CPU count.
        """
        return {
            "t": self.clock.now,
            "groups": {"backend": {"cpu_s": self.clock.now, "read_bytes": 0.0, "write_bytes": 0.0}},
            "trees": {"backend": {"cpu_s": self.clock.now, "rss_kb": 1024.0}},
        }


def test_stack_is_polled_until_the_job_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The run polls the job, samples while it waits and reads the log."""
    clock = FakeClock()
    backend = FakeBackend(clock)
    monkeypatch.setattr(processing_run, "call_rpc", backend.call)
    recorder = processing_run.Recorder(FakeTracker(clock), clock=clock.time, sleep=clock.sleep)
    record = processing_run.run_stack(1234, "M 31", [{"path": "/a.fits"}], recorder, timeout=500.0)
    assert record["status"] == "completed"
    assert record["job_id"] == "stack-1"
    assert len(record["log_lines"]) == len(JOB_LOG)
    assert len(recorder.samples) >= 100 / processing_run.POLL_SECONDS
    assert backend.calls[0] == "processing:stack"


def test_stack_times_out_when_the_job_never_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    """A job that does not end within the timeout is reported as a timeout."""
    clock = FakeClock()
    backend = FakeBackend(clock)
    backend.stack_done_at = 1e9
    monkeypatch.setattr(processing_run, "call_rpc", backend.call)
    recorder = processing_run.Recorder(FakeTracker(clock), clock=clock.time, sleep=clock.sleep)
    record = processing_run.run_stack(1234, "M 31", [{"path": "/a.fits"}], recorder, timeout=10.0)
    assert record["status"] == "timeout"


def test_analysis_step_waits_for_the_new_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """A step ends when every job that is new for the target has ended."""
    clock = FakeClock()
    backend = FakeBackend(clock)
    monkeypatch.setattr(processing_run, "call_rpc", backend.call)
    recorder = processing_run.Recorder(FakeTracker(clock), clock=clock.time, sleep=clock.sleep)
    backend.step_jobs["quality:measure_stack"] = 12.0
    record = processing_run.run_analysis_step(
        1234, "M 31", "stack_comparison", "/s.fits", recorder, timeout=100.0
    )
    assert record["status"] == "ok"
    assert record["seconds"] == pytest.approx(12.0, abs=1.0)
    assert [job["id"] for job in record["jobs"]] == ["job-quality:measure_stack"]


def test_report_has_stage_and_step_numbers_and_counts_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """The report ties stages, steps, totals and the failure count."""
    start = at("2026-11-06 10:00:00.200")
    samples = make_samples(start - 5, start + 200)
    stack = {
        "job_id": "stack-1",
        "status": "completed",
        "start": start,
        "end": at("2026-11-06 10:01:23.700"),
        "seconds": 83.5,
        "message": "ok",
        "log_lines": JOB_LOG,
        "log_file": "/x.log",
    }
    step = {
        "step": "photometry",
        "rpc": "analysis:analyze_image",
        "start": start + 90,
        "end": start + 100,
        "seconds": 10.0,
        "status": "ok",
        "jobs": [],
        "reply": {},
    }
    failed = {**step, "step": "frame_measurements", "status": "failed"}
    recorder = processing_run.Recorder(FakeTracker(FakeClock()))
    recorder.samples = samples
    plan = {"frame_count": 40}
    report = processing_run.build_report(plan, stack, [step, failed], recorder, [])
    assert report["total_seconds"] == pytest.approx(103.5)
    assert report["failed_jobs"] == 1
    assert [stage["key"] for stage in report["stack"]["stages"]] == list(KEYS)
    assert report["analysis"][0]["usage"]["cpu_s"] == pytest.approx(10.0)


def test_processing_tpms_come_from_the_report() -> None:
    """Each stage that ran and each step gives TPMs; a skipped one none."""
    start = at("2026-11-06 10:00:00.200")
    samples = make_samples(start - 5, start + 200)
    stack = {
        "job_id": "stack-1",
        "status": "completed",
        "start": start,
        "end": at("2026-11-06 10:01:23.700"),
        "seconds": 83.5,
        "message": "ok",
        "log_lines": JOB_LOG,
    }
    step = {
        "step": "photometry",
        "rpc": "x",
        "start": start + 90,
        "end": start + 100,
        "seconds": 10.0,
        "status": "ok",
        "jobs": [],
    }
    recorder = processing_run.Recorder(FakeTracker(FakeClock()))
    recorder.samples = samples
    report = processing_run.build_report({"frame_count": 40}, stack, [step], recorder, [])
    entries = {item["name"]: item for item in compile_tpm.processing_entries(report)}
    assert entries["processing_stage_seconds[stacking]"]["value"] == pytest.approx(60.0)
    assert entries["processing_stage_cpu_seconds[stacking]"]["value"] == pytest.approx(60.0)
    assert entries["processing_stage_nvidia_util_percent_mean[stacking]"]["value"] is None
    assert "processing_stage_seconds[preview]" not in entries  # skipped
    assert entries["analysis_seconds[photometry]"]["value"] == pytest.approx(10.0)
    assert entries["processing_total_seconds"]["value"] == pytest.approx(93.5)
    assert entries["processing_frame_count"]["value"] == pytest.approx(40.0)
    assert entries["processing_failed_jobs"]["value"] == pytest.approx(0.0)
    catalog = compile_tpm.load_catalog()
    for name in entries:
        assert compile_tpm.catalog_match(catalog, name) is not None, name


def test_setting_edit_replaces_adds_and_checks() -> None:
    """A setting is replaced, added to its section or added with it."""
    replaced = processing_settings.set_value(
        '["Processing.Siril"]\nx = "1"\nskip_unchanged_stacks_enabled = "true"\n'
        'y = "2"\n["Other"]\nz = "3"\n',
        "Processing.Siril",
        "skip_unchanged_stacks_enabled",
        "false",
    )
    assert tomllib.loads(replaced)["Processing.Siril"] == {
        "x": "1",
        "skip_unchanged_stacks_enabled": "false",
        "y": "2",
    }
    assert tomllib.loads(replaced)["Other"] == {"z": "3"}
    added = processing_settings.set_value('["Processing.Siril"]\nx = "1"\n', "Processing.Siril", "k", "v")
    assert tomllib.loads(added)["Processing.Siril"] == {"k": "v", "x": "1"}
    created = processing_settings.set_value('["Image Library"]\npath = "p"\n', "Processing.Siril", "k", "v")
    assert tomllib.loads(created)["Processing.Siril"] == {"k": "v"}


def test_synthetic_settings_copy_the_program_commands_only(tmp_path: Path) -> None:
    """The synthetic mode copies only the program commands."""
    config = tmp_path / "astrometrics.config.toml"
    config.write_text('["Image Library"]\npath = "p"\n')
    real = {
        "Processing.Siril": {"siril_executable": "flatpak run org.siril.Siril", "stack_weight": "wfwhm"},
        "Processing.GraXpert": {"graxpert_executable": "/x/graxpert"},
        "Processing.CosmicClarity": {"denoise_executable": "/x/cc", "denoise_enabled": "false"},
        "Observatory.Telescope": {"hostname": "10.0.0.5"},
    }
    applied = processing_settings.apply(config, "synthetic", real)
    parsed = tomllib.loads(config.read_text())
    assert parsed["Processing.Siril"] == {
        "skip_unchanged_stacks_enabled": "false",
        "siril_executable": "flatpak run org.siril.Siril",
    }
    assert parsed["Processing.Astrometry.Local Solver"] == {"index_path": "/nonexistent-tpm-index"}
    assert "Observatory.Telescope" not in parsed
    assert applied["Processing.Siril.skip_unchanged_stacks_enabled"] == "false"
    config.write_text('["Processing.Siril"]\nskip_unchanged_stacks_enabled = "true"\n')
    processing_settings.apply(config, "real-snapshot")
    assert tomllib.loads(config.read_text())["Processing.Siril"]["skip_unchanged_stacks_enabled"] == "false"


def test_setup_key_changes_with_a_tool_upgrade_only_for_processing() -> None:
    """A tool upgrade changes a processing key and leaves a flow key."""
    facts = {
        "cpu_model": "cpu",
        "displays": ["1920x1080 @60"],
        "ac_online": True,
        "power_profile": "balanced",
    }
    flow_key = setup_fingerprint.make_setup_key(facts, "nvidia", "synthetic", "offline")
    assert (
        setup_fingerprint.make_setup_key(
            facts, "nvidia", "synthetic", "offline", False, "flow", {"siril": "1"}
        )
        == flow_key
    )
    old = setup_fingerprint.make_setup_key(
        facts, "none", "synthetic", "offline", False, "processing", {"siril": "1.4.4"}
    )
    new = setup_fingerprint.make_setup_key(
        facts, "none", "synthetic", "offline", False, "processing", {"siril": "1.4.5"}
    )
    assert old != new
    assert old != flow_key


def test_tool_versions_read_the_program_names_from_the_settings(tmp_path: Path) -> None:
    """Versions come from the programs the settings name."""
    script = tmp_path / "tool.sh"
    script.write_text("#!/bin/sh\necho 'tool version 3.2.1'\n")
    script.chmod(0o755)
    config = tmp_path / "c.toml"
    config.write_text(f'["Processing.Siril"]\nsiril_executable = "{script}"\n')
    versions = setup_fingerprint.tool_versions(str(config))
    assert versions["siril"] == "tool version 3.2.1"
    assert versions["graxpert"] == "not set"
    assert setup_fingerprint.tool_versions(None)["siril"] == "not set"


def test_real_library_check_reports_a_changed_file(tmp_path: Path) -> None:
    """The comparison lists added, removed and changed files."""
    import real_snapshot

    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    before.write_text('{"files": {"/a": [1, 1], "/b": [2, 2]}, "databases": {"/db": [5, 5]}}')
    after.write_text('{"files": {"/a": [1, 1], "/b": [2, 3], "/c": [1, 1]}, "databases": {"/db": [6, 6]}}')
    out = tmp_path / "report.json"
    assert real_snapshot.verify(before, after, out) == 1
    report = out.read_text()
    assert '"changed": [\n      "/b"' in report
    assert '"added": [\n      "/c"' in report
    after.write_text('{"files": {"/a": [1, 1], "/b": [2, 2]}, "databases": {"/db": [6, 6]}}')
    assert real_snapshot.verify(before, after, out) == 1


def test_real_library_check_ignores_only_the_shared_memory_file(tmp_path: Path) -> None:
    """A changed ``-shm`` file is information; a changed database fails."""
    import real_snapshot

    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    out = tmp_path / "report.json"
    before.write_text('{"files": {}, "databases": {"/db": [5, 5], "/db-shm": [1, 1]}}')
    after.write_text('{"files": {}, "databases": {"/db": [5, 5], "/db-shm": [1, 2]}}')
    assert real_snapshot.verify(before, after, out) == 0
    after.write_text('{"files": {}, "databases": {"/db": [5, 5], "/db-wal": [1, 2], "/db-shm": [1, 1]}}')
    assert real_snapshot.verify(before, after, out) == 1
