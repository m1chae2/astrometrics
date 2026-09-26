#!/usr/bin/env python3
"""FITS thumbnail generator for Nautilus and other file managers.

Takes an input FITS file, generates a stretched PNG thumbnail up to the
requested size, and writes it to the destination path.
"""

import os
import sys


def main() -> None:
    """Convert the FITS file named on the command line to a PNG thumbnail.

    Called by the desktop's file manager as
    ``generate_fits_thumbnail.py <input.fits> <output.png> <size>``. Exits
    with status 0 on success and 1 on any failure (missing arguments, a
    missing input file, or a conversion error), which is how a thumbnailer
    is expected to report failure -- there is no caller to hand an
    exception to.
    """
    if len(sys.argv) < 4:
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]
    try:
        size = int(sys.argv[3])
    except ValueError:
        size = 256

    if not os.path.exists(input_file):
        sys.exit(1)

    try:
        from astrometricslib import Astrometrics

        png_bytes, _, _ = Astrometrics().visualization.convert_fits_to_png_with_stats(
            input_file, max_dimensions=size, stretch=True
        )

        with open(output_file, "wb") as f:
            f.write(png_bytes)
        sys.exit(0)
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()
