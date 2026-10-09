"""Tests for the known-variability classifier.

The object-type strings in these tests are the ones live SIMBAD returned on
2026-10-09, so the tests show the classifier works on what SIMBAD really
sends, including the case that motivated reading every object type: the main
type of the eclipsing binary Algol is only "SB*".
"""

import pytest

from astrometricslib.models.known_variability import (
    KNOWN_VARIABLE_OBJECT_TYPES,
    SUSPECTED_VARIABLE_OBJECT_TYPES,
    KnownVariability,
    classify_simbad_object_types,
    describe_known_variability,
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


def test_every_answer_has_a_description_that_claims_only_simbads_record() -> None:
    """The sentences name SIMBAD, so they never claim more than it shows."""
    for status in KnownVariability:
        assert "SIMBAD" in describe_known_variability(status)


def test_a_star_with_no_recorded_types_is_unknown() -> None:
    """A star saved before types were recorded is unknown, not "not listed"."""
    assert StellarObject().known_variability is KnownVariability.UNKNOWN


def test_a_star_reads_its_known_variability_from_its_stored_types() -> None:
    """The star's property follows its stored object types."""
    star = StellarObject(simbadObjectTypes="*|**|EB*|SB*|V*")

    assert star.known_variability is KnownVariability.KNOWN_VARIABLE
    assert "simbadObjectTypes" in star.model_dump(by_alias=True)
