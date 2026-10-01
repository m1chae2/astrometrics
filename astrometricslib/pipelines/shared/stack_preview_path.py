"""Names and sizes the picture that goes with a finished stack.

Stacking saves a stretched JPEG next to each stack (see
`stacking/stack_preview.py`), and the image viewer looks for that file. Both
sides take the name and the JPEG quality from here, so they cannot drift
apart. The module holds no other code, so the viewer can import it without
loading the Siril driver.
"""

import os

PREVIEW_SUFFIX = "_preview"
"""Added to the stack's file name, before the extension, for its preview."""

# JPEG quality, in percent, for the saved picture. Tested once, on the M 13
# luminosity stack (3008 x 3008 pixels): 90 gave a 3.3 MB file with the faint
# stars, the cluster core and a small background galaxy all visible. Not tuned
# on other stacks.
PREVIEW_JPEG_QUALITY = 90


def preview_path_for(stacked_path: str) -> str:
    """Name the preview picture of a stack.

    Parameters
    ----------
    stacked_path : `str`
        Path of the stacked FITS file.

    Returns
    -------
    preview_path : `str`
        The same folder and file name, with `PREVIEW_SUFFIX` added and a
        ``.jpg`` extension.
    """
    stem, _ = os.path.splitext(stacked_path)
    return f"{stem}{PREVIEW_SUFFIX}.jpg"
