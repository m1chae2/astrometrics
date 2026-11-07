"""Purpose: Record the setup a TPM run happens on and guard its conditions.

Description: The same code is slower or faster depending on the computer's
power state, the graphics card the browser draws with and the displays that
are connected. This script reads those facts without root rights and writes
them to a file, so every ``tpm.json`` says what it was measured on. It also
turns the facts into a short ``setup_key``: two runs are compared with the same
baseline only if their keys match.

Three commands::

    setup_fingerprint.py collect --out setup.json [--config settings.toml]
    setup_fingerprint.py check-power <profile>
    setup_fingerprint.py nvidia-sample --out nvidia-samples.jsonl

``collect`` writes the facts that exist before the browser starts. With
``--config`` it also records the versions of the external programs the
settings name (Siril, GraXpert, Cosmic Clarity), for the processing runs. The
graphics card actually used is added later by ``compile_tpm.py``, because only
the page can say it (the WebGL renderer name). ``check-power`` refuses (exit
code 1) unless ``powerprofilesctl get`` prints the profile and the AC adapter
is online. It never changes the profile. ``nvidia-sample`` writes the NVIDIA
card's state every 2 seconds until it is stopped.
"""

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

#: Output names that belong to the laptop's own screen (not a dock).
INTERNAL_OUTPUT_PATTERN = re.compile(r"^(eDP|LVDS|DSI)", re.IGNORECASE)
#: Seconds between two NVIDIA samples.
NVIDIA_SAMPLE_SECONDS = 2.0
#: Seconds to wait for an external tool to print its version (GraXpert loads
#: its libraries first).
TOOL_VERSION_SECONDS = 60.0
#: The external tools a stacking job runs: the tool, the settings section and
#: the key that names its program.
EXTERNAL_TOOLS = (
    ("siril", "Processing.Siril", "siril_executable"),
    ("graxpert", "Processing.GraXpert", "graxpert_executable"),
    ("cosmic_clarity_denoise", "Processing.CosmicClarity", "denoise_executable"),
)


def run_text(command: list[str], timeout: float = 5.0) -> str | None:
    """Run a command and return its output, or `None` if it cannot run.

    Parameters
    ----------
    command : `list` [`str`]
        The command and arguments.
    timeout : `float`, optional
        Seconds to wait.

    Returns
    -------
    output : `str` or `None`
        Standard output with the ends trimmed, or `None` on any failure.
    """
    if shutil.which(command[0]) is None:
        return None
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except OSError, subprocess.SubprocessError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def read_file(path: str) -> str | None:
    """Read a small system file.

    Parameters
    ----------
    path : `str`
        The file.

    Returns
    -------
    text : `str` or `None`
        The trimmed text, or `None` if it cannot be read.
    """
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def cpu_facts() -> dict[str, object]:
    """Read the processor model, core counts and frequency governor.

    Returns
    -------
    facts : `dict`
        ``cpu_model``, ``cpu_logical_cores``, ``cpu_physical_cores`` and
        ``cpu_governor`` (the governors of all cores, joined when they differ).
    """
    model = None
    for line in (read_file("/proc/cpuinfo") or "").splitlines():
        if line.startswith("model name"):
            model = line.split(":", 1)[1].strip()
            break
    physical = None
    table = run_text(["lscpu", "-p=CORE,SOCKET"])
    if table:
        physical = len({line for line in table.splitlines() if not line.startswith("#")})
    governors = sorted({
        read_file(str(path)) or "?"
        for path in Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_governor")
    })
    return {
        "cpu_model": model,
        "cpu_logical_cores": os.cpu_count(),
        "cpu_physical_cores": physical,
        "cpu_governor": "+".join(governors) if governors else None,
    }


def memory_total_gb() -> float | None:
    """Read the installed memory.

    Returns
    -------
    gigabytes : `float` or `None`
        Total RAM in GB, rounded to one decimal.
    """
    for line in (read_file("/proc/meminfo") or "").splitlines():
        if line.startswith("MemTotal"):
            return round(int(line.split()[1]) / 1048576.0, 1)
    return None


def power_facts() -> dict[str, object]:
    """Read whether the computer runs on AC power and its power profile.

    Returns
    -------
    facts : `dict`
        ``ac_online`` (`True`, `False` or `None` if unknown) and
        ``power_profile``.
    """
    online = None
    for supply in Path("/sys/class/power_supply").glob("*"):
        if read_file(str(supply / "type")) == "Mains":
            online = bool(online) or read_file(str(supply / "online")) == "1"
    return {"ac_online": online, "power_profile": run_text(["powerprofilesctl", "get"])}


def display_facts() -> dict[str, object]:
    """Read the connected displays, their resolution and refresh rate.

    The active screens come from ``xrandr`` (works through XWayland, no root
    needed). The connected outputs come from the kernel's ``/sys/class/drm``
    files, which also list an output the desktop has switched off, for
    example the laptop screen with the lid closed.

    Returns
    -------
    facts : `dict`
        ``displays`` (a sorted list of ``name WxH @Hz`` texts of the active
        screens), ``connected_outputs``, ``docked`` (an external output is
        connected) and ``display_source``.
    """
    connected = []
    drm_modes = {}
    for folder in sorted(Path("/sys/class/drm").glob("card*-*")):
        if read_file(str(folder / "status")) == "connected":
            name = folder.name.split("-", 1)[1]
            connected.append(name)
            first_mode = (read_file(str(folder / "modes")) or "").splitlines()[:1]
            drm_modes[name] = first_mode[0] if first_mode else None
    displays = []
    source = "drm"
    text = run_text(["xrandr", "--current"]) if os.environ.get("DISPLAY") else None
    if text:
        name = None
        for line in text.splitlines():
            header = re.match(r"^(\S+) connected(?: primary)? (\d+x\d+)\+", line)
            if header:
                name = header.group(1)
                displays.append([name, header.group(2), None])
            elif line.startswith("   ") and "*" in line and displays and displays[-1][2] is None:
                rate = re.search(r"([\d.]+)\*", line)
                displays[-1][2] = rate.group(1) if rate else None
        if displays:
            source = "xrandr"
    if not displays:
        displays = [[name, mode, None] for name, mode in drm_modes.items() if mode]
    labels = sorted(
        f"{name} {size}" + (f" @{float(rate):.0f}Hz" if rate else "") for name, size, rate in displays
    )
    return {
        "displays": labels,
        "connected_outputs": connected,
        "docked": any(not INTERNAL_OUTPUT_PATTERN.match(name) for name in connected),
        "display_source": source,
    }


def gpu_facts() -> dict[str, object]:
    """Read the graphics cards and the PRIME mode.

    Returns
    -------
    facts : `dict`
        ``gpus_present`` (lspci lines), ``prime_mode`` (`prime-select query`,
        `None` where the tool does not exist) and ``session_type``.
    """
    gpus = []
    for line in (run_text(["lspci"]) or "").splitlines():
        if re.search(r"VGA compatible|3D controller|Display controller", line):
            gpus.append(line.split(" ", 1)[1].strip())
    return {
        "gpus_present": gpus,
        "prime_mode": run_text(["prime-select", "query"]),
        "session_type": os.environ.get("XDG_SESSION_TYPE"),
    }


def collect() -> dict[str, object]:
    """Gather every fact that exists before the browser starts.

    Returns
    -------
    fingerprint : `dict`
        The facts, with the keys the other functions describe, plus ``ram_gb``
        and ``kernel``.
    """
    facts: dict[str, object] = {}
    facts.update(cpu_facts())
    facts["ram_gb"] = memory_total_gb()
    facts.update(power_facts())
    facts["kernel"] = platform.release()
    facts.update(display_facts())
    facts.update(gpu_facts())
    return facts


def program_version(command: str) -> str:
    """Ask an external program for its version, or describe the file.

    Siril and GraXpert print a version for ``--version``. Cosmic Clarity has
    no such option, and starting it would run its model, so it (and a program
    that does not answer) is described by its size and change time instead; a
    changed program then still changes the description.

    Parameters
    ----------
    command : `str`
        The command as the settings give it (a path, or a command with
        arguments such as ``flatpak run org.siril.Siril``).

    Returns
    -------
    version : `str`
        The version line, or ``file <size> bytes, changed <time>``, or
        ``not found``.
    """
    words = shlex.split(command)
    if not words:
        return "not set"
    if "cosmicclarity" not in command.lower() and shutil.which(words[0]):
        try:
            result = subprocess.run(
                [*words, "--version"],
                capture_output=True,
                text=True,
                timeout=TOOL_VERSION_SECONDS,
                check=False,
            )
            text = result.stdout + result.stderr
        except OSError, subprocess.SubprocessError:
            text = ""
        # Flatpak prints notes with an "F:" prefix; GraXpert puts the version
        # in a log line.
        lines = [
            line for line in text.splitlines() if re.search(r"\d+\.\d+", line) and not line.startswith("F:")
        ]
        if lines:
            # GraXpert prefixes a log line: date, time, process, logger, level.
            return re.sub(r"^\d{4}-\d\d-\d\d \S+ \S+ \S+ \w+\s+", "", lines[-1].strip())
    path = Path(words[0])
    if path.exists():
        status = path.stat()
        stamp = time.strftime("%Y-%m-%d", time.gmtime(status.st_mtime))
        return f"file {status.st_size} bytes, changed {stamp}"
    return "not found"


def tool_versions(config_path: str | None) -> dict[str, str]:
    """Record the version of every external tool a stacking job uses.

    A tool upgrade changes the stacking speed and result, so the processing
    scenario puts these in its setup key.

    Parameters
    ----------
    config_path : `str` or `None`
        The run's settings file (it names the programs). `None` or an
        unreadable file gives ``not set`` for every tool.

    Returns
    -------
    versions : `dict` [`str`, `str`]
        For each tool in `EXTERNAL_TOOLS`, its version text.
    """
    sections: dict = {}
    if config_path:
        try:
            sections = tomllib.loads(Path(config_path).read_text(encoding="utf-8"))
        except OSError, tomllib.TOMLDecodeError:
            sections = {}
    return {
        tool: program_version(str(sections.get(section, {}).get(key, "") or ""))
        for tool, section, key in EXTERNAL_TOOLS
    }


def classify_renderer(renderer: str | None) -> str:
    """Name the kind of graphics card a WebGL renderer string shows.

    Parameters
    ----------
    renderer : `str` or `None`
        The unmasked WebGL renderer.

    Returns
    -------
    kind : `str`
        ``nvidia``, ``intel``, ``amd``, ``software`` or ``unknown``.
    """
    text = (renderer or "").lower()
    if re.search(r"swiftshader|llvmpipe|softpipe|software|basic render", text):
        return "software"
    for kind in ("nvidia", "intel", "amd"):
        if kind in text or (kind == "amd" and "radeon" in text):
            return kind
    return "unknown"


def make_setup_key(
    fingerprint: dict,
    gpu_used: str,
    mode: str,
    observatory_profile: str,
    cold: bool = False,
    scenario: str = "flow",
    tools: dict[str, str] | None = None,
    flow_target: str | None = None,
) -> str:
    """Make the short key that decides which baseline a run belongs to.

    Parameters
    ----------
    fingerprint : `dict`
        The result of `collect`.
    gpu_used : `str`
        The kind of graphics card that drew the page (`classify_renderer`).
    mode : `str`
        The library mode: ``simulation`` (a read-only copy of the real
        library, the default), ``synthetic`` (the made-up library) or
        ``real-snapshot``. A different mode is a different key.
    observatory_profile : `str`
        The observatory profile.
    cold : `bool`, optional
        The run started with a cold file cache (``--cold``). A cold and a warm
        run need separate baselines, so this changes the key. The default
        leaves the key of every older (warm) baseline as it was.
    scenario : `str`, optional
        ``flow`` (the display flow) or ``processing`` (a stack and analysis
        run). Only a non-default scenario changes the key, so older keys stay
        as they were.
    tools : `dict` [`str`, `str`], optional
        The external tool versions (`tool_versions`). They enter the key only
        for the ``processing`` scenario, because only it runs those tools: an
        upgrade of Siril or GraXpert then starts a new baseline.
    flow_target : `str`, optional
        What the display flow ran in: ``electron`` (the real app, the default
        target) or the browser's name (``brave`` or ``chrome``). It enters the
        key of the ``flow`` scenario only, so an Electron baseline never mixes
        with a browser one. `None` (an older run) leaves the key as it was, and
        the ``processing`` scenario, which has no page, ignores it.

    Returns
    -------
    key : `str`
        Ten hexadecimal characters. It covers the CPU model, the displays
        with their resolutions, AC power, the power profile, the graphics
        card used, the mode, the observatory profile, the flow target (when
        given) and (only when true) the cold flag.
    """
    parts = {
        "cpu": fingerprint.get("cpu_model"),
        "displays": [label.rsplit(" @", 1)[0] for label in fingerprint.get("displays") or []],
        "ac": fingerprint.get("ac_online"),
        "power_profile": fingerprint.get("power_profile"),
        "gpu_used": gpu_used,
        "mode": mode,
        "observatory_profile": observatory_profile,
    }
    if cold:
        parts["cold"] = True
    if scenario == "flow" and flow_target:
        parts["flow_target"] = flow_target
    if scenario != "flow":
        parts["scenario"] = scenario
        parts["tools"] = tools or {}
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:10]


def check_power(required: str) -> int:
    """Refuse unless the power profile matches and AC power is online.

    Parameters
    ----------
    required : `str`
        The profile name, for example ``performance``.

    Returns
    -------
    status : `int`
        0 if the conditions hold, 1 otherwise (message on standard error).
    """
    facts = power_facts()
    problems = []
    if facts["power_profile"] != required:
        problems.append(f"the power profile is {facts['power_profile']!r}, not {required!r}")
    if facts["ac_online"] is not True:
        problems.append("the computer is not on AC power")
    if problems:
        print("ERROR: " + "; and ".join(problems) + ".", file=sys.stderr)
        print(
            f"Set it yourself with: powerprofilesctl set {required}   (this script never changes it)",
            file=sys.stderr,
        )
        return 1
    return 0


def nvidia_sample(out: Path) -> None:
    """Write the NVIDIA card's state every 2 seconds until stopped.

    Parameters
    ----------
    out : `pathlib.Path`
        The JSON-lines file. Each line has ``t`` (seconds since the epoch),
        ``pstate``, ``utilization_percent`` and ``memory_mb``.
    """
    command = [
        "nvidia-smi",
        "--query-gpu=pstate,utilization.gpu,memory.used",
        "--format=csv,noheader,nounits",
    ]
    with out.open("a") as handle:
        while True:
            text = run_text(command)
            if text:
                pstate, utilization, memory = (item.strip() for item in text.splitlines()[0].split(","))
                row = {
                    "t": time.time(),
                    "pstate": pstate,
                    "utilization_percent": float(utilization),
                    "memory_mb": float(memory),
                }
                handle.write(json.dumps(row) + "\n")
                handle.flush()
            time.sleep(NVIDIA_SAMPLE_SECONDS)


def main() -> int:
    """Run one of the three commands.

    Returns
    -------
    status : `int`
        The exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    collect_parser = commands.add_parser("collect")
    collect_parser.add_argument("--out", required=True)
    collect_parser.add_argument(
        "--config", help="the run's settings file; its tool programs get their versions recorded"
    )
    collect_parser.add_argument(
        "--library-record",
        help="the record simulation_library.py wrote (the copied library's size goes into the setup record)",
    )
    collect_parser.add_argument(
        "--deep-catalog",
        help="the record copy_deep_catalog.py wrote; the deep-star load goes into the setup record",
    )
    power_parser = commands.add_parser("check-power")
    power_parser.add_argument("profile")
    sample_parser = commands.add_parser("nvidia-sample")
    sample_parser.add_argument("--out", required=True)
    arguments = parser.parse_args()
    if arguments.command == "collect":
        facts = collect()
        if arguments.config:
            facts["tool_versions"] = tool_versions(arguments.config)
        if arguments.deep_catalog and Path(arguments.deep_catalog).is_file():
            # The star load the Planetarium ran with (not part of the key).
            record = json.loads(Path(arguments.deep_catalog).read_text())
            facts["deep_catalog"] = {"star_count": record["star_count"], "size_bytes": record["size_bytes"]}
        else:
            facts["deep_catalog"] = None
        # The size of the copied library (not part of the key).
        record_file = Path(arguments.library_record) if arguments.library_record else None
        has_record = record_file is not None and record_file.is_file()
        facts["library"] = json.loads(record_file.read_text()) if has_record else None
        Path(arguments.out).write_text(json.dumps(facts, indent=2) + "\n")
        return 0
    if arguments.command == "check-power":
        return check_power(arguments.profile)
    try:
        nvidia_sample(Path(arguments.out))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
