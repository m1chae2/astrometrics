"""Purpose: Live test of the real PHD2 in a simulated observatory.

Description: Starts the pieces the interactive simulation mode starts, in a
temporary world: an INDI server with the telescope and guide camera
simulators, and the real PHD2 (``~/.local/bin/phd2``) with its profile written
by `simulator.phd2_launch`. Then the guiding step (`run_guiding_step`) brings
PHD2 to guiding, the public PHD2 client reads real guide steps, and after PHD2
quits the guide log it wrote is parsed with the app's own parser.

This is the acceptance check for the real PHD2: the guide steps and the
guide log come from the real PHD2, not from a stand-in.

Safety: PHD2 runs with ``HOME`` inside the temporary world, never the user's
home, and on its own virtual display (``xvfb-run``) when one is installed, so
no window opens on the user's screen. The INDI server has its own socket and
home. Every process is stopped at the end. The test is skipped when PHD2, the
INDI programs or a display are missing, and when ``ASTROMETRICS_FAST_TEST`` is
``1``.

Run with ``.venv/bin/pytest simulator/test/test_phd2_live.py -q -s`` (about
three minutes: PHD2 calibrates before it guides).
"""

import importlib.util
import math
import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from simulator import device_settings, observatory, phd2_launch
from simulator.guiding_operator import GUIDING, mount_is_tracking, run_guiding_step
from simulator.phd2_launch import GUIDE_CAMERA_DEVICE as GUIDE
from simulator.phd2_launch import MOUNT_DEVICE as MOUNT
from wayfindinglib.drivers import PHD2Client
from wayfindinglib.drivers.phd2.guide_log_parser import parse_guide_log_file
from wayfindinglib.drivers.phd2.phd2_events import parse_guide_step_event

RECORDER = Path(__file__).resolve().parents[2] / "build" / "tpm" / "record_phd2_contract.py"
INDI_PROGRAMS = (
    "indiserver",
    "indi_setprop",
    "indi_getprop",
    *observatory.INDI_DRIVERS[:1],
    "indi_simulator_guide",
)

pytestmark = pytest.mark.skipif(
    os.environ.get("ASTROMETRICS_FAST_TEST") == "1"
    or phd2_launch.find_phd2() is None
    or phd2_launch.display_prefix() is None
    or any(shutil.which(name) is None for name in INDI_PROGRAMS),
    reason="PHD2, a display or the INDI simulators are missing, or ASTROMETRICS_FAST_TEST is set.",
)

#: M 13, where the real-catalog test points the mount.
M13_RA_HOURS = 16.695
M13_DEC_DEGREES = 36.46
#: Seconds PHD2 guides before the test reads the steps.
GUIDING_SECONDS = 20.0
#: Guide controller settings that differ from PHD2's defaults (aggressiveness
#: 0.7, hysteresis 0.1), so the log header shows whether they took effect.
CONTROLLER_SETTINGS = {
    "aggressiveness": 0.45,
    "minimum_move": 0.4,
    "hysteresis": 0.25,
    "exposure_seconds": 1.0,
}


def _recorder() -> ModuleType:
    """Load the contract recorder, which holds the made-up star catalog.

    Returns
    -------
    module : `types.ModuleType`
        ``build/tpm/record_phd2_contract.py``, loaded by path.
    """
    spec = importlib.util.spec_from_file_location("record_phd2_contract", RECORDER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _start_world(tmp_path: Path, real_catalog: bool) -> Iterator[dict]:
    """Start an INDI server with the simulators in a temporary world.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The temporary folder that holds the world.
    real_catalog : `bool`
        `True` to give the simulators the installed GSC star catalog (the
        world's own ``GSCDAT``), `False` for the recorder's made-up one.

    Yields
    ------
    world : `dict`
        ``root``, ``home`` (the stand-in telescope computer's home),
        ``indi_port`` and ``processes``.
    """
    recorder = _recorder()
    root = tmp_path / "world"
    home = observatory.standin_home(root)
    (home / "Pictures").mkdir(parents=True)
    (observatory.indi_home(root) / ".indi").mkdir(parents=True)
    environment = {**os.environ, "HOME": str(observatory.indi_home(root))}
    if real_catalog:
        environment.update(observatory.indiserver_environment(root))
    else:
        star_catalog = root / "bin"
        star_catalog.mkdir()
        # A made-up star catalog, so the guide camera always has stars to find.
        (star_catalog / "gsc").write_text(recorder.FAKE_GSC, encoding="utf-8")
        (star_catalog / "gsc").chmod(0o755)
        environment["PATH"] = f"{star_catalog}{os.pathsep}{os.environ['PATH']}"
    indi_port = observatory.choose_free_port()
    server = subprocess.Popen(
        [
            "indiserver",
            *("-p", str(indi_port)),
            *("-u", observatory.indi_socket_name(root)),
            "indi_simulator_telescope",
            "indi_simulator_guide",
        ],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    processes = [server]
    try:
        assert recorder.wait_for_port(indi_port, 20), "the INDI server did not listen"
        if real_catalog:
            _prepare_real_sky(indi_port)
        else:
            recorder.prepare_simulators(indi_port)
        yield {"root": root, "home": home, "indi_port": indi_port, "processes": processes}
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        time.sleep(1)
        for process in processes:
            try:
                os.killpg(process.pid, 9)
            except OSError:
                pass


def _set_property(indi_port: int, *specifications: str) -> None:
    """Set INDI properties with ``indi_setprop``, one call each.

    Parameters
    ----------
    indi_port : `int`
        The world's INDI port.
    *specifications : `str`
        ``device.property.element=value`` texts.
    """
    for specification in specifications:
        subprocess.run(["indi_setprop", "-p", str(indi_port), specification], check=False, timeout=30)


def _prepare_real_sky(indi_port: int) -> None:
    """Set up the guide camera like the world does, and point at M 13.

    The guide camera gets the real guide scope's geometry (1280 x 960
    pixels of 3.75 micrometers behind 121.05 mm), the narrowest star PHD2 can
    use (`simulator.device_settings`) and a quiet mount (a slow drift and a
    small random jitter, like the typical runs of the unguided mount error
    fit). The mount is sent to M 13, tracking, and waited for.

    Parameters
    ----------
    indi_port : `int`
        The world's INDI port.

    Raises
    ------
    RuntimeError
        If the mount does not arrive in time.
    """
    settings = f"{GUIDE}.SIMULATOR_SETTINGS."
    scale = 206.265 * 3.75 / 121.05
    seeing = device_settings.guide_star_seeing_floor(scale)
    _set_property(indi_port, f"{MOUNT}.CONNECTION.CONNECT=On", f"{GUIDE}.CONNECTION.CONNECT=On")
    time.sleep(3)
    _set_property(
        indi_port,
        f"{GUIDE}.SCOPE_INFO.FOCAL_LENGTH;APERTURE=121.05;32",
        f"{settings}SIM_XRES;SIM_YRES;SIM_XSIZE;SIM_YSIZE=1280;960;3.75;3.75",
        f"{settings}SIM_SEEING={seeing}",
        f"{settings}SIM_RA_DRIFT=0.05",
        f"{settings}SIM_DEC_DRIFT=-0.03",
        f"{settings}SIM_RA_RAND=0.5",
        f"{settings}SIM_DEC_RAND=0.5",
        f"{MOUNT}.TELESCOPE_TRACK_STATE.TRACK_ON=On",
        f"{MOUNT}.EQUATORIAL_EOD_COORD.RA;DEC={M13_RA_HOURS};{M13_DEC_DEGREES}",
    )
    time.sleep(2)
    for _ in range(120):
        if mount_is_tracking(indi_port):
            return
        time.sleep(1)
    raise RuntimeError("the simulated mount did not arrive at M 13")


@pytest.fixture
def world(tmp_path: Path) -> Iterator[dict]:
    """Start the simulators with the recorder's made-up star catalog.

    Yields
    ------
    world : `dict`
        See `_start_world`.
    """
    yield from _start_world(tmp_path, real_catalog=False)


@pytest.fixture
def real_catalog_world(tmp_path: Path) -> Iterator[dict]:
    """Start the simulators with the installed GSC catalog, pointed at M 13.

    Yields
    ------
    world : `dict`
        See `_start_world`.
    """
    if not observatory.star_catalog_installed() or observatory.star_catalog_data_folder() is None:
        pytest.skip("the GSC star catalog is not installed")
    yield from _start_world(tmp_path, real_catalog=True)


def _start_phd2(
    world: dict, focal_length_mm: float = 400.0, pixel_size_um: float = 3.75
) -> tuple[phd2_launch.Phd2Instance, subprocess.Popen]:
    """Write PHD2's profile in the world and start the real PHD2.

    Parameters
    ----------
    world : `dict`
        The world from `_start_world`.
    focal_length_mm, pixel_size_um : `float`, optional
        The guide scope and the guide camera's pixel size for the profile.

    Returns
    -------
    record : `Phd2Instance`
        What the guiding step needs.
    process : `subprocess.Popen`
        The PHD2 process (also kept in the world to be stopped at the end).
    """
    home: Path = world["home"]
    root: Path = world["root"]
    instance = phd2_launch.choose_instance()
    assert instance is not None
    record = phd2_launch.write_profile(
        home, root, instance, world["indi_port"], CONTROLLER_SETTINGS, focal_length_mm, pixel_size_um
    )
    # The user's display is not used: PHD2 gets its own virtual one.
    environment = {key: value for key, value in os.environ.items() if key not in phd2_launch.UNSET_VARIABLES}
    if shutil.which("xvfb-run"):
        environment.pop("DISPLAY", None)
    environment.update(phd2_launch.phd2_environment(home, environment))
    phd2_process = subprocess.Popen(
        phd2_launch.phd2_command(phd2_launch.find_phd2(), instance, environment),
        env=environment,
        stdout=(root / "phd2.out").open("w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    world["processes"].append(phd2_process)
    deadline = time.time() + 90
    while time.time() < deadline and not observatory.is_listening(record.event_port):
        time.sleep(0.5)
    assert observatory.is_listening(record.event_port), (root / "phd2.out").read_text()
    return record, phd2_process


def test_real_phd2_guides_and_writes_a_genuine_guide_log(world: dict) -> None:
    """The guiding step reaches Guiding; the steps and log are PHD2's."""
    home: Path = world["home"]
    record, phd2_process = _start_phd2(world)
    instance = record.instance

    outcome = run_guiding_step(record, is_tracking=mount_is_tracking)
    print(f"guiding step: {outcome}")
    assert outcome.status == GUIDING, outcome.detail

    # The app's way of reading PHD2: the public client's event stream.
    steps = []
    stream = PHD2Client(host="127.0.0.1", port=record.event_port).events()
    end = time.time() + GUIDING_SECONDS
    for event in stream:
        sample = parse_guide_step_event(event)
        if sample is not None:
            steps.append(sample)
        if time.time() > end and len(steps) >= 5:
            break
    assert len(steps) >= 5

    # Stop PHD2 politely, so it closes its guide log.
    with PHD2Client(host="127.0.0.1", port=record.event_port).open_session() as session:
        assert session.get_profile()["name"] == record.profile_name
        session.stop_capture()
        session.shutdown()
    phd2_process.wait(timeout=60)
    logs = sorted((home / "PHD2").glob("PHD2_GuideLog_*.txt"))
    assert logs, "PHD2 wrote no guide log in the stand-in home"
    parsed = parse_guide_log_file(str(logs[-1]))
    assert parsed.written_by_ekos is False
    assert parsed.sections, "the app's parser found no guiding section in PHD2's log"
    assert any(section.samples for section in parsed.sections)
    # The controller settings written into the profile are in the log header.
    controller = next(section.controller for section in parsed.sections if section.samples)
    print(f"controller read from the real log header: {controller}")
    assert controller.exposure_seconds == pytest.approx(1.0)
    assert controller.ra_axis is not None
    assert controller.ra_axis.aggressiveness == pytest.approx(CONTROLLER_SETTINGS["aggressiveness"])
    assert controller.ra_axis.hysteresis == pytest.approx(CONTROLLER_SETTINGS["hysteresis"])
    # PHD2 stayed inside the world: the profile is in the stand-in home.
    assert (home / phd2_launch.config_file_name(instance)).is_file()


def test_real_phd2_finds_a_real_gsc_star_and_guides(real_catalog_world: dict) -> None:
    """With the installed GSC catalog, PHD2 finds a star at M 13 and guides.

    The guide camera has the real guide scope's geometry (6.39 arcsec per
    pixel). The star is the width `simulator.device_settings` sets, so PHD2
    keeps it instead of rejecting it as a hot pixel.
    """
    world = real_catalog_world
    record, phd2_process = _start_phd2(world, 121.05, 3.75)
    outcome = run_guiding_step(record, is_tracking=mount_is_tracking)
    print(f"guiding step with real GSC stars: {outcome}")
    assert outcome.status == GUIDING, outcome.detail
    steps = []
    end = time.time() + GUIDING_SECONDS
    for event in PHD2Client(host="127.0.0.1", port=record.event_port).events():
        sample = parse_guide_step_event(event)
        if sample is not None:
            steps.append(sample)
        if time.time() > end and len(steps) >= 5:
            break
    assert len(steps) >= 5
    # The star is a bright one: a real night guides on a signal-to-noise
    # ratio of 20 or more.
    assert all(step.snr is not None and step.snr > 20 for step in steps), [step.snr for step in steps]
    with PHD2Client(host="127.0.0.1", port=record.event_port).open_session() as session:
        session.stop_capture()
        session.shutdown()
    phd2_process.wait(timeout=60)
    # The guide log PHD2 wrote holds the raw errors, in arcseconds.
    logs = sorted((world["home"] / "PHD2").glob("PHD2_GuideLog_*.txt"))
    assert logs, "PHD2 wrote no guide log"
    sections = parse_guide_log_file(str(logs[-1])).sections
    samples = [sample for section in sections for sample in section.samples]
    assert len(samples) >= 5, [
        (
            section.frames_total,
            section.frames_with_error_code,
            section.frames_without_pixel_scale,
            section.pixel_scale_arcsec_per_px,
        )
        for section in sections
    ]
    rms_ra = math.sqrt(sum(sample["dra"] ** 2 for sample in samples) / len(samples))
    rms_dec = math.sqrt(sum(sample["ddec"] ** 2 for sample in samples) / len(samples))
    print(
        f"{len(steps)} guide steps seen, {len(samples)} in the log; "
        f"RMS RA {rms_ra:.2f}, Dec {rms_dec:.2f} arcsec"
    )
    # A quiet mount stays within a few arcseconds (one pixel is 6.4).
    assert rms_ra < 10.0
    assert rms_dec < 10.0


def test_the_observatory_runs_the_real_phd2_and_replays_no_guide_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The world starts the real PHD2 on its instance port and no replay."""
    if observatory.unavailable_reason(include_overlay=False) is not None:
        pytest.skip(observatory.unavailable_reason(include_overlay=False))
    instance = phd2_launch.choose_instance()
    assert instance is not None
    world = observatory.SimulatedObservatory(
        tmp_path / "world",
        observatory.SimulationKind.SIMULATED_SKY,
        observatory.choose_free_port(),
        phd2_launch.event_port(instance),
        guider=observatory.GuiderKind.PHD2,
        controller_settings={"aggressiveness": 0.6},
    )
    try:
        world.start()
        world.wait_ready()
        assert world.notes == []
        assert world.status()["guider"] == "phd2"
        assert world.phd2 is not None
        with PHD2Client(host="127.0.0.1", port=world.phd2.event_port).open_session() as session:
            assert session.get_profile()["name"] == world.phd2.profile_name
        profile = (world.standin_home / phd2_launch.config_file_name(instance)).read_text(encoding="utf-8")
        assert "aggression=0.6" in profile
        # No guide log is replayed into the stand-in's Ekos folder.
        replayed = list((world.standin_home / ".local" / "share" / "kstars" / "guidelogs").glob("*.txt"))
        assert not replayed, "an empty or replayed guide log was written next to PHD2's own"
    finally:
        world.stop()
