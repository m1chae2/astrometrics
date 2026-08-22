"""Convert the project's PNG application icon to Windows ICO format."""

import os
import sys

from PIL import Image


def convert_png_to_ico(png_path, ico_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Convert a PNG image file to an ICO file.

    Parameters
    ----------
    png_path : `str`
        Path to the source PNG image.
    ico_path : `str`
        Path to write the resulting ICO file to.
    """
    try:
        if not os.path.exists(png_path):
            print(f"Error: {png_path} not found.")
            sys.exit(1)

        img = Image.open(png_path)
        img.save(ico_path, format="ICO")
        print(f"Successfully converted {png_path} to {ico_path}")
    except Exception as e:
        print(f"Error converting icon: {e}")
        sys.exit(1)


if __name__ == "__main__":
    # Adjust paths relative to script location or assume running from root
    # This script is intended to be run from project root or scripts/windows

    # Try to find assets folder
    base_dir = os.path.abspath(os.curdir)
    assets_dir = os.path.join(base_dir, "assets")

    if not os.path.isdir(assets_dir):
        # Try going up levels if we are in scripts/windows
        if os.path.isdir(os.path.join(base_dir, "..", "..", "assets")):
            assets_dir = os.path.join(base_dir, "..", "..", "assets")

    if not os.path.isdir(assets_dir):
        print("Could not find assets directory")
        sys.exit(1)

    png_path = os.path.join(assets_dir, "orbit.png")
    ico_path = os.path.join(assets_dir, "orbit.ico")

    convert_png_to_ico(png_path, ico_path)
