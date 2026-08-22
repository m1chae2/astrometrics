"""Pure FITS-header classification logic for frame scanning.

The disk-scanning/file-I/O half of the former
targetlib/frame_scanning_operations.py lives in
data_access/frame_scanning.py instead.
"""

from astrometricslib.utilities.enums import FilterType


def get_filter_type(header: dict) -> FilterType:
    """Extract FilterType from a FITS header via keyword matching.

    Falls back to `FilterType.NONE` when no keyword matches.

    Parameters
    ----------
    header : `dict`
        Dictionary-like FITS header object.

    Returns
    -------
    filter_type : `FilterType`
        The matched filter type, or `FilterType.NONE` if the header's
        ``FILTER`` value does not match any known keyword.
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
