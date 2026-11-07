"""Purpose: Tests for the process-tree resource sampler of the TPM harness.

Description: Builds a fake ``/proc`` folder (stat, status, io and cmdline
files) in a temporary folder and checks that ``resource_usage.py`` reads the
files correctly, sorts processes into groups, follows descendants, keeps the
numbers of children that end between two samples and cuts a time window out of
the samples.

Run with ``.venv/bin/pytest build/tpm/test/test_resource_usage.py -q``.
"""

import sys
from pathlib import Path

import pytest

TPM_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TPM_DIR))

import resource_usage  # ruff: ignore[module-import-not-at-top-of-file]

#: Clock ticks per second used by the fake files.
TICKS = 100


def write_process(
    proc: Path,
    pid: int,
    parent: int,
    name: str = "python",
    command: str = "python",
    own: int = 0,
    children: int = 0,
    start: int | None = None,
    rss_kb: int = 1000,
    peak_kb: int | None = None,
    read_bytes: int = 0,
    write_bytes: int = 0,
) -> None:
    """Write the fake files of one process.

    Parameters
    ----------
    proc : `pathlib.Path`
        The fake ``/proc`` folder.
    pid, parent : `int`
        The process and its parent.
    name, command : `str`
        The short name and the command line.
    own, children : `int`
        CPU ticks of the process and of its collected children.
    start : `int`, optional
        Start time in ticks; the pid by default.
    rss_kb, peak_kb : `int`
        Resident memory and its peak.
    read_bytes, write_bytes : `int`
        The disk counters.
    """
    folder = proc / str(pid)
    folder.mkdir(parents=True, exist_ok=True)
    start = pid if start is None else start
    # Fields after the name: state, ppid, then zeros up to utime (index 11),
    # stime, cutime, cstime and starttime (19).
    fields = ["S", str(parent)] + ["0"] * 9 + [str(own), "0", str(children), "0"] + ["0"] * 4 + [str(start)]
    (folder / "stat").write_text(f"{pid} ({name}) " + " ".join(fields) + " 0 0\n")
    (folder / "status").write_text(f"Name:\t{name}\nVmHWM:\t{peak_kb or rss_kb} kB\nVmRSS:\t{rss_kb} kB\n")
    (folder / "io").write_text(
        f"rchar: {read_bytes * 2}\nwchar: {write_bytes * 2}\n"
        f"read_bytes: {read_bytes}\nwrite_bytes: {write_bytes}\n"
        "cancelled_write_bytes: 0\n"
    )
    (folder / "cmdline").write_bytes(command.replace(" ", "\0").encode() + b"\0")


def remove_process(proc: Path, pid: int) -> None:
    """Delete a fake process, as if it had ended and been collected.

    Parameters
    ----------
    proc : `pathlib.Path`
        The fake ``/proc`` folder.
    pid : `int`
        The process to remove.
    """
    folder = proc / str(pid)
    for item in folder.iterdir():
        item.unlink()
    folder.rmdir()


def make_tracker(proc: Path, **roots: int) -> resource_usage.ProcessTreeTracker:
    """Make a tracker on a fake ``/proc`` with a counting clock.

    Parameters
    ----------
    proc : `pathlib.Path`
        The fake ``/proc`` folder.
    **roots : `int`
        Group name to root pid.

    Returns
    -------
    tracker : `resource_usage.ProcessTreeTracker`
        A tracker whose clock gives 1.0, 2.0, 3.0 ...
    """
    counter = iter(range(1, 1000))
    return resource_usage.ProcessTreeTracker(
        roots, proc_root=proc, clock=lambda: float(next(counter)), ticks_per_second=TICKS
    )


def test_parse_stat_handles_spaces_and_brackets_in_the_name() -> None:
    """The command name may hold spaces and a closing bracket."""
    fields = ["S", "7"] + ["0"] * 9 + ["150", "50", "30", "20"] + ["0"] * 4 + ["999"]
    parsed = resource_usage.parse_stat("42 (a (b) c) " + " ".join(fields), TICKS)
    assert parsed["name"] == "a (b) c"
    assert parsed["parent_pid"] == 7
    assert parsed["own_cpu_s"] == pytest.approx(2.0)
    assert parsed["children_cpu_s"] == pytest.approx(0.5)
    assert parsed["start_ticks"] == 999


def test_parse_io_and_status() -> None:
    """The io counters and the status memory lines are read by name."""
    counters = resource_usage.parse_io("rchar: 10\nread_bytes: 4096\nwrite_bytes: 8192\nbad line\n")
    assert counters == {"rchar": 10.0, "read_bytes": 4096.0, "write_bytes": 8192.0}
    assert resource_usage.parse_key_value_kb("VmRSS:\t  500 kB\n", "VmRSS") == pytest.approx(500.0)
    assert resource_usage.parse_key_value_kb("VmRSS:\t  500 kB\n", "VmHWM") is None


def test_read_process_reads_every_file(tmp_path: Path) -> None:
    """One process gives its CPU, memory, disk and command line."""
    write_process(
        tmp_path,
        10,
        1,
        command="python -m backend.main_backend",
        own=250,
        children=100,
        rss_kb=2048,
        peak_kb=4096,
        read_bytes=1000,
        write_bytes=2000,
    )
    record = resource_usage.read_process(10, tmp_path, TICKS)
    assert record is not None
    assert (record.own_cpu_s, record.children_cpu_s) == (2.5, 1.0)
    assert (record.rss_kb, record.peak_rss_kb) == (2048.0, 4096.0)
    assert (record.read_bytes, record.write_bytes) == (1000.0, 2000.0)
    assert record.command == "python -m backend.main_backend"
    assert resource_usage.read_process(11, tmp_path, TICKS) is None


def test_read_process_survives_an_unreadable_io_file(tmp_path: Path) -> None:
    """A process whose io file cannot be read keeps zero disk counters."""
    write_process(tmp_path, 10, 1)
    (tmp_path / "10" / "io").unlink()
    record = resource_usage.read_process(10, tmp_path, TICKS)
    assert record is not None
    assert record.read_bytes == pytest.approx(0.0)


@pytest.mark.parametrize(
    ("name", "command", "expected"),
    [
        ("flatpak", "flatpak run org.siril.Siril -s script.ssf", "siril"),
        ("siril-cli", "siril-cli", "siril"),
        ("GraXpert", "/home/x/Applications/GraXpert-linux/GraXpert --cli", "graxpert"),
        ("SetiAstroCosmi", "/x/SetiAstroCosmicClarity_denoise", "denoise_tools"),
        ("indiserver", "indiserver indi_simulator_ccd", "indi"),
        ("python", "python -c from multiprocessing.spawn import spawn_main", "backend_workers"),
        ("python", "python -m backend.main_backend", "backend_workers"),
        ("bash", "bash -c ls", None),
    ],
)
def test_classify_process(name: str, command: str, expected: str | None) -> None:
    """Programs are sorted into groups by name."""
    result = resource_usage.classify_process(name, command, "python -m backend.main_backend")
    assert result == expected


def test_tracker_adds_up_descendants_by_group(tmp_path: Path) -> None:
    """The tree under the root is found and each process gets a group."""
    write_process(
        tmp_path, 100, 1, command="python -m backend.main_backend", own=100, rss_kb=1000, read_bytes=10
    )
    write_process(
        tmp_path, 101, 100, command="python -m backend.main_backend", own=50, rss_kb=500, read_bytes=20
    )
    write_process(
        tmp_path,
        102,
        100,
        name="flatpak",
        command="flatpak run org.siril.Siril",
        own=30,
        rss_kb=300,
        write_bytes=40,
    )
    write_process(
        tmp_path, 103, 102, name="bwrap", command="bwrap --args 40", own=20, rss_kb=200, write_bytes=60
    )
    write_process(tmp_path, 900, 1, own=999, rss_kb=99999)  # not in the tree
    sample = make_tracker(tmp_path, backend=100).poll()
    groups = sample["groups"]
    assert set(groups) == {"backend", "backend_workers", "siril"}
    assert groups["backend"]["read_bytes"] == pytest.approx(10.0)
    assert groups["backend_workers"]["read_bytes"] == pytest.approx(20.0)
    # bwrap has no rule of its own, so it takes its parent's group (siril).
    assert groups["siril"]["write_bytes"] == pytest.approx(100.0)
    assert sample["trees"]["backend"]["processes"] == 4
    assert sample["trees"]["backend"]["rss_kb"] == pytest.approx(2000.0)
    assert sample["trees"]["backend"]["cpu_s"] == pytest.approx(2.0)


def test_exited_child_keeps_its_disk_and_its_cpu_in_the_totals(tmp_path: Path) -> None:
    """A child that ends between samples is still counted."""
    write_process(tmp_path, 100, 1, command="python -m backend.main_backend", own=100)
    write_process(tmp_path, 102, 100, name="siril", command="siril-cli", own=300, read_bytes=5 * 2**20)
    tracker = make_tracker(tmp_path, backend=100)
    first = tracker.poll()
    assert first["trees"]["backend"]["cpu_s"] == pytest.approx(4.0)
    # The child runs 100 more ticks, ends, and the parent collects it: its 400
    # ticks are now in the parent's cutime.
    remove_process(tmp_path, 102)
    write_process(tmp_path, 100, 1, command="python -m backend.main_backend", own=110, children=400)
    second = tracker.poll()
    assert second["trees"]["backend"]["cpu_s"] == pytest.approx(
        5.1
    )  # 1.1 own + 4.0 collected, no loss, no double count
    assert second["groups"]["siril"]["read_bytes"] == 5 * 2**20  # the last values seen stay
    assert second["groups"]["siril"]["processes"] == 0
    assert second["groups"]["siril"]["seen"] == 1


def test_orphaned_child_that_ends_is_added_from_the_ledger(tmp_path: Path) -> None:
    """An orphan that ends is added from its last values."""
    write_process(tmp_path, 100, 1, command="python -m backend.main_backend", own=100)
    write_process(tmp_path, 102, 100, name="siril", command="siril-cli", own=300)
    tracker = make_tracker(tmp_path, backend=100)
    tracker.poll()
    # The parent in the tree ends the child's tracking: the child now has
    # parent 1 (init), still running.
    write_process(tmp_path, 102, 1, name="siril", command="siril-cli", own=350)
    during = tracker.poll()
    assert during["trees"]["backend"]["processes"] == 2  # still followed, same pid and start time
    remove_process(tmp_path, 102)
    after = tracker.poll()
    assert after["trees"]["backend"]["cpu_s"] == pytest.approx(1.0 + 3.5)


def test_reused_pid_is_not_the_old_process(tmp_path: Path) -> None:
    """A reused pid with another start time is a new process."""
    write_process(tmp_path, 100, 1, command="python -m backend.main_backend")
    write_process(tmp_path, 102, 100, name="siril", command="siril-cli", start=500, read_bytes=1000)
    tracker = make_tracker(tmp_path, backend=100)
    tracker.poll()
    remove_process(tmp_path, 102)
    write_process(tmp_path, 102, 100, name="siril", command="siril-cli", start=900, read_bytes=300)
    sample = tracker.poll()
    assert sample["groups"]["siril"]["seen"] == 2
    assert sample["groups"]["siril"]["read_bytes"] == pytest.approx(1300.0)


def test_peak_rss_is_the_largest_sampled_sum(tmp_path: Path) -> None:
    """The tree peak is the largest sampled sum, not the last."""
    write_process(tmp_path, 100, 1, command="python -m backend.main_backend", rss_kb=1000, peak_kb=1000)
    write_process(tmp_path, 102, 100, name="siril", command="siril-cli", rss_kb=3000, peak_kb=3500)
    tracker = make_tracker(tmp_path, backend=100)
    assert tracker.poll()["trees"]["backend"]["peak_rss_kb"] == pytest.approx(4000.0)
    remove_process(tmp_path, 102)
    last = tracker.poll()
    assert last["trees"]["backend"]["rss_kb"] == pytest.approx(1000.0)
    assert last["trees"]["backend"]["peak_rss_kb"] == pytest.approx(4000.0)
    assert last["trees"]["backend"]["peak_sum_kb"] == pytest.approx(4500.0)


def test_second_root_is_one_group(tmp_path: Path) -> None:
    """A watched root such as Electron is one group."""
    write_process(tmp_path, 100, 1, command="python -m backend.main_backend")
    write_process(tmp_path, 200, 1, name="electron", command="electron .", write_bytes=500)
    write_process(tmp_path, 201, 200, name="electron", command="electron --type=renderer", write_bytes=700)
    sample = make_tracker(tmp_path, backend=100, electron=200).poll()
    assert sample["groups"]["electron"]["write_bytes"] == pytest.approx(1200.0)
    assert sample["trees"]["electron"]["processes"] == 2


def test_window_usage_interpolates_and_finds_the_peak(tmp_path: Path) -> None:
    """A window gets the growth of the cumulative counters."""
    samples = [
        {
            "t": 0.0,
            "groups": {"backend": {"cpu_s": 0.0, "read_bytes": 0.0, "write_bytes": 0.0}},
            "trees": {"backend": {"cpu_s": 0.0, "rss_kb": 1024.0}},
        },
        {
            "t": 10.0,
            "groups": {"backend": {"cpu_s": 10.0, "read_bytes": 10 * 2**20, "write_bytes": 0.0}},
            "trees": {"backend": {"cpu_s": 10.0, "rss_kb": 4096.0}},
        },
        {
            "t": 20.0,
            "groups": {"backend": {"cpu_s": 12.0, "read_bytes": 10 * 2**20, "write_bytes": 6 * 2**20}},
            "trees": {"backend": {"cpu_s": 12.0, "rss_kb": 2048.0}},
        },
    ]
    usage = resource_usage.window_usage(samples, 5.0, 15.0)
    assert usage["seconds"] == pytest.approx(10.0)
    assert usage["cpu_s"] == pytest.approx(6.0)  # 5 s at 1 s/s, then 5 s at 0.2 s/s
    assert usage["read_mb"] == pytest.approx(5.0)
    assert usage["write_mb"] == pytest.approx(3.0)
    assert usage["peak_rss_mb"] == pytest.approx(4.0)
    assert resource_usage.window_usage([], 0.0, 1.0)["peak_rss_mb"] is None


def test_summarize_run_counts_from_the_first_sample() -> None:
    """Run totals are last minus first; the peak is the largest sample."""
    group = {
        "cpu_s": 1.0,
        "rss_kb": 2048.0,
        "read_bytes": 2.0**20,
        "write_bytes": 0.0,
        "read_chars": 0.0,
        "write_chars": 0.0,
    }
    later = {**group, "cpu_s": 4.0, "read_bytes": 5 * 2.0**20, "write_bytes": 2**20, "rss_kb": 1024.0}
    samples = [
        {
            "t": 0.0,
            "groups": {"backend": group},
            "trees": {"backend": {"cpu_s": 1.0, "rss_kb": 2048.0, "peak_sum_kb": 3000.0}},
        },
        {
            "t": 5.0,
            "groups": {"backend": later},
            "trees": {"backend": {"cpu_s": 4.0, "rss_kb": 1024.0, "peak_sum_kb": 3500.0}},
        },
    ]
    summary = resource_usage.summarize_run(samples)
    assert summary["groups"]["backend"]["read_mb"] == pytest.approx(4.0)
    assert summary["groups"]["backend"]["write_mb"] == pytest.approx(1.0)
    assert summary["trees"]["backend"]["cpu_s"] == pytest.approx(3.0)
    assert summary["trees"]["backend"]["peak_rss_mb"] == pytest.approx(2.0)
    assert summary["trees"]["backend"]["peak_sum_mb"] == pytest.approx(3500.0 / 1024.0)
    assert resource_usage.summarize_run([])["sample_count"] == 0


def test_sampler_refuses_a_process_that_is_not_the_throwaway_backend() -> None:
    """The sampler refuses port 5000, the real backend."""
    import argparse

    arguments = argparse.Namespace(backend_pid=1, backend_port=5000, watch_dir=None, out="/dev/null")
    assert resource_usage.sample_command(arguments) == 2
