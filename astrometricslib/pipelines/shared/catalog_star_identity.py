"""Rules for telling whether two catalog names are one star.

The same star has a different name in every catalog: ``HD 151086`` in the
Henry Draper catalog, ``Gaia DR3 1328045433153485824`` in Gaia,
``2MASS J16...`` in 2MASS. A run that looks a star up in one catalog and a
later run that looks it up in another would save it twice, so both the
save step (`star_recording`) and the one-time cleanup script
(`scripts/merge_duplicate_catalog_stars.py`) need the same three answers:

* how close two positions must be to count as one star,
* whether two names come from the same catalog (then they are different
  stars, however close), and
* which name to keep.

They live here so the two can never disagree.
"""

import re

# Two catalog rows closer than this on the sky are the same star. On the
# real catalog, pairs of non-position-only rows within 1 arcsecond number
# 678, within 2 arcseconds 754, and within 5 arcseconds 793: the count
# nearly stops growing after 2 arcseconds, so pairs inside it are one star
# under two names, and the few beyond are ordinary neighbors. 2 arcseconds
# is also well below the separation of real neighbors in these fields.
SAME_STAR_POSITION_TOLERANCE_ARCSEC = 2.0

# Names that beat every other catalog, best first. A name matching the
# first pattern is preferred to one matching the second, and both to any
# other name.
_PREFERRED_NAME_PATTERNS = (r"^(HD|BD|CD|CPD)[\s+\-\d]", r"^Gaia DR3 ")


def catalog_family(star_id: str) -> str:
    """Name the catalog a star's id comes from.

    Parameters
    ----------
    star_id : `str`
        A catalog id such as ``"HD 151086"``, ``"Gaia DR3 132..."`` or
        ``"2MASS J0535..."``.

    Returns
    -------
    family : `str`
        The letters that start the id (``"HD"``, ``"Gaia DR"``,
        ``"2MASS J"``), enough to tell two catalogs apart.
    """
    match = re.match(r"^\s*(\d*[A-Za-z\[\]*.]+(?:\s+[A-Za-z]+)?)", star_id)
    return match.group(1).strip() if match else star_id


def name_preference_rank(star_id: str) -> int:
    """Rank how much a star's name is preferred; lower is better.

    Parameters
    ----------
    star_id : `str`
        A catalog id.

    Returns
    -------
    rank : `int`
        ``0`` for an HD/BD/CD/CPD name, ``1`` for a Gaia DR3 name, and
        ``2`` (after both) for any other catalog.
    """
    for rank, pattern in enumerate(_PREFERRED_NAME_PATTERNS):
        if re.match(pattern, star_id):
            return rank
    return len(_PREFERRED_NAME_PATTERNS)


def choose_survivor_id(ids: list[str]) -> str:
    """Pick which name of one star to keep.

    Parameters
    ----------
    ids : `list` [`str`]
        The ids of the rows for one star.

    Returns
    -------
    survivor_id : `str`
        An HD/BD/CD/CPD name if any, else a Gaia DR3 name, else the
        first id alphabetically.
    """
    return min(sorted(ids), key=name_preference_rank)
