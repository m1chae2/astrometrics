"""Purpose: Tests for the Siril GPU setup program.

Description: Checks the pure parts of ``siril_gpu_setup.py``: reading
Flatpak key files and planning the overrides (only missing settings are
added, nothing is removed, a different value is a conflict), checking files
against their SHA-256 pins (a mismatch is refused and the old file kept),
filling in the wrapper templates, comparing package versions, and setting a
value in the app's and Siril's settings only where it is empty. Then it runs
the whole program in a temporary home folder with stand-in ``flatpak`` and
``nvidia-smi`` commands, so nothing on the real machine is read or changed.

Run with ``.venv/bin/pytest build/linux/siril_gpu/test -q``.
"""

import hashlib
import os
import re
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- runs the setup program against stand-in commands
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

SETUP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SETUP_DIR))

import siril_gpu_pins as pins  # ruff: ignore[module-import-not-at-top-of-file]
import siril_gpu_setup as setup  # ruff: ignore[module-import-not-at-top-of-file]

HOME = Path("/home/tester")

#: What ``flatpak override --user --show`` printed on the machine the pins
#: were taken from, with the home folder replaced.
CURRENT_OVERRIDE = """[Context]
filesystems=!~/.astropy;/usr/local/lib:ro;/usr/local/lib/ollama/cuda_v12:ro;~/cuda_libs:ro;
devices=all;dri;

[Environment]
TMPDIR=/home/tester/.var/app/org.siril.Siril/cache/tmp
LD_LIBRARY_PATH=/home/tester/cuda_libs
TF_FORCE_GPU_ALLOW_GROWTH=true
"""

#: What ``flatpak info --show-permissions`` printed on that machine.
EFFECTIVE_PERMISSIONS = """[Context]
shared=ipc;network;
filesystems=~/cuda_libs:ro;host;

[Session Bus Policy]
org.freedesktop.Flatpak=talk
org.gtk.vfs.*=talk
"""


def permissions(text: str) -> setup.FlatpakPermissions:
    """Read permissions from a key-file text.

    Parameters
    ----------
    text : `str`
        The key file.

    Returns
    -------
    permissions : `FlatpakPermissions`
        The permissions.
    """
    return setup.FlatpakPermissions.from_keyfile(text)


# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------


def test_parse_keyfile_reads_sections() -> None:
    """The key-file reader splits sections and keys."""
    sections = setup.parse_keyfile(CURRENT_OVERRIDE)
    assert sections["Context"]["devices"] == "all;dri;"
    assert sections["Environment"]["TF_FORCE_GPU_ALLOW_GROWTH"] == "true"


def test_split_filesystem_expands_home_and_reads_mode() -> None:
    """A filesystems entry splits into folder, hidden flag and mode."""
    assert setup.split_filesystem("~/cuda_libs:ro", HOME) == ("/home/tester/cuda_libs", False, "ro")
    assert setup.split_filesystem("!~/.astropy", HOME) == ("/home/tester/.astropy", True, "")
    assert setup.split_filesystem("/usr/local/lib/", HOME) == ("/usr/local/lib", False, "")


def test_plan_overrides_finds_nothing_missing_on_the_pinned_machine() -> None:
    """The overrides of the machine the pins came from need nothing more."""
    plan = setup.plan_overrides(permissions(CURRENT_OVERRIDE), permissions(EFFECTIVE_PERMISSIONS), HOME)
    assert plan.flags == []
    assert plan.missing == []
    assert plan.conflicts == []
    assert len(plan.notes) == 2, "the two /usr entries have no effect"


def test_plan_overrides_adds_only_missing_settings() -> None:
    """An empty override gets every pinned setting and nothing else."""
    plan = setup.plan_overrides(permissions(""), permissions(EFFECTIVE_PERMISSIONS), HOME)
    assert plan.flags == [
        "--filesystem=~/cuda_libs:ro",
        "--nofilesystem=~/.astropy",
        "--device=all",
        "--device=dri",
        "--env=LD_LIBRARY_PATH=/home/tester/cuda_libs",
        "--env=TMPDIR=/home/tester/.var/app/org.siril.Siril/cache/tmp",
        "--env=TF_FORCE_GPU_ALLOW_GROWTH=true",
    ]
    assert not any(flag.startswith("--no") and "cuda" in flag for flag in plan.flags)


def test_plan_overrides_adds_the_flatpak_talk_name_only_when_missing() -> None:
    """The ``flatpak-spawn`` bus name is added only when Siril lacks it."""
    plan = setup.plan_overrides(permissions(CURRENT_OVERRIDE), permissions("[Context]\n"), HOME)
    assert plan.flags == ["--talk-name=org.freedesktop.Flatpak"]


def test_plan_overrides_reports_a_different_value_as_a_conflict() -> None:
    """A setting with another value is a conflict and is left alone."""
    text = CURRENT_OVERRIDE.replace(
        "TMPDIR=/home/tester/.var/app/org.siril.Siril/cache/tmp", "TMPDIR=/scratch"
    )
    text = text.replace("~/cuda_libs:ro", "~/cuda_libs")
    plan = setup.plan_overrides(permissions(text), permissions(EFFECTIVE_PERMISSIONS), HOME)
    assert plan.flags == []
    assert len(plan.conflicts) == 2


def test_plan_overrides_accepts_cuda_libs_among_other_library_folders() -> None:
    """``LD_LIBRARY_PATH`` may list ``~/cuda_libs`` among other folders."""
    text = CURRENT_OVERRIDE.replace(
        "LD_LIBRARY_PATH=/home/tester/cuda_libs", "LD_LIBRARY_PATH=/opt/lib:/home/tester/cuda_libs"
    )
    plan = setup.plan_overrides(permissions(text), permissions(EFFECTIVE_PERMISSIONS), HOME)
    assert plan.flags == []
    assert plan.conflicts == []


# ---------------------------------------------------------------------------
# Checksums and wheels
# ---------------------------------------------------------------------------


def make_wheel(folder: Path, members: dict[str, bytes]) -> Path:
    """Write a small stand-in wheel.

    Parameters
    ----------
    folder : `Path`
        Where to write it.
    members : `dict` [`str`, `bytes`]
        The files inside it.

    Returns
    -------
    wheel : `Path`
        The wheel.
    """
    wheel = folder / "demo_pkg-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return wheel


def pin_for(member: str, data: bytes, **extra: tuple[str, ...]) -> pins.PinnedFile:
    """Pin a stand-in file.

    Parameters
    ----------
    member : `str`
        Its path inside the wheel.
    data : `bytes`
        Its contents.
    **extra : `tuple` [`str`, ...]
        Further `PinnedFile` fields.

    Returns
    -------
    pinned : `PinnedFile`
        The pin.
    """
    return pins.PinnedFile(
        member=member,
        name=Path(member).name,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        **extra,
    )


def test_extract_verified_puts_a_matching_file_in_place(tmp_path: Path) -> None:
    """A file that matches its pin is copied out of the wheel."""
    data = b"library bytes"
    wheel = make_wheel(tmp_path, {"lib/libdemo.so.1": data})
    target = setup.extract_verified(wheel, pin_for("lib/libdemo.so.1", data), tmp_path / "out")
    assert target.read_bytes() == data
    assert os.access(target, os.X_OK)


def test_extract_verified_refuses_a_mismatch_and_keeps_the_old_file(tmp_path: Path) -> None:
    """A file whose checksum differs is refused and nothing is replaced."""
    wheel = make_wheel(tmp_path, {"lib/libdemo.so.1": b"tampered bytes"})
    out = tmp_path / "out"
    out.mkdir()
    (out / "libdemo.so.1").write_bytes(b"old")
    pinned = pin_for("lib/libdemo.so.1", b"library bytes!")
    with pytest.raises(setup.ChecksumMismatchError):
        setup.extract_verified(wheel, pinned, out)
    assert (out / "libdemo.so.1").read_bytes() == b"old"
    assert sorted(path.name for path in out.iterdir()) == ["libdemo.so.1"], "no temporary file is left"


def test_extract_verified_refuses_a_missing_member(tmp_path: Path) -> None:
    """A wheel without the pinned file is refused."""
    wheel = make_wheel(tmp_path, {"other": b"x"})
    with pytest.raises(setup.ChecksumMismatchError):
        setup.extract_verified(wheel, pin_for("lib/libdemo.so.1", b"x"), tmp_path / "out")


def test_file_state_tells_pinned_accepted_and_unknown_builds(tmp_path: Path) -> None:
    """A file is pinned, an accepted hand-built copy, unknown or missing."""
    path = tmp_path / "libdemo.so.1"
    pinned = pin_for(
        "lib/libdemo.so.1", b"pinned", hand_installed_sha256=(hashlib.sha256(b"hand").hexdigest(),)
    )
    assert setup.file_state(path, pinned) == "missing"
    path.write_bytes(b"pinned")
    assert setup.file_state(path, pinned) == "present"
    path.write_bytes(b"hand")
    assert setup.file_state(path, pinned) == "accepted"
    path.write_bytes(b"other")
    assert setup.file_state(path, pinned) == "wrong"


def test_apply_folder_installs_missing_files_and_links(tmp_path: Path) -> None:
    """A run puts missing files and links in place and leaves accepted ones."""
    home = tmp_path / "home"
    wheel_file = make_wheel(tmp_path, {"lib/liba.so.1": b"a", "lib/libb.so.1": b"b"})
    wheel = pins.PinnedWheel(
        project="demo-pkg",
        version="1.0",
        files=(
            pin_for("lib/liba.so.1", b"a"),
            pin_for("lib/libb.so.1", b"b", hand_installed_sha256=(hashlib.sha256(b"hand").hexdigest(),)),
        ),
    )
    folder = pins.LibraryFolder("demo", "Demo", "libs", (wheel,), links=(("liba.so", "liba.so.1"),))
    (home / "libs").mkdir(parents=True)
    (home / "libs" / "libb.so.1").write_bytes(b"hand")
    report, plan = setup.check_folder(folder, home, force=False)
    assert [state for state, _ in report.lines] == ["missing", "accepted", "missing"]
    cache = setup.WheelCache(tmp_path / "work")
    cache._wheels["demo-pkg==1.0"] = wheel_file
    setup.apply_folder(folder, plan, home, cache)
    assert (home / "libs" / "liba.so.1").read_bytes() == b"a"
    assert (home / "libs" / "libb.so.1").read_bytes() == b"hand", "an accepted build is kept without --force"
    assert os.readlink(home / "libs" / "liba.so") == "liba.so.1"
    report, plan = setup.check_folder(folder, home, force=False)
    assert report.ready
    assert plan.install == []


def test_wheel_file_name_prefix_follows_the_wheel_naming_standard() -> None:
    """A project name with dashes is written with underscores."""
    assert setup.wheel_file_name_prefix(pins.CUDNN_CU12) == "nvidia_cudnn_cu12-9.24.0.43-"


def test_pip_download_command_pins_the_python_of_a_cpython_wheel(tmp_path: Path) -> None:
    """The ONNX Runtime wheel is fetched for Siril's Python, not the host's."""
    command = setup.pip_download_command(pins.ONNXRUNTIME_GPU, tmp_path)
    assert command[-1] == "onnxruntime-gpu==1.26.0"
    assert "--no-deps" in command
    assert command[command.index("--python-version") + 1] == "3.13"
    assert command[command.index("--abi") + 1] == "cp313"


def test_every_pin_is_a_sha256_with_a_size() -> None:
    """Every pinned checksum has 64 hexadecimal digits; every size is > 0."""
    for folder in pins.LIBRARY_FOLDERS:
        for wheel in folder.wheels:
            for pinned in wheel.files:
                for checksum in (pinned.sha256, *pinned.hand_installed_sha256):
                    assert len(checksum) == 64
                    int(checksum, 16)
                assert pinned.size_bytes > 0


# ---------------------------------------------------------------------------
# Python packages
# ---------------------------------------------------------------------------


def test_compare_packages_reports_each_state() -> None:
    """Packages are present, missing, another version, or conflicting."""
    lines = setup.compare_packages(
        {"onnxruntime_gpu": "1.26.0", "NumPy": "2.3.0", "onnxruntime": "1.26.0"},
        [("onnxruntime-gpu", "1.26.0"), ("numpy", "2.4.2"), ("sympy", "1.14.0")],
        ["onnxruntime"],
    )
    assert [state for state, _ in lines] == ["present", "wrong", "missing", "wrong"]


# ---------------------------------------------------------------------------
# Wrappers
# ---------------------------------------------------------------------------


def test_home_relative_uses_dollar_home_inside_home() -> None:
    """A path under home is written with ``$HOME``; others stay absolute."""
    assert setup.home_relative(HOME / "Applications/x", HOME) == "$HOME/Applications/x"
    assert setup.home_relative(Path("/opt/x"), HOME) == "/opt/x"


def test_render_template_fills_every_placeholder() -> None:
    """Each placeholder is replaced by its value."""
    assert setup.render_template('A="@ONE@"\n', {"ONE": "$HOME/x"}) == 'A="$HOME/x"\n'


@pytest.mark.parametrize(
    "values",
    [{}, {"ONE": "x", "TWO": "y"}, {"ONE": 'x"; rm -rf ~; "'}, {"ONE": "$(whoami)"}, {"ONE": "`id`"}],
)
def test_render_template_refuses_missing_unused_or_unsafe_values(values: dict[str, str]) -> None:
    """A missing, unused or shell-unsafe value is refused.

    Parameters
    ----------
    values : `dict` [`str`, `str`]
        The template values to try.
    """
    with pytest.raises(ValueError, match=r"no value|does not use|unsafe"):
        setup.render_template('A="@ONE@"\n', values)


def test_rendered_wrappers_match_the_hand_built_ones() -> None:
    """The templates give the same commands as the hand-built wrappers."""
    programs = {
        pins.GRAXPERT_WRAPPER.name: HOME / pins.GRAXPERT_WRAPPER.default_program,
        pins.STARNET_WRAPPER.name: HOME / pins.STARNET_WRAPPER.default_program,
    }
    values = setup.wrapper_values(HOME, programs)
    graxpert = setup.render_template(
        (setup.TEMPLATE_FOLDER / pins.GRAXPERT_WRAPPER.template).read_text(), values["GraXpert"]
    )
    starnet = setup.render_template(
        (setup.TEMPLATE_FOLDER / pins.STARNET_WRAPPER.template).read_text(), values["StarNet2"]
    )
    assert 'GRAXPERT="$HOME/Applications/GraXpert-linux/GraXpert"' in setup.effective_lines(graxpert)
    assert 'LIBS="$HOME/.local/share/GraXpert/cudnn13/nvidia/cudnn/lib"' in setup.effective_lines(graxpert)
    assert 'LIBS="$HOME/.local/share/StarNetGPU/ort-gpu-1.26:$HOME/cuda_libs"' in setup.effective_lines(
        starnet
    )
    for script in (graxpert, starnet):
        assert not re.search(r"@[A-Z_]+@", script), "no placeholder is left"


def test_effective_lines_ignore_comments_and_blank_lines() -> None:
    """Comments and blank lines do not count; the ``#!`` line does."""
    assert setup.effective_lines("#!/bin/sh\n# note\n\n  exec x\n") == ["#!/bin/sh", "exec x"]


# ---------------------------------------------------------------------------
# Settings files
# ---------------------------------------------------------------------------

APP_CONFIG = """# settings
["Processing.GraXpert"]
# The command that starts GraXpert.
graxpert_executable = ""

["Processing.Siril"]
siril_executable = "siril-cli"
"""


def test_set_toml_value_fills_an_empty_setting_and_keeps_comments() -> None:
    """An empty setting is filled in place; comments and sections stay."""
    text, state = setup.set_toml_value_if_unset(
        APP_CONFIG, "Processing.GraXpert", "graxpert_executable", "/home/tester/g.sh"
    )
    assert state == "set"
    assert 'graxpert_executable = "/home/tester/g.sh"' in text
    assert "# The command that starts GraXpert." in text
    assert 'siril_executable = "siril-cli"' in text


def test_set_toml_value_leaves_a_set_value_alone() -> None:
    """A setting that already has a value is not changed."""
    text = APP_CONFIG.replace('graxpert_executable = ""', 'graxpert_executable = "/opt/GraXpert"')
    assert setup.set_toml_value_if_unset(text, "Processing.GraXpert", "graxpert_executable", "/x") == (
        text,
        "already set",
    )


def test_set_toml_value_adds_a_missing_key_and_a_missing_section() -> None:
    """A missing key goes into its section; a missing section is added."""
    without_key = APP_CONFIG.replace('graxpert_executable = ""\n', "")
    text, _ = setup.set_toml_value_if_unset(without_key, "Processing.GraXpert", "graxpert_executable", "/x")
    assert tomllib.loads(text)["Processing.GraXpert"]["graxpert_executable"] == "/x"
    assert tomllib.loads(text)["Processing.Siril"]["siril_executable"] == "siril-cli"
    text, _ = setup.set_toml_value_if_unset(
        "[Other]\na = 1", "Processing.GraXpert", "graxpert_executable", "/x"
    )
    assert tomllib.loads(text)["Processing.GraXpert"]["graxpert_executable"] == "/x"


def test_set_ini_value_fills_only_empty_or_missing_keys() -> None:
    """Siril's settings are filled only where empty or missing."""
    text = "[core]\nstarnet_exe=\ngraxpert_path=/opt/g\n[gui]\nx=1\n"
    text, state = setup.set_ini_value_if_unset(text, "core", "starnet_exe", "/home/tester/s.sh")
    assert state == "set"
    assert "starnet_exe=/home/tester/s.sh\n" in text
    assert setup.set_ini_value_if_unset(text, "core", "graxpert_path", "/new")[1] == "already set"
    text, _ = setup.set_ini_value_if_unset(text, "core", "other_key", "v")
    assert text.index("other_key=v") < text.index("[gui]")


def test_newest_siril_config_picks_the_highest_version(tmp_path: Path) -> None:
    """The settings file of the newest Siril version is chosen."""
    for name in ("config.1.2.ini", "config.1.10.ini", "config.1.4.ini", "config.1.4.ini.ABCDEF"):
        (tmp_path / name).write_text("[core]\n")
    assert setup.newest_siril_config(tmp_path).name == "config.1.10.ini"


# ---------------------------------------------------------------------------
# The whole program, with stand-in commands
# ---------------------------------------------------------------------------

FAKE_FLATPAK = """#!/bin/sh
echo "$*" >> "$FAKE_LOG"
case "$1 $2" in
  "info org.siril.Siril") exit 0 ;;
  "info --show-permissions") printf '%s' "$FAKE_EFFECTIVE" ;;
  "override --user")
    if [ "$3" = "--show" ]; then printf '%s' "$FAKE_OVERRIDE"; fi ;;
  "ps --columns=application") ;;
  run*) echo '{"python": "3.13", "packages": {"onnxruntime-gpu": "1.26.0", "sirilpy": "1.0.26"}}' ;;
esac
exit 0
"""


@pytest.fixture
def fake_machine(tmp_path: Path) -> dict[str, str]:
    """Build a temporary home folder and stand-in commands.

    Parameters
    ----------
    tmp_path : `Path`
        The test's temporary folder.

    Returns
    -------
    environment : `dict` [`str`, `str`]
        The environment to run the program with.
    """
    home = tmp_path / "home"
    bin_folder = tmp_path / "bin"
    bin_folder.mkdir()
    (bin_folder / "flatpak").write_text(FAKE_FLATPAK)
    (bin_folder / "nvidia-smi").write_text("#!/bin/sh\necho 'GPU 0: Test Card (UUID: GPU-0)'\n")
    for command in bin_folder.iterdir():
        command.chmod(0o755)
    for program in (pins.GRAXPERT_WRAPPER.default_program, pins.STARNET_WRAPPER.default_program):
        (home / program).parent.mkdir(parents=True)
        (home / program).write_text("#!/bin/sh\n")
    venv_python = home / pins.SIRIL_VENV_RELATIVE_PATH / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("")
    siril_config = home / pins.SIRIL_CONFIG_RELATIVE_FOLDER / "config.1.4.ini"
    siril_config.parent.mkdir(parents=True)
    siril_config.write_text("[core]\nstarnet_exe=\nstarnet_weights=\n")
    app_config = tmp_path / "config" / "astrometrics" / "astrometrics.config.toml"
    app_config.parent.mkdir(parents=True)
    app_config.write_text(APP_CONFIG)
    return {
        # Only the stand-ins: never the real flatpak or nvidia-smi.
        "PATH": str(bin_folder),
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "FAKE_LOG": str(tmp_path / "flatpak.log"),
        "FAKE_OVERRIDE": "[Context]\ndevices=dri;\n",
        "FAKE_EFFECTIVE": EFFECTIVE_PERMISSIONS,
    }


def run_setup(environment: dict[str, str], *arguments: str) -> subprocess.CompletedProcess:
    """Run the setup program with the stand-in environment.

    Parameters
    ----------
    environment : `dict` [`str`, `str`]
        The environment.
    *arguments : `str`
        Command-line arguments.

    Returns
    -------
    result : `subprocess.CompletedProcess`
        The finished program.
    """
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] -- list form, stand-in commands
        [sys.executable, str(SETUP_DIR / "siril_gpu_setup.py"), *arguments],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_check_changes_nothing(fake_machine: dict[str, str]) -> None:
    """``--check`` reports and runs no command that changes anything.

    Parameters
    ----------
    fake_machine : `dict` [`str`, `str`]
        The stand-in environment.
    """
    home = Path(fake_machine["HOME"])
    before = sorted(str(path) for path in home.rglob("*"))
    result = run_setup(fake_machine, "--check")
    assert result.returncode == 0, result.stderr
    assert "missing   filesystem ~/cuda_libs:ro" in result.stdout
    assert "present   onnxruntime-gpu 1.26.0" in result.stdout
    assert "missing   sympy 1.14.0" in result.stdout
    assert sorted(str(path) for path in home.rglob("*")) == before
    log = Path(fake_machine["FAKE_LOG"]).read_text()
    assert "override --user --show" in log
    assert "--filesystem" not in log


def test_dry_run_prints_the_plan_and_changes_nothing(fake_machine: dict[str, str]) -> None:
    """``--dry-run`` prints each action and changes nothing.

    Parameters
    ----------
    fake_machine : `dict` [`str`, `str`]
        The stand-in environment.
    """
    result = run_setup(fake_machine, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "would     run flatpak override --user --filesystem=~/cuda_libs:ro" in result.stdout
    assert "would     download nvidia-cublas-cu12==12.9.2.10" in result.stdout
    assert "would     write" in result.stdout
    assert "--filesystem" not in Path(fake_machine["FAKE_LOG"]).read_text().replace("--show", "")
    assert not (Path(fake_machine["HOME"]) / pins.GRAXPERT_WRAPPER.relative_path).exists()


def test_run_adds_overrides_wrappers_and_empty_settings(fake_machine: dict[str, str]) -> None:
    """A run of the parts that download nothing makes the expected changes.

    Parameters
    ----------
    fake_machine : `dict` [`str`, `str`]
        The stand-in environment.
    """
    home = Path(fake_machine["HOME"])
    result = run_setup(fake_machine, "--parts", "overrides,wrappers,config")
    assert result.returncode == 0, result.stderr + result.stdout
    log = Path(fake_machine["FAKE_LOG"]).read_text()
    override = next(line for line in log.splitlines() if line.startswith("override --user --filesystem"))
    assert "--device=all" in override
    assert "--device=dri" not in override, "a setting already there is not added again"
    assert "--talk-name" not in override
    wrapper = home / pins.GRAXPERT_WRAPPER.relative_path
    assert os.access(wrapper, os.X_OK)
    assert 'GRAXPERT="$HOME/Applications/GraXpert-linux/GraXpert"' in wrapper.read_text()
    app_config = Path(fake_machine["XDG_CONFIG_HOME"]) / "astrometrics" / "astrometrics.config.toml"
    assert f'graxpert_executable = "{wrapper}"' in app_config.read_text()
    siril_config = (home / pins.SIRIL_CONFIG_RELATIVE_FOLDER / "config.1.4.ini").read_text()
    assert f"starnet_exe={home / pins.STARNET_WRAPPER.relative_path}" in siril_config
    assert f"graxpert_path={wrapper}" in siril_config
    assert list(app_config.parent.glob("*.bak-*")), "the old settings file is kept"


def test_no_gpu_skips_everything(fake_machine: dict[str, str], tmp_path: Path) -> None:
    """Without an NVIDIA card the program says so and exits without error.

    Parameters
    ----------
    fake_machine : `dict` [`str`, `str`]
        The stand-in environment.
    tmp_path : `Path`
        The test's temporary folder.
    """
    (tmp_path / "bin" / "nvidia-smi").unlink()
    result = run_setup(fake_machine)
    assert result.returncode == 0
    assert "No NVIDIA graphics card" in result.stdout
    assert not Path(fake_machine["FAKE_LOG"]).exists()


def test_no_flatpak_siril_skips_everything(fake_machine: dict[str, str], tmp_path: Path) -> None:
    """Without the Flatpak Siril the program says so and exits without error.

    Parameters
    ----------
    fake_machine : `dict` [`str`, `str`]
        The stand-in environment.
    tmp_path : `Path`
        The test's temporary folder.
    """
    (tmp_path / "bin" / "flatpak").unlink()
    result = run_setup(fake_machine)
    assert result.returncode == 0
    assert "is not installed" in result.stdout
