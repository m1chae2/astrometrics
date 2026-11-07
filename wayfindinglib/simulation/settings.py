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

from pathlib import Path

#: Settings for a simulated observatory. Every host is this computer. The
#: INDI and PHD2 ports are the random ports of the stand-ins that were
#: started. The picture folder is inside the stand-in telescope computer's
#: home folder. Automatic downloads are on, so finished frames and guide logs
#: copy by themselves through the app's one transfer code.
SIMULATED_TELESCOPE_SETTINGS = """
["Observatory.Telescope"]
hostname = "127.0.0.1"
indi_port = "{indi_port}"
allow_commands = "false"
focal_length_mm = "405"
focal_ratio = "5.4"
remote_pictures_path = "{pictures}"

["Observatory.RemoteTransfer"]
auto_download = "true"

["Observatory.Guiding"]
phd2_location = "local"
phd2_port = "{phd2_port}"
"""

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
name_aliases = "ZWO CCD ASI533MM Pro"
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
    return library_settings_text(root) + OFFLINE_TELESCOPE_SETTINGS + OTHER_EQUIPMENT_SETTINGS


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
        library_settings_text(root)
        + SIMULATED_TELESCOPE_SETTINGS.format(indi_port=indi_port, phd2_port=phd2_port, pictures=pictures)
        + OTHER_EQUIPMENT_SETTINGS
    )
