"""Shared enumerations for astrometrics domain models."""

from enum import StrEnum


class FilterType(StrEnum):
    """Optical filter or spectroscopy accessory used to capture a frame.

    Notes
    -----
    ``LUMINANCE``, ``RED``, ``GREEN``, and ``BLUE`` are separate
    members (not true Python Enum aliases, since their values differ
    from ``L``, ``R``, ``G``, and ``B``) kept so that FITS headers
    written by capture software using the full color-name spelling
    still resolve to a matching filter.
    """

    L = "Luminance"
    R = "R"
    G = "G"
    B = "B"
    Ha = "Ha"
    OIII = "OIII"
    SII = "SII"
    SPEC = "Star Analyzer 200"
    NONE = "None"

    # Aliases for better compatibility with different software headers
    LUMINANCE = "Luminance"
    RED = "Red"
    GREEN = "Green"
    BLUE = "Blue"
