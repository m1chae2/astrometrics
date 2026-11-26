"""Write the id the library gives a star that Gaia DR3 lists.

Gaia DR3 (the third data release of the European Space Agency's Gaia
mission) gives every star a source number. The library saves such a star
under ``Gaia DR3 <source number>``, and the local Gaia cache, the Gaia
download and the star pipelines all write that same text. This module is
the one place that spells it, so the drivers (which may not import the
pipelines) and the pipelines agree.
"""

__all__ = ["GAIA_DR3_PREFIX", "gaia_record_id"]

GAIA_DR3_PREFIX = "Gaia DR3 "
"""The text in front of a Gaia DR3 source number in a star's id."""


def gaia_record_id(source_id: object) -> str:
    """Write the library id of the Gaia DR3 star with a source number.

    Parameters
    ----------
    source_id : `object`
        The Gaia DR3 source number, as an integer or as text.

    Returns
    -------
    record_id : `str`
        ``Gaia DR3 <source number>``, for example
        ``Gaia DR3 1328045433153485824``.
    """
    return f"{GAIA_DR3_PREFIX}{source_id}"
