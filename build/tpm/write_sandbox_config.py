"""Purpose: Write the settings file for the TPM run's throwaway backend.

Description: Mirrors `writeSandboxConfig` in `ui/globalSetup.ts`: the settings
file names the image library and the wayfinding library inside the throwaway
folder, so the backend never falls back to the user's real ones. It adds a
small set of made-up equipment and a made-up site so the observatory views
have something to show. Nothing here names a real host, device or path.

The settings come in two observatory profiles. ``offline`` (the default)
names this computer as the telescope host with a port nothing listens on.
``simulated`` points at stand-ins the harness starts on random local ports: an
INDI server with simulators, a stand-in PHD2, and a folder that plays the
telescope computer (reached through the app's own transfer code and a local
stand-in for ssh; see `standin_ssh.sh`). ``replay`` writes the same settings;
only what the stand-ins play differs (a real night, see `night_replay.py`).

Usage::

    .venv/bin/python build/tpm/write_sandbox_config.py <sandbox> \
        [--profile offline|simulated --indi-port N --phd2-port N]
"""

import argparse
from pathlib import Path

from simulator.settings import (
    OFFLINE_TELESCOPE_SETTINGS,
    OTHER_EQUIPMENT_SETTINGS,
    SIMULATED_TELESCOPE_SETTINGS,
    library_settings_text,
    standin_home,
)

#: The observatory profile the offline settings give. The simulated profile (a
#: telescope "on" with local simulators) has another name, so results are
#: compared only within one profile.
OBSERVATORY_PROFILE = "offline"
#: The name of the profile with a simulated telescope.
SIMULATED_PROFILE = "simulated"
#: The name of the profile that plays a real, recorded night through the
#: same stand-ins. Its settings are the simulated profile's.
REPLAY_PROFILE = "replay"
#: The profiles whose telescope is a set of local stand-ins.
STANDIN_PROFILES = (SIMULATED_PROFILE, REPLAY_PROFILE)

EQUIPMENT_SETTINGS = OFFLINE_TELESCOPE_SETTINGS + OTHER_EQUIPMENT_SETTINGS


def write_sandbox_config(
    sandbox: Path,
    profile: str = OBSERVATORY_PROFILE,
    indi_port: int | None = None,
    phd2_port: int | None = None,
) -> Path:
    """Create the throwaway folders and the settings file inside `sandbox`.

    Parameters
    ----------
    sandbox : `pathlib.Path`
        The throwaway folder for this run.
    profile : `str`, optional
        ``"offline"`` (default), ``"simulated"`` or ``"replay"``.
    indi_port : `int`, optional
        The INDI stand-in's port. Required for the simulated profile.
    phd2_port : `int`, optional
        The PHD2 stand-in's port. Required for the simulated profile.

    Returns
    -------
    config_path : `pathlib.Path`
        The settings file.

    Raises
    ------
    ValueError
        If the profile is unknown, or a stand-in profile has no ports.
    """
    if profile == OBSERVATORY_PROFILE:
        telescope_settings = OFFLINE_TELESCOPE_SETTINGS
    elif profile in STANDIN_PROFILES:
        if indi_port is None or phd2_port is None:
            raise ValueError(f"The {profile} profile needs --indi-port and --phd2-port.")
        pictures = standin_home(sandbox) / "Pictures"
        pictures.mkdir(parents=True, exist_ok=True)
        telescope_settings = SIMULATED_TELESCOPE_SETTINGS.format(
            indi_port=indi_port, phd2_port=phd2_port, pictures=pictures
        )
    else:
        raise ValueError(f"Unknown observatory profile '{profile}'.")
    library = sandbox / "library"
    frames = library / "frames"
    wayfinding = sandbox / "wayfinding"
    frames.mkdir(parents=True, exist_ok=True)
    wayfinding.mkdir(parents=True, exist_ok=True)
    config_path = sandbox / "astrometrics.config.toml"
    config_path.write_text(
        library_settings_text(sandbox) + telescope_settings + OTHER_EQUIPMENT_SETTINGS, encoding="utf-8"
    )
    return config_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Write the TPM run's settings file.")
    parser.add_argument("sandbox", type=Path)
    parser.add_argument(
        "--profile", choices=(OBSERVATORY_PROFILE, *STANDIN_PROFILES), default=OBSERVATORY_PROFILE
    )
    parser.add_argument("--indi-port", type=int)
    parser.add_argument("--phd2-port", type=int)
    arguments = parser.parse_args()
    print(
        write_sandbox_config(
            arguments.sandbox.resolve(), arguments.profile, arguments.indi_port, arguments.phd2_port
        )
    )
