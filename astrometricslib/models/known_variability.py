"""Says whether the catalogs already list a star as a variable star.

A star whose brightness changes is only news if nobody has listed it yet.
This module reads the object type SIMBAD (an online database of named
astronomical objects) gives a star and sorts it into one of four answers.
The answer is a statement about SIMBAD's record, not about the sky: a star
that SIMBAD does not call variable may still be one.

The two sets of object-type codes below were generated from SIMBAD's own
definition table (``SELECT otype, otype_longname, is_candidate, path FROM
otypedef``, read from the SIMBAD TAP service on 2026-10-09, 226 types), so
the classifier needs no network. The rule used:

- ``KNOWN_VARIABLE_OBJECT_TYPES`` holds every non-candidate type that lies
  under SIMBAD's variable-star branch (``V*``), every type whose long name
  contains "Variable", and a few types that are variable by definition but
  sit elsewhere in SIMBAD's tree: eclipsing binaries (``EB*``), cataclysmic
  binaries and novae (``CV*``, ``No*``), symbiotic stars (``Sy*``), T Tauri
  and Herbig Ae/Be stars (``TT*``, ``Ae*``), Be stars (``Be*``) and X-ray
  binaries (``XB*``, ``LXB``, ``HXB``). The last group is a judgement call:
  SIMBAD does not put them under ``V*``, but they are listed as variable
  types in the General Catalogue of Variable Stars.
- ``SUSPECTED_VARIABLE_OBJECT_TYPES`` holds the candidate ("?") version of
  each type above.

If SIMBAD adds or renames a type, rerun the query and update the sets.
"""

from enum import StrEnum


class KnownVariability(StrEnum):
    """What the catalogs say about a star being variable."""

    KNOWN_VARIABLE = "known_variable"
    SUSPECTED_VARIABLE = "suspected_variable"
    NOT_LISTED = "not_listed_as_variable"
    UNKNOWN = "unknown"


KNOWN_VARIABLE_OBJECT_TYPES = frozenset({
    "Ae*",
    "BY*",
    "Be*",
    "CV*",
    "Ce*",
    "EB*",
    "El*",
    "Er*",
    "HXB",
    "Ir*",
    "LP*",
    "LXB",
    "Mi*",
    "No*",
    "Or*",
    "Pu*",
    "RC*",
    "RR*",
    "RS*",
    "RV*",
    "Ro*",
    "SX*",
    "Sy*",
    "TT*",
    "V*",
    "WV*",
    "XB*",
    "a2*",
    "bC*",
    "cC*",
    "dS*",
    "gD*",
    "var",
})

SUSPECTED_VARIABLE_OBJECT_TYPES = frozenset({
    "Ae?",
    "BY?",
    "Be?",
    "CV?",
    "Ce?",
    "EB?",
    "El?",
    "Er?",
    "HX?",
    "LP?",
    "LX?",
    "Mi?",
    "No?",
    "Pu?",
    "RC?",
    "RR?",
    "RS?",
    "RV?",
    "Ro?",
    "Sy?",
    "TT?",
    "V*?",
    "WV?",
    "XB?",
    "a2?",
    "bC?",
})

# SIMBAD joins the object types of one star with this character when a
# caller asks for all of them.
_OBJECT_TYPE_SEPARATOR = "|"


def classify_simbad_object_types(object_types: str | bytes | None) -> KnownVariability:
    """Sort a star's SIMBAD object type(s) into a known-variability answer.

    Parameters
    ----------
    object_types : `str`, `bytes` or `None`
        One SIMBAD object-type code (such as ``"EB*"``), several joined with
        ``|``, or nothing. Codes are case-sensitive (``a2*`` is not ``A2*``).

    Returns
    -------
    status : `KnownVariability`
        ``UNKNOWN`` when there is no object type (no SIMBAD record, or SIMBAD
        gave none), so nothing can be said. ``KNOWN_VARIABLE`` when any type
        is a variable type, else ``SUSPECTED_VARIABLE`` when any is a
        candidate variable type, else ``NOT_LISTED``.
    """
    if isinstance(object_types, bytes):
        object_types = object_types.decode("utf-8", errors="replace")
    codes = [code.strip() for code in (object_types or "").split(_OBJECT_TYPE_SEPARATOR)]
    codes = [code for code in codes if code]
    if not codes:
        return KnownVariability.UNKNOWN
    if any(code in KNOWN_VARIABLE_OBJECT_TYPES for code in codes):
        return KnownVariability.KNOWN_VARIABLE
    if any(code in SUSPECTED_VARIABLE_OBJECT_TYPES for code in codes):
        return KnownVariability.SUSPECTED_VARIABLE
    return KnownVariability.NOT_LISTED


def describe_known_variability(status: KnownVariability) -> str:
    """Say what a known-variability answer means, in one sentence.

    Parameters
    ----------
    status : `KnownVariability`
        The answer to describe.

    Returns
    -------
    sentence : `str`
        A plain sentence that never claims more than SIMBAD's record shows.
    """
    sentences = {
        KnownVariability.KNOWN_VARIABLE: "SIMBAD lists this star as a variable star.",
        KnownVariability.SUSPECTED_VARIABLE: "SIMBAD lists this star as a candidate variable star.",
        KnownVariability.NOT_LISTED: "SIMBAD does not list this star as variable.",
        KnownVariability.UNKNOWN: "There is no SIMBAD object type for this star, so nothing is known.",
    }
    return sentences[status]
