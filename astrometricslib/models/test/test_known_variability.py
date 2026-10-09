"""Tests for the known-variability classifier.

The object-type strings in these tests are the ones live SIMBAD returned on
2026-10-09, so the tests show the classifier works on what SIMBAD really
sends, including the case that motivated reading every object type: the main
type of the eclipsing binary Algol is only "SB*".
"""

import pytest

from astrometricslib.models.known_variability import (
    KNOWN_VARIABLE_OBJECT_TYPES,
    NO_GAIA_MATCH,
    NOT_IN_VSX,
    SUSPECTED_VARIABLE_OBJECT_TYPES,
    VSX_LISTED_WITHOUT_TYPE,
    KnownVariability,
    catalogs_consulted,
    classify_gaia_variable_flag,
    classify_simbad_object_types,
    classify_vsx_type,
    combine_known_variability,
    describe_known_variability,
    is_confirmed_constant,
    variable_object_types_in,
)
from astrometricslib.models.stellar_source import StellarObject


@pytest.mark.parametrize(
    ("object_types", "expected"),
    [
        # Live SIMBAD "all types" strings.
        ("*|**|EB*|IR|NIR|SB*|UV|V*|X", KnownVariability.KNOWN_VARIABLE),  # Algol
        ("*|**|dS*|IR|MIR|NIR|PM*|smm|UV|V*|X", KnownVariability.KNOWN_VARIABLE),  # Vega
        ("*|**|AB*|AB?|Em*|G|IR|Mas|Mi*|NIR|PM*|smm|UV|V*|X", KnownVariability.KNOWN_VARIABLE),  # Mira
        ("*|IR|NIR|V*", KnownVariability.KNOWN_VARIABLE),
        # A single type is enough.
        ("RR*", KnownVariability.KNOWN_VARIABLE),
        ("cC*", KnownVariability.KNOWN_VARIABLE),
        ("EB*", KnownVariability.KNOWN_VARIABLE),
        ("a2*", KnownVariability.KNOWN_VARIABLE),
    ],
)
def test_a_variable_type_anywhere_in_the_list_makes_a_known_variable(
    object_types: str, expected: KnownVariability
) -> None:
    """Any variable type in the list is enough, wherever it sits."""
    assert classify_simbad_object_types(object_types) is expected


def test_a_candidate_type_alone_is_a_suspected_variable() -> None:
    """A candidate variable type with no firm one is only suspected."""
    assert classify_simbad_object_types("*|IR|V*?") is KnownVariability.SUSPECTED_VARIABLE
    assert classify_simbad_object_types("EB?") is KnownVariability.SUSPECTED_VARIABLE


def test_a_firm_type_beats_a_candidate_type() -> None:
    """A star with both a firm and a candidate type is a known variable."""
    assert classify_simbad_object_types("V*?|EB*") is KnownVariability.KNOWN_VARIABLE


def test_types_with_no_variable_type_are_not_listed() -> None:
    """A full list with no variable type is "not listed" in SIMBAD."""
    assert classify_simbad_object_types("*|**|IR|NIR|UV|X") is KnownVariability.NOT_LISTED
    assert classify_simbad_object_types("*") is KnownVariability.NOT_LISTED


@pytest.mark.parametrize("nothing", [None, "", "   ", "|", " | "])
def test_no_object_type_means_unknown_not_not_listed(nothing: str | None) -> None:
    """No type at all says nothing; it must never read as "not listed"."""
    assert classify_simbad_object_types(nothing) is KnownVariability.UNKNOWN


def test_bytes_from_the_database_are_read() -> None:
    """A byte string is decoded."""
    assert classify_simbad_object_types(b"*|EB*") is KnownVariability.KNOWN_VARIABLE


def test_codes_are_case_sensitive() -> None:
    """``a2*`` is a variable type; ``A2*`` is not a SIMBAD code at all."""
    assert classify_simbad_object_types("a2*") is KnownVariability.KNOWN_VARIABLE
    assert classify_simbad_object_types("A2*") is KnownVariability.NOT_LISTED


def test_the_main_type_alone_can_hide_a_variable() -> None:
    """Algol's main type is "SB*"; on its own it reads as not listed.

    This is why the star identifier stores every object type, and why a result
    with only the main type is treated as unknown unless that type is itself a
    variable type.
    """
    assert classify_simbad_object_types("SB*") is KnownVariability.NOT_LISTED
    assert classify_simbad_object_types("*|**|EB*|SB*|V*") is KnownVariability.KNOWN_VARIABLE


def test_the_two_sets_are_disjoint_and_well_formed() -> None:
    """Known types are firm codes; suspected types are candidate codes."""
    assert KNOWN_VARIABLE_OBJECT_TYPES.isdisjoint(SUSPECTED_VARIABLE_OBJECT_TYPES)
    assert not any(code.endswith("?") for code in KNOWN_VARIABLE_OBJECT_TYPES)
    assert all(code.endswith("?") for code in SUSPECTED_VARIABLE_OBJECT_TYPES)


def test_the_description_names_the_catalogs_that_were_consulted() -> None:
    """The sentence claims no more than was asked, and names the rest."""
    simbad_only = describe_known_variability(KnownVariability.NOT_LISTED)
    assert simbad_only == "Not listed as variable in SIMBAD. Gaia DR3 or VSX not checked."

    all_three = describe_known_variability(KnownVariability.NOT_LISTED, ["SIMBAD", "Gaia DR3", "VSX"])
    assert all_three == "Not listed as variable in SIMBAD, Gaia DR3 or VSX."

    assert "SIMBAD or VSX" in describe_known_variability(KnownVariability.KNOWN_VARIABLE, ["SIMBAD", "VSX"])
    assert "nothing is known" in describe_known_variability(KnownVariability.UNKNOWN, [])


def test_a_star_with_no_recorded_types_is_unknown() -> None:
    """A star saved before types were recorded is unknown, not "not listed"."""
    assert StellarObject().known_variability is KnownVariability.UNKNOWN


def test_a_star_reads_its_known_variability_from_its_stored_types() -> None:
    """The star's property follows its stored object types."""
    star = StellarObject(simbadObjectTypes="*|**|EB*|SB*|V*")

    assert star.known_variability is KnownVariability.KNOWN_VARIABLE
    assert "simbadObjectTypes" in star.model_dump(by_alias=True)


def test_the_helper_lists_the_codes_that_made_a_star_variable() -> None:
    """The codes are returned in order, firm and candidate alike."""
    assert variable_object_types_in("*|**|EB*|SB*|V*|V*?") == ["EB*", "V*", "V*?"]
    assert variable_object_types_in("*|IR|NIR") == []
    assert variable_object_types_in(None) == []
    assert variable_object_types_in(b"RR*") == ["RR*"]


@pytest.mark.parametrize(
    ("flag", "expected"),
    [
        ("VARIABLE", KnownVariability.KNOWN_VARIABLE),
        ("CONSTANT", KnownVariability.NOT_LISTED),
        ("NOT_AVAILABLE", KnownVariability.NOT_LISTED),
        (NO_GAIA_MATCH, KnownVariability.NOT_LISTED),
        ("", KnownVariability.UNKNOWN),
        (None, KnownVariability.UNKNOWN),
    ],
)
def test_gaia_flags_are_read_and_an_unasked_catalog_is_unknown(
    flag: str | None, expected: KnownVariability
) -> None:
    """Only VARIABLE lists a star; any other answer means Gaia does not."""
    assert classify_gaia_variable_flag(flag) is expected


@pytest.mark.parametrize(
    ("vsx_type", "expected"),
    [
        ("EA/SD", KnownVariability.KNOWN_VARIABLE),
        ("DCEP", KnownVariability.KNOWN_VARIABLE),
        ("RRAB:", KnownVariability.KNOWN_VARIABLE),
        ("CST", KnownVariability.NOT_LISTED),
        ("CST:", KnownVariability.NOT_LISTED),
        (NOT_IN_VSX, KnownVariability.NOT_LISTED),
        (VSX_LISTED_WITHOUT_TYPE, KnownVariability.SUSPECTED_VARIABLE),
        ("", KnownVariability.UNKNOWN),
        (None, KnownVariability.UNKNOWN),
    ],
)
def test_vsx_types_are_read_and_a_constant_star_is_not_a_variable(
    vsx_type: str | None, expected: KnownVariability
) -> None:
    """VSX lists constant stars too; type CST must never read as variable."""
    assert classify_vsx_type(vsx_type) is expected


def test_the_catalogs_are_joined_with_the_strongest_answer_winning() -> None:
    """Any catalog listing the star wins; unasked catalogs are only unknown."""
    algol_types = "*|**|EB*|SB*|V*"
    assert combine_known_variability(algol_types, "", "") is KnownVariability.KNOWN_VARIABLE
    assert combine_known_variability("*|IR", "VARIABLE", "") is KnownVariability.KNOWN_VARIABLE
    assert combine_known_variability("*|IR", "CONSTANT", "DCEP") is KnownVariability.KNOWN_VARIABLE
    assert (
        combine_known_variability("*|IR", "", VSX_LISTED_WITHOUT_TYPE) is KnownVariability.SUSPECTED_VARIABLE
    )
    assert combine_known_variability("*|IR", "CONSTANT", "CST") is KnownVariability.NOT_LISTED
    assert combine_known_variability("", "", "") is KnownVariability.UNKNOWN


def test_catalogs_consulted_lists_only_the_ones_that_were_asked() -> None:
    """The list is in a fixed order and leaves out unasked catalogs."""
    assert catalogs_consulted("*|IR", "", NOT_IN_VSX) == ["SIMBAD", "VSX"]
    assert catalogs_consulted("", "", "") == []
    assert catalogs_consulted("*", NO_GAIA_MATCH, "CST") == ["SIMBAD", "Gaia DR3", "VSX"]


def test_a_confirmed_constant_needs_a_positive_statement_and_no_variable_listing() -> None:
    """Silence is not constancy: only Gaia CONSTANT or VSX CST counts."""
    assert is_confirmed_constant("*|IR", "CONSTANT", "") is True
    assert is_confirmed_constant("*|IR", "", "CST") is True
    assert is_confirmed_constant("*|IR", "", NOT_IN_VSX) is False
    assert is_confirmed_constant("*|IR", NO_GAIA_MATCH, "") is False
    assert is_confirmed_constant("*|IR", "CONSTANT", "") is True
    # A variable listing anywhere overrides a constant statement elsewhere.
    assert is_confirmed_constant("*|V*", "CONSTANT", "CST") is False
    assert is_confirmed_constant("*|IR", "CONSTANT", "EA") is False
    # An uncertain constant ("CST:") is not enough.
    assert is_confirmed_constant("*|IR", "", "CST:") is False


def test_a_star_joins_its_three_catalog_fields() -> None:
    """The star's property and helpers follow the stored fields."""
    star = StellarObject(simbadObjectTypes="*|IR", gaiaVariableFlag="CONSTANT", vsxVariabilityType="CST")

    assert star.known_variability is KnownVariability.NOT_LISTED
    assert star.known_variability_catalogs == ["SIMBAD", "Gaia DR3", "VSX"]
    assert star.is_confirmed_constant is True
    assert StellarObject(vsxVariabilityType="EA/SD").known_variability is KnownVariability.KNOWN_VARIABLE
