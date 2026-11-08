"""Purpose: Start the real PHD2 for the simulated observatory.

Description: PHD2 is the guiding program the user's telescope runs. In the
interactive simulation mode the simulated
telescope runs the real PHD2 too, not a stand-in, against the INDI guide
camera and telescope simulators. This module prepares that PHD2 and builds
the command that starts it:

* It finds the program (``phd2`` on the PATH, or ``~/.local/bin/phd2``) and
  a display for it. PHD2 needs an X display. The app has full control of the
  simulated telescope, so PHD2's window must not open on the user's desktop:
  PHD2 runs under ``xvfb-run`` (a hidden display). Only when ``xvfb-run`` is
  not installed does it use the user's display (``DISPLAY``), and the launcher
  reports that as a status note.
* It picks an *instance number* whose two ports are free. Instance ``i``
  listens on ``4399 + i`` (the event and command server) and ``4299 + i``.
  Instance 1 would use 4400, the usual PHD2 port, so the numbers start at 2.
* It writes PHD2's settings file before PHD2 starts: a profile that connects
  to the world's INDI server, with the guide controller settings (the
  aggressiveness, the minimum move, the hysteresis and the exposure) taken
  from the caller, or PHD2's own defaults when there are none.
* It runs PHD2 with ``HOME`` set to the stand-in telescope computer's home
  folder inside the world, so PHD2 never reads or writes the user's own
  ``~/.PHDGuidingV2`` or ``~/PHD2``. The guide log PHD2 writes lands in
  ``<home>/PHD2``, one of the folders the app's log download searches.
  ``WAYLAND_DISPLAY`` is unset and ``GDK_BACKEND`` is ``x11``, because PHD2
  runs on X.

Safety: `require_world_home` refuses a ``HOME`` that is not inside the
simulator's folder. The module opens no connection of its own beyond looking
at which local ports are free.
"""

import math
import os
import shutil
import socket
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from simulator import observatory_guard

__all__ = [
    "CONTROLLER_SETTING_KEYS",
    "FIRST_INSTANCE",
    "GUIDE_CAMERA_DEVICE",
    "MOUNT_DEVICE",
    "UNSET_VARIABLES",
    "Phd2Instance",
    "calibration_step_milliseconds",
    "choose_instance",
    "command_prefix",
    "config_file_name",
    "controller_settings_text",
    "display_note",
    "display_prefix",
    "event_port",
    "find_phd2",
    "phd2_command",
    "phd2_environment",
    "phd2_missing_note",
    "phd2_missing_reason",
    "profile_name",
    "profile_text",
    "require_world_home",
    "second_port",
    "write_profile",
]

#: The first instance number tried. Instance 1 listens on 4400, the usual
#: port of a real PHD2, which the simulator never uses.
FIRST_INSTANCE = 2
#: The last instance number tried.
LAST_INSTANCE = 40
#: Instance ``i`` listens for events and commands on ``EVENT_PORT_BASE + i``.
EVENT_PORT_BASE = 4399
#: PHD2's second port for instance ``i`` is ``SECOND_PORT_BASE + i``.
SECOND_PORT_BASE = 4299
#: The INDI simulators PHD2 is pointed at (their device names).
GUIDE_CAMERA_DEVICE = "Guide Simulator"
MOUNT_DEVICE = "Telescope Simulator"
#: Guide scope focal length and guide camera pixel size when the settings give
#: none. The values are those of the contract recording.
DEFAULT_GUIDE_FOCAL_LENGTH_MM = 400.0
DEFAULT_GUIDE_PIXEL_SIZE_UM = 3.75
#: How far the guide star must travel in each calibration direction, in
#: pixels. PHD2's calibration distance default
#: (``CalstepDialog::DEFAULT_DISTANCE``).
CALIBRATION_DISTANCE_PIXELS = 25
#: The number of calibration steps aimed for in each direction. PHD2's
#: calibration step calculator default (``CalstepDialog::DEFAULT_STEPS``). It
#: leaves room under PHD2's limit of 60 steps per direction.
CALIBRATION_TARGET_STEPS = 12
#: The fewest and most steps the calculator accepts (``MIN_STEPS``,
#: ``MAX_STEPS``).
CALIBRATION_MIN_STEPS = 6
CALIBRATION_MAX_STEPS = 60
#: The calculator rounds the step up to a multiple of this many milliseconds.
CALIBRATION_STEP_ROUNDING_MILLISECONDS = 50
#: The calculator limits the declination it uses to this many degrees.
CALIBRATION_MAX_DECLINATION_DEGREES = 60.0
#: Sidereal rate in arcseconds per second (PHD2 uses 15 in the same formula).
SIDEREAL_ARCSEC_PER_SEC = 15.0
#: The guide rate (arcseconds per second) assumed when the caller gives none:
#: the mount simulator's default ``GUIDE_RATE`` of half sidereal.
DEFAULT_GUIDE_RATE_ARCSEC_PER_SEC = 7.5
#: Guide exposure when no controller settings say otherwise (PHD2's default).
DEFAULT_EXPOSURE_MS = 1000
#: The keys of the guide controller settings, with the range each accepts.
#: ``aggressiveness`` and ``hysteresis`` are fractions (PHD2 shows them as
#: percent), ``minimum_move`` is in pixels and ``exposure_seconds`` in seconds.
CONTROLLER_SETTING_RANGES = {
    "aggressiveness": (0.0, 2.0),
    "minimum_move": (0.0, 5.0),
    "hysteresis": (0.0, 0.99),
    "exposure_seconds": (0.05, 30.0),
}
CONTROLLER_SETTING_KEYS = tuple(CONTROLLER_SETTING_RANGES)
#: The note the launcher reports when the real PHD2 cannot be used.
PHD2_MISSING_NOTE = "PHD2 (guiding program): not used, the stand-in guider plays PHD2"
#: The note the launcher reports when PHD2 has to use the user's display.
PHD2_VISIBLE_NOTE = "PHD2 (guiding program): xvfb-run is not installed, so its window opens on your display"


@dataclass(frozen=True)
class Phd2Instance:
    """The PHD2 the launcher started, and how to recognize it.

    Attributes
    ----------
    instance : `int`
        PHD2's instance number.
    event_port : `int`
        The port of PHD2's event and command server.
    profile_name : `str`
        The name of the profile written for this PHD2. Asking PHD2 for its
        profile and finding this name shows that it is the one started here.
    indi_port : `int`
        The port of the world's INDI server the profile connects to.
    home : `pathlib.Path`
        The ``HOME`` folder PHD2 runs with.
    exposure_milliseconds : `int`
        The guide exposure written into the profile.
    """

    instance: int
    event_port: int
    profile_name: str
    indi_port: int
    home: Path
    exposure_milliseconds: int = DEFAULT_EXPOSURE_MS


def calibration_step_milliseconds(
    guide_rate_arcsec_per_sec: float,
    pixel_scale_arcsec_per_pixel: float,
    declination_degrees: float,
    distance_pixels: float = CALIBRATION_DISTANCE_PIXELS,
    target_steps: int = CALIBRATION_TARGET_STEPS,
) -> int:
    """Compute the calibration step the way PHD2's step calculator does.

    PHD2 calibrates by pulsing the mount in one direction, step after step,
    until the guide star has moved the calibration distance. It gives up after
    60 steps ("star did not move enough"). With PHD2's default step of 750 ms
    and a slow guide rate, 60 steps are not enough, so the step must fit the
    guide rate. This is the formula of PHD2's
    ``CalstepDialog::GetCalibrationStepSize`` (``src/calstep_dialog.cpp`` in
    the PHD2 source)::

        total_seconds = distance * pixel_scale / guide_rate
        step = total_seconds / steps / cos(declination)
        step = min(step, total_seconds / 6)       # keep at least 6 steps
        step rounded up to a multiple of 50 ms

    The default aim is 12 steps (PHD2's own default; it is about the number
    PHD2 documents as working "fine for most setups"), so calibration ends
    well before the 60-step limit even when the real motion is somewhat
    slower than the guide rate says. The calculator treats the guide rate as a
    fraction of sidereal; this function takes arcseconds per second, which is
    that fraction times 15.

    Parameters
    ----------
    guide_rate_arcsec_per_sec : `float`
        The mount's guide rate in arcseconds per second.
    pixel_scale_arcsec_per_pixel : `float`
        The guide camera's pixel scale.
    declination_degrees : `float`
        The declination calibration happens at. Limited to plus or minus 60
        degrees, as in PHD2's calculator.
    distance_pixels : `float`, optional
        The calibration distance. Default 25 pixels.
    target_steps : `int`, optional
        The number of steps aimed for. Limited to 6 to 60.

    Returns
    -------
    step_milliseconds : `int`
        The calibration step, a multiple of 50 ms.

    Raises
    ------
    ValueError
        If the guide rate or the pixel scale is not positive.
    """
    if guide_rate_arcsec_per_sec <= 0 or pixel_scale_arcsec_per_pixel <= 0:
        raise ValueError("The guide rate and the pixel scale must be positive.")
    steps = min(max(int(target_steps), CALIBRATION_MIN_STEPS), CALIBRATION_MAX_STEPS)
    declination = min(
        max(declination_degrees, -CALIBRATION_MAX_DECLINATION_DEGREES), CALIBRATION_MAX_DECLINATION_DEGREES
    )
    total_seconds = distance_pixels * pixel_scale_arcsec_per_pixel / guide_rate_arcsec_per_sec
    pulse = total_seconds / steps * 1000.0 / math.cos(math.radians(declination))
    pulse = min(pulse, total_seconds / CALIBRATION_MIN_STEPS * 1000.0)
    rounding = CALIBRATION_STEP_ROUNDING_MILLISECONDS
    return int(math.ceil(pulse / rounding) * rounding)


def event_port(instance: int) -> int:
    """Give the event and command port of a PHD2 instance.

    Parameters
    ----------
    instance : `int`
        The instance number.

    Returns
    -------
    port : `int`
        ``4399 + instance``.
    """
    return EVENT_PORT_BASE + instance


def second_port(instance: int) -> int:
    """Give the second port of a PHD2 instance.

    Parameters
    ----------
    instance : `int`
        The instance number.

    Returns
    -------
    port : `int`
        ``4299 + instance``.
    """
    return SECOND_PORT_BASE + instance


def profile_name(instance: int) -> str:
    """Give the name of the profile written for an instance.

    Parameters
    ----------
    instance : `int`
        The instance number.

    Returns
    -------
    name : `str`
        For example ``Astrometrics simulator 2``.
    """
    return f"Astrometrics simulator {instance}"


def config_file_name(instance: int) -> str:
    """Give the name of PHD2's settings file for an instance.

    Parameters
    ----------
    instance : `int`
        The instance number.

    Returns
    -------
    name : `str`
        ``.PHDGuidingV2`` for instance 1, else ``.PHDGuidingV2-instanceN``.
    """
    return ".PHDGuidingV2" if instance <= 1 else f".PHDGuidingV2-instance{instance}"


def find_phd2(environ: Mapping[str, str] | None = None) -> Path | None:
    """Find the PHD2 program.

    Parameters
    ----------
    environ : `~collections.abc.Mapping`, optional
        The environment, for its ``PATH``. Default: the process environment.

    Returns
    -------
    program : `pathlib.Path` or `None`
        ``phd2`` on the PATH, else ``~/.local/bin/phd2``, else `None`.
    """
    environment = os.environ if environ is None else environ
    found = shutil.which("phd2", path=environment.get("PATH"))
    if found:
        return Path(found)
    fallback = Path.home() / ".local" / "bin" / "phd2"
    return fallback if fallback.is_file() and os.access(fallback, os.X_OK) else None


def display_prefix(environ: Mapping[str, str] | None = None) -> list[str] | None:
    """Say how PHD2 gets an X display.

    Parameters
    ----------
    environ : `~collections.abc.Mapping`, optional
        The environment. Default: the process environment.

    Returns
    -------
    prefix : `list` [`str`] or `None`
        ``["xvfb-run", "-a"]`` when ``xvfb-run`` is installed (the default:
        PHD2's window stays on a hidden display), an empty list when it is not
        but ``DISPLAY`` names the user's display (PHD2 opens its window
        there), and `None` when PHD2 has no display.
    """
    environment = os.environ if environ is None else environ
    if shutil.which("xvfb-run", path=environment.get("PATH")):
        return ["xvfb-run", "-a"]
    if environment.get("DISPLAY"):
        return []
    return None


def display_note(environ: Mapping[str, str] | None = None) -> str | None:
    """Give the launch status for PHD2 falling back to the user's display.

    Parameters
    ----------
    environ : `~collections.abc.Mapping`, optional
        The environment. Default: the process environment.

    Returns
    -------
    note : `str` or `None`
        The status text when PHD2 will open its window on the user's display
        because ``xvfb-run`` is missing, else `None`. A status, not an error.
    """
    return PHD2_VISIBLE_NOTE if display_prefix(environ) == [] else None


def phd2_missing_reason(environ: Mapping[str, str] | None = None) -> str | None:
    """Say why the real PHD2 cannot be used, if it cannot.

    Parameters
    ----------
    environ : `~collections.abc.Mapping`, optional
        The environment. Default: the process environment.

    Returns
    -------
    reason : `str` or `None`
        `None` when PHD2 and a display are there. Otherwise what is missing,
        in a few words.
    """
    if find_phd2(environ) is None:
        return "phd2 is not installed"
    if display_prefix(environ) is None:
        return "no display, and xvfb-run is not installed"
    return None


def phd2_missing_note(environ: Mapping[str, str] | None = None) -> str | None:
    """Give the launch status for a missing PHD2, if it is missing.

    Parameters
    ----------
    environ : `~collections.abc.Mapping`, optional
        The environment. Default: the process environment.

    Returns
    -------
    note : `str` or `None`
        `None` when PHD2 can be used. Otherwise the status text the launcher
        passes on: it names what is missing and says the stand-in guider is
        used. A status, not an error.
    """
    reason = phd2_missing_reason(environ)
    return None if reason is None else f"{PHD2_MISSING_NOTE} ({reason})"


def _port_is_free(port: int) -> bool:
    """Say whether a local TCP port can be bound.

    Parameters
    ----------
    port : `int`
        The port.

    Returns
    -------
    free : `bool`
        `True` when nothing holds the port.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def choose_instance(
    is_free: Callable[[int], bool] = _port_is_free,
    candidates: Iterable[int] | None = None,
) -> int | None:
    """Pick an instance number whose two ports are free.

    Parameters
    ----------
    is_free : `~collections.abc.Callable`, optional
        ``is_free(port)`` says whether a port is free. Tests pass a fake.
    candidates : `~collections.abc.Iterable` [`int`], optional
        The instance numbers to try, in order. Default: 2 to 40.

    Returns
    -------
    instance : `int` or `None`
        The first instance whose event port and second port are both free
        and are not one of the real ports. `None` when there is none.
    """
    for instance in candidates if candidates is not None else range(FIRST_INSTANCE, LAST_INSTANCE + 1):
        ports = (event_port(instance), second_port(instance))
        if any(port in observatory_guard.FORBIDDEN_PORTS for port in ports):
            continue
        if all(is_free(port) for port in ports):
            return instance
    return None


def require_world_home(home: Path, root: Path) -> None:
    """Refuse a ``HOME`` for PHD2 that is not inside the simulator's folder.

    Parameters
    ----------
    home : `pathlib.Path`
        The folder PHD2 would use as ``HOME``.
    root : `pathlib.Path`
        The simulator's root folder.

    Raises
    ------
    ValueError
        If `home` is the user's home folder or is not inside `root`.
    """
    resolved_home, resolved_root = home.resolve(), root.resolve()
    if resolved_home == Path.home().resolve() or not resolved_home.is_relative_to(resolved_root):
        raise ValueError(
            f"Refusing to run PHD2 with HOME {home}: it is not inside the simulator folder {root}."
        )


def controller_settings_text(controller_settings: Mapping[str, float] | None) -> tuple[str, int]:
    """Turn guide controller settings into PHD2 profile text.

    Parameters
    ----------
    controller_settings : `~collections.abc.Mapping` [`str`, `float`] or `None`
        Any of `CONTROLLER_SETTING_KEYS`. A key left out keeps PHD2's default.
        `None` means all defaults.

    Returns
    -------
    text : `str`
        The guide algorithm sections of the profile: right ascension uses
        PHD2's Hysteresis algorithm and declination its Resist Switch
        algorithm, which are its defaults. Aggressiveness and minimum move
        apply to both axes, hysteresis to right ascension only.
    exposure_milliseconds : `int`
        The guide exposure.

    Raises
    ------
    ValueError
        If a key is unknown or a value is outside its range.
    """
    settings = dict(controller_settings or {})
    unknown = sorted(set(settings) - set(CONTROLLER_SETTING_KEYS))
    if unknown:
        raise ValueError(
            f"Unknown guide controller setting(s) {unknown}; use {list(CONTROLLER_SETTING_KEYS)}."
        )
    for key, value in settings.items():
        low, high = CONTROLLER_SETTING_RANGES[key]
        if not low <= float(value) <= high:
            raise ValueError(f"Guide controller setting {key}={value} is outside {low} to {high}.")
    shared = []
    if "aggressiveness" in settings:
        shared.append(f"aggression={float(settings['aggressiveness'])}")
    if "minimum_move" in settings:
        shared.append(f"minMove={float(settings['minimum_move'])}")
    right_ascension = list(shared)
    if "hysteresis" in settings:
        right_ascension.append(f"hysteresis={float(settings['hysteresis'])}")
    text = ""
    if right_ascension:
        text += "[profile/1/scope/GuideAlgorithm/X/Hysteresis]\n" + "\n".join(right_ascension) + "\n"
    if shared:
        text += "[profile/1/scope/GuideAlgorithm/Y/ResistSwitch]\n" + "\n".join(shared) + "\n"
    exposure = settings.get("exposure_seconds")
    milliseconds = DEFAULT_EXPOSURE_MS if exposure is None else round(float(exposure) * 1000)
    return text, milliseconds


def profile_text(
    instance: int,
    indi_port: int,
    controller_settings: Mapping[str, float] | None = None,
    guide_focal_length_mm: float = DEFAULT_GUIDE_FOCAL_LENGTH_MM,
    guide_pixel_size_um: float = DEFAULT_GUIDE_PIXEL_SIZE_UM,
    guide_rate_arcsec_per_sec: float | None = None,
    declination_degrees: float = 0.0,
) -> tuple[str, int]:
    """Write PHD2's settings file for the simulated observatory.

    The profile uses PHD2's INDI camera and INDI mount, pointed at the world's
    INDI server and the guide camera and telescope simulators. The server
    mode is on, so PHD2 offers its event and command port.

    Parameters
    ----------
    instance : `int`
        PHD2's instance number (it names the profile).
    indi_port : `int`
        The port of the world's INDI server.
    controller_settings : `~collections.abc.Mapping` [`str`, `float`], optional
        The guide controller settings (see `controller_settings_text`).
    guide_focal_length_mm, guide_pixel_size_um : `float`, optional
        The guide scope and the guide camera's pixel size. PHD2 reads the
        focal length as a whole number of millimeters (``GetInt``: a value
        such as 121.05 is refused and the pixel scale is "unspecified", so
        the guide log has no arcseconds), so it is rounded here.
    guide_rate_arcsec_per_sec : `float`, optional
        The mount's guide rate. When given, the profile's calibration step
        (``/profile/1/scope/CalibrationDuration``) is computed from it with
        `calibration_step_milliseconds`, so calibration can reach its distance
        in about 12 steps. When `None`, PHD2's default step (750 ms) stays.
    declination_degrees : `float`, optional
        The declination the world points at, for the calibration step.

    Returns
    -------
    text : `str`
        The content of PHD2's settings file.
    exposure_milliseconds : `int`
        The guide exposure written into it.
    """
    algorithms, milliseconds = controller_settings_text(controller_settings)
    calibration_line = ""
    if guide_rate_arcsec_per_sec is not None:
        pixel_scale = 206.265 * guide_pixel_size_um / round(guide_focal_length_mm)
        step = calibration_step_milliseconds(guide_rate_arcsec_per_sec, pixel_scale, declination_degrees)
        calibration_line = f"CalibrationDuration={step}\n"
    text = f"""ConfigVersion=2001
LoggingMode=1
currentProfile=1
ServerMode=1
[profile]
[profile/1]
name={profile_name(instance)}
ExposureDurationMs={milliseconds}
[profile/1/frame]
focalLength={round(guide_focal_length_mm)}
[profile/1/camera]
LastMenuChoice=INDI Camera
pixelsize={guide_pixel_size_um:g}
[profile/1/scope]
LastMenuChoice=INDI Mount
LastAuxMenuChoice=None
{calibration_line}[profile/1/stepguider]
LastMenuChoice=None
[profile/1/rotator]
LastMenuChoice=None
[profile/1/indi]
INDIhost=127.0.0.1
INDIport={indi_port}
INDIcam={GUIDE_CAMERA_DEVICE}
INDIcam_ccd=0
INDImount={MOUNT_DEVICE}
"""
    return text + algorithms, milliseconds


def write_profile(
    home: Path,
    root: Path,
    instance: int,
    indi_port: int,
    controller_settings: Mapping[str, float] | None = None,
    guide_focal_length_mm: float = DEFAULT_GUIDE_FOCAL_LENGTH_MM,
    guide_pixel_size_um: float = DEFAULT_GUIDE_PIXEL_SIZE_UM,
    guide_rate_arcsec_per_sec: float | None = None,
    declination_degrees: float = 0.0,
) -> Phd2Instance:
    """Write PHD2's settings file in the stand-in home, before PHD2 starts.

    Parameters
    ----------
    home : `pathlib.Path`
        The stand-in telescope computer's home folder (PHD2's ``HOME``).
    root : `pathlib.Path`
        The simulator's root folder; `home` must be inside it.
    instance : `int`
        PHD2's instance number.
    indi_port : `int`
        The port of the world's INDI server.
    controller_settings : `~collections.abc.Mapping` [`str`, `float`], optional
        The guide controller settings (see `controller_settings_text`).
    guide_focal_length_mm, guide_pixel_size_um : `float`, optional
        The guide scope and the guide camera's pixel size.
    guide_rate_arcsec_per_sec : `float`, optional
        The mount's guide rate, for the calibration step (see `profile_text`).
    declination_degrees : `float`, optional
        The declination the world points at.

    Returns
    -------
    instance_record : `Phd2Instance`
        What the launcher needs to start PHD2 and to recognize it. A `home`
        outside `root`, or an invalid controller setting, raises
        `ValueError` before anything is written.

    """
    require_world_home(home, root)
    text, milliseconds = profile_text(
        instance,
        indi_port,
        controller_settings,
        guide_focal_length_mm,
        guide_pixel_size_um,
        guide_rate_arcsec_per_sec,
        declination_degrees,
    )
    home.mkdir(parents=True, exist_ok=True)
    (home / config_file_name(instance)).write_text(text, encoding="utf-8")
    return Phd2Instance(
        instance=instance,
        event_port=event_port(instance),
        profile_name=profile_name(instance),
        indi_port=indi_port,
        home=home,
        exposure_milliseconds=milliseconds,
    )


def phd2_environment(home: Path, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Give the environment variables PHD2 runs with.

    ``HOME`` and the ``XDG_*`` folders are inside the stand-in home, so PHD2
    touches nothing of the user's. The desktop settings service and the
    accessibility bus are switched off for the same reason. The caller also
    removes the variables in `UNSET_VARIABLES`.

    Parameters
    ----------
    home : `pathlib.Path`
        The stand-in telescope computer's home folder.
    base : `~collections.abc.Mapping`, optional
        The launcher's environment, for ``XAUTHORITY``. Default: the process
        environment.

    Returns
    -------
    variables : `dict` [`str`, `str`]
        The variables to set (a copy of nothing else).
    """
    environment = os.environ if base is None else base
    variables = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "GDK_BACKEND": "x11",
        "GSETTINGS_BACKEND": "memory",
        "NO_AT_BRIDGE": "1",
    }
    if not environment.get("XAUTHORITY"):
        # X finds the authority file under HOME by default, and HOME changed.
        real_authority = Path.home() / ".Xauthority"
        if real_authority.is_file():
            variables["XAUTHORITY"] = str(real_authority)
    return variables


#: Variables PHD2 must not inherit: it runs on X, never on Wayland.
UNSET_VARIABLES = ("WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS")


def command_prefix(environ: Mapping[str, str] | None = None) -> list[str]:
    """Give the part of the PHD2 command that supplies the display.

    Parameters
    ----------
    environ : `~collections.abc.Mapping`, optional
        The environment. Default: the process environment.

    Returns
    -------
    prefix : `list` [`str`]
        Empty, or ``["xvfb-run", "-a"]``.

    Raises
    ------
    RuntimeError
        If PHD2 has no display (see `display_prefix`).
    """
    prefix = display_prefix(environ)
    if prefix is None:
        raise RuntimeError("PHD2 has no display and xvfb-run is not installed.")
    return prefix


def phd2_command(executable: Path, instance: int, environ: Mapping[str, str] | None = None) -> list[str]:
    """Give the command that starts PHD2.

    Parameters
    ----------
    executable : `pathlib.Path`
        The PHD2 program (from `find_phd2`).
    instance : `int`
        PHD2's instance number.
    environ : `~collections.abc.Mapping`, optional
        The environment, to decide on the display.

    Returns
    -------
    command : `list` [`str`]
        The display prefix, the program and ``-i`` with the instance number.
    """
    return [*command_prefix(environ), str(executable), "-i", str(instance)]
