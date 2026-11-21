"""Pick the most familiar name of a star from its catalog identifiers.

SIMBAD lists every identifier a star has, such as ``NAME Vega``,
``* alf Lyr``, ``* 3 Lyr``, ``HD 172167``, ``HIP 91262``,
``TYC 3105-2070-1`` and ``Gaia DR3 2097...``. A person knows a star best by
its proper name, then by its Bayer letter (a Greek letter and the
constellation), then by its Flamsteed number, then by its number in the Henry
Draper (HD), Hipparcos (HIP) or Tycho (TYC) catalogs, and last by its Gaia
DR3 number. `most_familiar_designation` returns the first of these that the
list holds, written the way people read it (``Vega``, ``alf Lyr``,
``3 Lyr``).
"""

import re
from collections.abc import Iterable

__all__ = ["designation_rank", "most_familiar_designation", "readable_designation"]

# The kinds of name in the order people know them. A name of no listed kind
# ranks after all of them.
_RANKED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("proper", re.compile(r"^NAME\s+\S")),
    ("bayer", re.compile(r"^\*\s+[a-z]{2,3}\d{0,2}\s+[A-Z][a-z]{2}\b")),
    ("flamsteed", re.compile(r"^\*\s+\d+\s+[A-Z][a-z]{2}\b")),
    ("hd", re.compile(r"^HD\s+\d")),
    ("hip", re.compile(r"^HIP\s+\d")),
    ("tycho", re.compile(r"^TYC\s+\d")),
    ("gaia", re.compile(r"^Gaia DR3\s+\d")),
)


def designation_rank(identifier: str) -> int:
    """Rank one identifier by how familiar it is; lower is more familiar.

    Parameters
    ----------
    identifier : `str`
        One SIMBAD identifier.

    Returns
    -------
    rank : `int`
        0 for a proper name, 1 Bayer, 2 Flamsteed, 3 HD, 4 HIP, 5 TYC,
        6 Gaia DR3, and 7 for anything else.
    """
    text = identifier.strip()
    for rank, (_, pattern) in enumerate(_RANKED_PATTERNS):
        if pattern.match(text):
            return rank
    return len(_RANKED_PATTERNS)


def readable_designation(identifier: str) -> str:
    """Write one identifier the way people read it.

    Parameters
    ----------
    identifier : `str`
        One SIMBAD identifier.

    Returns
    -------
    name : `str`
        The identifier without SIMBAD's ``NAME`` and ``*`` prefixes and with
        single spaces.
    """
    text = " ".join(identifier.split())
    if text.startswith("NAME "):
        return text[5:]
    if text.startswith("* "):
        return text[2:]
    return text


def most_familiar_designation(identifiers: Iterable[str]) -> str | None:
    """Pick the identifier a person knows the star by best.

    Parameters
    ----------
    identifiers : `iterable` [`str`]
        The star's identifiers, in any order.

    Returns
    -------
    name : `str` or `None`
        The most familiar one, made readable, or `None` for an empty list.
        Among names of the same kind the first in alphabetical order wins,
        so the answer does not depend on the order SIMBAD listed them in.
    """
    cleaned = [identifier.strip() for identifier in identifiers if identifier and identifier.strip()]
    if not cleaned:
        return None
    best = min(enumerate(cleaned), key=lambda item: (designation_rank(item[1]), item[1]))[1]
    return readable_designation(best)
