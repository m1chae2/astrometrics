#!/usr/bin/env python3
"""Make a PNG thumbnail of a FITS file for Nautilus and other file managers.

The desktop runs this through `astrometrics-fits.thumbnailer`, which
`install_desktop_icon.sh` installs. It draws the frame with the same
brightness stretch the app uses and writes a PNG of the size the file manager
asked for.
"""

import sys
import traceback

# Longest picture side that `Visualization.render_fits` accepts, in pixels.
_MINIMUM_SIZE = 100
_MAXIMUM_SIZE = 2000
_DEFAULT_SIZE = 256


def main() -> None:
    """Convert the FITS file named on the command line to a PNG thumbnail.

    Called by the file manager as
    ``generate_fits_thumbnail.py <input.fits> <output.png> <size>``. Exits
    with status 0 on success and 1 on any failure (missing arguments or a
    conversion error). That is how a thumbnailer reports failure, because
    there is no caller to hand an exception to. The reason is printed to
    standard error so it shows up when the script is run by hand.
    """
    if len(sys.argv) < 4:
        print("usage: generate_fits_thumbnail.py <input.fits> <output.png> <size>", file=sys.stderr)
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]
    try:
        size = int(sys.argv[3])
    except ValueError:
        size = _DEFAULT_SIZE
    size = max(_MINIMUM_SIZE, min(_MAXIMUM_SIZE, size))

    from astrometricslib import DATA_ERRORS, Astrometrics, AstrometricsError

    try:
        picture = Astrometrics().visualization.render_fits(
            path=input_file, kind="image", max_dimensions=size, stretch=True
        )
        with open(output_file, "wb") as output_stream:
            output_stream.write(picture.png_bytes)
    except (AstrometricsError, OSError, *DATA_ERRORS):
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
