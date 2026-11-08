"""Purpose: Test the set-up of the simulator's world.

Description: The INDI server of a world has its own socket and its own home
folder, so two worlds run side by side and the simulators never touch the
user's ``~/.indi``. The world's settings keep the real telescope and guiding
sections and change only the endpoint keys. The star catalog is optional: a
missing one is a note, not an error. The live tests start real ``indiserver``
processes with the INDI simulators (skipped when they are not installed, and
in the fast test run).
"""

import os
import shutil
import subprocess
import time
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from simulator import observatory as observatory_module
from simulator.device_settings import device_settings_from_world
from simulator.exposure_driver import ExposureDriver, stamp_offset
from simulator.observatory import (
    INDI_DRIVERS,
    SimulatedObservatory,
    SimulationKind,
    choose_free_port,
    indi_home,
    indi_socket_name,
    indiserver_command,
    indiserver_environment,
    is_listening,
    launch_notes,
    with_camera_alias,
)
from simulator.settings import SIMULATED_LOCKED_KEYS, endpoint_keys

REAL_SETTINGS = """
["Observatory.Telescope"]
hostname = "stellarmate.local"
indi_port = "7624"
focal_length_mm = "405"
focal_ratio = "5.4"
allow_commands = "false"
remote_pictures_path = "/home/stellarmate/Pictures"
mount_name = "Real Mount"

["Observatory.Guiding"]
phd2_location = "remote"
phd2_port = "4400"
guide_rate = "0.5"

["Observatory.Camera"]
models = "ZWO ASI 533MM Pro"
default_primary_camera = "ZWO ASI 533MM Pro"
default_secondary_camera = "ZWO ASI120MC-S"

["Observatory.Camera.ZWO ASI 533MM Pro"]
name = "ZWO ASI 533MM Pro"
name_aliases = "ZWO CCD ASI533MM Pro"
"pixel_size_μm" = "3.76"
sensor_width_px = "3008"
sensor_height_px = "3008"
bias_levels = [{ gain_setting = "0", offset = "10", temperature_c = -10.0, binning = "1x1" }]

["Observatory.Filters"]
owned = "L, SPEC"

["Image Library"]
path = "/real/library"
"""


def _world(
    tmp_path: Path, real_text: str | None, monkeypatch: pytest.MonkeyPatch, copy_library: bool = True
) -> SimulatedObservatory:
    """Build a library-copy world whose real settings are the given text.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        A temporary folder.
    real_text : `str` or `None`
        The real settings file, or `None` for none at all.
    monkeypatch : `pytest.MonkeyPatch`
        Used to skip the program check.
    copy_library : `bool`, optional
        `True` (default) for a world that makes its settings from a copy of
        the real ones. Its `prepare` would copy databases, so the tests of
        the copy call `_copy_settings_text` directly.

    Returns
    -------
    observatory : `SimulatedObservatory`
        A world whose copy settings come from `real_text`.
    """
    monkeypatch.setattr(observatory_module, "missing_programs", lambda include_overlay=True: [])
    config = tmp_path / "real.toml"
    if real_text is not None:
        config.write_text(real_text, encoding="utf-8")
    observatory = SimulatedObservatory(
        tmp_path / "simulator",
        SimulationKind.SIMULATED_SKY,
        choose_free_port(),
        choose_free_port(),
        copy_library=copy_library,
        real_library=SimpleNamespace(config_file=config),
    )
    return observatory


def test_the_socket_is_inside_the_world_and_short_enough() -> None:
    """A short folder gives its own socket; a long one a short name."""
    short = Path("/tmp/world")
    assert indi_socket_name(short) == "/tmp/world/indiserver.sock"
    long = Path("/tmp/" + "x" * 150)
    name = indi_socket_name(long)
    assert len(name.encode()) <= observatory_module.INDI_SOCKET_NAME_LIMIT
    assert name == indi_socket_name(long)
    assert name != indi_socket_name(Path("/tmp/" + "y" * 150))


def test_the_server_command_has_its_own_socket_and_never_the_default() -> None:
    """The command carries ``-u`` with the world's socket, and the drivers."""
    root = Path("/tmp/world")
    command = indiserver_command(root, 4321)
    assert command[:5] == ["indiserver", "-p", "4321", "-u", "/tmp/world/indiserver.sock"]
    assert tuple(command[5:]) == INDI_DRIVERS
    assert "/tmp/indiserver" not in command


def test_the_server_runs_with_a_home_inside_the_world(tmp_path: Path) -> None:
    """``HOME`` is ``<root>/indi_home``, not the user's home."""
    variables = indiserver_environment(tmp_path)
    assert variables["HOME"] == str(tmp_path / "indi_home") == str(indi_home(tmp_path))
    assert variables["HOME"] != str(Path.home())


def test_the_star_catalog_is_named_only_when_it_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``GSCDAT`` is set when ``gsc`` and its data exist, and not otherwise."""
    data = tmp_path / "gsc_data"
    data.mkdir()
    monkeypatch.setattr(observatory_module.shutil, "which", lambda name: "/usr/bin/gsc")
    assert indiserver_environment(tmp_path, {"GSCDAT": str(data)})["GSCDAT"] == str(data)
    monkeypatch.setattr(observatory_module, "STAR_CATALOG_DATA_FOLDERS", (data,))
    assert indiserver_environment(tmp_path, {})["GSCDAT"] == str(data)
    monkeypatch.setattr(observatory_module.shutil, "which", lambda name: None)
    assert "GSCDAT" not in indiserver_environment(tmp_path, {"GSCDAT": str(data)})


def test_a_missing_star_catalog_is_a_note_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without ``gsc`` the world starts and the launcher reports a note."""
    monkeypatch.setattr(observatory_module.shutil, "which", lambda name: None)
    assert launch_notes() == ["gsc (star catalog): stars are not drawn"]
    assert "gsc" not in observatory_module.REQUIRED_PROGRAMS
    monkeypatch.setattr(observatory_module.shutil, "which", lambda name: "/usr/bin/" + name)
    assert launch_notes() == []


def test_the_server_starts_with_its_own_socket_and_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Starting passes the socket, the home and the settings file."""
    observatory = _world(tmp_path, None, monkeypatch, copy_library=False)
    observatory.prepare()
    launched: dict[str, tuple] = {}
    monkeypatch.setattr(
        observatory,
        "_launch",
        lambda name, command, log_name=None, extra_environment=None: launched.update({
            name: (command, extra_environment)
        }),
    )
    monkeypatch.setattr(observatory, "_wait_listening", lambda ports: None)

    observatory._start_processes()

    command, environment = launched["indiserver"]
    assert command[command.index("-u") + 1] == indi_socket_name(observatory.root)
    assert environment["HOME"] == str(indi_home(observatory.root))
    assert (indi_home(observatory.root) / ".indi").is_dir()
    driver_command = launched["driver"][0]
    assert driver_command[driver_command.index("--config") + 1] == str(observatory.config_path)


def test_the_copy_keeps_every_real_telescope_key_and_changes_only_the_endpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real optics and mount keys stay; the endpoints aim at stand-ins."""
    observatory = _world(tmp_path, REAL_SETTINGS, monkeypatch)
    text = observatory._copy_settings_text()
    settings = tomllib.loads(text)
    telescope = settings["Observatory.Telescope"]
    guiding = settings["Observatory.Guiding"]
    assert telescope["focal_length_mm"] == "405"
    assert telescope["focal_ratio"] == "5.4"
    assert telescope["mount_name"] == "Real Mount"
    assert guiding["guide_rate"] == "0.5"
    assert telescope["hostname"] == "127.0.0.1"
    assert telescope["indi_port"] == str(observatory.indi_port)
    assert telescope["remote_pictures_path"] == str(observatory.standin_home / "Pictures")
    assert telescope["allow_commands"] == "true"
    assert guiding["phd2_location"] == "local"
    assert guiding["phd2_port"] == str(observatory.phd2_port)
    assert settings["Observatory.RemoteTransfer"]["auto_download"] == "true"
    assert settings["Image Library"]["path"] != "/real/library"
    assert "stellarmate" not in text


def test_the_copy_differs_from_the_real_settings_only_in_the_endpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every key of the real telescope and guiding sections is still there."""
    observatory = _world(tmp_path, REAL_SETTINGS, monkeypatch)
    real = tomllib.loads(REAL_SETTINGS)
    copy = tomllib.loads(observatory._copy_settings_text())
    changed = {(section, key) for section, key, _ in endpoint_keys(0, 0, Path())}
    for section in ("Observatory.Telescope", "Observatory.Guiding"):
        for key, value in real[section].items():
            if (section, key) not in changed:
                assert copy[section][key] == value


def test_a_real_file_without_telescope_sections_gets_the_made_up_optic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing sections fall back to today's defaults."""
    observatory = _world(tmp_path, '["Observatory.Camera"]\nmodels = "x"\n', monkeypatch)
    settings = tomllib.loads(observatory._copy_settings_text())
    assert settings["Observatory.Telescope"]["focal_length_mm"] == "405"
    assert settings["Observatory.Telescope"]["focal_ratio"] == "5.4"
    assert settings["Observatory.Telescope"]["hostname"] == "127.0.0.1"
    assert settings["Observatory.Guiding"]["phd2_port"] == str(observatory.phd2_port)


def test_the_locked_keys_match_the_endpoint_keys() -> None:
    """Every endpoint key but the download switch is locked."""
    endpoints = {f"{section}.{key}" for section, key, _ in endpoint_keys(1, 2, Path("/p"))}
    locked_observatory = {key for key in SIMULATED_LOCKED_KEYS if key.startswith("Observatory.")}
    assert locked_observatory == endpoints - {"Observatory.RemoteTransfer.auto_download"}


def test_the_primary_camera_profile_gets_the_simulator_name_as_an_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The copy lists ``CCD Simulator`` as an alias, once."""
    observatory = _world(tmp_path, REAL_SETTINGS, monkeypatch)
    text = observatory._copy_settings_text()
    section = tomllib.loads(text)["Observatory.Camera.ZWO ASI 533MM Pro"]
    assert section["name_aliases"] == "ZWO CCD ASI533MM Pro, CCD Simulator"
    assert section["bias_levels"][0]["offset"] == "10"
    assert with_camera_alias(text, "CCD Simulator") == text
    assert "CCD Simulator" not in (tmp_path / "real.toml").read_text()


def test_a_profile_without_aliases_gets_one_and_no_profile_changes_nothing() -> None:
    """The alias starts a list; a missing profile changes nothing."""
    bare = '["Observatory.Camera"]\ndefault_primary_camera = "A"\n["Observatory.Camera.A"]\nname = "A"\n'
    assert tomllib.loads(with_camera_alias(bare, "CCD Simulator"))["Observatory.Camera.A"][
        "name_aliases"
    ] == ("CCD Simulator")
    nothing = '["Observatory.Camera"]\ndefault_primary_camera = "B"\n'
    assert with_camera_alias(nothing, "CCD Simulator") == nothing


# ----------------------------------------------------------------------
# Live tests: real indiserver processes with the INDI simulators
# ----------------------------------------------------------------------

LIVE = pytest.mark.skipif(
    os.environ.get("ASTROMETRICS_FAST_TEST") == "1"
    or any(shutil.which(name) is None for name in ("indiserver", "indi_setprop", "indi_getprop"))
    or any(shutil.which(driver) is None for driver in INDI_DRIVERS),
    reason="The INDI server and simulators are not installed, or ASTROMETRICS_FAST_TEST is set.",
)


def _abstract_sockets() -> set[str]:
    """List the local (abstract) sockets that listen.

    Returns
    -------
    names : `set` [`str`]
        The names, starting with ``@``.
    """
    names = set()
    for line in Path("/proc/net/unix").read_text().splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 8 and fields[7].startswith("@") and fields[3] == "00010000":
            names.add(fields[7])
    return names


def _start_server(root: Path, monkeypatch: pytest.MonkeyPatch, home: Path) -> tuple[subprocess.Popen, int]:
    """Start one world's INDI server as the simulator does.

    Parameters
    ----------
    root : `pathlib.Path`
        The world's root folder.
    monkeypatch : `pytest.MonkeyPatch`
        Not used; kept so a caller can arrange the environment first.
    home : `pathlib.Path`
        The user's home folder in this test (a temporary one).

    Returns
    -------
    server : `tuple` [`subprocess.Popen`, `int`]
        The process and its TCP port.
    """
    (indi_home(root) / ".indi").mkdir(parents=True)
    port = choose_free_port()
    server = subprocess.Popen(
        indiserver_command(root, port),
        env={**os.environ, "HOME": str(home), **indiserver_environment(root)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.time() + 20
    while time.time() < deadline and not is_listening(port):
        time.sleep(0.2)
    assert is_listening(port), "the INDI server did not listen"
    return server, port


def _stop(server: subprocess.Popen) -> None:
    """End a server and wait for it.

    Parameters
    ----------
    server : `subprocess.Popen`
        The process.
    """
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait()


def _get(port: int, name: str) -> str:
    """Read one INDI property value.

    Parameters
    ----------
    port : `int`
        The server's TCP port.
    name : `str`
        ``device.property.element``.

    Returns
    -------
    value : `str`
        The value, or an empty text.
    """
    result = subprocess.run(
        ["indi_getprop", "-h", "127.0.0.1", "-p", str(port), "-t", "3", name],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip().partition("=")[2]


@LIVE
def test_two_worlds_run_side_by_side_without_the_default_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two servers listen at once, each on its own socket."""
    default_before = "@/tmp/indiserver" in _abstract_sockets()
    first_root, second_root = tmp_path / "one", tmp_path / "two"
    home = tmp_path / "user_home"
    (home / ".indi").mkdir(parents=True)
    first, first_port = _start_server(first_root, monkeypatch, home)
    try:
        second, second_port = _start_server(second_root, monkeypatch, home)
        try:
            sockets = _abstract_sockets()
            assert "@" + indi_socket_name(first_root) in sockets
            assert "@" + indi_socket_name(second_root) in sockets
            assert ("@/tmp/indiserver" in sockets) == default_before
            assert first.poll() is None
            assert second.poll() is None
            assert _get(first_port, "CCD Simulator.DRIVER_INFO.DRIVER_NAME") == "CCD Simulator"
            assert _get(second_port, "CCD Simulator.DRIVER_INFO.DRIVER_NAME") == "CCD Simulator"
        finally:
            _stop(second)
    finally:
        _stop(first)


@LIVE
def test_the_drivers_save_their_settings_in_the_world_and_never_in_the_users_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A driver config save lands in the world, not the user's home."""
    root = tmp_path / "world"
    home = tmp_path / "user_home"
    (home / ".indi").mkdir(parents=True)
    server, port = _start_server(root, monkeypatch, home)
    try:
        for command in (
            ["CCD Simulator.CONNECTION.CONNECT=On"],
            ["CCD Simulator.CONFIG_PROCESS.CONFIG_SAVE=On"],
        ):
            subprocess.run(["indi_setprop", "-h", "127.0.0.1", "-p", str(port), *command], check=True)
            time.sleep(2)
    finally:
        _stop(server)
    saved = sorted(path.name for path in (indi_home(root) / ".indi").iterdir())
    assert any(name.startswith("CCD Simulator_config") for name in saved), saved
    assert list((home / ".indi").iterdir()) == []


@LIVE
def test_the_simulators_take_the_real_sensor_optics_and_capture_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The set-up shows in the properties and in the next frame."""
    root = tmp_path / "world"
    home = tmp_path / "user_home"
    (home / ".indi").mkdir(parents=True)
    observatory = _world(tmp_path, REAL_SETTINGS, monkeypatch)
    settings = tomllib.loads(observatory._copy_settings_text())
    settings["Observatory.Camera.ZWO ASI120MC-S"] = {
        "pixel_size_μm": "3.75",
        "sensor_width_px": "1280",
        "sensor_height_px": "960",
    }
    server, port = _start_server(root, monkeypatch, home)
    try:
        driver = ExposureDriver(
            port, root / "standin", Path("log"), root / "stats.json", 1.0, "M 13", 16.695, 36.46,
            device_settings_from_world(settings),
        )  # fmt: skip
        for device in ("CCD Simulator", "Guide Simulator", "Filter Simulator"):
            driver._set(f"{device}.CONNECTION.CONNECT=On")
        time.sleep(3)
        assert driver.apply_device_settings() == []
        assert _get(port, "CCD Simulator.CCD_INFO.CCD_MAX_X") == "3008"
        assert float(_get(port, "CCD Simulator.CCD_INFO.CCD_PIXEL_SIZE")) == pytest.approx(3.76, abs=1e-4)
        assert _get(port, "Guide Simulator.CCD_INFO.CCD_MAX_Y") == "960"
        assert _get(port, "CCD Simulator.SCOPE_INFO.FOCAL_LENGTH") == "405"
        assert _get(port, "CCD Simulator.CCD_GAIN.GAIN") == "0"
        assert _get(port, "CCD Simulator.CCD_OFFSET.OFFSET") == "10"
        pictures = root / "frames"
        pictures.mkdir()
        driver._set(
            "CCD Simulator.UPLOAD_MODE.UPLOAD_LOCAL=On",
            f"CCD Simulator.UPLOAD_SETTINGS.UPLOAD_DIR={pictures}",
            "CCD Simulator.UPLOAD_SETTINGS.UPLOAD_PREFIX=t_XXX",
        )
        driver._set("CCD Simulator.CCD_EXPOSURE.CCD_EXPOSURE_VALUE=1")
        deadline = time.time() + 30
        while time.time() < deadline and not list(pictures.glob("*.fits")):
            time.sleep(0.5)
        time.sleep(1)
    finally:
        _stop(server)
    from astropy.io import fits

    frames = list(pictures.glob("*.fits"))
    assert frames, "the camera simulator wrote no frame"
    with fits.open(frames[0], memmap=False) as handle:
        header = handle[0].header
    assert header["NAXIS1"] == header["NAXIS2"] == 3008
    assert header["FOCALLEN"] == pytest.approx(405.0)
    assert header["GAIN"] == pytest.approx(0.0)
    assert header["INSTRUME"] == "CCD Simulator"
    assert "OFFSET" not in header
    stamped = tmp_path / "stamped.fits"
    stamp_offset(frames[0], stamped, driver.camera_offset())
    assert fits.getheader(stamped)["OFFSET"] == 10


@LIVE
def test_the_running_driver_files_frames_with_the_offset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A frame taken by the driver reaches the picture folder stamped."""
    import threading

    from astropy.io import fits

    root = tmp_path / "world"
    home = tmp_path / "user_home"
    (home / ".indi").mkdir(parents=True)
    observatory = _world(tmp_path, REAL_SETTINGS, monkeypatch)
    settings = tomllib.loads(observatory._copy_settings_text())
    server, port = _start_server(root, monkeypatch, home)
    driver = ExposureDriver(
        port, root / "standin", Path("log"), root / "stats.json", 1.0, "M 13", 16.695, 36.46,
        device_settings_from_world(settings),
    )  # fmt: skip

    def operate() -> None:
        """Connect, cool and expose, as the driver's run does."""
        if driver.connect_devices():
            driver.wait_for_cooling()
            driver.take_exposures()

    worker = threading.Thread(target=operate)
    try:
        worker.start()
        deadline = time.time() + 150
        while time.time() < deadline and not list(driver.pictures_directory.glob("*.fits")):
            time.sleep(0.5)
    finally:
        driver.stop_event.set()
        worker.join(timeout=30)
        _stop(server)
    frames = list(driver.pictures_directory.glob("*.fits"))
    assert frames, "no frame reached the picture folder"
    header = fits.getheader(frames[0])
    assert header["OFFSET"] == 10
    assert header["GAIN"] == pytest.approx(0.0)
    assert header["FILTER"] == "L"
    assert header["CCD-TEMP"] == pytest.approx(-10.0, abs=1.0)
    assert driver.cooling_seconds is not None
    assert driver.cooling_seconds < 120
    assert not list(driver.staging_directory.glob("*.fits"))


def test_the_picture_folder_exists_when_the_processes_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A prebuilt world still gets ``Pictures`` and the INDI home."""
    observatory = _world(tmp_path, None, monkeypatch, copy_library=False)
    observatory.prepare()
    for folder in (observatory.standin_home / "Pictures", indi_home(observatory.root) / ".indi"):
        folder.rmdir()
    monkeypatch.setattr(observatory, "_launch", lambda *args, **kwargs: None)
    monkeypatch.setattr(observatory, "_wait_listening", lambda ports: None)
    observatory._start_processes()
    assert (observatory.standin_home / "Pictures").is_dir()
    assert (indi_home(observatory.root) / ".indi").is_dir()
