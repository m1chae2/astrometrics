"""Purpose: Measure CPU, memory and disk use of a process and all its children.

Description: The backend starts many other programs: pool workers, the INDI
worker, Siril, GraXpert and the denoise tools. One process's numbers do not
show what the app costs, so this module follows the whole tree under a root
process and adds the numbers up per process group. It reads only ``/proc``.

What it reads for each process:

* ``stat``: the parent, the start time (to tell a reused process number from
  the old one), the CPU time the process used (``utime`` plus ``stime``) and
  the CPU time of its finished and collected children (``cutime`` plus
  ``cstime``).
* ``status``: the resident memory now (``VmRSS``) and its peak (``VmHWM``).
* ``io``: bytes the process asked the disk layer to read and write
  (``read_bytes``, ``write_bytes``) and bytes it passed to read and write
  calls (``rchar``, ``wchar``, which include the file cache and pipes, so they
  are information only).
* ``cmdline`` and ``comm``: the name, to sort a process into a group.

Process groups under the backend: ``backend`` (the root), ``backend_workers``
(pool workers), ``indi`` (the INDI server and drivers), ``siril``,
``graxpert``, ``denoise_tools`` (Cosmic Clarity, StarNet) and
``backend_other``. A process the rules do not recognize takes its parent's
group. Another root (the Electron app, the browser) is one group of its own.

How a process that ends between two samples is counted. The disk and CPU
counters are cumulative, so a ledger keeps the last values seen for every
process. A finished process stays in the ledger, and its last values stay in
its group's sum. The disk bytes it moved after the last sample are lost, so
the disk numbers of a very short program are a lower bound. For the whole
tree's CPU time the kernel gives a better answer: a parent's ``cutime`` and
``cstime`` already include its collected children (and theirs, all the way
down). So the tree CPU time is the sum over the live processes of their own
time plus their children's time. A finished process whose parent was not in
the tree any more (it was handed to ``init``) is added from the ledger.

Memory is sampled, not integrated: the tree's peak is the largest sum of
resident memory over the samples. A peak shorter than the sampling interval
can be missed. ``peak_sum_kb`` (the sum of each process's own ``VmHWM``) is an
upper bound that does not miss it, but it adds peaks that happened at
different times.

Two uses. ``ProcessTreeTracker`` is the reader (the processing scenario
polls it in its own process). ``sample`` is a command that appends one JSON
line per second to a file, for the whole run; ``run_tpm.sh`` starts it.

Safety: it only reads ``/proc`` of the processes it is told about. The
backend root must have been started with the throwaway port, and port 5000 is
refused (the same guard as ``rpc_timings.py``).

Usage::

    .venv/bin/python build/tpm/resource_usage.py sample --backend-pid 123 \
        --backend-port 47123 --watch-dir results/<run> --out samples.jsonl
"""

import argparse
import itertools
import json
import os
import signal
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rpc_timings import REAL_BACKEND_PORT, read_process_port

#: The root of the process file system. Tests point it at a fake tree.
PROC_ROOT = Path("/proc")
#: Seconds between two samples of the background sampler.
SAMPLE_SECONDS = 1.0
#: Roots that appear later, named by a file ``<group>.pid`` in the watch
#: folder.
WATCHED_ROOTS = ("electron", "browser")
#: The group of the backend root.
BACKEND_GROUP = "backend"
#: Bytes in a megabyte, for the disk TPMs.
BYTES_PER_MB = 1024.0 * 1024.0


def clock_ticks_per_second() -> int:
    """Give the kernel's clock ticks per second (CPU times are in ticks).

    Returns
    -------
    ticks : `int`
        Usually 100.
    """
    return int(os.sysconf("SC_CLK_TCK"))


@dataclass
class ProcessRecord:
    """What one read of one process gives.

    Attributes
    ----------
    pid : `int`
        The process number.
    start_ticks : `int`
        When the process started, in ticks after boot. With the pid it names
        the process for good, even if the number is reused.
    parent_pid : `int`
        The parent's process number.
    name : `str`
        The short command name (``comm``).
    command : `str`
        The command line, with spaces between the words.
    own_cpu_s : `float`
        CPU seconds the process itself used.
    children_cpu_s : `float`
        CPU seconds of its finished, collected children.
    rss_kb : `float`
        Resident memory now.
    peak_rss_kb : `float`
        The largest resident memory the process has had.
    read_bytes, write_bytes : `float`
        Bytes the process caused to be read from and written to storage.
    read_chars, write_chars : `float`
        Bytes passed to read and write calls (information only).
    """

    pid: int
    start_ticks: int
    parent_pid: int
    name: str = ""
    command: str = ""
    own_cpu_s: float = 0.0
    children_cpu_s: float = 0.0
    rss_kb: float = 0.0
    peak_rss_kb: float = 0.0
    read_bytes: float = 0.0
    write_bytes: float = 0.0
    read_chars: float = 0.0
    write_chars: float = 0.0

    @property
    def key(self) -> tuple[int, int]:
        """Name the process for the ledger.

        Returns
        -------
        key : `tuple` [`int`, `int`]
            The pid and the start time.
        """
        return (self.pid, self.start_ticks)


def parse_stat(text: str, ticks_per_second: int) -> dict[str, float | int | str]:
    """Pick the fields we need out of a ``/proc/<pid>/stat`` line.

    The command name is in parentheses and may hold spaces and parentheses,
    so the fields are counted from the last closing parenthesis.

    Parameters
    ----------
    text : `str`
        The file's text.
    ticks_per_second : `int`
        Clock ticks per second.

    Returns
    -------
    fields : `dict`
        ``name``, ``parent_pid``, ``own_cpu_s``, ``children_cpu_s`` and
        ``start_ticks``.

    Raises
    ------
    ValueError
        If the text is not a stat line.
    """
    open_index = text.find("(")
    close_index = text.rfind(")")
    if open_index < 0 or close_index < open_index:
        raise ValueError("not a stat line")
    rest = text[close_index + 1 :].split()
    # Field numbers from proc(5) minus 3: state is 0, ppid 1, utime 11, stime
    # 12, cutime 13, cstime 14, starttime 19.
    return {
        "name": text[open_index + 1 : close_index],
        "parent_pid": int(rest[1]),
        "own_cpu_s": (int(rest[11]) + int(rest[12])) / ticks_per_second,
        "children_cpu_s": (int(rest[13]) + int(rest[14])) / ticks_per_second,
        "start_ticks": int(rest[19]),
    }


def parse_key_value_kb(text: str, key: str) -> float | None:
    """Read one ``Key:   123 kB`` line of ``status``.

    Parameters
    ----------
    text : `str`
        The file's text.
    key : `str`
        The key, for example ``VmRSS``.

    Returns
    -------
    value : `float` or `None`
        The number (in kB), or `None` if the line is missing.
    """
    for line in text.splitlines():
        if line.startswith(f"{key}:"):
            parts = line.split()
            if len(parts) >= 2:
                return float(parts[1])
    return None


def parse_io(text: str) -> dict[str, float]:
    """Read the counters of ``/proc/<pid>/io``.

    Parameters
    ----------
    text : `str`
        The file's text, lines such as ``read_bytes: 4096``.

    Returns
    -------
    counters : `dict` [`str`, `float`]
        The numbers by name (``rchar``, ``wchar``, ``read_bytes``,
        ``write_bytes`` and the others the kernel gives).
    """
    counters = {}
    for line in text.splitlines():
        name, separator, value = line.partition(":")
        if separator and value.strip().lstrip("-").isdigit():
            counters[name.strip()] = float(value)
    return counters


def read_process(
    pid: int, proc_root: Path = PROC_ROOT, ticks_per_second: int | None = None
) -> ProcessRecord | None:
    """Read one process.

    Parameters
    ----------
    pid : `int`
        The process number.
    proc_root : `pathlib.Path`, optional
        The ``/proc`` folder (a fake one in tests).
    ticks_per_second : `int`, optional
        Clock ticks per second; the kernel's value by default.

    Returns
    -------
    record : `ProcessRecord` or `None`
        What was read, or `None` if the process is gone. A file that cannot be
        read (no permission for ``io``) leaves its numbers at zero.
    """
    ticks = ticks_per_second or clock_ticks_per_second()
    folder = proc_root / str(pid)
    try:
        stat = parse_stat((folder / "stat").read_text(), ticks)
    except OSError, ValueError, IndexError:
        return None
    record = ProcessRecord(
        pid=pid,
        start_ticks=int(stat["start_ticks"]),
        parent_pid=int(stat["parent_pid"]),
        name=str(stat["name"]),
        own_cpu_s=float(stat["own_cpu_s"]),
        children_cpu_s=float(stat["children_cpu_s"]),
    )
    try:
        status = (folder / "status").read_text()
        record.rss_kb = parse_key_value_kb(status, "VmRSS") or 0.0
        record.peak_rss_kb = parse_key_value_kb(status, "VmHWM") or 0.0
    except OSError:
        pass
    try:
        io = parse_io((folder / "io").read_text())
        record.read_bytes = io.get("read_bytes", 0.0)
        record.write_bytes = io.get("write_bytes", 0.0)
        record.read_chars = io.get("rchar", 0.0)
        record.write_chars = io.get("wchar", 0.0)
    except OSError:
        pass
    try:
        record.command = (
            (folder / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
        )
    except OSError:
        pass
    return record


def list_parents(
    proc_root: Path = PROC_ROOT, ticks_per_second: int | None = None
) -> dict[int, tuple[int, int]]:
    """Map every process to its parent and start time, in one pass.

    Parameters
    ----------
    proc_root : `pathlib.Path`, optional
        The ``/proc`` folder.
    ticks_per_second : `int`, optional
        Clock ticks per second.

    Returns
    -------
    processes : `dict` [`int`, `tuple` [`int`, `int`]]
        For each pid: the parent's pid and the start time in ticks.
    """
    ticks = ticks_per_second or clock_ticks_per_second()
    processes = {}
    try:
        names = os.listdir(proc_root)
    except OSError:
        return processes
    for name in names:
        if not name.isdigit():
            continue
        try:
            stat = parse_stat((proc_root / name / "stat").read_text(), ticks)
        except OSError, ValueError, IndexError:
            continue
        processes[int(name)] = (int(stat["parent_pid"]), int(stat["start_ticks"]))
    return processes


def classify_process(name: str, command: str, root_command: str = "") -> str | None:
    """Sort a process of the backend's tree into a group by its name.

    Parameters
    ----------
    name : `str`
        The short command name.
    command : `str`
        The command line.
    root_command : `str`, optional
        The backend's own command line. A forked pool worker has the same one.

    Returns
    -------
    group : `str` or `None`
        ``siril``, ``graxpert``, ``denoise_tools``, ``indi`` or
        ``backend_workers``; `None` if the rules do not recognize it (it then
        takes its parent's group).
    """
    lowered = f"{name} {command}".lower()
    if "siril" in lowered:
        return "siril"
    if "graxpert" in lowered:
        return "graxpert"
    if any(
        word in lowered for word in ("cosmicclarity", "cosmic_clarity", "setiastro", "starnet", "denoise")
    ):
        return "denoise_tools"
    if name.lower().startswith("indi"):
        return "indi"
    if "multiprocessing" in lowered or "loky" in lowered or (root_command and command == root_command):
        return "backend_workers"
    return None


@dataclass
class _LedgerEntry:
    """What the ledger keeps for one process it has seen."""

    group: str
    root_group: str
    record: ProcessRecord
    alive: bool = True


@dataclass
class _Root:
    """One process the tracker follows the tree of."""

    group: str
    pid: int
    start_ticks: int | None = None
    command: str = ""
    orphan_cpu_s: float = 0.0
    peak_rss_kb: float = 0.0
    peak_sum_kb: float = 0.0


@dataclass
class ProcessTreeTracker:
    """Follow the trees under some root processes and keep a ledger.

    Parameters
    ----------
    roots : `dict` [`str`, `int`], optional
        Group name to root pid. The backend's group is ``backend``; any other
        root is a single group.
    proc_root : `pathlib.Path`, optional
        The ``/proc`` folder (a fake one in tests).
    clock : `Callable`, optional
        Gives the time in seconds since the epoch.
    ticks_per_second : `int`, optional
        Clock ticks per second; the kernel's value by default.
    """

    roots: dict[str, int] = field(default_factory=dict)
    proc_root: Path = PROC_ROOT
    clock: Callable[[], float] = time.time
    ticks_per_second: int | None = None

    def __post_init__(self) -> None:
        """Set up the ledger and the roots."""
        self._ledger: dict[tuple[int, int], _LedgerEntry] = {}
        self._roots: dict[str, _Root] = {}
        for group, pid in self.roots.items():
            self.add_root(group, pid)

    def add_root(self, group: str, pid: int) -> None:
        """Start following a new root process.

        Parameters
        ----------
        group : `str`
            The group name (``backend``, ``electron``, ``browser``).
        pid : `int`
            The root's process number.
        """
        self._roots.setdefault(group, _Root(group=group, pid=pid))

    def root_alive(self) -> bool:
        """Say whether any followed root is still running.

        Returns
        -------
        alive : `bool`
            `True` if at least one root process exists.
        """
        return any(
            read_process(root.pid, self.proc_root, self.ticks_per_second) is not None
            for root in self._roots.values()
        )

    def _live_members(self, root: _Root, parents: dict[int, tuple[int, int]]) -> list[int]:
        """Find the pids of the live tree under a root.

        Parameters
        ----------
        root : `_Root`
            The root.
        parents : `dict`
            The result of `list_parents`.

        Returns
        -------
        pids : `list` [`int`]
            The root and its descendants, plus processes this root's earlier
            samples saw that were handed to another parent since (same start
            time, so the same process).
        """
        if root.pid not in parents:
            return []
        children: dict[int, list[int]] = {}
        for pid, (parent_pid, _start) in parents.items():
            children.setdefault(parent_pid, []).append(pid)
        members = []
        queue = [root.pid]
        while queue:
            pid = queue.pop()
            members.append(pid)
            queue.extend(children.get(pid, []))
        reached = set(members)
        for (pid, start_ticks), entry in self._ledger.items():
            if (
                entry.alive
                and entry.root_group == root.group
                and pid not in reached
                and parents.get(pid, (None, None))[1] == start_ticks
            ):
                members.append(pid)
        return members

    def poll(self) -> dict:
        """Read every followed tree once and update the ledger.

        Returns
        -------
        sample : `dict`
            ``t`` (seconds since the epoch), ``groups`` (for each group:
            ``cpu_s``, ``rss_kb``, ``read_bytes``, ``write_bytes``,
            ``read_chars``, ``write_chars``, ``processes`` now alive and
            ``seen`` since the start) and ``trees`` (for each root: ``cpu_s``
            the whole tree's CPU time, ``rss_kb``, ``peak_rss_kb`` the largest
            sum so far, ``peak_sum_kb``, ``processes``).
        """
        ticks = self.ticks_per_second or clock_ticks_per_second()
        parents = list_parents(self.proc_root, ticks)
        live_keys: set[tuple[int, int]] = set()
        live_by_root: dict[str, list[ProcessRecord]] = {}
        for root in self._roots.values():
            records = []
            for pid in self._live_members(root, parents):
                record = read_process(pid, self.proc_root, ticks)
                if record is None:
                    continue
                if pid == root.pid:
                    if root.start_ticks is None:
                        root.start_ticks = record.start_ticks
                        root.command = record.command
                    elif root.start_ticks != record.start_ticks:
                        continue
                records.append(record)
            live_by_root[root.group] = records
            live_keys.update(record.key for record in records)
        for root in self._roots.values():
            for record in live_by_root[root.group]:
                self._update_ledger(root, record)
        live_pids = {key[0] for key in live_keys}
        for key, entry in self._ledger.items():
            if entry.alive and key not in live_keys:
                entry.alive = False
                if entry.record.parent_pid not in live_pids:
                    # Nobody in the tree collected it, so its CPU time is in no
                    # live parent's total.
                    self._roots[entry.root_group].orphan_cpu_s += (
                        entry.record.own_cpu_s + entry.record.children_cpu_s
                    )
        return self._build_sample(live_by_root)

    def _update_ledger(self, root: _Root, record: ProcessRecord) -> None:
        """Put a process's latest values into the ledger.

        Parameters
        ----------
        root : `_Root`
            The tree it belongs to.
        record : `ProcessRecord`
            What was just read.
        """
        entry = self._ledger.get(record.key)
        if entry is not None:
            entry.record = record
            return
        if root.group != BACKEND_GROUP:
            group = root.group
        elif record.pid == root.pid:
            group = BACKEND_GROUP
        else:
            parent = next(
                (item for (pid, _s), item in self._ledger.items() if pid == record.parent_pid and item.alive),
                None,
            )
            group = (
                classify_process(record.name, record.command, root.command)
                or (parent.group if parent is not None and parent.group != BACKEND_GROUP else None)
                or "backend_other"
            )
        self._ledger[record.key] = _LedgerEntry(group=group, root_group=root.group, record=record)

    def _build_sample(self, live_by_root: dict[str, list[ProcessRecord]]) -> dict:
        """Add the ledger up into one sample.

        Parameters
        ----------
        live_by_root : `dict`
            The live records of each root, from this poll.

        Returns
        -------
        sample : `dict`
            See `poll`.
        """
        groups: dict[str, dict[str, float]] = {}
        for entry in self._ledger.values():
            totals = groups.setdefault(
                entry.group,
                {
                    "cpu_s": 0.0,
                    "rss_kb": 0.0,
                    "read_bytes": 0.0,
                    "write_bytes": 0.0,
                    "read_chars": 0.0,
                    "write_chars": 0.0,
                    "processes": 0,
                    "seen": 0,
                },
            )
            record = entry.record
            totals["cpu_s"] += record.own_cpu_s
            totals["read_bytes"] += record.read_bytes
            totals["write_bytes"] += record.write_bytes
            totals["read_chars"] += record.read_chars
            totals["write_chars"] += record.write_chars
            totals["seen"] += 1
            if entry.alive:
                totals["rss_kb"] += record.rss_kb
                totals["processes"] += 1
        trees = {}
        for root in self._roots.values():
            records = live_by_root.get(root.group, [])
            rss = sum(record.rss_kb for record in records)
            peak_sum = sum(record.peak_rss_kb for record in records)
            root.peak_rss_kb = max(root.peak_rss_kb, rss)
            root.peak_sum_kb = max(root.peak_sum_kb, peak_sum)
            cpu = sum(record.own_cpu_s + record.children_cpu_s for record in records) + root.orphan_cpu_s
            trees[root.group] = {
                "cpu_s": cpu,
                "rss_kb": rss,
                "peak_rss_kb": root.peak_rss_kb,
                "peak_sum_kb": root.peak_sum_kb,
                "processes": len(records),
            }
        return {"t": self.clock(), "groups": groups, "trees": trees}


def interpolate(samples: list[dict], when: float, read: Callable[[dict], float]) -> float:
    """Give a cumulative value at a time, by straight lines between samples.

    Parameters
    ----------
    samples : `list` [`dict`]
        Samples with ``t`` in time order.
    when : `float`
        The time.
    read : `Callable`
        Picks the cumulative value out of a sample.

    Returns
    -------
    value : `float`
        The value; the first sample's before the first and the last sample's
        after the last. 0.0 with no samples.
    """
    if not samples:
        return 0.0
    if when <= samples[0]["t"]:
        return read(samples[0])
    if when >= samples[-1]["t"]:
        return read(samples[-1])
    for earlier, later in itertools.pairwise(samples):
        if earlier["t"] <= when <= later["t"]:
            span = later["t"] - earlier["t"]
            if span <= 0:
                return read(later)
            fraction = (when - earlier["t"]) / span
            return read(earlier) + (read(later) - read(earlier)) * fraction
    return read(samples[-1])


def group_field(group: str, name: str) -> Callable[[dict], float]:
    """Make a reader for one field of one group.

    Parameters
    ----------
    group : `str`
        The process group.
    name : `str`
        The field (``read_bytes``, ``cpu_s``, ...).

    Returns
    -------
    read : `Callable`
        Reads that field out of a sample, 0.0 if the group is absent.
    """
    return lambda sample: float(sample["groups"].get(group, {}).get(name, 0.0))


def tree_field(root: str, name: str) -> Callable[[dict], float]:
    """Make a reader for one field of one root's tree.

    Parameters
    ----------
    root : `str`
        The root group (``backend``).
    name : `str`
        The field (``cpu_s``, ``rss_kb``).

    Returns
    -------
    read : `Callable`
        Reads that field out of a sample, 0.0 if the root is absent.
    """
    return lambda sample: float(sample["trees"].get(root, {}).get(name, 0.0))


def window_usage(samples: list[dict], start: float, end: float, root: str = BACKEND_GROUP) -> dict:
    """Add up what a time window used.

    Parameters
    ----------
    samples : `list` [`dict`]
        Samples from `ProcessTreeTracker.poll`, in time order.
    start, end : `float`
        The window, seconds since the epoch.
    root : `str`, optional
        The tree whose CPU and memory to report.

    Returns
    -------
    usage : `dict`
        ``seconds``, ``cpu_s`` (the tree's CPU time in the window),
        ``peak_rss_mb`` (the largest tree memory seen in the window,
        including the samples just before and after it, `None` with no
        samples), ``read_mb`` and ``write_mb`` (disk bytes, all groups of the
        root's tree) and ``groups`` (for each group: ``cpu_s``, ``read_mb``,
        ``write_mb``).
    """
    names = sorted({name for sample in samples for name in sample["groups"]})
    groups = {}
    for name in names:
        groups[name] = {
            "cpu_s": _delta(samples, start, end, group_field(name, "cpu_s")),
            "read_mb": _delta(samples, start, end, group_field(name, "read_bytes")) / BYTES_PER_MB,
            "write_mb": _delta(samples, start, end, group_field(name, "write_bytes")) / BYTES_PER_MB,
        }
    inside = [
        sample["trees"].get(root, {}).get("rss_kb", 0.0)
        for sample in samples
        if start <= sample["t"] <= end and root in sample["trees"]
    ]
    before = [sample for sample in samples if sample["t"] < start]
    after = [sample for sample in samples if sample["t"] > end]
    for neighbour in before[-1:] + after[:1]:
        if root in neighbour["trees"]:
            inside.append(neighbour["trees"][root]["rss_kb"])
    return {
        "seconds": end - start,
        "cpu_s": _delta(samples, start, end, tree_field(root, "cpu_s")),
        "peak_rss_mb": max(inside) / 1024.0 if inside else None,
        "read_mb": sum(item["read_mb"] for item in groups.values()),
        "write_mb": sum(item["write_mb"] for item in groups.values()),
        "groups": groups,
    }


def _delta(samples: list[dict], start: float, end: float, read: Callable[[dict], float]) -> float:
    """Give how much a cumulative value grew in a window.

    Parameters
    ----------
    samples : `list` [`dict`]
        Samples in time order.
    start, end : `float`
        The window.
    read : `Callable`
        Picks the cumulative value out of a sample.

    Returns
    -------
    growth : `float`
        The value at the end minus the value at the start (never negative).
    """
    return max(0.0, interpolate(samples, end, read) - interpolate(samples, start, read))


def summarize_run(samples: list[dict]) -> dict:
    """Reduce the sampler's whole file to per-group totals for the run.

    Parameters
    ----------
    samples : `list` [`dict`]
        The samples in time order.

    Returns
    -------
    summary : `dict`
        ``sample_count``, ``seconds``, ``groups`` (``read_mb``, ``write_mb``,
        ``read_chars_mb``, ``write_chars_mb``, ``cpu_s``, ``peak_rss_mb`` for
        each group) and ``trees`` (``cpu_s``, ``peak_rss_mb``, ``peak_sum_mb``
        for each root). Counters count from the first sample.
    """
    if not samples:
        return {"sample_count": 0, "seconds": 0.0, "groups": {}, "trees": {}}
    first, last = samples[0], samples[-1]
    names = sorted({name for sample in samples for name in sample["groups"]})
    groups = {}
    for name in names:

        def difference(field_name: str, name: str = name) -> float:
            """Give last minus first for one field of one group.

            Returns
            -------
            growth : `float`
                The growth, never negative.
            """
            return max(0.0, group_field(name, field_name)(last) - group_field(name, field_name)(first))

        peak_rss = max(sample["groups"].get(name, {}).get("rss_kb", 0.0) for sample in samples)
        groups[name] = {
            "read_mb": difference("read_bytes") / BYTES_PER_MB,
            "write_mb": difference("write_bytes") / BYTES_PER_MB,
            "read_chars_mb": difference("read_chars") / BYTES_PER_MB,
            "write_chars_mb": difference("write_chars") / BYTES_PER_MB,
            "cpu_s": difference("cpu_s"),
            "peak_rss_mb": peak_rss / 1024.0,
        }
    roots = sorted({name for sample in samples for name in sample["trees"]})
    trees = {}
    for name in roots:
        trees[name] = {
            "cpu_s": max(0.0, tree_field(name, "cpu_s")(last) - tree_field(name, "cpu_s")(first)),
            "peak_rss_mb": max(sample["trees"].get(name, {}).get("rss_kb", 0.0) for sample in samples)
            / 1024.0,
            "peak_sum_mb": max(sample["trees"].get(name, {}).get("peak_sum_kb", 0.0) for sample in samples)
            / 1024.0,
        }
    return {
        "sample_count": len(samples),
        "seconds": last["t"] - first["t"],
        "groups": groups,
        "trees": trees,
    }


def read_samples(path: Path) -> list[dict]:
    """Read a samples file.

    Parameters
    ----------
    path : `pathlib.Path`
        The JSON-lines file from the sampler.

    Returns
    -------
    samples : `list` [`dict`]
        The samples, or an empty list if the file does not exist.
    """
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


_running = True


def _stop(_signal_number: int, _frame: object) -> None:
    """Ask the sampling loop to finish."""
    global _running
    _running = False


def sample_command(arguments: argparse.Namespace) -> int:
    """Append one sample a second until the roots end or SIGTERM comes.

    Parameters
    ----------
    arguments : `argparse.Namespace`
        ``backend_pid``, ``backend_port``, ``watch_dir`` and ``out``.

    Returns
    -------
    status : `int`
        0 on success, 2 if the backend pid is not the throwaway backend.
    """
    if (
        arguments.backend_port == REAL_BACKEND_PORT
        or read_process_port(arguments.backend_pid) != arguments.backend_port
    ):
        print("Refusing to sample: that process is not the throwaway backend of this run.", file=sys.stderr)
        return 2
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    tracker = ProcessTreeTracker({BACKEND_GROUP: arguments.backend_pid})
    watch = Path(arguments.watch_dir) if arguments.watch_dir else None
    with open(arguments.out, "a", encoding="utf-8") as output:
        while _running:
            if watch is not None:
                for group in WATCHED_ROOTS:
                    pid_file = watch / f"{group}.pid"
                    if group not in tracker._roots and pid_file.exists():
                        try:
                            tracker.add_root(group, int(pid_file.read_text().strip()))
                        except ValueError:
                            pass
            sample = tracker.poll()
            output.write(json.dumps(sample) + "\n")
            output.flush()
            if not tracker.root_alive():
                break
            time.sleep(SAMPLE_SECONDS)
    return 0


def main() -> int:
    """Run the command named on the command line.

    Returns
    -------
    status : `int`
        The command's exit status.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    sample = commands.add_parser("sample")
    sample.add_argument("--backend-pid", type=int, required=True)
    sample.add_argument("--backend-port", type=int, required=True)
    sample.add_argument("--watch-dir")
    sample.add_argument("--out", required=True)
    arguments = parser.parse_args()
    return sample_command(arguments)


if __name__ == "__main__":
    sys.exit(main())
