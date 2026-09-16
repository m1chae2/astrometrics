"""Reads a single SpectroscopyResult field off a star, dict-or-object.

Shared by every visualization module that needs one spectroscopy-owned
field (wavelengths, the overlay rectangle, the dispersion angle, ...)
without caring whether it was handed a real `StellarObject` or a plain
dict shaped like one. Kept dependency-free so both `star_field_visualization`
and the individual `layers/` modules can import it without a cycle.
"""

from typing import Any


def get_spectroscopy_field(obj: Any, field_name: str, default: Any = None) -> Any:
    """Read one spectroscopy-result field off a `StellarObject` or dict.

    Returns
    -------
    value : `Any`
        The field's value, or `default` if there's no spectroscopy
        result at all.
    """
    if hasattr(obj, "star_data"):
        spectroscopy = getattr(obj, "spectroscopy", None)
        return getattr(spectroscopy, field_name, default) if spectroscopy is not None else default
    return (obj.get("spectroscopy") or {}).get(field_name, default)
