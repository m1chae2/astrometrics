"""Functions to read image settings and figure out what kind of picture it is.

This file only contains the logic for reading the header data.
The actual file reading happens elsewhere.
"""

from astrometricslib.utilities.enums import FilterType


def get_filter_type(header: dict) -> FilterType:
    """Read the image data to figure out which filter was used.

    Looks at the text in the 'FILTER' section of the image data and tries
    to match it to a known filter type (like Red, Green, Blue, or H-Alpha).

    Parameters
    ----------
    header : `dict`
        The image metadata.

    Returns
    -------
    filter_type : `FilterType`
        The matching filter, or `FilterType.NONE` if it doesn't match anything.
    """
    filter_str = str(header.get("FILTER", "")).upper()

    mapping = {
        "SPECTROSCOPY": FilterType.SPEC,
        "SPEC": FilterType.SPEC,
        "H-ALPHA": FilterType.Ha,
        "HA": FilterType.Ha,
        "OIII": FilterType.OIII,
        "SII": FilterType.SII,
        "RED": FilterType.R,
        "GREEN": FilterType.G,
        "BLUE": FilterType.B,
        "L": FilterType.L,
        "R": FilterType.R,
        "G": FilterType.G,
        "B": FilterType.B,
        "NONE": FilterType.NONE,
    }

    # Sort by length descending to match most specific terms first
    sorted_keys = sorted(mapping.keys(), key=len, reverse=True)
    for key in sorted_keys:
        if key in filter_str:
            return mapping[key]

    return FilterType.NONE
