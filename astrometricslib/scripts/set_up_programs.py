r"""Set up Siril and GraXpert, the outside programs, on Windows.

Run by ``build/windows/setup_windows.ps1`` after the Python and Node packages,
the same way it installs them. For each program it downloads one pinned
release from the project's own site, checks it against the recorded size and
SHA-256 checksum, unpacks it into
``%LOCALAPPDATA%\astrometrics\<program>\<version>`` and sets the
program's setting (``siril_executable``, ``graxpert_executable``). No
administrator rights are needed. The pinned releases are listed in
``astrometricslib/_internal/drivers/program_setup.py``.

A setting that names a program the user installed elsewhere is left alone, so
someone with their own Siril or GraXpert keeps it (``--force`` sets up the
pinned one anyway). A setting that names a copy this script made is moved to
the pinned release, so an update that pins a newer version sets it up. Running
it again downloads nothing once the pinned programs are set up. On Linux
it does nothing: the programs come from the package manager there.

Run::

    python -m astrometricslib.scripts.set_up_programs
    python -m astrometricslib.scripts.set_up_programs --force
"""

import argparse
import sys

from astrometricslib._internal.drivers.program_setup import (
    PINNED_RELEASES,
    ProgramRelease,
    current_setting,
    program_exists,
    set_up_by_this_script,
    set_up_program,
)
from astrometricslib.foundation.config import AppConfiguration, get_configuration
from astrometricslib.foundation.errors import AstrometricsError
from astrometricslib.foundation.system import on_windows


class _ProgressPrinter:
    """Print a setup's progress in steps of ten percent."""

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
        help="Set up the pinned programs even if the settings already name existing ones.",
    )
    return parser


def _set_up_one(config: AppConfiguration, release: ProgramRelease, force: bool) -> bool:
    """Set up one program, unless the settings already name an existing one.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings.
    release : `ProgramRelease`
        Which program.
    force : `bool`
        Set up the pinned program even if another one is configured.

    Returns
    -------
    ready : `bool`
        `True` when the program is ready (set up now, already set up, or
        the user's own kept); `False` when the setup failed.
    """
    current = current_setting(config, release)
    if not force and current and program_exists(current) and not set_up_by_this_script(current, release):
        print(f"Keeping the {release.name} already set in {release.key}: {current}")
        return True
    try:
        program = set_up_program(config, release, progress=_ProgressPrinter())
    except AstrometricsError as error:
        print(f"{release.name} setup failed: {error}", file=sys.stderr)
        return False
    print(f"{release.name} {release.version} is set up: {program}")
    return True


def main(argv: list[str] | None = None) -> int:
    """Set up each pinned program on Windows.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments. Default: ``sys.argv[1:]``.

    Returns
    -------
    exit_code : `int`
        0 when every program is ready (or on Linux, where there is nothing
        to do), 1 when a setup failed. A failure does not stop the others.
    """
    arguments = _build_argument_parser().parse_args(argv)
    if not on_windows():
        print("Nothing to do: on Linux, install Siril and GraXpert with the package manager.")
        return 0
    config = get_configuration()
    results = [_set_up_one(config, release, arguments.force) for release in PINNED_RELEASES]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
