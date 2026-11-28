"""Purpose: Ask Siril for its version.

Description: The stacking driver reports the version with each stack, and the
stacking quality step stores it in each run's calibration record. Siril prints
its version for ``--version`` (see `program.version_arguments`). The program
does not change while this process runs, so `cached_siril_version` asks once.
"""

import functools
import re
import subprocess

from astrometricslib._internal.drivers.siril.core.program import version_arguments

# The longest the version question may take, in seconds. Siril answers in
# well under a second; a Flatpak's first start can take a few seconds more.
_VERSION_TIMEOUT_SECONDS = 30


@functools.cache
def cached_siril_version(siril_executable: str) -> str | None:
    """Read Siril's version once per process for a given command.

    The stacking quality step records the version with each run's
    calibration record; asking Siril once is enough, because the program
    does not change while the process runs.

    Parameters
    ----------
    siril_executable : `str`
        The command that starts Siril.

    Returns
    -------
    version : `str` or `None`
        The version text, or `None` when Siril cannot be run.
    """
    return siril_version(siril_executable)


def siril_version(siril_executable: str) -> str | None:
    """Read Siril's version from its command line.

    Parameters
    ----------
    siril_executable : `str`
        The command that starts Siril, as the driver stores it. A Flatpak
        command is run with its command-line program instead of the GUI.

    Returns
    -------
    version : `str` or `None`
        The first version number found, or `None` when Siril is missing or
        prints none.
    """
    try:
        completed = subprocess.run(
            version_arguments(siril_executable),
            capture_output=True,
            text=True,
            timeout=_VERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return None
    match = re.search(r"siril\s+(\d+\.\d+(?:\.\d+)?)", completed.stdout + completed.stderr)
    return match.group(1) if match else None
