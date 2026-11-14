r"""Set up Siril, the stacking program, on Windows.

Run by ``build/windows/setup_windows.ps1`` after the Python and Node packages,
the same way it installs them. On Windows it downloads the pinned portable
Siril from Siril's own site, checks it against the recorded size and SHA-256
checksum, unpacks it into ``%LOCALAPPDATA%\Astrometrics\Data\Siril\<version>``
and sets ``siril_executable`` in ``[Processing.Siril]`` to its
``siril-cli.exe``. No administrator rights are needed.

A ``siril_executable`` that already names an existing Siril is left alone, so
someone with their own Siril keeps it (``--force`` sets up the pinned one
anyway). Running it again does nothing once Siril is set up. On Linux it does
nothing: Siril comes from the package manager there.

Run::

    python -m astrometricslib.scripts.set_up_siril
    python -m astrometricslib.scripts.set_up_siril --force
"""

import argparse
import sys

from astrometricslib._internal.drivers.siril_setup import (
    SIRIL_RELEASE,
    installed_siril_program,
    set_up_siril,
    siril_program_exists,
)
from astrometricslib.foundation.config import get_configuration
from astrometricslib.foundation.errors import AstrometricsError
from astrometricslib.foundation.platform_tools import on_windows


class _ProgressPrinter:
    """Print the setup's progress in steps of ten percent."""

    def __init__(self) -> None:
        """Start before the first step."""
        self.last_step = -1

    def __call__(self, percent: int, message: str) -> None:
        """Print a line when the progress reaches a new ten-percent step.

        Parameters
        ----------
        percent : `int`
            How far the setup is, out of 100.
        message : `str`
            What it is doing.
        """
        step = percent // 10
        if step != self.last_step:
            self.last_step = step
            print(f"{percent:3d}%  {message}", flush=True)


def _build_argument_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser for this script.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        The parser.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--force",
        action="store_true",
        help=f"Set up Siril {SIRIL_RELEASE.version} even if siril_executable already names a Siril.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Set up Siril on Windows, unless a Siril is already configured.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments. Default: ``sys.argv[1:]``.

    Returns
    -------
    exit_code : `int`
        0 when Siril is ready (or on Linux, where there is nothing to do), 1
        when the download or setup failed.
    """
    arguments = _build_argument_parser().parse_args(argv)
    if not on_windows():
        print("Nothing to do: on Linux, install Siril with the package manager.")
        return 0
    config = get_configuration()
    current = config.get_siril_executable() or ""
    ours = installed_siril_program()
    if (
        not arguments.force
        and current
        and siril_program_exists(current)
        and (ours is None or str(ours) != current)
    ):
        print(f"Keeping the Siril already set in siril_executable: {current}")
        return 0
    try:
        program = set_up_siril(config, progress=_ProgressPrinter())
    except AstrometricsError as error:
        print(f"Siril setup failed: {error}", file=sys.stderr)
        return 1
    print(f"Siril {SIRIL_RELEASE.version} is set up: {program}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
