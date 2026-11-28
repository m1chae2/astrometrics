"""Purpose: Set up the GPU path of the Flatpak Siril on Linux, from pins.

Description: Siril's GPU tools (GraXpert, StarNet2 and the Python scripts
Siril runs, such as GraXpert-AI and Cosmic Clarity) use the NVIDIA graphics
card only when several pieces are in place. This program puts each piece in
place from the pins in ``siril_gpu_pins.py``:

1. ``overrides``: the Flatpak permission overrides for ``org.siril.Siril``
   (``flatpak override --user``). Only missing settings are added. A setting
   that is already there with another value is reported and left alone, and
   nothing is ever removed.
2. ``cuda-libs``, ``starnet-ort``, ``graxpert-cudnn``: three folders of
   CUDA libraries. Each file is taken from a pinned wheel that ``pip
   download`` fetches from PyPI, and is checked against its SHA-256 checksum
   and size before it is moved into place. A file that does not match is
   refused.
3. ``siril-python``: the GPU build of ONNX Runtime and the packages it needs,
   with exact versions, in Siril's own Python environment.
4. ``wrappers``: the GraXpert and StarNet2 wrapper scripts, made from the
   templates in ``templates/``.
5. ``config``: the wrapper paths, written into the app's settings
   (``graxpert_executable``) and Siril's settings (``starnet_exe``,
   ``graxpert_path``) only where they are empty.

``--check`` reports the state of each piece and changes nothing.
``--dry-run`` reports the state and prints what a real run would do.

The CUDA and cuDNN wheels are under NVIDIA's license agreements, which are
not open source. Before a real run downloads the first of them, the program
shows each agreement's address and asks the user to accept it (y/N). Without
a terminal to ask on, it downloads them only with
``--accept-nvidia-license``. A refusal downloads nothing from NVIDIA, and the
parts that need those files fail. ``--check`` and ``--dry-run`` only say that
a run would ask. Without
an NVIDIA graphics card, or without the Flatpak Siril, the program says so
and exits without error: Siril and its tools still work on the processor.

Run it through ``build/linux/setup_siril_gpu.sh``.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import siril_gpu_pins as pins

TEMPLATE_FOLDER = Path(__file__).resolve().parent / "templates"
"""Where the wrapper templates are kept."""

PARTS = ("overrides", "cuda-libs", "starnet-ort", "graxpert-cudnn", "siril-python", "wrappers", "config")
"""Every part of the setup, in the order it runs."""

PROBE_TIMEOUT_SECONDS = 120
"""How long a read-only call (``flatpak info``, the Python probe) may take."""

INSTALL_TIMEOUT_SECONDS = 3600
"""How long a download or an install may take."""

_SIRIL_PYTHON_PROBE = """
import json, sys
from importlib import metadata
packages = {}
for distribution in metadata.distributions():
    name = distribution.metadata["Name"]
    if name:
        packages[name] = distribution.version
print(json.dumps({"python": "%d.%d" % sys.version_info[:2], "packages": packages}))
"""
"""A short program run with Siril's Python that prints its version and
installed packages as JSON. It reads only."""


class SetupError(Exception):
    """A piece of the setup could not be put in place."""


class LicenseNotAcceptedError(SetupError):
    """The user did not accept a license that a download needs."""


class ChecksumMismatchError(SetupError):
    """A downloaded file does not match its pinned checksum or size."""


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@dataclass
class PartReport:
    """The state of one part of the setup and the actions it needs.

    Attributes
    ----------
    part : `str`
        The part's name, one of `PARTS`.
    title : `str`
        What the part is, for the report.
    lines : `list` [`tuple` [`str`, `str`]]
        One (state, text) pair per checked item. The states are
        ``present``, ``missing``, ``wrong``, ``conflict``, ``accepted``,
        ``note`` and ``skipped``.
    actions : `list` [`str`]
        What a real run would do, in plain words.
    skipped : `str` or `None`
        Why the part was skipped, when it was.
    """

    part: str
    title: str
    lines: list[tuple[str, str]] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    skipped: str | None = None

    def add(self, state: str, text: str) -> None:
        """Record the state of one item.

        Parameters
        ----------
        state : `str`
            The item's state.
        text : `str`
            What the item is.
        """
        self.lines.append((state, text))

    @property
    def ready(self) -> bool:
        """Whether nothing in this part needs doing.

        Returns
        -------
        ready : `bool`
            `True` when no item is missing or wrong.
        """
        return not any(state in {"missing", "wrong"} for state, _ in self.lines)

    def print(self) -> None:
        """Print the part's state."""
        print(f"\n{self.title} [{self.part}]")
        if self.skipped:
            print(f"  skipped   {self.skipped}")
        for state, text in self.lines:
            print(f"  {state:<9} {text}")


# ---------------------------------------------------------------------------
# Running programs
# ---------------------------------------------------------------------------


def run_command(
    command: Sequence[str], timeout: float = PROBE_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess:
    """Run a program in list form and capture its output.

    Parameters
    ----------
    command : `Sequence` [`str`]
        The program and its arguments.
    timeout : `float`
        Seconds to wait before giving up.

    Returns
    -------
    result : `subprocess.CompletedProcess`
        The finished program. A program that is not installed or that runs
        too long gives return code 127 or 124, the way a shell reports them.
    """
    try:
        return subprocess.run(list(command), capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        return subprocess.CompletedProcess(list(command), 127, "", f"{command[0]}: not found")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(list(command), 124, "", f"{command[0]}: timed out")


def has_nvidia_gpu() -> str | None:
    """Find an NVIDIA graphics card with a working driver.

    Returns
    -------
    gpu : `str` or `None`
        The card's name as ``nvidia-smi -L`` prints it, or `None` when there
        is no card or no driver.
    """
    result = run_command(["nvidia-smi", "-L"])
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            if line.startswith("GPU"):
                return line.strip()
    return None


def has_flatpak_siril() -> bool:
    """Check that the Flatpak Siril is installed.

    Returns
    -------
    installed : `bool`
        `True` when ``flatpak info org.siril.Siril`` succeeds.
    """
    return run_command(["flatpak", "info", pins.SIRIL_APP_ID]).returncode == 0


def siril_is_running() -> bool:
    """Check whether the Flatpak Siril is open.

    Returns
    -------
    running : `bool`
        `True` when ``flatpak ps`` lists Siril.
    """
    result = run_command(["flatpak", "ps", "--columns=application"])
    return pins.SIRIL_APP_ID in result.stdout.split()


# ---------------------------------------------------------------------------
# Flatpak overrides
# ---------------------------------------------------------------------------


def parse_keyfile(text: str) -> dict[str, dict[str, str]]:
    """Read the key file that ``flatpak override --show`` prints.

    Parameters
    ----------
    text : `str`
        The printed key file, with ``[Section]`` lines and ``key=value``
        lines.

    Returns
    -------
    sections : `dict` [`str`, `dict` [`str`, `str`]]
        Each section's keys and values.
    """
    sections: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], {})
        elif "=" in line and current is not None:
            key, value = line.split("=", 1)
            current[key.strip()] = value.strip()
    return sections


@dataclass(frozen=True)
class FlatpakPermissions:
    """Flatpak permissions read from a key file.

    Attributes
    ----------
    filesystems : `tuple` [`str`, ...]
        The ``filesystems`` entries, as written.
    devices : `tuple` [`str`, ...]
        The ``devices`` entries.
    environment : `dict` [`str`, `str`]
        The ``[Environment]`` section.
    talk_names : `frozenset` [`str`]
        Session-bus names with ``talk`` or ``own`` access.
    """

    filesystems: tuple[str, ...]
    devices: tuple[str, ...]
    environment: dict[str, str]
    talk_names: frozenset[str]

    @classmethod
    def from_keyfile(cls, text: str) -> FlatpakPermissions:
        """Build the permissions from a printed key file.

        Parameters
        ----------
        text : `str`
            What ``flatpak override --show`` or ``flatpak info
            --show-permissions`` printed.

        Returns
        -------
        permissions : `FlatpakPermissions`
            The permissions.
        """
        sections = parse_keyfile(text)
        context = sections.get("Context", {})
        bus = sections.get("Session Bus Policy", {})

        def entries(key: str) -> tuple[str, ...]:
            """Split one ``;``-separated list of the context section.

            Parameters
            ----------
            key : `str`
                The key.

            Returns
            -------
            entries : `tuple` [`str`, ...]
                Its entries.
            """
            return tuple(item for item in context.get(key, "").split(";") if item)

        return cls(
            filesystems=entries("filesystems"),
            devices=entries("devices"),
            environment=dict(sections.get("Environment", {})),
            talk_names=frozenset(name for name, access in bus.items() if access in {"talk", "own"}),
        )


def split_filesystem(entry: str, home: Path) -> tuple[str, bool, str]:
    """Split a Flatpak ``filesystems`` entry into its parts.

    Parameters
    ----------
    entry : `str`
        An entry such as ``~/cuda_libs:ro`` or ``!~/.astropy``.
    home : `Path`
        The home folder, used to expand ``~``.

    Returns
    -------
    path : `str`
        The folder, with ``~`` expanded and no trailing ``/``.
    hidden : `bool`
        `True` for an entry that starts with ``!`` (hide the folder).
    mode : `str`
        ``ro``, ``rw``, ``create`` or an empty string.
    """
    hidden = entry.startswith("!")
    body = entry[1:] if hidden else entry
    mode = ""
    if ":" in body:
        body, mode = body.rsplit(":", 1)
    if body == "~" or body.startswith("~/"):
        body = str(home) + body[1:]
    return body.rstrip("/") or "/", hidden, mode


@dataclass
class OverridePlan:
    """What the override needs and what it found.

    Attributes
    ----------
    present : `list` [`str`]
        Wanted settings that are already there.
    missing : `list` [`str`]
        Wanted settings that are not there, as readable text.
    flags : `list` [`str`]
        The ``flatpak override`` arguments that add the missing settings.
    conflicts : `list` [`str`]
        Settings that are there with another value; left alone.
    notes : `list` [`str`]
        Other remarks, such as settings that have no effect.
    """

    present: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def plan_overrides(current: FlatpakPermissions, effective: FlatpakPermissions, home: Path) -> OverridePlan:
    """Compare the user's overrides with the pinned ones.

    Parameters
    ----------
    current : `FlatpakPermissions`
        The user's overrides (``flatpak override --user --show``).
    effective : `FlatpakPermissions`
        Siril's permissions with every override applied (``flatpak info
        --show-permissions``), used for the session-bus names that Siril's
        own manifest already grants.
    home : `Path`
        The home folder.

    Returns
    -------
    plan : `OverridePlan`
        What is there, what is missing and the arguments that add it.
    """
    plan = OverridePlan()

    existing = {split_filesystem(entry, home)[0]: entry for entry in current.filesystems}
    for wanted in pins.OVERRIDE_FILESYSTEMS:
        path, hidden, mode = split_filesystem(wanted, home)
        found = existing.get(path)
        if found is None:
            plan.missing.append(f"filesystem {wanted}")
            plan.flags.append(f"--nofilesystem={wanted[1:]}" if hidden else f"--filesystem={wanted}")
        elif split_filesystem(found, home) == (path, hidden, mode):
            plan.present.append(f"filesystem {wanted}")
        else:
            plan.conflicts.append(f"filesystem {found} (wanted {wanted}); left alone")
    for entry in current.filesystems:
        path = split_filesystem(entry, home)[0]
        if path == "/usr" or path.startswith("/usr/"):
            plan.notes.append(f"filesystem {entry} has no effect: Flatpak never shares folders under /usr")

    for device in pins.OVERRIDE_DEVICES:
        if device in current.devices:
            plan.present.append(f"device {device}")
        elif f"!{device}" in current.devices:
            plan.conflicts.append(f"device !{device} (wanted {device}); left alone")
        else:
            plan.missing.append(f"device {device}")
            plan.flags.append(f"--device={device}")

    for name, template in pins.OVERRIDE_ENVIRONMENT:
        wanted = template.format(home=home)
        found = current.environment.get(name)
        satisfied = found == wanted or (
            name == "LD_LIBRARY_PATH" and found is not None and wanted in found.split(":")
        )
        if satisfied:
            plan.present.append(f"env {name}={found}")
        elif found is not None:
            plan.conflicts.append(f"env {name}={found} (wanted {wanted}); left alone")
        else:
            plan.missing.append(f"env {name}={wanted}")
            plan.flags.append(f"--env={name}={wanted}")

    for name in pins.REQUIRED_TALK_NAMES:
        if name in effective.talk_names or name in current.talk_names:
            plan.present.append(f"talk-name {name}")
        else:
            plan.missing.append(f"talk-name {name}")
            plan.flags.append(f"--talk-name={name}")
    return plan


def check_overrides(home: Path) -> tuple[PartReport, list[str]]:
    """Report the Flatpak overrides and build the command that completes them.

    Parameters
    ----------
    home : `Path`
        The home folder.

    Returns
    -------
    report : `PartReport`
        The state of the overrides.
    command : `list` [`str`]
        The ``flatpak override`` command that adds the missing settings, or
        an empty list when nothing is missing.
    """
    report = PartReport("overrides", "Flatpak permissions for Siril")
    shown = run_command(["flatpak", "override", "--user", "--show", pins.SIRIL_APP_ID])
    effective = run_command(["flatpak", "info", "--show-permissions", pins.SIRIL_APP_ID])
    if shown.returncode != 0 or effective.returncode != 0:
        report.add(
            "wrong", f"could not read Siril's permissions: {(shown.stderr or effective.stderr).strip()}"
        )
        return report, []
    plan = plan_overrides(
        FlatpakPermissions.from_keyfile(shown.stdout), FlatpakPermissions.from_keyfile(effective.stdout), home
    )
    for text in plan.present:
        report.add("present", text)
    for text in plan.missing:
        report.add("missing", text)
    for text in plan.conflicts:
        report.add("conflict", text)
    for text in plan.notes:
        report.add("note", text)
    if not plan.flags:
        return report, []
    command = ["flatpak", "override", "--user", *plan.flags, pins.SIRIL_APP_ID]
    report.actions.append("run " + " ".join(command))
    return report, command


# ---------------------------------------------------------------------------
# Library folders from pinned wheels
# ---------------------------------------------------------------------------


def sha256_of_file(path: Path) -> str:
    """Compute a file's SHA-256 checksum.

    Parameters
    ----------
    path : `Path`
        The file. A symbolic link is followed.

    Returns
    -------
    checksum : `str`
        The checksum in lowercase hexadecimal.
    """
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def file_state(path: Path, pinned: pins.PinnedFile) -> str:
    """Compare a file on disk with its pin.

    Parameters
    ----------
    path : `Path`
        The file in the target folder.
    pinned : `PinnedFile`
        Its pin.

    Returns
    -------
    state : `str`
        ``missing``, ``present`` (the pinned build), ``accepted`` (a known
        hand-installed build) or ``wrong`` (anything else).
    """
    if not path.exists():
        return "missing"
    checksum = sha256_of_file(path)
    if checksum == pinned.sha256:
        return "present"
    if checksum in pinned.hand_installed_sha256:
        return "accepted"
    return "wrong"


def extract_verified(wheel: Path, pinned: pins.PinnedFile, destination: Path) -> Path:
    """Copy one file out of a wheel, refusing it unless it matches its pin.

    The file is written to a temporary name next to its destination, checked,
    and only then renamed into place, so a refused or half-written file never
    takes the place of a working one.

    Parameters
    ----------
    wheel : `Path`
        The downloaded wheel (a zip file).
    pinned : `PinnedFile`
        The file to take out of it.
    destination : `Path`
        The folder to put it in.

    Returns
    -------
    path : `Path`
        The file in place.

    Raises
    ------
    ChecksumMismatchError
        The file is missing from the wheel, or its size or checksum differs
        from the pin.
    """
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / pinned.name
    with zipfile.ZipFile(wheel) as archive:
        try:
            info = archive.getinfo(pinned.member)
        except KeyError:
            raise ChecksumMismatchError(f"{wheel.name} has no {pinned.member}") from None
        if info.file_size != pinned.size_bytes:
            raise ChecksumMismatchError(
                f"{pinned.member} in {wheel.name} is {info.file_size} bytes, pinned {pinned.size_bytes}"
            )
        digest = hashlib.sha256()
        handle, temporary_name = tempfile.mkstemp(prefix=f".{pinned.name}.", dir=destination)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(handle, "wb") as output, archive.open(info) as source:
                for block in iter(lambda: source.read(1 << 20), b""):
                    digest.update(block)
                    output.write(block)
            if digest.hexdigest() != pinned.sha256:
                raise ChecksumMismatchError(
                    f"{pinned.member} in {wheel.name} has SHA-256 {digest.hexdigest()}, "
                    f"pinned {pinned.sha256}"
                )
            temporary.chmod(0o755 if ".so" in pinned.name else 0o644)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def wheel_file_name_prefix(wheel: pins.PinnedWheel) -> str:
    """Give the start of the pinned wheel's file name.

    Parameters
    ----------
    wheel : `PinnedWheel`
        The wheel.

    Returns
    -------
    prefix : `str`
        The name up to the version, such as ``nvidia_cudnn_cu12-9.24.0.43-``
        (the wheel file name standard writes ``-`` in a name as ``_``).
    """
    return f"{re.sub(r'[-_.]+', '_', wheel.project).lower()}-{wheel.version}-"


def pip_download_command(wheel: pins.PinnedWheel, folder: Path) -> list[str]:
    """Build the ``pip download`` command for one pinned wheel.

    Parameters
    ----------
    wheel : `PinnedWheel`
        The wheel.
    folder : `Path`
        Where pip saves it.

    Returns
    -------
    command : `list` [`str`]
        The command.
    """
    command = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--no-deps",
        "--only-binary=:all:",
        "--dest",
        str(folder),
    ]
    if wheel.python_version:
        tag = "cp" + wheel.python_version.replace(".", "")
        command += [
            "--python-version",
            wheel.python_version,
            "--implementation",
            "cp",
            "--abi",
            tag,
            "--platform",
            wheel.platform or "manylinux_2_28_x86_64",
        ]
    command.append(f"{wheel.project}=={wheel.version}")
    return command


def license_notice(licenses: Sequence[pins.License]) -> str:
    """Write the notice shown before a wheel under these licenses downloads.

    Parameters
    ----------
    licenses : `Sequence` [`License`]
        The licenses to accept.

    Returns
    -------
    notice : `str`
        The notice, one license per line with its address.
    """
    lines = [
        "The NVIDIA CUDA and cuDNN libraries are NVIDIA's software, under NVIDIA's own license",
        "agreements. They are not open source. Read them before you accept:",
    ]
    lines += [f"  - {license_.name}: {license_.url}" for license_ in licenses]
    lines.append("Astrometrics does not include these libraries; this setup downloads them from PyPI.")
    return "\n".join(lines)


class LicenseGate:
    """Ask once per run for the user's acceptance of NVIDIA's licenses.

    Parameters
    ----------
    accepted : `bool`
        `True` when ``--accept-nvidia-license`` was given.
    interactive : `bool`
        Whether there is a terminal to ask on.
    ask : `Callable` [[`str`], `str`]
        Reads the answer to a question; `input` by default.
    licenses : `Sequence` [`License`]
        The licenses to accept; `siril_gpu_pins.LICENSES_TO_ACCEPT` by default.
    """

    def __init__(
        self,
        accepted: bool,
        interactive: bool,
        ask: Callable[[str], str] = input,
        licenses: Sequence[pins.License] = pins.LICENSES_TO_ACCEPT,
    ) -> None:
        """Remember the answer given on the command line and how to ask."""
        self.accepted = accepted
        self.interactive = interactive
        self.ask = ask
        self.licenses = tuple(licenses)
        self.refused = False

    def require(self) -> None:
        """Make sure the user has accepted the licenses, asking if needed.

        The question is asked at most once per run. A refusal is remembered,
        so later downloads are refused without asking again.

        Raises
        ------
        LicenseNotAcceptedError
            The user said no, or there is no terminal to ask on and
            ``--accept-nvidia-license`` was not given.
        """
        if self.accepted:
            return
        if not self.refused:
            print("\n" + license_notice(self.licenses), flush=True)
            if self.interactive:
                try:
                    answer = self.ask("Do you accept these NVIDIA license agreements? [y/N] ")
                except EOFError:
                    answer = ""
                if answer.strip().lower() in {"y", "yes"}:
                    self.accepted = True
                    return
            self.refused = True
        if self.interactive:
            raise LicenseNotAcceptedError(
                "NVIDIA's license agreements were not accepted; nothing was downloaded from NVIDIA"
            )
        raise LicenseNotAcceptedError(
            "NVIDIA's license agreements must be accepted before their libraries are downloaded. "
            "Read them, then run again with --accept-nvidia-license"
        )


class WheelCache:
    """Download each pinned wheel once per run, into one work folder.

    Parameters
    ----------
    folder : `Path`
        The work folder. It must be on disk, not in the RAM-backed ``/tmp``,
        because the CUDA wheels are several hundred megabytes each.
    license_gate : `LicenseGate`, optional
        Asked before a wheel whose license is not open source is
        downloaded. Without one, such a wheel is refused.
    """

    def __init__(self, folder: Path, license_gate: LicenseGate | None = None) -> None:
        """Remember the work folder and the license gate."""
        self.folder = folder
        self.license_gate = license_gate
        self._wheels: dict[str, Path] = {}

    def get(self, wheel: pins.PinnedWheel) -> Path:
        """Download a pinned wheel, or give the copy already downloaded.

        Parameters
        ----------
        wheel : `PinnedWheel`
            The wheel.

        Returns
        -------
        path : `Path`
            The downloaded file.

        Raises
        ------
        LicenseNotAcceptedError
            The wheel's license is not open source and was not accepted.
        SetupError
            pip failed or saved no matching file.
        """
        key = f"{wheel.project}=={wheel.version}"
        if key in self._wheels:
            return self._wheels[key]
        if wheel.needs_license_acceptance:
            if self.license_gate is None:
                raise LicenseNotAcceptedError(f"{key} needs its license accepted, and nothing can ask")
            self.license_gate.require()
        self.folder.mkdir(parents=True, exist_ok=True)
        print(f"  downloading {key} with pip ...", flush=True)
        result = run_command(pip_download_command(wheel, self.folder), timeout=INSTALL_TIMEOUT_SECONDS)
        if result.returncode != 0:
            raise SetupError(f"pip download {key} failed: {result.stderr.strip()[-400:]}")
        found = sorted(self.folder.glob(wheel_file_name_prefix(wheel) + "*.whl"))
        if not found:
            raise SetupError(f"pip download {key} saved no wheel in {self.folder}")
        self._wheels[key] = found[0]
        return found[0]


@dataclass
class FolderPlan:
    """The files of a library folder that a run would put in place.

    Attributes
    ----------
    install : `list` [`tuple` [`PinnedWheel`, `PinnedFile`]]
        Files to take from their wheels.
    links : `list` [`tuple` [`str`, `str`]]
        Symbolic links to make, as (link name, target name).
    """

    install: list[tuple[pins.PinnedWheel, pins.PinnedFile]] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)


def check_folder(folder: pins.LibraryFolder, home: Path, force: bool) -> tuple[PartReport, FolderPlan]:
    """Report a library folder and work out which files to put in place.

    Parameters
    ----------
    folder : `LibraryFolder`
        The folder's pins.
    home : `Path`
        The home folder.
    force : `bool`
        Also replace accepted hand-installed builds and unknown builds.

    Returns
    -------
    report : `PartReport`
        The state of each file.
    plan : `FolderPlan`
        The files and links a real run would put in place.
    """
    report = PartReport(folder.part, folder.title)
    plan = FolderPlan()
    target = home / folder.relative_path
    for wheel in folder.wheels:
        for pinned in wheel.files:
            state = file_state(target / pinned.name, pinned)
            source = f"{wheel.project} {wheel.version}"
            if state == "present":
                report.add("present", f"{pinned.name} ({source})")
            elif state == "accepted":
                report.add("accepted", f"{pinned.name}: a known working build, not the pinned {source}")
            elif state == "wrong":
                report.add(
                    "wrong",
                    f"{pinned.name}: unknown build, left alone (pinned {source}; --force replaces it)",
                )
            else:
                report.add("missing", f"{pinned.name} (from {source})")
            if state == "missing" or (force and state in {"accepted", "wrong"}):
                plan.install.append((wheel, pinned))
    for link, link_target in folder.links:
        path = target / link
        if path.is_symlink() and os.readlink(path) == link_target:
            report.add("present", f"{link} -> {link_target}")
        elif path.exists() or path.is_symlink():
            report.add("conflict", f"{link} exists but is not a link to {link_target}; left alone")
        else:
            report.add("missing", f"{link} -> {link_target}")
            plan.links.append((link, link_target))
    wheels = sorted({f"{wheel.project}=={wheel.version}" for wheel, _ in plan.install})
    licenses = list(
        dict.fromkeys(
            wheel.license for wheel, _ in plan.install if wheel.license and wheel.needs_license_acceptance
        )
    )
    if licenses:
        names = "; ".join(f"{license_.name} ({license_.url})" for license_ in licenses)
        report.actions.append(f"ask you to accept {names} (or use --accept-nvidia-license)")
    if wheels:
        report.actions.append(f"download {', '.join(wheels)} with pip and check each file's SHA-256")
        report.actions.append(f"put {len(plan.install)} file(s) in {target}")
    for link, link_target in plan.links:
        report.actions.append(f"link {target / link} -> {link_target}")
    return report, plan


def apply_folder(folder: pins.LibraryFolder, plan: FolderPlan, home: Path, cache: WheelCache) -> None:
    """Put a library folder's planned files in place.

    A file being replaced (``--force``) is first renamed with a ``.bak-``
    suffix, so nothing is lost.

    Parameters
    ----------
    folder : `LibraryFolder`
        The folder's pins.
    plan : `FolderPlan`
        What to put in place.
    home : `Path`
        The home folder.
    cache : `WheelCache`
        Where the wheels are downloaded.

    Raises
    ------
    ChecksumMismatchError
        A downloaded file does not match its pin. The file it was to
        replace is put back.
    """
    target = home / folder.relative_path
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    for wheel, pinned in plan.install:
        wheel_path = cache.get(wheel)
        existing = target / pinned.name
        backup = existing.with_name(f"{pinned.name}.bak-{stamp}")
        kept = existing.exists() or existing.is_symlink()
        if kept:
            existing.rename(backup)
        try:
            extract_verified(wheel_path, pinned, target)
        except ChecksumMismatchError:
            if kept:
                backup.rename(existing)
            raise
        print(f"  checked and installed {target / pinned.name}")
    for link, link_target in plan.links:
        (target / link).symlink_to(link_target)
        print(f"  linked {target / link} -> {link_target}")


def check_host_cuda13(report: PartReport) -> None:
    """Add the system CUDA 13 libraries that GraXpert needs to a report.

    Parameters
    ----------
    report : `PartReport`
        The GraXpert part's report.
    """
    for library in pins.HOST_CUDA13_LIBRARIES:
        if Path(library).exists():
            report.add("present", f"{library} (system CUDA 13)")
        else:
            report.add(
                "note",
                f"{library} is missing: install NVIDIA's CUDA 13 libraries (cuda-libraries-13-x) "
                "for GraXpert to use the graphics card",
            )


# ---------------------------------------------------------------------------
# Siril's Python environment
# ---------------------------------------------------------------------------


def normalize_package_name(name: str) -> str:
    """Normalize a Python package name the way pip compares them.

    Parameters
    ----------
    name : `str`
        A package name such as ``PyYAML`` or ``onnxruntime_gpu``.

    Returns
    -------
    name : `str`
        The lowercase name with runs of ``-``, ``_`` and ``.`` made ``-``.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def compare_packages(
    installed: dict[str, str], pinned: Sequence[tuple[str, str]], conflicting: Sequence[str]
) -> list[tuple[str, str]]:
    """Compare installed Python packages with their pins.

    Parameters
    ----------
    installed : `dict` [`str`, `str`]
        Installed package name to version.
    pinned : `Sequence` [`tuple` [`str`, `str`]]
        Pinned (name, version) pairs.
    conflicting : `Sequence` [`str`]
        Packages that must not be installed.

    Returns
    -------
    lines : `list` [`tuple` [`str`, `str`]]
        One (state, text) pair per pinned or conflicting package: states
        ``present``, ``missing``, ``wrong`` (another version, or a
        conflicting package that is installed).
    """
    versions = {normalize_package_name(name): version for name, version in installed.items()}
    lines = []
    for name, version in pinned:
        found = versions.get(normalize_package_name(name))
        if found == version:
            lines.append(("present", f"{name} {version}"))
        elif found is None:
            lines.append(("missing", f"{name} {version}"))
        else:
            lines.append(("wrong", f"{name} {found} (pinned {version})"))
    for name in conflicting:
        found = versions.get(normalize_package_name(name))
        if found is not None:
            lines.append(("wrong", f"{name} {found} is installed and hides the GPU build; uninstall it"))
    return lines


def siril_python_command(home: Path, *arguments: str) -> list[str]:
    """Build a command that runs Siril's Python inside the Siril sandbox.

    Parameters
    ----------
    home : `Path`
        The home folder.
    *arguments : `str`
        Arguments for Python.

    Returns
    -------
    command : `list` [`str`]
        The ``flatpak run --command=...`` command.
    """
    python = home / pins.SIRIL_VENV_RELATIVE_PATH / "bin" / "python"
    return ["flatpak", "run", f"--command={python}", pins.SIRIL_APP_ID, *arguments]


def check_siril_python(home: Path) -> tuple[PartReport, list[str]]:
    """Report Siril's Python packages and work out which to install.

    Parameters
    ----------
    home : `Path`
        The home folder.

    Returns
    -------
    report : `PartReport`
        The state of each pinned package.
    to_install : `list` [`str`]
        Names of the pinned packages that are missing or another version.
    """
    report = PartReport("siril-python", "Python packages in Siril's own Python")
    venv = home / pins.SIRIL_VENV_RELATIVE_PATH
    if not (venv / "bin" / "python").exists():
        report.skipped = (
            f"Siril has no Python environment yet ({venv}). Open Siril once and run any script "
            "from its Scripts menu; Siril then makes it."
        )
        return report, []
    result = run_command(siril_python_command(home, "-c", _SIRIL_PYTHON_PROBE))
    try:
        probe = json.loads(result.stdout.strip().splitlines()[-1])
    except IndexError, json.JSONDecodeError:
        report.add("wrong", f"could not run Siril's Python: {result.stderr.strip()[-300:]}")
        return report, []
    if probe["python"] != pins.SIRIL_PYTHON_VERSION:
        report.skipped = (
            f"Siril's Python is {probe['python']}; these pins are for {pins.SIRIL_PYTHON_VERSION}. "
            "Update the pins in siril_gpu_pins.py for the new Python."
        )
        return report, []
    lines = compare_packages(probe["packages"], pins.SIRIL_PYTHON_PACKAGES, pins.CONFLICTING_PYTHON_PACKAGES)
    to_install = []
    for (state, text), (name, _) in zip(lines, pins.SIRIL_PYTHON_PACKAGES, strict=False):
        report.add(state, text)
        if state != "present":
            to_install.append(name)
    for state, text in lines[len(pins.SIRIL_PYTHON_PACKAGES) :]:
        report.add(state, text)
    installed = {normalize_package_name(name): version for name, version in probe["packages"].items()}
    for name in pins.SIRIL_MANAGED_PACKAGES:
        version = installed.get(normalize_package_name(name))
        report.add("note", f"{name} {version or 'not installed'} (Siril installs and updates it itself)")
    if to_install:
        report.actions.append(
            "install into Siril's Python with pip --no-deps: "
            + ", ".join(
                f"{name}=={version}" for name, version in pins.SIRIL_PYTHON_PACKAGES if name in to_install
            )
            + f" ({pins.ONNXRUNTIME_GPU.project} from the checked wheel)"
        )
    return report, to_install


def verify_wheel(wheel: Path, pinned_wheel: pins.PinnedWheel) -> None:
    """Check every pinned file inside a downloaded wheel without extracting it.

    Parameters
    ----------
    wheel : `Path`
        The downloaded wheel.
    pinned_wheel : `PinnedWheel`
        Its pins.

    Raises
    ------
    ChecksumMismatchError
        A pinned file is missing or differs.
    """
    with zipfile.ZipFile(wheel) as archive:
        for pinned in pinned_wheel.files:
            try:
                info = archive.getinfo(pinned.member)
            except KeyError:
                raise ChecksumMismatchError(f"{wheel.name} has no {pinned.member}") from None
            digest = hashlib.sha256()
            with archive.open(info) as source:
                for block in iter(lambda: source.read(1 << 20), b""):
                    digest.update(block)
            if info.file_size != pinned.size_bytes or digest.hexdigest() != pinned.sha256:
                raise ChecksumMismatchError(f"{pinned.member} in {wheel.name} does not match its pin")


def apply_siril_python(home: Path, to_install: list[str], cache: WheelCache) -> None:
    """Install the planned packages into Siril's Python.

    Parameters
    ----------
    home : `Path`
        The home folder.
    to_install : `list` [`str`]
        Names of the pinned packages to install.
    cache : `WheelCache`
        Where the ONNX Runtime wheel is downloaded. The folder must be one
        the Siril sandbox can read.

    Raises
    ------
    SetupError
        pip failed inside the sandbox.
    """
    requirements = []
    for name, version in pins.SIRIL_PYTHON_PACKAGES:
        if name not in to_install:
            continue
        if name == pins.ONNXRUNTIME_GPU.project:
            wheel = cache.get(pins.ONNXRUNTIME_GPU)
            verify_wheel(wheel, pins.ONNXRUNTIME_GPU)
            requirements.append(str(wheel))
        else:
            requirements.append(f"{name}=={version}")
    command = siril_python_command(home, "-m", "pip", "install", "--no-deps", *requirements)
    print("  running " + " ".join(command), flush=True)
    result = run_command(command, timeout=INSTALL_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise SetupError(f"pip install in Siril's Python failed: {result.stderr.strip()[-400:]}")


# ---------------------------------------------------------------------------
# Wrappers
# ---------------------------------------------------------------------------

_SAFE_PATH = re.compile(r"^(\$HOME)?[A-Za-z0-9 ._/+@,=-]*$")
"""Characters allowed in each ``:``-separated path written into a wrapper's
double quotes. ``$HOME`` is allowed only at the start of a path."""


def home_relative(path: Path, home: Path) -> str:
    """Write a path for a shell script, using ``$HOME`` when it is under home.

    Parameters
    ----------
    path : `Path`
        The path.
    home : `Path`
        The home folder.

    Returns
    -------
    text : `str`
        ``$HOME/...`` for a path under the home folder, otherwise the path.
    """
    try:
        return "$HOME/" + path.relative_to(home).as_posix()
    except ValueError:
        return str(path)


def render_template(text: str, values: dict[str, str]) -> str:
    """Fill in a wrapper template.

    Parameters
    ----------
    text : `str`
        The template, with ``@NAME@`` placeholders.
    values : `dict` [`str`, `str`]
        A value for each placeholder name.

    Returns
    -------
    script : `str`
        The filled-in script.

    Raises
    ------
    ValueError
        A placeholder has no value, a value is not used, or a value holds
        characters that would change the meaning of the shell script.
    """
    names = set(re.findall(r"@([A-Z_]+)@", text))
    if missing := names - values.keys():
        raise ValueError(f"no value for {sorted(missing)}")
    if unused := values.keys() - names:
        raise ValueError(f"the template does not use {sorted(unused)}")
    for name, value in values.items():
        if not all(_SAFE_PATH.match(part) for part in value.split(":")):
            raise ValueError(f"{name} holds characters that are unsafe in a shell script: {value!r}")
        text = text.replace(f"@{name}@", value)
    return text


def effective_lines(script: str) -> list[str]:
    """Give the lines of a shell script that do something.

    Parameters
    ----------
    script : `str`
        The script.

    Returns
    -------
    lines : `list` [`str`]
        The stripped lines, without blank lines and comments (the ``#!``
        line is kept).
    """
    lines = []
    for raw_line in script.splitlines():
        line = raw_line.strip()
        if line and (not line.startswith("#") or line.startswith("#!")):
            lines.append(line)
    return lines


def wrapper_values(home: Path, programs: dict[str, Path]) -> dict[str, dict[str, str]]:
    """Work out each wrapper's template values.

    Parameters
    ----------
    home : `Path`
        The home folder.
    programs : `dict` [`str`, `Path`]
        The program each wrapper starts, by wrapper name.

    Returns
    -------
    values : `dict` [`str`, `dict` [`str`, `str`]]
        For each wrapper's name, its template values.
    """
    star_libs = ":".join(
        home_relative(home / folder.relative_path, home) for folder in (pins.STARNET_ORT, pins.CUDA_LIBS)
    )
    return {
        pins.GRAXPERT_WRAPPER.name: {
            "GRAXPERT_PROGRAM": home_relative(programs[pins.GRAXPERT_WRAPPER.name], home),
            "GRAXPERT_LIBS": home_relative(home / pins.GRAXPERT_CUDNN.relative_path, home),
        },
        pins.STARNET_WRAPPER.name: {
            "STARNET_PROGRAM": home_relative(programs[pins.STARNET_WRAPPER.name], home),
            "STARNET_LIBS": star_libs,
        },
    }


def check_wrappers(
    home: Path, programs: dict[str, Path], force: bool
) -> tuple[PartReport, list[tuple[pins.Wrapper, str]]]:
    """Report the wrapper scripts and work out which to write.

    Parameters
    ----------
    home : `Path`
        The home folder.
    programs : `dict` [`str`, `Path`]
        The program each wrapper starts, by wrapper name.
    force : `bool`
        Also replace a wrapper that differs from the template.

    Returns
    -------
    report : `PartReport`
        The state of each wrapper.
    to_write : `list` [`tuple` [`Wrapper`, `str`]]
        The wrappers to write, with their contents.
    """
    report = PartReport("wrappers", "GPU wrapper scripts")
    values = wrapper_values(home, programs)
    to_write = []
    for wrapper in (pins.GRAXPERT_WRAPPER, pins.STARNET_WRAPPER):
        program = programs[wrapper.name]
        if not program.exists():
            report.add(
                "note",
                f"{wrapper.name} is not at {program}; its wrapper is not written. "
                f"Pass --{wrapper.name.lower().rstrip('2')} with its path.",
            )
            continue
        try:
            script = render_template((TEMPLATE_FOLDER / wrapper.template).read_text(), values[wrapper.name])
        except ValueError as error:
            report.add("note", f"{wrapper.name} wrapper not written: {error}")
            continue
        path = home / wrapper.relative_path
        if not path.exists():
            report.add("missing", f"{path}")
            to_write.append((wrapper, script))
        elif path.read_text() == script:
            report.add("present", f"{path}")
        elif effective_lines(path.read_text()) == effective_lines(script):
            report.add("present", f"{path} (only its comments differ from the template)")
        else:
            report.add("conflict", f"{path} differs from the template; left alone (use --force to replace)")
            if force:
                to_write.append((wrapper, script))
    for wrapper, _ in to_write:
        report.actions.append(f"write {home / wrapper.relative_path}")
    return report, to_write


def write_file_keeping_backup(path: Path, text: str, mode: int) -> None:
    """Write a file in one step, keeping any earlier copy as ``.bak-<time>``.

    Parameters
    ----------
    path : `Path`
        The file.
    text : `str`
        Its new contents.
    mode : `int`
        Its permission bits.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, path.with_name(f"{path.name}.bak-{stamp}"))
    temporary = path.with_name(f".{path.name}.new")
    temporary.write_text(text)
    temporary.chmod(mode)
    temporary.replace(path)


# ---------------------------------------------------------------------------
# Settings files
# ---------------------------------------------------------------------------


def _quote_toml(value: str) -> str:
    """Write a TOML basic string.

    Parameters
    ----------
    value : `str`
        The text.

    Returns
    -------
    text : `str`
        The quoted, escaped string.
    """
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def set_toml_value_if_unset(text: str, section: str, key: str, value: str) -> tuple[str, str]:
    """Set one string setting in a TOML file, only when it is empty or missing.

    The file is edited line by line, so its comments and layout stay as they
    are. The section header is the quoted form the app uses, such as
    ``["Processing.GraXpert"]``.

    Parameters
    ----------
    text : `str`
        The file's contents.
    section : `str`
        The section's name, without brackets or quotes.
    key : `str`
        The setting.
    value : `str`
        The value to write.

    Returns
    -------
    text : `str`
        The new contents (unchanged unless the setting was empty).
    state : `str`
        ``set`` (written), ``already set`` (left alone).
    """
    current = tomllib.loads(text).get(section, {}).get(key)
    if current:
        return text, "already set"
    lines = text.splitlines(keepends=True)
    header = re.compile(r'^\s*\[\s*"' + re.escape(section) + r'"\s*\]\s*(#.*)?$')
    any_header = re.compile(r"^\s*\[")
    key_line = re.compile(r"^\s*" + re.escape(key) + r"\s*=")
    new_line = f"{key} = {_quote_toml(value)}\n"
    start = next((index for index, line in enumerate(lines) if header.match(line)), None)
    if start is None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines += ["\n", f'["{section}"]\n', new_line]
        return "".join(lines), "set"
    end = next(
        (index for index in range(start + 1, len(lines)) if any_header.match(lines[index])), len(lines)
    )
    for index in range(start + 1, end):
        if key_line.match(lines[index]):
            lines[index] = new_line
            return "".join(lines), "set"
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    if lines[insert_at - 1] and not lines[insert_at - 1].endswith("\n"):
        lines[insert_at - 1] += "\n"
    lines.insert(insert_at, new_line)
    return "".join(lines), "set"


def set_ini_value_if_unset(text: str, section: str, key: str, value: str) -> tuple[str, str]:
    """Set one setting in Siril's settings, only when it is empty or missing.

    Siril's settings file is a GLib key file: ``[section]`` lines and
    ``key=value`` lines with no quotes. It is edited line by line.

    Parameters
    ----------
    text : `str`
        The file's contents.
    section : `str`
        The section's name.
    key : `str`
        The setting.
    value : `str`
        The value to write.

    Returns
    -------
    text : `str`
        The new contents (unchanged unless the setting was empty).
    state : `str`
        ``set`` (written) or ``already set`` (left alone).
    """
    lines = text.splitlines(keepends=True)
    start = next((index for index, line in enumerate(lines) if line.strip() == f"[{section}]"), None)
    new_line = f"{key}={value}\n"
    if start is None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines += [f"[{section}]\n", new_line]
        return "".join(lines), "set"
    end = next((index for index in range(start + 1, len(lines)) if lines[index].startswith("[")), len(lines))
    for index in range(start + 1, end):
        name, equals, current = lines[index].partition("=")
        if equals and name.strip() == key:
            if current.strip():
                return text, "already set"
            lines[index] = new_line
            return "".join(lines), "set"
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, new_line)
    return "".join(lines), "set"


def newest_siril_config(folder: Path) -> Path | None:
    """Find the settings file of the newest Siril version.

    Parameters
    ----------
    folder : `Path`
        Siril's settings folder.

    Returns
    -------
    path : `Path` or `None`
        The ``config.<version>.ini`` with the highest version, or `None`.
    """
    candidates = []
    for path in folder.glob("config.*.ini"):
        match = re.fullmatch(r"config\.(\d+(?:\.\d+)*)\.ini", path.name)
        if match:
            candidates.append((tuple(int(part) for part in match.group(1).split(".")), path))
    return max(candidates)[1] if candidates else None


def app_config_path(repository: Path) -> Path:
    """Find the app's settings file.

    Parameters
    ----------
    repository : `Path`
        The repository root, for an older install whose settings file is
        still inside it.

    Returns
    -------
    path : `Path`
        ``astrometrics.config.toml`` in the user's settings folder, unless
        only the older copy exists.
    """
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    path = config_home / "astrometrics" / "astrometrics.config.toml"
    legacy = repository / "astrometricslib" / "astrometrics.config.toml"
    return legacy if not path.exists() and legacy.exists() else path


@dataclass
class ConfigEdit:
    """A settings file and its new contents.

    Attributes
    ----------
    path : `Path`
        The file.
    text : `str`
        Its new contents.
    """

    path: Path
    text: str


def check_config(home: Path, repository: Path) -> tuple[PartReport, list[ConfigEdit]]:
    """Report the settings that name the wrappers and work out the edits.

    Parameters
    ----------
    home : `Path`
        The home folder.
    repository : `Path`
        The repository root.

    Returns
    -------
    report : `PartReport`
        The state of each setting.
    edits : `list` [`ConfigEdit`]
        The files to write.
    """
    report = PartReport("config", "Settings that name the wrappers")
    edits = []

    app_path = app_config_path(repository)
    graxpert = str(home / pins.GRAXPERT_WRAPPER.relative_path)
    label = f"{app_path} [{pins.APP_CONFIG_SECTION}] {pins.APP_CONFIG_KEY}"
    if not app_path.exists():
        report.add("note", f"{app_path} does not exist yet; run build/linux/setup_venv.sh first")
    else:
        text = app_path.read_text()
        try:
            new_text, state = set_toml_value_if_unset(
                text, pins.APP_CONFIG_SECTION, pins.APP_CONFIG_KEY, graxpert
            )
        except tomllib.TOMLDecodeError as error:
            report.add("note", f"{app_path} is not valid TOML ({error}); left alone")
        else:
            if state == "already set":
                current = tomllib.loads(text)[pins.APP_CONFIG_SECTION][pins.APP_CONFIG_KEY]
                report.add("present", f"{label} = {current}")
            else:
                report.add("missing", f"{label} (empty)")
                edits.append(ConfigEdit(app_path, new_text))
                report.actions.append(f"set {label} = {graxpert}")

    siril_path = newest_siril_config(home / pins.SIRIL_CONFIG_RELATIVE_FOLDER)
    if siril_path is None:
        report.add("note", "Siril has no settings file yet; open Siril once, then run this again")
        return report, edits
    text = siril_path.read_text()
    new_text = text
    siril_actions = []
    for key, wrapper in pins.SIRIL_CONFIG_KEYS:
        value = str(home / wrapper.relative_path)
        new_text, state = set_ini_value_if_unset(new_text, pins.SIRIL_CONFIG_SECTION, key, value)
        label = f"{siril_path} [{pins.SIRIL_CONFIG_SECTION}] {key}"
        if state == "already set":
            current = next(
                (line.split("=", 1)[1].strip() for line in text.splitlines() if line.startswith(f"{key}=")),
                "",
            )
            report.add("present", f"{label} = {current}")
        else:
            report.add("missing", f"{label} (empty)")
            siril_actions.append(f"set {label} = {value}")
    if new_text != text:
        if siril_is_running():
            report.add(
                "note", "Siril is open and rewrites its settings when it closes; close it and run again"
            )
        else:
            edits.append(ConfigEdit(siril_path, new_text))
            report.actions += siril_actions
    return report, edits


# ---------------------------------------------------------------------------
# Main program
# ---------------------------------------------------------------------------


def _build_argument_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        The parser.
    """
    parser = argparse.ArgumentParser(
        prog="setup_siril_gpu.sh",
        description="Set up the GPU path of the Flatpak Siril (GraXpert, StarNet2, Siril's Python).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Report the state of each piece; change nothing.")
    mode.add_argument(
        "--dry-run", action="store_true", help="Report the state and print what a run would do."
    )
    parser.add_argument(
        "--parts",
        default=",".join(PARTS),
        help=f"Comma-separated parts to handle (default: all): {', '.join(PARTS)}.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Also replace library files and wrappers that differ from the pins (keeps old ones as .bak-*).",
    )
    parser.add_argument(
        "--accept-nvidia-license",
        action="store_true",
        help=(
            "Accept NVIDIA's CUDA and cuDNN license agreements without being asked "
            f"({'; '.join(license_.url for license_ in pins.LICENSES_TO_ACCEPT)}). "
            "Needed when no terminal can ask."
        ),
    )
    parser.add_argument(
        "--graxpert",
        type=Path,
        help=f"The GraXpert program the wrapper starts (default: ~/{pins.GRAXPERT_WRAPPER.default_program}).",
    )
    parser.add_argument(
        "--starnet",
        type=Path,
        help=f"The StarNet2 program the wrapper starts (default: ~/{pins.STARNET_WRAPPER.default_program}).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Check or set up each part of the Siril GPU path.

    Parameters
    ----------
    argv : `Sequence` [`str`], optional
        Command-line arguments. Default: ``sys.argv[1:]``.

    Returns
    -------
    status : `int`
        0 when every part is ready, was set up or was skipped; 1 when a part
        failed during a real run; 2 for a bad argument.
    """
    parser = _build_argument_parser()
    arguments = parser.parse_args(argv)
    parts = [part.strip() for part in arguments.parts.split(",") if part.strip()]
    if unknown := sorted(set(parts) - set(PARTS)):
        parser.error(f"unknown part(s): {', '.join(unknown)}")
    home = Path.home()
    repository = Path(__file__).resolve().parents[3]
    applying = not (arguments.check or arguments.dry_run)

    gpu = has_nvidia_gpu()
    if gpu is None:
        print("No NVIDIA graphics card with a working driver was found (nvidia-smi -L).")
        print("Nothing to set up: Siril, GraXpert and StarNet2 run on the processor.")
        return 0
    print(f"Graphics card: {gpu}")
    if not has_flatpak_siril():
        print(f"The Flatpak Siril ({pins.SIRIL_APP_ID}) is not installed.")
        print("Nothing to set up. Install it with build/linux/install_ubuntu_deps.sh --siril-source=flatpak,")
        print("or keep using another Siril on the processor.")
        return 0

    programs = {
        pins.GRAXPERT_WRAPPER.name: arguments.graxpert or home / pins.GRAXPERT_WRAPPER.default_program,
        pins.STARNET_WRAPPER.name: arguments.starnet or home / pins.STARNET_WRAPPER.default_program,
    }
    work_folder = home / ".var/app" / pins.SIRIL_APP_ID / "cache/tmp/astrometrics-gpu-setup"
    gate = LicenseGate(accepted=arguments.accept_nvidia_license, interactive=sys.stdin.isatty())
    cache = WheelCache(work_folder, gate)
    reports: list[PartReport] = []
    failed: list[str] = []

    def finish(report: PartReport, action: Callable[[], None]) -> None:
        """Print a part's report, then carry out its actions on a real run.

        Parameters
        ----------
        report : `PartReport`
            The part's report.
        action : `Callable` [[], `None`]
            A function with no arguments that carries out the actions.
        """
        reports.append(report)
        report.print()
        if not report.actions:
            return
        if not applying:
            if arguments.dry_run:
                for text in report.actions:
                    print(f"  would     {text}")
            return
        try:
            action()
        except (SetupError, OSError) as error:
            failed.append(report.part)
            print(f"  FAILED    {error}", file=sys.stderr)

    try:
        if "overrides" in parts:
            report, command = check_overrides(home)

            def apply_overrides() -> None:
                """Run the ``flatpak override`` command.

                Raises
                ------
                SetupError
                    The command failed.
                """
                result = run_command(command)
                if result.returncode != 0:
                    raise SetupError(f"flatpak override failed: {result.stderr.strip()}")
                print("  added the missing overrides")

            finish(report, apply_overrides)

        for folder in pins.LIBRARY_FOLDERS:
            if folder.part not in parts:
                continue
            print(f"\nChecking {folder.title} (reads every file; this can take a minute) ...", flush=True)
            report, plan = check_folder(folder, home, arguments.force)
            if folder is pins.GRAXPERT_CUDNN:
                check_host_cuda13(report)
            finish(report, lambda folder=folder, plan=plan: apply_folder(folder, plan, home, cache))

        if "siril-python" in parts:
            report, to_install = check_siril_python(home)
            finish(report, lambda: apply_siril_python(home, to_install, cache))

        if "wrappers" in parts:
            report, to_write = check_wrappers(home, programs, arguments.force)

            def apply_wrappers() -> None:
                """Write the planned wrappers."""
                for wrapper, script in to_write:
                    path = home / wrapper.relative_path
                    write_file_keeping_backup(path, script, 0o755)
                    print(f"  wrote {path}")

            finish(report, apply_wrappers)

        if "config" in parts:
            report, edits = check_config(home, repository)

            def apply_config() -> None:
                """Write the planned settings files."""
                for edit in edits:
                    write_file_keeping_backup(edit.path, edit.text, edit.path.stat().st_mode & 0o777)
                    print(f"  updated {edit.path} (the old copy is kept as .bak-*)")

            finish(report, apply_config)
    finally:
        shutil.rmtree(work_folder, ignore_errors=True)

    print("\nSummary")
    for report in reports:
        if report.part in failed:
            status = "failed"
        elif report.skipped:
            status = "skipped"
        elif report.ready:
            status = "ready"
        elif applying:
            status = "set up (run --check to confirm)"
        else:
            status = "needs setup"
        print(f"  {report.part:<15} {status}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
