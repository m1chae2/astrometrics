"""Record how hard the computer works during a long run, to a CSV file.

Every few seconds the script writes one row: CPU use, memory and swap use,
disk reads and writes, CPU and disk temperatures, the GPU's load, memory and
temperature, and the memory and CPU used by Siril and by the Python worker
processes. A batch run's slow spots, memory pressure or overheating can then
be matched to the targets it was working on at that time.

It stops on Ctrl+C or SIGTERM (for example ``systemctl --user stop``).

Run::

    .venv/bin/python build/linux/record_performance.py
    .venv/bin/python build/linux/record_performance.py --interval 5 \
        --output run.csv
"""

import argparse
import csv
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

# Process groups whose total memory and CPU are recorded. A process belongs to
# the first group whose name pattern appears in its command line.
PROCESS_GROUPS = {
    "siril": ("siril",),
    "python": ("python",),
}

COLUMNS = [
    "time",
    "cpu_percent",
    "cpu_busiest_core_percent",
    "load_1min",
    "memory_used_gb",
    "memory_available_gb",
    "swap_used_gb",
    "disk_read_mb_per_s",
    "disk_write_mb_per_s",
    "cpu_temperature_c",
    "disk_temperature_c",
    "gpu_percent",
    "gpu_memory_used_mb",
    "gpu_temperature_c",
    "siril_processes",
    "siril_memory_gb",
    "siril_cpu_percent",
    "python_processes",
    "python_memory_gb",
    "python_cpu_percent",
]

GIGABYTE = 1024**3
MEGABYTE = 1024**2


def default_output_path() -> Path:
    """Give the default CSV path, in the user's log folder.

    Returns
    -------
    output_path : `pathlib.Path`
        ``~/.local/state/astrometrics/logs/performance_<YYYYmmdd_HHMMSS>.csv``
        (under ``$XDG_STATE_HOME`` when that is set).
    """
    state_root = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(state_root) / "astrometrics" / "logs" / f"performance_{stamp}.csv"


def whole_disk_names() -> list[str]:
    """Name the physical disks, leaving out partitions and loop devices.

    Returns
    -------
    disk_names : `list` [`str`]
        Names such as ``nvme0n1`` or ``sda``. Counting only whole disks keeps
        a partition's traffic from being counted twice.
    """
    names = []
    for name in psutil.disk_io_counters(perdisk=True):
        if name.startswith(("loop", "ram", "zram", "dm-")):
            continue
        if Path(f"/sys/block/{name}").exists():
            names.append(name)
    return names


def read_temperatures() -> tuple[float | None, float | None]:
    """Read the hottest CPU sensor and the disk temperature.

    Returns
    -------
    cpu_temperature : `float` or `None`
        Hottest ``coretemp`` (Intel) or ``k10temp`` (AMD) reading, in °C.
    disk_temperature : `float` or `None`
        The NVMe drive's composite reading, in °C.
    """
    sensors = psutil.sensors_temperatures() if hasattr(psutil, "sensors_temperatures") else {}
    cpu_readings = [entry.current for key in ("coretemp", "k10temp") for entry in sensors.get(key, [])]
    disk_readings = [entry.current for entry in sensors.get("nvme", [])]
    cpu_temperature = max(cpu_readings) if cpu_readings else None
    disk_temperature = disk_readings[0] if disk_readings else None
    return cpu_temperature, disk_temperature


def read_gpu() -> tuple[float | None, float | None, float | None]:
    """Read the NVIDIA GPU's load, memory and temperature.

    Returns
    -------
    gpu_readings : `tuple` [`float` or `None`, ...]
        Load in percent, memory used in MB, and temperature in °C. All
        `None` when ``nvidia-smi`` is missing or fails.
    """
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return None, None, None
    try:
        output = subprocess.run(
            [
                executable,
                "--query-gpu=utilization.gpu,memory.used,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout
        load, memory, temperature = (float(value) for value in output.splitlines()[0].split(","))
        return load, memory, temperature
    except OSError, subprocess.SubprocessError, ValueError, IndexError:
        return None, None, None


def summarize_process_groups(own_process_id: int) -> dict[str, Any]:
    """Add up memory and CPU for each process group.

    Parameters
    ----------
    own_process_id : `int`
        This script's process id, left out of the Python group.

    Returns
    -------
    group_columns : `dict` [`str`, `Any`]
        For each group: ``<group>_processes``, ``<group>_memory_gb`` (resident
        memory) and ``<group>_cpu_percent`` (100 = one full core).
    """
    totals = {group: [0, 0.0, 0.0] for group in PROCESS_GROUPS}
    for process in psutil.process_iter(["pid", "cmdline", "memory_info"]):
        if process.info["pid"] == own_process_id:
            continue
        command_line = " ".join(process.info["cmdline"] or []).lower()
        for group, patterns in PROCESS_GROUPS.items():
            if any(pattern in command_line for pattern in patterns):
                try:
                    cpu_percent = process.cpu_percent(None)
                except psutil.NoSuchProcess, psutil.AccessDenied:
                    break
                memory = process.info["memory_info"]
                totals[group][0] += 1
                totals[group][1] += (memory.rss if memory else 0) / GIGABYTE
                totals[group][2] += cpu_percent
                break
    columns: dict[str, Any] = {}
    for group, (count, memory_gb, cpu_percent) in totals.items():
        columns[f"{group}_processes"] = count
        columns[f"{group}_memory_gb"] = round(memory_gb, 2)
        columns[f"{group}_cpu_percent"] = round(cpu_percent, 1)
    return columns


def main(argv: list[str] | None = None) -> int:
    """Write one row per interval until stopped.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments. Default: ``sys.argv[1:]``.

    Returns
    -------
    exit_code : `int`
        0 once stopped.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--interval", type=float, default=10.0, help="Seconds between rows (default 10).")
    parser.add_argument("--output", type=Path, default=None, help="CSV file to write.")
    arguments = parser.parse_args(argv)

    output_path = arguments.output or default_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    stop_requested = False

    def request_stop(signal_number: int, frame: Any) -> None:
        """Finish the current row, then stop."""
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    disks = whole_disk_names()
    own_process_id = os.getpid()
    # The first CPU readings only start the measurement window.
    psutil.cpu_percent(None)
    psutil.cpu_percent(None, percpu=True)
    summarize_process_groups(own_process_id)
    previous_disk = psutil.disk_io_counters(perdisk=True)
    previous_time = time.monotonic()
    print(f"Recording to {output_path} every {arguments.interval:g} s", flush=True)

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        while not stop_requested:
            time.sleep(arguments.interval)
            now = time.monotonic()
            elapsed = max(now - previous_time, 1e-6)
            disk = psutil.disk_io_counters(perdisk=True)
            read_bytes = sum(
                disk[name].read_bytes - previous_disk[name].read_bytes for name in disks if name in disk
            )
            write_bytes = sum(
                disk[name].write_bytes - previous_disk[name].write_bytes for name in disks if name in disk
            )
            previous_disk, previous_time = disk, now

            memory = psutil.virtual_memory()
            cpu_temperature, disk_temperature = read_temperatures()
            gpu_percent, gpu_memory, gpu_temperature = read_gpu()
            row = {
                "time": datetime.now().isoformat(timespec="seconds"),
                "cpu_percent": psutil.cpu_percent(None),
                "cpu_busiest_core_percent": max(psutil.cpu_percent(None, percpu=True)),
                "load_1min": round(os.getloadavg()[0], 2),
                "memory_used_gb": round((memory.total - memory.available) / GIGABYTE, 2),
                "memory_available_gb": round(memory.available / GIGABYTE, 2),
                "swap_used_gb": round(psutil.swap_memory().used / GIGABYTE, 2),
                "disk_read_mb_per_s": round(read_bytes / MEGABYTE / elapsed, 1),
                "disk_write_mb_per_s": round(write_bytes / MEGABYTE / elapsed, 1),
                "cpu_temperature_c": cpu_temperature,
                "disk_temperature_c": disk_temperature,
                "gpu_percent": gpu_percent,
                "gpu_memory_used_mb": gpu_memory,
                "gpu_temperature_c": gpu_temperature,
            }
            row.update(summarize_process_groups(own_process_id))
            writer.writerow(row)
            handle.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
