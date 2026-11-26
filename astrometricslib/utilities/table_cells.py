"""Read one cell of a catalog table as a number.

Catalog answers (SIMBAD, Gaia, VizieR) come back as astropy tables. A
missing value there is often a masked cell, not `None`, and turning a masked
cell into a float gives a made-up number or a warning. `optional_float` is
the one reader that treats a masked, empty, non-numeric or infinite cell as
"no value".
"""

import math
from typing import Any

import numpy as np

__all__ = ["optional_float"]


def optional_float(value: Any) -> float | None:
    """Turn a catalog table cell into a float, or `None` if it has no value.

    Parameters
    ----------
    value : `Any`
        A table cell: a number, a NumPy scalar, text, `None`, or an astropy
        masked value.

    Returns
    -------
    number : `float` or `None`
        The value, or `None` when it is missing, masked, not a number, or
        not finite (NaN or infinite).
    """
    if value is None or (hasattr(value, "mask") and bool(np.all(value.mask))):
        return None
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    return number if math.isfinite(number) else None
