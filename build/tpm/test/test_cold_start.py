"""Purpose: Tests for the cold-start tools of the TPM harness.

Description: Covers the choice of the files whose cache is dropped
(`cold_cache.file_groups`), the drop itself, the residency readers, the cold
flag in the setup key, the quiet-computer check (`preflight.find_problems`,
with ``pgrep`` and ``ss`` replaced by stand-ins), the cold-start catalog
entries and the rule that a warm run gets no limit for a cold-only TPM.

Run with ``.venv/bin/pytest build/tpm/test -q``.
"""

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

TPM_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TPM_DIR))

import cold_cache  # ruff: ignore[module-import-not-at-top-of-file]
import compile_tpm  # ruff: ignore[module-import-not-at-top-of-file]
import preflight  # ruff: ignore[module-import-not-at-top-of-file]
from setup_fingerprint import make_setup_key  # ruff: ignore[module-import-not-at-top-of-file]


def _write(path: Path, size: int = 10) -> Path:
    """Make a file with some bytes, creating its folders.

    Parameters
    ----------
    path : `pathlib.Path`
        The file to make.
    size : `int`, optional
        Number of bytes.

    Returns
    -------
    path : `pathlib.Path`
        The same path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


@pytest.fixture
def world(tmp_path: Path) -> dict:
    """Build a small fake throwaway folder and repository.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        Pytest's temporary folder.

    Returns
    -------
    world : `dict`
        ``sandbox`` and ``repo`` paths, and the ``overlay_file`` that must
        be skipped.
    """
    sandbox, repo = tmp_path / "sandbox", tmp_path / "repo"
    _write(sandbox / "library" / "astrometrics.db", 100)
    _write(sandbox / "library" / "astrometrics.db-wal", 10)
    _write(sandbox / "library" / "frames" / "a.fits", 50)
    _write(sandbox / "wayfinding" / "wayfinding.db", 20)
    _write(sandbox / "xdg_cache" / "astrometrics" / "catalogs" / "catalog_cache.db", 200)
    _write(sandbox / "dist" / "index.html", 5)
    overlay_file = _write(sandbox / "overlay" / "mnt" / "huge.fits", 999)
    _write(repo / "node_modules" / "electron" / "dist" / "electron", 30)
    _write(repo / ".venv" / "lib" / "python3.14" / "site-packages" / "numpy" / "core.so", 40)
    _write(repo / "astrometricslib" / "x.py", 3)
    _write(repo / "wayfindinglib" / "y.py", 3)
    _write(repo / "backend" / "z.py", 3)
    return {"sandbox": sandbox, "repo": repo, "overlay_file": overlay_file}


def test_file_groups_select_what_the_app_reads(world: dict) -> None:
    """Each file kind lands in its group; the overlay mount is not walked."""
    groups = cold_cache.file_groups(world["sandbox"], world["repo"])
    names = {name: {path.name for path in files} for name, files in groups.items()}
    assert names["sandbox_databases"] == {"astrometrics.db", "astrometrics.db-wal", "wayfinding.db"}
    assert names["sandbox_library"] == {"a.fits"}
    assert names["catalog_caches"] == {"catalog_cache.db"}
    assert names["ui_build"] == {"index.html"}
    assert names["electron"] == {"electron"}
    assert names["python_site_packages"] == {"core.so"}
    assert names["repo_packages"] == {"x.py", "y.py", "z.py"}
    every_file = {path for files in groups.values() for path in files}
    assert world["overlay_file"] not in every_file


def test_a_file_is_in_one_group_only(world: dict) -> None:
    """A hard link in two places counts once."""
    link = world["sandbox"] / "dist" / "copy.db"
    os.link(world["sandbox"] / "library" / "astrometrics.db", link)
    groups = cold_cache.file_groups(world["sandbox"], world["repo"])
    total = sum(
        1 for files in groups.values() for path in files if path.name in {"astrometrics.db", "copy.db"}
    )
    assert total == 1


def test_real_library_picks_previews_and_stacks_only(tmp_path: Path) -> None:
    """Pictures and FITS files named 'stack' are taken; raw frames are not."""
    folder = tmp_path / "stacks"
    for name in ("m31/preview.jpg", "m31/m31_stack.fits", "m31/light_0001.fits", "m31/notes.txt"):
        _write(folder / name)
    picked = {path.name for path in cold_cache.real_library_files(folder)}
    assert picked == {"preview.jpg", "m31_stack.fits"}


def test_browser_in_a_shared_folder_is_taken_alone(world: dict, tmp_path: Path) -> None:
    """A program in /usr/bin brings only itself, not the folder."""
    program = _write(tmp_path / "brave" / "brave", 5)
    _write(tmp_path / "brave" / "locales" / "en.pak", 5)
    groups = cold_cache.file_groups(world["sandbox"], world["repo"], browser=program)
    assert {path.name for path in groups["browser"]} == {"brave", "en.pak"}
    shared = Path("/bin/sh")
    if shared.exists():
        groups = cold_cache.file_groups(world["sandbox"], world["repo"], browser=shared)
        assert len(groups["browser"]) == 1


def test_drop_files_counts_files_bytes_and_errors(tmp_path: Path) -> None:
    """The drop reports what it touched; a missing file counts as an error."""
    good = _write(tmp_path / "good.bin", 123)
    counts = cold_cache.drop_files([good, tmp_path / "missing.bin"], tmp_path)
    assert counts["files"] == 1
    assert counts["bytes"] == 123
    assert counts["errors"] == 1


def test_drop_file_calls_fadvise_dontneed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The kernel forgets the whole file; the sandbox is flushed first."""
    calls = []
    monkeypatch.setattr(
        os, "posix_fadvise", lambda fd, offset, length, advice: calls.append((offset, length, advice))
    )
    synced = []
    monkeypatch.setattr(os, "fsync", lambda fd: synced.append(fd))
    path = _write(tmp_path / "f.bin")
    cold_cache.drop_file(path, flush=True)
    cold_cache.drop_file(path, flush=False)
    assert calls == [(0, 0, os.POSIX_FADV_DONTNEED)] * 2
    assert len(synced) == 1


def test_sample_is_stable_and_bounded(tmp_path: Path) -> None:
    """The sample is at most the asked size, has the largest file, repeats."""
    files = [_write(tmp_path / f"f{index:03d}.bin", index + 1) for index in range(100)]
    sample = cold_cache.sample_paths(files, 10)
    assert len(sample) <= 10
    assert files[-1] in sample
    assert sample == cold_cache.sample_paths(files, 10)


def test_parse_fincore_and_vmtouch() -> None:
    """Both residency programs give resident and total bytes."""
    fincore = json.dumps({
        "fincore": [{"res": 4096, "size": 8192, "file": "a"}, {"res": 0, "size": 4096, "file": "b"}]
    })
    assert cold_cache.parse_fincore(fincore) == (4096, 12288)
    assert cold_cache.parse_vmtouch("  Resident Pages: 12/34  48K/136K  35.3%\n") == (12 * 4096, 34 * 4096)


def test_drop_report_measures_before_and_after(world: dict) -> None:
    """The report holds the resident share before and after the drop."""
    states = iter([4096, 0])

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess:
        """Answer like fincore: resident bytes from the next state.

        Returns
        -------
        result : `subprocess.CompletedProcess`
            The finished program.
        """
        rows = [{"res": next(states), "size": 8192, "file": name} for name in command[3:4]]
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"fincore": rows}), stderr="")

    groups = {"one": [world["sandbox"] / "dist" / "index.html"]}
    report = cold_cache.drop_report(groups, world["sandbox"], "fincore", fake_run)
    group = report["groups"]["one"]
    assert group["resident_before"]["percent"] == pytest.approx(50.0)
    assert group["resident_after"]["percent"] == pytest.approx(0.0)
    assert report["files"] == 1
    assert cold_cache.overall_residency(report) == {"before": 50.0, "after": 0.0}


def test_no_residency_tool_gives_no_percent(world: dict) -> None:
    """Without fincore or vmtouch the drop works and percentages are `None`."""
    groups = {"one": [world["sandbox"] / "dist" / "index.html"]}
    report = cold_cache.drop_report(groups, world["sandbox"], None)
    assert report["groups"]["one"]["resident_before"]["percent"] is None
    assert report["files"] == 1


def test_setup_key_separates_cold_from_warm() -> None:
    """The cold flag changes the key; leaving it out keeps the warm key."""
    fingerprint = {
        "cpu_model": "cpu",
        "displays": ["eDP-1 1920x1080 @60Hz"],
        "ac_online": True,
        "power_profile": "balanced",
    }
    warm = make_setup_key(fingerprint, "nvidia", "synthetic", "offline")
    assert make_setup_key(fingerprint, "nvidia", "synthetic", "offline", False) == warm
    assert make_setup_key(fingerprint, "nvidia", "synthetic", "offline", True) != warm


def _fake_runner(processes: dict[str, str], listening: str) -> Callable[[list[str]], str | None]:
    """Make a stand-in for ``preflight.run_command``.

    Parameters
    ----------
    processes : `dict` [`str`, `str`]
        For each ``pgrep -f`` pattern, the output it gives.
    listening : `str`
        The output of ``ss``.

    Returns
    -------
    runner : callable
        A function that answers like the real one.
    """

    def runner(command: list[str]) -> str | None:
        """Answer like pgrep or ss.

        Returns
        -------
        output : `str`
            The program's output.
        """
        if command[0] == "pgrep":
            return processes.get(command[2], "")
        return listening

    return runner


def test_preflight_passes_on_a_quiet_computer() -> None:
    """No upload, no backup and nothing on port 5000 gives no problem."""
    assert preflight.find_problems(5000, _fake_runner({}, "")) == []


def test_preflight_names_each_problem() -> None:
    """An upload, a backup and the real backend each say what to stop."""
    runner = _fake_runner(
        {"rclone (copy|sync)": "4242\n", "backup_library_databases": "777\n"},
        "LISTEN 0 2048 127.0.0.1:5000 0.0.0.0:*\n",
    )
    problems = preflight.find_problems(5000, runner)
    assert len(problems) == 3
    assert "rclone" in problems[0] and "4242" in problems[0]
    assert "backup" in problems[1] and "777" in problems[1]
    assert "port 5000" in problems[2]


def test_preflight_ignores_its_own_process() -> None:
    """The check's own process id never counts as a running upload."""
    runner = _fake_runner({"rclone (copy|sync)": f"{os.getpid()}\n"}, "")
    assert preflight.find_problems(5000, runner) == []


def test_preflight_refuses_when_a_tool_is_missing() -> None:
    """A check that cannot be made is a problem, not a pass."""
    problems = preflight.find_problems(5000, lambda command: None)
    assert len(problems) == 3


def test_cold_start_catalog_entries() -> None:
    """The cold-start TPMs are TBR and cold-only; the idle TPM is TBD."""
    catalog = compile_tpm.load_catalog()
    by_id = {definition["id"]: definition for definition in catalog["tpms"]}
    expected = {
        "time_to_app_ready_cold": (10000, 5000),
        "first_display_open_by_display": (3000, 1000),
        "first_rpc": (5000, 1000),
    }
    for identifier, (threshold, objective) in expected.items():
        definition = by_id[identifier]
        assert definition["threshold_status"] == "TBR"
        assert (definition["threshold"], definition["objective"]) == (threshold, objective)
        assert definition["source"] == "proposal 2026-11-06"
        assert definition["cold_only"] is True
    idle = by_id["planetarium_idle_draws_per_second"]
    assert idle["threshold_status"] == "TBD"
    assert idle["threshold"] is None


def test_warm_run_gets_no_limit_for_a_cold_only_tpm() -> None:
    """A warm run measures the cold TPM without a limit; a cold run has one."""
    catalog = compile_tpm.load_catalog()
    for cold, expect_limit in ((False, False), (True, True)):
        tpms = [{"name": "time_to_app_ready_cold", "unit": "ms", "value": 20000.0, "n": 1}]
        compile_tpm.annotate(tpms, catalog, {}, cold)
        assert tpms[0]["cold"] is cold
        assert (tpms[0]["limit"]["threshold"] is not None) is expect_limit
        assert tpms[0]["limit"]["threshold_status"] == "TBR"


def test_first_lap_values_come_from_lap_one() -> None:
    """first_display_open is the first visit; first_rpc the first call."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_tpm_catalog import _fake_flow

    displays = compile_tpm.DISPLAYS
    flow = _fake_flow()
    flow["switches"] = [
        {"lap": lap, "display": name, "settledMs": 100.0 * lap + index, "timedOut": False}
        for lap in (1, 2)
        for index, name in enumerate(displays)
    ]
    flow["rpc"] = [
        {"method": method, "phase": "load", "ms": ms}
        for ms in (40.0, 5.0)
        for method in compile_tpm.TRACKED_RPC_METHODS
    ]
    backend = {"backend_peak_rss_mb": 100.0, "backend_cpu_seconds": 1.0}
    by_name = {tpm["name"]: tpm for tpm in compile_tpm.build_tpms(flow, backend, None)}
    assert by_name["time_to_app_ready_cold"]["value"] == pytest.approx(1000.0)
    for index, name in enumerate(displays):
        assert by_name[f"first_display_open[{name}]"]["value"] == pytest.approx(100.0 + index)
    for method in compile_tpm.TRACKED_RPC_METHODS:
        assert by_name[f"first_rpc[{method}]"]["value"] == pytest.approx(40.0)
