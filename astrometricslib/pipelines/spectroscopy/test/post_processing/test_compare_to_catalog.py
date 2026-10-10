"""Purpose: Unit tests for comparing a classification against the catalog.

Description: Verifies the luminosity-class note (a catalog giant's type
is explained against the nearest dwarf reference), the catalog-type
disagreement flag (a classification more than two spectral classes from
the catalog's own type), and the catalog-colour disagreement flag (a
synthetic colour far from the catalog B-V). These tests used to live in
`test_spectral_classifier.py` and `test_synthetic_colour.py`; they moved
here when `luminosity_class_note`/`catalog_disagreement_note`/
`is_catalog_giant`/`colour_disagreement_note` moved out of
`spectral_classifier.py`/`synthetic_colour.py` into
`post_processing/compare_to_catalog.py`.
"""

import pytest

from astrometricslib.pipelines.spectroscopy.post_processing.compare_to_catalog import (
    COLOUR_DISAGREEMENT_MAGNITUDES,
    MAXIMUM_CALIBRATED_B_MINUS_V,
    catalog_disagreement_note,
    colour_disagreement_note,
    is_catalog_giant,
    luminosity_class_note,
)


@pytest.mark.parametrize("catalog_type", ["K3II", "G8III", "K1.5III", "F5Ib", "B2Iab", "G8III/IV"])
def test_a_catalog_giant_gets_a_luminosity_note(catalog_type: str) -> None:
    """A catalog giant's type is the dwarf that looks alike, and it says so."""
    note = luminosity_class_note(catalog_type, "K7V", ("K3I", 0.29))

    assert is_catalog_giant(catalog_type)
    assert "closest main-sequence reference (K7V)" in note
    assert "K3I" in note


@pytest.mark.parametrize(
    "catalog_type", ["A0V", "B9.5V", "K5", "A2IV", "Unknown", "", None, "kA1h(eA)mA7", "A5V+M3-4V"]
)
def test_a_dwarf_or_untyped_star_gets_no_luminosity_note(catalog_type: str | None) -> None:
    """Only a catalog class of I, II or III raises the note."""
    assert not is_catalog_giant(catalog_type)
    assert luminosity_class_note(catalog_type, "K7V", ("K3I", 0.29)) == ""


def test_no_luminosity_note_without_a_matched_type() -> None:
    """An unclassified star has no type to explain."""
    assert luminosity_class_note("K3III", "Unknown") == ""
    assert luminosity_class_note("K3III", None) == ""


@pytest.mark.parametrize(
    ("catalog_type", "classified_type"),
    [("B7III", "M2V"), ("B9.5V", "K4V"), ("A0V", "K0V"), ("O9", "G2V")],
)
def test_a_type_two_classes_from_the_catalog_is_flagged(catalog_type: str, classified_type: str) -> None:
    """Elnath (B7III to M2V) and beta1 Cyg (B9.5V to K4V) are caught."""
    assert "subtype steps" in catalog_disagreement_note(catalog_type, classified_type)


@pytest.mark.parametrize(
    ("catalog_type", "classified_type"),
    [
        ("F8", "K0V"),
        ("A0V", "A2V"),
        ("K5", "K5V"),
        ("A5V+M3-4V", "A5V"),
        ("Unknown", "K0V"),
        (None, "G2V"),
        ("K0", "Unknown"),
    ],
)
def test_a_type_near_the_catalog_or_a_missing_type_is_not_flagged(
    catalog_type: str | None, classified_type: str
) -> None:
    """The widest ordinary miss on record (F8 matched K0V) stays quiet."""
    assert catalog_disagreement_note(catalog_type, classified_type) == ""


@pytest.mark.parametrize(
    ("catalog", "synthetic", "word"),
    [(-0.13, 0.91, "redder"), (0.47, -0.11, "bluer"), (0.0, 1.00, "redder"), (1.23, 1.70, "redder")],
)
def test_the_colours_of_the_problem_spectra_are_flagged(catalog: float, synthetic: float, word: str) -> None:
    """Elnath, TYC 3105-899-1, HD 172449 and Arcturus are all flagged."""
    note = colour_disagreement_note(catalog, synthetic)

    assert word in note
    assert "may not be this star's" in note


@pytest.mark.parametrize(
    ("catalog", "synthetic"),
    [(0.76, 1.06), (0.87, 0.99), (0.0, -0.08), (1.5, 1.5 + COLOUR_DISAGREEMENT_MAGNITUDES)],
)
def test_ordinary_scatter_is_not_flagged(catalog: float, synthetic: float) -> None:
    """The worst ordinary case (0.30 mag) and the threshold stay quiet."""
    assert colour_disagreement_note(catalog, synthetic) == ""


@pytest.mark.parametrize(
    ("catalog", "synthetic"),
    [
        (None, 0.5),
        (0.5, None),
        (float("nan"), 0.5),
        (0.5, float("nan")),
        (MAXIMUM_CALIBRATED_B_MINUS_V + 0.1, 0.0),
    ],
)
def test_missing_or_uncalibratable_colours_are_not_flagged(
    catalog: float | None, synthetic: float | None
) -> None:
    """Nothing is said for a missing colour or one beyond the range."""
    assert colour_disagreement_note(catalog, synthetic) == ""
