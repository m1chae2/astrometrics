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
from astropy.wcs import WCS

from astrometricslib.models.stellar_source import SpectroscopyResult, StellarObject
from astrometricslib.pipelines.astrometry.spectral_star_registration import (
    estimate_registration_offset,
    identify_spectral_stars_via_registration,
    shift_wcs_to_frame,
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
    star.spectroscopy = SpectroscopyResult(
        wavelengths_angstrom=[4000.0], intensities=[1.0], dispersion_angle=1.5
    )
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
        # The same id, so the catalog merge lands in the star's existing row.
        assert spectral_star.id == reference_star.id
        assert spectral_star.name == reference_star.name
        assert spectral_star.is_catalog_identified is True
        # Spectroscopy-owned fields must survive identification untouched.
        assert spectral_star.spectroscopy.dispersion_angle == pytest.approx(1.5)
        expected_spectrum = SpectroscopyResult(
            wavelengths_angstrom=[4000.0], intensities=[1.0], dispersion_angle=1.5
        )
        assert spectral_star.spectroscopy == expected_spectrum


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

    logger_name = "astrometricslib.pipelines.astrometry.spectral_star_registration"
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


def test_estimate_registration_offset_recovers_the_shift_between_the_images():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """After matching, the median position difference is the image shift."""
    rng = np.random.default_rng(3)
    reference_stars, spectral_stars, _ = _build_matched_fields(
        rng, count=25, rotation_deg=0.0, translation=(-4.0, 6.0), jitter_px=0.5
    )
    identify_spectral_stars_via_registration(spectral_stars, reference_stars)

    dx, dy = estimate_registration_offset(spectral_stars, reference_stars)

    assert dx == pytest.approx(-4.0, abs=0.5)
    assert dy == pytest.approx(6.0, abs=0.5)


def test_estimate_registration_offset_refuses_a_rotated_field():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A rotation makes the stars disagree about the shift; report none."""
    rng = np.random.default_rng(4)
    reference_stars, spectral_stars, _ = _build_matched_fields(
        rng, count=25, rotation_deg=3.0, translation=(0.0, 0.0), jitter_px=0.5
    )
    for spectral_star, reference_star in zip(spectral_stars, reference_stars, strict=True):
        spectral_star.id = reference_star.id

    assert estimate_registration_offset(spectral_stars, reference_stars) is None


def test_estimate_registration_offset_needs_enough_pairs():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Three identified stars are too few to trust a median."""
    reference_stars = [_reference_star(f"HD{i}", 100.0 * i, 50.0 * i) for i in range(3)]
    spectral_stars = [_spectral_star(f"HD{i}", 100.0 * i + 2.0, 50.0 * i) for i in range(3)]

    assert estimate_registration_offset(spectral_stars, reference_stars) is None


def test_shift_wcs_to_frame_moves_a_sky_position_by_the_offset():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A sky position lands `offset` pixels away in the shifted solution."""
    reference_wcs = WCS(naxis=2)
    reference_wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    reference_wcs.wcs.crpix = [1500.0, 1500.0]
    reference_wcs.wcs.crval = [283.4, 33.0]
    reference_wcs.wcs.cd = [[-5e-4, 0.0], [0.0, 5e-4]]

    shifted_wcs = shift_wcs_to_frame(reference_wcs, (-6.0, 2.5))

    x_reference, y_reference = reference_wcs.wcs_world2pix(283.41, 33.02, 0)
    x_shifted, y_shifted = shifted_wcs.wcs_world2pix(283.41, 33.02, 0)
    assert x_shifted - x_reference == pytest.approx(-6.0)
    assert y_shifted - y_reference == pytest.approx(2.5)
    assert reference_wcs.wcs.crpix[0] == pytest.approx(1500.0)


def test_estimate_registration_offset_with_a_solution_ignores_stored_positions_and_repeated_names():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Stored pixel positions and repeated names must not skew the offset.

    Regression for M 27: the target's stars came from a second camera whose
    pixel frame was ~120 px away from the solved stack's, and registration
    gave one star's name to eight trail points, so the median shift moved the
    solved stack's WCS about 125 px from where the nebula really was.
    """
    reference_wcs = WCS(naxis=2)
    reference_wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    reference_wcs.wcs.crpix = [1500.0, 1500.0]
    reference_wcs.wcs.crval = [300.0, 22.0]
    reference_wcs.wcs.cd = [[-5e-4, 0.0], [0.0, 5e-4]]
    rng = np.random.default_rng(5)
    reference_stars = []
    spectral_stars = []
    for index in range(12):
        right_ascension = 300.0 + rng.uniform(-0.3, 0.3)
        declination = 22.0 + rng.uniform(-0.3, 0.3)
        x_true, y_true = reference_wcs.wcs_world2pix(right_ascension, declination, 0)
        # Stored position from another stack's pixel frame.
        star = _reference_star(f"HD{index}", float(x_true) + 120.0, float(y_true) - 4.0)
        star.right_ascension = right_ascension
        star.declination = declination
        reference_stars.append(star)
        spectral_stars.append(_spectral_star(f"HD{index}", float(x_true) - 5.0, float(y_true) + 43.0))
    # One star name wrongly given to several unrelated detections.
    spectral_stars.extend(_spectral_star("HD0", 1919.0 + 3.0 * copy, 1243.0) for copy in range(8))

    dx, dy = estimate_registration_offset(spectral_stars, reference_stars, reference_wcs)

    assert dx == pytest.approx(-5.0, abs=0.01)
    assert dy == pytest.approx(43.0, abs=0.01)


def test_registration_carries_the_reference_stars_catalog_colour():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A spectral star matched to a reference star takes on its B-V too."""
    rng = np.random.default_rng(6)
    reference_stars, spectral_stars, _ = _build_matched_fields(
        rng, count=25, rotation_deg=0.0, translation=(2.0, -1.0)
    )
    for star in reference_stars:
        star.b_minus_v = 0.47

    identify_spectral_stars_via_registration(spectral_stars, reference_stars)

    assert all(star.b_minus_v == pytest.approx(0.47) for star in spectral_stars if star.is_catalog_identified)
    assert any(star.is_catalog_identified for star in spectral_stars)
