"""Reads the object types out of a row of a SIMBAD result.

Used by the astrometry pipeline when it matches a detected star to SIMBAD,
and by the script that fills in the types of stars saved before they were
recorded, so both read a result the same way.
"""

from typing import Any

from astrometricslib.models.known_variability import KnownVariability, classify_simbad_object_types

# The column the all-types field arrives in, under the names it has had.
ALL_TYPES_COLUMNS = ("alltypes.otypes", "ALLTYPES.OTYPES", "otypes")

# The votable fields to ask SIMBAD for so a row carries what this module reads.
OBJECT_TYPE_VOTABLE_FIELDS = ("otype", "alltypes")


def read_simbad_object_types(match: Any) -> str:
    """Read every object type SIMBAD lists for a matched star.

    The all-types column is used when the result has one. A result with only
    the main type cannot show that a star is *not* listed as variable (the
    main type of the eclipsing binary Algol is "SB*"), so then only a main
    type that is itself a variable type is kept, and anything else is
    treated as unknown.

    Parameters
    ----------
    match : `astropy.table.Row`
        One row of a SIMBAD result.

    Returns
    -------
    object_types : `str`
        The types joined with ``|``, or an empty string when the row has no
        usable type.
    """

    def read(*columns: str) -> str | None:
        """Read the first present, unmasked column as text.

        Returns
        -------
        text : `str` or `None`
            The value, or `None` when no column is present and unmasked.
        """
        for column in columns:
            if column not in match.colnames:
                continue
            value = match[column]
            if value is None or (hasattr(value, "mask") and bool(value.mask)):
                continue
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="replace")
            text = str(value).strip()
            if text:
                return text
        return None

    all_types = read(*ALL_TYPES_COLUMNS)
    if all_types is not None:
        return all_types
    main_type = read("otype", "OTYPE", "main_type")
    if main_type is not None and classify_simbad_object_types(main_type) is not KnownVariability.NOT_LISTED:
        return main_type
    return ""
