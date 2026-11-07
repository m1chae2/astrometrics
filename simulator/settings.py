"""Purpose: The settings file text for a simulated observatory.

Description: One place for the settings that point the app at the local
stand-ins: every host is this computer, the INDI and PHD2 ports are the ones
the stand-ins listen on, and the picture folder is inside the stand-in
telescope computer's home folder. The app's simulator source and the TPM
harness both write their settings file from here.

The offline settings (`OFFLINE_TELESCOPE_SETTINGS`) name no telescope at all.
The app uses them when the simulator cannot start, so it never falls back to
the real telescope.
"""

import json
import re
from collections.abc import Iterable
from pathlib import Path

#: The launch identity of a simulated observatory (see
#: `astrometricslib.foundation.launch_identity`): it shows the SIMULATOR chip,
#: has no real devices, can be thrown away, sends nothing off the computer, and
#: locks the keys that aim it at its stand-ins and its own folders.
SIMULATED_LABEL = "SIMULATOR"
#: The same for the offline world used when the simulator cannot start.
OFFLINE_LABEL = "SIMULATOR FAILED"
#: Settings a simulated observatory's settings page cannot change: where the
#: stand-in telescope computer and PHD2 are, and where the libraries are.
SIMULATED_LOCKED_KEYS = (
    "Observatory.Telescope.hostname",
    "Observatory.Telescope.indi_port",
    "Observatory.Telescope.remote_pictures_path",
    "Observatory.Telescope.allow_commands",
    "Observatory.Guiding.phd2_port",
    "Observatory.Guiding.phd2_location",
    "Image Library.path",
    "Image Library.frames_path",
    "Image Library.stacks_path",
    "Wayfinding Library.path",
)


def launch_section_text(
    label: str,
    live_hardware: bool,
    disposable_library: bool,
    offline: bool,
    locked_keys: Iterable[str] = (),
) -> str:
    """Write a ``[Launch]`` section.

    Parameters
    ----------
    label : `str`
        The status chip text.
    live_hardware, disposable_library, offline : `bool`
        The three flags (see `astrometricslib.foundation.launch_identity`).
    locked_keys : `~collections.abc.Iterable` [`str`], optional
        The ``Section.key`` names the settings page cannot change.

    Returns
    -------
    text : `str`
        The section, ending in a blank line.
    """
    keys = ", ".join(json.dumps(key) for key in locked_keys)
    return (
        "[Launch]\n"
        f"label = {json.dumps(label)}\n"
        f"live_hardware = {str(live_hardware).lower()}\n"
        f"disposable_library = {str(disposable_library).lower()}\n"
        f"offline = {str(offline).lower()}\n"
        f"locked_keys = [{keys}]\n\n"
    )


#: The ``[Launch]`` section of a simulated observatory.
SIMULATED_LAUNCH_SETTINGS = launch_section_text(SIMULATED_LABEL, False, True, True, SIMULATED_LOCKED_KEYS)
#: The ``[Launch]`` section of the offline world (the simulator failed).
OFFLINE_LAUNCH_SETTINGS = launch_section_text(OFFLINE_LABEL, False, True, True)


def with_launch_section(text: str, launch_text: str) -> str:
    """Replace the ``[Launch]`` section of settings text, or add it.

    Parameters
    ----------
    text : `str`
        The settings in TOML.
    launch_text : `str`
        The new section, from `launch_section_text`.

    Returns
    -------
    text : `str`
        The settings with exactly one ``[Launch]`` section, the new one,
        first.
    """
    blocks = re.split(r'(?m)^(?=\["?[A-Za-z])', text)
    kept = [block for block in blocks if not re.match(r'\["?Launch"?\]\s*$', block.split("\n", 1)[0])]
    return launch_text + "".join(kept)


#: Settings for a simulated observatory. Every host is this computer. The
#: INDI and PHD2 ports are the random ports of the stand-ins that were
#: started. The picture folder is inside the stand-in telescope computer's
#: home folder. Automatic downloads are on, so finished frames and guide logs
#: copy by themselves through the app's one transfer code. Commands are
#: allowed: they reach only the simulated devices on this computer, so a
#: slew, an exposure or a focus move can be tried without risk.
SIMULATED_TELESCOPE_SETTINGS = """
["Observatory.Telescope"]
hostname = "127.0.0.1"
indi_port = "{indi_port}"
allow_commands = "true"
focal_length_mm = "405"
focal_ratio = "5.4"
remote_pictures_path = "{pictures}"

["Observatory.RemoteTransfer"]
auto_download = "true"

["Observatory.Guiding"]
phd2_location = "local"
phd2_port = "{phd2_port}"
"""

#: The name the INDI CCD simulator writes into ``INSTRUME`` (its device name).
#: The world's copy of the primary camera's profile lists it as an alias, so
#: simulator frames file under the real camera (see `with_camera_alias`).
SIMULATOR_INSTRUMENT_NAME = "CCD Simulator"
#: Focal length and ratio of the made-up optic, used when the real settings
#: have no ``Observatory.Telescope`` section (or lack these keys).
FALLBACK_TELESCOPE_KEYS = (("focal_length_mm", "405"), ("focal_ratio", "5.4"))


def endpoint_keys(indi_port: int, phd2_port: int, pictures: Path) -> tuple[tuple[str, str, str], ...]:
    """List the settings that aim the app at the stand-ins.

    These are the only telescope keys the world changes. Every other real
    key (focal length, focal ratio, ...) stays as the user has it.

    Parameters
    ----------
    indi_port, phd2_port : `int`
        The ports of the stand-in INDI server and PHD2.
    pictures : `pathlib.Path`
        The stand-in telescope computer's picture folder.

    Returns
    -------
    keys : `tuple` [`tuple` [`str`, `str`, `str`], ...]
        ``(section, key, value)`` with plain text values.
    """
    return (
        ("Observatory.Telescope", "hostname", "127.0.0.1"),
        ("Observatory.Telescope", "indi_port", str(indi_port)),
        ("Observatory.Telescope", "remote_pictures_path", str(pictures)),
        ("Observatory.Telescope", "allow_commands", "true"),
        ("Observatory.RemoteTransfer", "auto_download", "true"),
        ("Observatory.Guiding", "phd2_location", "local"),
        ("Observatory.Guiding", "phd2_port", str(phd2_port)),
    )


#: Settings that keep the app away from every telescope computer. The host is
#: this computer and the INDI port is one nothing listens on, so a connection
#: attempt fails at once. Automatic downloads are off, and PHD2 (the guiding
#: program) is "local" on port 1 (nothing listens there), so nothing reaches
#: a PHD2 on this computer's usual port 4400 or an observatory host.
OFFLINE_TELESCOPE_SETTINGS = """
["Observatory.Telescope"]
hostname = "localhost"
indi_port = "1"
allow_commands = "false"
focal_length_mm = "405"
focal_ratio = "5.4"

["Observatory.RemoteTransfer"]
auto_download = "false"

["Observatory.Guiding"]
phd2_location = "local"
phd2_port = "1"
"""

#: The one port the offline settings use for INDI and PHD2.
OFFLINE_PORT = 1

#: Settings for the made-up equipment and site (no hostnames, no hardware).
OTHER_EQUIPMENT_SETTINGS = """
["Observatory.Filters"]
available = "NONE, L, SPEC"

["Observatory.Location"]
latitude = "45.76"
longitude = "-110.74"
elevation = "1500"

["Observatory.Camera"]
models = "ZWO ASI533MM Pro, Generic unlisted camera"
default_primary_camera = "ZWO ASI533MM Pro"

["Observatory.Camera.ZWO ASI533MM Pro"]
name = "ZWO ASI533MM Pro"
name_aliases = "ZWO CCD ASI533MM Pro, CCD Simulator"
record_name = "ZWO ASI 533MM Pro"
"pixel_size_μm" = "3.76"
sensor_width_px = "3008"
sensor_height_px = "3008"
sensor_min_wavelength = "300"
sensor_max_wavelength = "1000"
clip_ceiling_adu = { value = 65532.0, kind = "assumed", source = "TPM sandbox value." }
saturation_threshold_adu = { value = 65000.0, kind = "assumed", source = "TPM sandbox value." }

["Observatory.Camera.Generic unlisted camera"]
name = "Generic unlisted camera"
is_generic_fallback = "true"
clip_ceiling_adu = { value = 65535.0, kind = "assumed", source = "TPM sandbox value." }
saturation_threshold_adu = { value = 65000.0, kind = "assumed", source = "TPM sandbox value." }
"""


def standin_home(sandbox: Path) -> Path:
    """Return the home folder of the stand-in telescope computer.

    Parameters
    ----------
    sandbox : `pathlib.Path`
        The simulator's root folder.

    Returns
    -------
    home : `pathlib.Path`
        ``<sandbox>/standin/home``. Its ``Pictures`` folder holds the frames
        and its
        ``.local/share/kstars/guidelogs`` folder holds the guide logs.
    """
    return sandbox / "standin" / "home"


def library_settings_text(root: Path, frames: Path | None = None, stacks: Path | None = None) -> str:
    """Write the settings that place every library inside a simulator folder.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator's root folder.
    frames, stacks : `pathlib.Path`, optional
        Where the frames and the stacks are. Default: ``<root>/library/frames``
        for both (the stacks folder is then the frames folder, which is what
        leaving it out means).

    Returns
    -------
    text : `str`
        The ``Image Library`` (path, frames and stacks) and
        ``Wayfinding Library`` sections.
    """
    frames = frames or root / "library" / "frames"
    stacks = stacks or frames
    return (
        f'["Image Library"]\npath = "{root / "library"}"\nframes_path = "{frames}"\n'
        f'stacks_path = "{stacks}"\n\n'
        f'["Wayfinding Library"]\npath = "{root / "wayfinding"}"\n'
    )


def offline_config_text(root: Path) -> str:
    """Build the whole settings file of an offline world.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator's root folder. Both libraries are inside it.

    Returns
    -------
    text : `str`
        The settings file text: no telescope, no PHD2, no downloads.
    """
    return (
        OFFLINE_LAUNCH_SETTINGS
        + library_settings_text(root)
        + OFFLINE_TELESCOPE_SETTINGS
        + OTHER_EQUIPMENT_SETTINGS
    )


def simulator_config_text(root: Path, indi_port: int, phd2_port: int) -> str:
    """Build the whole settings file of a simulator world.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator's root folder. The image library, the wayfinding
        library and the stand-in telescope computer's home are inside it.
    indi_port : `int`
        The port the stand-in INDI server listens on.
    phd2_port : `int`
        The port the stand-in PHD2 listens on.

    Returns
    -------
    text : `str`
        The settings file text.
    """
    pictures = standin_home(root) / "Pictures"
    return (
        SIMULATED_LAUNCH_SETTINGS
        + library_settings_text(root)
        + SIMULATED_TELESCOPE_SETTINGS.format(indi_port=indi_port, phd2_port=phd2_port, pictures=pictures)
        + OTHER_EQUIPMENT_SETTINGS
    )
