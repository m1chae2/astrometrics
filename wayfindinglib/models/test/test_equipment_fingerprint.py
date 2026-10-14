"""Purpose: Unit tests for the equipment fingerprint.

Description: Verifies that one setup always gets one fingerprint, however
its device names are spelled, and that any real change of equipment gives a
different one, so measurements from different setups are never pooled.
"""

from wayfindinglib.models.equipment_and_site.equipment_fingerprint import (
    UNKNOWN_EQUIPMENT_FIELD,
    build_equipment_fingerprint,
)


def test_the_fingerprint_names_every_part_of_the_setup() -> None:
    """Verify the four parts appear, in a fixed order."""
    fingerprint = build_equipment_fingerprint("Apertura 75Q", "ZWO ASI 533MM Pro", 121.05, 6.39)

    assert fingerprint == "telescope=apertura75q|camera=zwoasi533mmpro|guide_focal_mm=121|guide_scale=6.4"


def test_different_spellings_of_one_camera_give_one_fingerprint() -> None:
    """Verify the header, config and frame-record spellings agree."""
    spellings = ("ZWO ASI 533MM Pro", "zwo-asi533mm-pro", "  ZWO  ASI533MM   Pro ")
    fingerprints = {build_equipment_fingerprint("Apertura 75Q", name, 121.05, 6.39) for name in spellings}

    assert len(fingerprints) == 1


def test_a_header_spelling_with_an_extra_word_is_a_different_camera() -> None:
    """Verify names that really differ are not merged.

    The FITS header says "ZWO CCD ASI533MM Pro" but the library spelling is
    "ZWO ASI 533MM Pro"; callers must translate through the camera profile's
    aliases before fingerprinting, and the fingerprint does not guess.
    """
    header_spelling = build_equipment_fingerprint("T", "ZWO CCD ASI533MM Pro", 121.0, 6.4)
    library_spelling = build_equipment_fingerprint("T", "ZWO ASI 533MM Pro", 121.0, 6.4)

    assert header_spelling != library_spelling


def test_changing_the_imaging_camera_changes_the_fingerprint() -> None:
    """Verify swapping the camera starts a new history."""
    with_asi = build_equipment_fingerprint("Apertura 75Q", "ZWO ASI 533MM Pro", 121.05, 6.39)
    with_nikon = build_equipment_fingerprint("Apertura 75Q", "Nikon D5300", 121.05, 6.39)

    assert with_asi != with_nikon


def test_changing_the_telescope_changes_the_fingerprint() -> None:
    """Verify swapping the telescope starts a new history."""
    first = build_equipment_fingerprint("Apertura 75Q", "ZWO ASI 533MM Pro", 121.05, 6.39)
    second = build_equipment_fingerprint("Nikkor 300mm", "ZWO ASI 533MM Pro", 121.05, 6.39)

    assert first != second


def test_changing_the_guide_scope_changes_the_fingerprint() -> None:
    """Verify a different guide focal length gives a different setup."""
    first = build_equipment_fingerprint("T", "C", 121.05, 6.39)
    second = build_equipment_fingerprint("T", "C", 240.0, 3.2)

    assert first != second


def test_tiny_differences_in_measured_guide_numbers_do_not_split_a_setup() -> None:
    """Verify rounding absorbs the noise in logged focal length and scale."""
    first = build_equipment_fingerprint("T", "C", 121.05, 6.39)
    second = build_equipment_fingerprint("T", "C", 120.98, 6.394)

    assert first == second


def test_unknown_parts_are_marked_and_never_match_known_ones() -> None:
    """Verify a session with no attributed imaging gear is kept apart."""
    unknown = build_equipment_fingerprint(None, None, 121.05, 6.39)
    known = build_equipment_fingerprint("Apertura 75Q", "ZWO ASI 533MM Pro", 121.05, 6.39)

    assert f"telescope={UNKNOWN_EQUIPMENT_FIELD}" in unknown
    assert unknown != known
