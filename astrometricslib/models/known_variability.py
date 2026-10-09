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

Two more catalogs are read the same way, and `combine_known_variability`
joins all three. The answer then means "not listed in the catalogs that were
consulted", and `describe_known_variability` names which they were:

- Gaia DR3's ``phot_variable_flag``. ``VARIABLE`` means Gaia's own analysis
  found the star variable. ``CONSTANT`` means Gaia tested it and found it
  steady. ``NOT_AVAILABLE`` means Gaia did not analyse it, which says nothing.
  The brightest stars (Algol, Vega, Sirius) are not in Gaia DR3 at all, so
  "no Gaia entry" is stored on its own and also says nothing.
- The AAVSO Variable Star Index (VSX). It lists variables, but also stars
  checked and found constant (type ``CST``), which are the best sample of
  steady stars the catalogs offer.
"""

from collections.abc import Sequence
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

# What is stored in place of a Gaia flag or a VSX type when the catalog was
# asked and had nothing to say. An empty string means it was never asked.
NO_GAIA_MATCH = "NO_GAIA_MATCH"
NOT_IN_VSX = "NOT_IN_VSX"
# A VSX entry whose type is blank or "*": someone listed the star as possibly
# variable but gave no type.
VSX_LISTED_WITHOUT_TYPE = "LISTED_WITHOUT_TYPE"

# VSX types for a star checked and found constant (the colon marks doubt).
VSX_CONSTANT_TYPE = "CST"
VSX_UNCERTAIN_CONSTANT_TYPE = "CST:"

GAIA_VARIABLE_FLAG = "VARIABLE"
GAIA_CONSTANT_FLAG = "CONSTANT"

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


def variable_object_types_in(object_types: str | bytes | None) -> list[str]:
    """List the variable and candidate-variable codes in a star's object types.

    Parameters
    ----------
    object_types : `str`, `bytes` or `None`
        SIMBAD object-type codes joined with ``|``, or nothing.

    Returns
    -------
    codes : `list` [`str`]
        The codes that made the star a known or suspected variable, in the
        order given. Empty when there are none.
    """
    if isinstance(object_types, bytes):
        object_types = object_types.decode("utf-8", errors="replace")
    codes = [code.strip() for code in (object_types or "").split(_OBJECT_TYPE_SEPARATOR)]
    return [
        code
        for code in codes
        if code in KNOWN_VARIABLE_OBJECT_TYPES or code in SUSPECTED_VARIABLE_OBJECT_TYPES
    ]


def classify_gaia_variable_flag(flag: str | None) -> KnownVariability:
    """Read a stored Gaia DR3 ``phot_variable_flag``.

    Parameters
    ----------
    flag : `str` or `None`
        ``"VARIABLE"``, ``"CONSTANT"``, ``"NOT_AVAILABLE"``, `NO_GAIA_MATCH`,
        or empty when Gaia was never asked.

    Returns
    -------
    status : `KnownVariability`
        ``UNKNOWN`` when Gaia was never asked, ``KNOWN_VARIABLE`` for
        ``VARIABLE``, and ``NOT_LISTED`` for anything else: Gaia was asked
        and does not list the star as variable.
    """
    flag = (flag or "").strip()
    if not flag:
        return KnownVariability.UNKNOWN
    if flag == GAIA_VARIABLE_FLAG:
        return KnownVariability.KNOWN_VARIABLE
    return KnownVariability.NOT_LISTED


def classify_vsx_type(vsx_type: str | None) -> KnownVariability:
    """Read a stored VSX variability type.

    Parameters
    ----------
    vsx_type : `str` or `None`
        A VSX type such as ``"EA/SD"`` or ``"DCEP"``, ``"CST"`` for a star
        listed as constant, `VSX_LISTED_WITHOUT_TYPE`, `NOT_IN_VSX`, or empty
        when VSX was never asked.

    Returns
    -------
    status : `KnownVariability`
        ``UNKNOWN`` when VSX was never asked. ``NOT_LISTED`` when the star is
        not in VSX or is listed as constant. ``SUSPECTED_VARIABLE`` when it is
        listed with no type. ``KNOWN_VARIABLE`` for any other type.
    """
    vsx_type = (vsx_type or "").strip()
    if not vsx_type:
        return KnownVariability.UNKNOWN
    if vsx_type in (NOT_IN_VSX, VSX_CONSTANT_TYPE, VSX_UNCERTAIN_CONSTANT_TYPE):
        return KnownVariability.NOT_LISTED
    if vsx_type == VSX_LISTED_WITHOUT_TYPE:
        return KnownVariability.SUSPECTED_VARIABLE
    return KnownVariability.KNOWN_VARIABLE


def combine_known_variability(
    simbad_object_types: str | None, gaia_variable_flag: str | None, vsx_variability_type: str | None
) -> KnownVariability:
    """Join what SIMBAD, Gaia DR3 and VSX say about a star.

    Parameters
    ----------
    simbad_object_types : `str` or `None`
        The star's SIMBAD object types.
    gaia_variable_flag : `str` or `None`
        The stored Gaia flag.
    vsx_variability_type : `str` or `None`
        The stored VSX type.

    Returns
    -------
    status : `KnownVariability`
        ``KNOWN_VARIABLE`` if any catalog lists it, else ``SUSPECTED_VARIABLE``
        if any suspects it, else ``NOT_LISTED`` if at least one catalog was
        consulted, else ``UNKNOWN``.
    """
    answers = {
        classify_simbad_object_types(simbad_object_types),
        classify_gaia_variable_flag(gaia_variable_flag),
        classify_vsx_type(vsx_variability_type),
    }
    for status in (
        KnownVariability.KNOWN_VARIABLE,
        KnownVariability.SUSPECTED_VARIABLE,
        KnownVariability.NOT_LISTED,
    ):
        if status in answers:
            return status
    return KnownVariability.UNKNOWN


def catalogs_consulted(
    simbad_object_types: str | None, gaia_variable_flag: str | None, vsx_variability_type: str | None
) -> list[str]:
    """Name the catalogs that have been asked about a star.

    Returns
    -------
    names : `list` [`str`]
        Any of ``"SIMBAD"``, ``"Gaia DR3"``, ``"VSX"``, in that order.
    """
    consulted = []
    if classify_simbad_object_types(simbad_object_types) is not KnownVariability.UNKNOWN:
        consulted.append("SIMBAD")
    if classify_gaia_variable_flag(gaia_variable_flag) is not KnownVariability.UNKNOWN:
        consulted.append("Gaia DR3")
    if classify_vsx_type(vsx_variability_type) is not KnownVariability.UNKNOWN:
        consulted.append("VSX")
    return consulted


def is_confirmed_constant(
    simbad_object_types: str | None, gaia_variable_flag: str | None, vsx_variability_type: str | None
) -> bool:
    """Say whether a catalog positively calls a star constant.

    "Not listed as variable" is not enough for a sample of steady stars: the
    star may simply never have been checked. This asks for a positive
    statement: Gaia's ``CONSTANT`` flag, or a VSX entry of type ``CST``.

    Returns
    -------
    is_constant : `bool`
        True when Gaia or VSX says constant and no catalog lists or suspects
        the star as variable.
    """
    if combine_known_variability(simbad_object_types, gaia_variable_flag, vsx_variability_type) not in (
        KnownVariability.NOT_LISTED,
    ):
        return False
    return (gaia_variable_flag or "").strip() == GAIA_CONSTANT_FLAG or (
        (vsx_variability_type or "").strip() == VSX_CONSTANT_TYPE
    )


ALL_CATALOGS = ("SIMBAD", "Gaia DR3", "VSX")


def _join_names(names: Sequence[str]) -> str:
    """Join names in plain English.

    Returns
    -------
    text : `str`
        ``"A"``, ``"A or B"`` or ``"A, B or C"``.
    """
    names = list(names)
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " or " + names[-1]


def describe_known_variability(status: KnownVariability, catalogs: Sequence[str] | None = None) -> str:
    """Say what a known-variability answer means, in one sentence.

    Parameters
    ----------
    status : `KnownVariability`
        The answer to describe.
    catalogs : `Sequence` [`str`], optional
        The catalogs that were asked (from `catalogs_consulted`). Defaults to
        SIMBAD alone.

    Returns
    -------
    sentence : `str`
        A plain sentence that never claims more than the catalogs consulted
        show. For ``NOT_LISTED`` it names the catalogs that were not asked.
    """
    asked = list(catalogs) if catalogs is not None else ["SIMBAD"]
    names = _join_names(asked)
    if status is KnownVariability.KNOWN_VARIABLE:
        return f"At least one of {names} lists this star as a variable star."
    if status is KnownVariability.SUSPECTED_VARIABLE:
        return f"At least one of {names} lists this star as a candidate variable star."
    if status is KnownVariability.NOT_LISTED:
        sentence = f"Not listed as variable in {names}."
        not_asked = [catalog for catalog in ALL_CATALOGS if catalog not in asked]
        if not_asked:
            sentence += f" {_join_names(not_asked)} not checked."
        return sentence
    return "No catalog has been asked about this star, so nothing is known."
