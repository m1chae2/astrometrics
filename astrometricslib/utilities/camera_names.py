"""Helpers for comparing camera names that are written in different ways.

The same camera shows up under several spellings: the FITS header says
"ZWO CCD ASI533MM Pro", a config file says "ZWO ASI533MM Pro", and a frame
record says "ZWO ASI 533MM Pro". This module turns any of them into one
form so they can be compared.
"""

import re


def normalize_camera_name(camera_name: str) -> str:
    """Reduce a camera name to lowercase letters and digits.

    Parameters
    ----------
    camera_name : `str`
        A camera name written in any spelling.

    Returns
    -------
    normalized_name : `str`
        The name with case, spaces, dashes and other punctuation removed,
        so "ZWO ASI 533MM Pro" and "zwo-asi533mm-pro" give the same text.
    """
    return re.sub(r"[^a-z0-9]", "", camera_name.lower())
