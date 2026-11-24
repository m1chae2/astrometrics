"""Purpose: Tell an extended catalog object from a star.

Description: A name such as "M 57" resolves to one SIMBAD entry. For a star
that entry belongs on the brightest detection near the frame centre. For an
extended object it does not: a ring nebula is a faint, wide glow, not a bright
point, and the detection nearest its position is a chance star. This module
holds the test on a SIMBAD type code and on a catalog row, so the star
identifier and the extended-source hint use one rule. An object is extended
when `target_object_category` calls it a galaxy, a nebula or a cluster.
"""

from typing import Any

from astrometricslib.utilities.target_object_category import target_object_category

__all__ = ["is_extended_catalog_entry", "is_extended_object_type"]

EXTENDED_CATEGORIES = frozenset({"galaxy", "nebula", "cluster"})
"""The broad kinds of object that are extended rather than point sources."""


def is_extended_object_type(simbad_object_type: str | None) -> bool:
    """Tell whether a SIMBAD type code is an extended object.

    Parameters
    ----------
    simbad_object_type : `str` or `None`
        SIMBAD's main type code, such as "PN" or "AGN".

    Returns
    -------
    is_extended : `bool`
        True for a galaxy, nebula or cluster of any kind.
    """
    return target_object_category(simbad_object_type) in EXTENDED_CATEGORIES


def is_extended_catalog_entry(result_table: Any, index: int) -> bool:
    """Tell whether one row of a SIMBAD result is an extended object.

    Parameters
    ----------
    result_table : `astropy.table.Table`
        The SIMBAD rows. A table with no ``otype`` column has no answer.
    index : `int`
        The row to test.

    Returns
    -------
    is_extended : `bool`
        True when the row's type code is a galaxy, nebula or cluster.
    """
    if "otype" not in result_table.colnames:
        return False
    return is_extended_object_type(str(result_table["otype"][index]).strip())
