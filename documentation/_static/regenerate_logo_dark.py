"""Regenerate logo-dark.png from logo.png.

logo.png is dark line art pre-blended onto an opaque white halo (every
opaque pixel has alpha 255; antialiasing lives in the RGB values, not
alpha), so it only reads correctly on a white page background. This
reconstructs a true alpha mask from pixel luminance and fills it with a
light stroke color, producing a variant that composites cleanly onto the
dark navbar (see html_theme_options["logo"]["image_dark"] in conf.py).

Run from the documentation/_static/ directory:
    python regenerate_logo_dark.py
"""

from PIL import Image

STROKE_COLOR = (224, 224, 224)


def main() -> None:
    """Write logo-dark.png next to this script."""
    im = Image.open("logo.png").convert("RGBA")
    width, height = im.size
    pixels = im.load()

    out = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    out_pixels = out.load()

    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a == 0:
                continue
            luminance = (r + g + b) / 3.0
            new_alpha = max(0, min(255, round(255 - luminance)))
            if new_alpha == 0:
                continue
            out_pixels[x, y] = (*STROKE_COLOR, new_alpha)

    out.save("logo-dark.png")


if __name__ == "__main__":
    main()
