"""Purpose: Regression tests for spectral-field star registration.

Description: Verifies identify_spectral_stars_via_registration() carries
catalog identity from a reference (plate-solved) star field onto a
spectral stack's blind detections via pixel-geometry registration, leaves
extra/unmatched spectral stars untouched, and degrades gracefully (no
crash, zero matches) when there aren't enough positioned stars on either
side to register.
"""

import numpy as np
import pytest

from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.tasks.stellar_tasks.astrometry_tasks.spectral_star_registration import (
    identify_spectral_stars_via_registration,
)


def _reference_star(star_id: str, x: float, y: float) -> StellarObject:
    star = StellarObject(id=star_id, name=star_id)
    star.star_data = {"xcentroid": x, "ycentroid": y}
    star.right_ascension = 250.0
    star.declination = 36.0
    star.spectral_type = "G2V"
    star.stellar_spectral_type = "G2V"
    star.magnitude = 10.0
    star.is_catalog_identified = True
    return star


def _spectral_star(star_id: str, x: float, y: float) -> StellarObject:
    star = StellarObject(id=star_id, name=star_id)
    star.star_data = {"xcentroid": x, "ycentroid": y}
    star.dispersion_angle = 1.5
    star.spectrum_data_processed = {"wavelengths_angstrom": [4000.0], "intensities": [1.0]}
    return star


def _build_matched_fields(rng, count=20, rotation_deg=0.4, translation=(3.0, -2.0), jitter_px=1.5):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Build a reference field and a rotated/translated/jittered copy.

    Returns
    -------
    reference_stars, spectral_stars, reference_positions : `tuple`
        The reference-field stars, the corresponding (transformed)
        spectral-field stars, and the reference field's raw `(x, y)`
        positions.
    """
    reference_positions = rng.uniform(100, 2900, size=(count, 2))
    reference_stars = [_reference_star(f"HD{1000 + i}", x, y) for i, (x, y) in enumerate(reference_positions)]

    theta = np.radians(rotation_deg)
    rotation = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    jitter = rng.normal(0, jitter_px, size=reference_positions.shape)
    spectral_positions = (reference_positions @ rotation.T) + np.array(translation) + jitter
    spectral_stars = [_spectral_star(f"Star_{i + 1}", x, y) for i, (x, y) in enumerate(spectral_positions)]

    return reference_stars, spectral_stars, reference_positions


def test_identify_spectral_stars_via_registration_matches_rotated_translated_field():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A rotated/translated/jittered copy of the field matches fully."""
    rng = np.random.default_rng(0)
    reference_stars, spectral_stars, _ = _build_matched_fields(rng)

    matched_count = identify_spectral_stars_via_registration(spectral_stars, reference_stars)

    assert matched_count == len(spectral_stars)
    for reference_star, spectral_star in zip(reference_stars, spectral_stars, strict=True):
        assert spectral_star.id == f"{reference_star.id}::spectroscopy"
        assert spectral_star.name == reference_star.name
        assert spectral_star.is_catalog_identified is True
        # Spectroscopy-owned fields must survive identification untouched.
        assert spectral_star.dispersion_angle == pytest.approx(1.5)
        expected_spectrum = {"wavelengths_angstrom": [4000.0], "intensities": [1.0]}
        assert spectral_star.spectrum_data_processed == expected_spectrum


def test_identify_spectral_stars_via_registration_leaves_extra_stars_unmatched():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Spectral detections with no reference counterpart stay unmatched."""
    rng = np.random.default_rng(0)
    reference_stars, spectral_stars, _ = _build_matched_fields(rng)

    # Two spectral detections with no counterpart in the reference field.
    extra_positions = rng.uniform(100, 2900, size=(2, 2))
    for i, (x, y) in enumerate(extra_positions):
        spectral_stars.append(_spectral_star(f"Star_extra_{i}", x, y))

    matched_count = identify_spectral_stars_via_registration(spectral_stars, reference_stars)

    assert matched_count == len(reference_stars)
    unmatched = [star for star in spectral_stars if star.id.startswith("Star_extra_")]
    assert len(unmatched) == 2
    for star in unmatched:
        assert star.is_catalog_identified is False


def test_identify_spectral_stars_via_registration_uses_translation_only_when_available(caplog):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A pure-translation (fixed-mount) field matches without astroalign."""
    rng = np.random.default_rng(1)
    reference_stars, spectral_stars, _ = _build_matched_fields(
        rng, rotation_deg=0.0, translation=(5.0, -3.0), jitter_px=1.0
    )

    logger_name = "astrometricslib.tasks.stellar_tasks.astrometry_tasks.spectral_star_registration"
    with caplog.at_level("INFO", logger=logger_name):
        matched_count = identify_spectral_stars_via_registration(spectral_stars, reference_stars)

    assert matched_count == len(spectral_stars)
    assert any("translation-only offset" in record.message for record in caplog.records)
    assert not any("astroalign" in record.message for record in caplog.records)


def test_identify_spectral_stars_via_registration_translation_only_with_sparse_field():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Translation voting still matches a small (~10-star) field reliably.

    Mirrors the real-world shape of a spectroscopy run: only a handful
    of zero-order stars extracted, which is too few for astroalign's
    triangle-asterism matching to reliably converge on (see the module
    docstring), but plenty for a 2-degree-of-freedom offset vote.
    """
    rng = np.random.default_rng(2)
    reference_stars, spectral_stars, _ = _build_matched_fields(
        rng, count=10, rotation_deg=0.0, translation=(2.0, 4.0), jitter_px=0.5
    )

    matched_count = identify_spectral_stars_via_registration(spectral_stars, reference_stars)

    assert matched_count == len(spectral_stars)


def test_identify_spectral_stars_via_registration_too_few_stars_returns_zero():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Too few positioned stars on either side skips registration entirely."""
    reference_stars = [_reference_star(f"HD{i}", i * 10.0, i * 10.0) for i in range(2)]
    spectral_stars = [_spectral_star(f"Star_{i + 1}", i * 10.0, i * 10.0) for i in range(2)]

    matched_count = identify_spectral_stars_via_registration(spectral_stars, reference_stars)

    assert matched_count == 0
    assert spectral_stars[0].id == "Star_1"
    assert spectral_stars[0].is_catalog_identified is False


def test_identify_spectral_stars_via_registration_no_position_data_returns_zero():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Spectral stars with no `star_data` centroid can't be registered."""
    reference_stars = [_reference_star(f"HD{i}", i * 10.0, i * 10.0) for i in range(6)]
    spectral_stars = [StellarObject(id=f"Star_{i + 1}") for i in range(6)]  # no star_data

    matched_count = identify_spectral_stars_via_registration(spectral_stars, reference_stars)

    assert matched_count == 0
