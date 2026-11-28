r"""Set up Siril's Gaia astrometry catalog, which its plate solver reads.

Siril solves where an image points by matching its stars to the Gaia stars in
one file on the disk (about 1.5 GB). This script downloads that file from
Zenodo, the record Siril's own catalog installer uses, decompresses it,
checks its size and SHA-256 checksum against the pinned ones and puts it in
the app's data folder (``siril`` in ``~/.local/share/astrometrics`` on Linux,
``%LOCALAPPDATA%\astrometrics\siril`` on Windows). A download that does not
match is refused and deleted. The pinned release is
``GAIA_ASTROMETRY_CATALOG`` in
``astrometricslib/_internal/drivers/siril/core/catalog_setup.py``.

A catalog that is already installed and matches the pin, in this folder or in
Siril's own data folder, is kept: nothing is downloaded.

Run::

    python -m astrometricslib.scripts.set_up_siril_catalog --check
    python -m astrometricslib.scripts.set_up_siril_catalog

``--check`` changes nothing and downloads nothing. It prints where the catalog
is and whether its size and checksum match, and exits with 0 only when the
catalog is ready.
"""

import argparse
import sys

from astrometricslib._internal.drivers.siril.core.catalog_setup import (
    GAIA_ASTROMETRY_CATALOG,
    check_catalog,
    set_up_catalog,
)
from astrometricslib.foundation.errors import AstrometricsError


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
        "--check",
        action="store_true",
        help="Report whether the catalog is installed and matches the pin; change nothing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Check or set up Siril's Gaia astrometry catalog.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments. Default: ``sys.argv[1:]``.

    Returns
    -------
    exit_code : `int`
        0 when the catalog is ready, 1 when it is not (with ``--check``) or
        the setup failed.
    """
    arguments = _build_argument_parser().parse_args(argv)
    release = GAIA_ASTROMETRY_CATALOG
    if arguments.check:
        status = check_catalog(release)
        print(f"{release.name} {release.version}")
        print(f"  file:     {status.path or 'not installed'}")
        size_text = "matches" if status.size_matches else "does not match"
        print(f"  size:     {size_text} the pin ({release.size_bytes} bytes)")
        print(f"  checksum: {'matches' if status.checksum_matches else 'does not match'} the pin")
        return 0 if status.ready else 1
    try:
        path = set_up_catalog(release, progress=_ProgressPrinter())
    except AstrometricsError as error:
        print(f"{release.name} setup failed: {error}", file=sys.stderr)
        return 1
    print(f"{release.name} is set up: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
