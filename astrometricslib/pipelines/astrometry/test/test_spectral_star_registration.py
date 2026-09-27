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
    identify_spectral_stars_via_solution,
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


def _solution_field(shift=(-163.0, 47.0), count=12):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Build a plate solution, catalog stars and spectral detections.

    Parameters
    ----------
    shift : `tuple` [`float`, `float`], optional
        How far the spectroscopy image sits from the solved image, in pixels.
    count : `int`, optional
        How many stars to make besides the bright one at the centre.

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        The solved image's plate solution.
    reference_stars : `list` [`StellarObject`]
        The catalog stars. The first is the bright one, whose stored pixel
        position is stale (it is the position in the spectroscopy image).
    spectral_stars : `list` [`StellarObject`]
        One unnamed detection per catalog star, at its spectroscopy-image
        position.
    """
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crpix = [1500.0, 1500.0]
    wcs.wcs.crval = [310.0, 45.0]
    wcs.wcs.cd = [[-5e-4, 0.0], [0.0, 5e-4]]
    rng = np.random.default_rng(11)
    reference_stars = []
    spectral_stars = []
    for index in range(count + 1):
        right_ascension = 310.0 + (0.0 if index == 0 else rng.uniform(-0.3, 0.3))
        declination = 45.0 + (0.0 if index == 0 else rng.uniform(-0.3, 0.3))
        x_true, y_true = (float(v) for v in wcs.wcs_world2pix(right_ascension, declination, 0))
        spectral_x, spectral_y = x_true + shift[0], y_true + shift[1]
        # The bright star's stored position is stale: it is the position
        # in the spectroscopy image, not the solved image.
        stored = (spectral_x, spectral_y) if index == 0 else (x_true, y_true)
        star = _reference_star("Bright" if index == 0 else f"HD{index}", *stored)
        star.right_ascension = right_ascension
        star.declination = declination
        reference_stars.append(star)
        spectral_stars.append(_spectral_star(f"Star_{index}", spectral_x + 0.4, spectral_y - 0.3))
    return wcs, reference_stars, spectral_stars


def test_a_bright_star_with_a_stale_stored_position_is_still_named_from_the_solution():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Regression for Deneb: the saturated star had a stale stored position.

    Geometric registration slid the fields by the other stars' offset and put
    the bright star 170 px from its own reference, so it stayed unnamed. The
    solution places it by sky position, so it is named.
    """
    wcs, reference_stars, spectral_stars = _solution_field()
    matched = identify_spectral_stars_via_solution(spectral_stars, reference_stars, wcs)
    assert matched == len(reference_stars)
    assert spectral_stars[0].id == "Bright"
    assert [star.id for star in spectral_stars[1:]] == [star.id for star in reference_stars[1:]]


def test_the_solution_gives_each_star_one_detection_and_each_detection_one_name():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A trail point beside a star does not get the star's name too."""
    wcs, reference_stars, spectral_stars = _solution_field()
    trail_point = _spectral_star("Star_trail", spectral_stars[3].star_data["xcentroid"] + 5.0, 1500.0)
    trail_point.star_data["ycentroid"] = spectral_stars[3].star_data["ycentroid"] + 4.0
    spectral_stars.append(trail_point)
    identify_spectral_stars_via_solution(spectral_stars, reference_stars, wcs)
    names = [star.id for star in spectral_stars]
    assert names.count("HD3") == 1
    assert names[3] == "HD3"
    assert trail_point.id == "Star_trail"


def test_the_solution_declines_when_the_fields_do_not_share_one_shift():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Unrelated detections give None so the caller can fall back."""
    wcs, reference_stars, _ = _solution_field()
    rng = np.random.default_rng(3)
    unrelated = [_spectral_star(f"Star_{i}", *rng.uniform(0, 3000, 2)) for i in range(15)]
    assert identify_spectral_stars_via_solution(unrelated, reference_stars, wcs) is None
    assert all(star.id.startswith("Star_") for star in unrelated)


def test_a_detection_farther_than_the_limit_from_its_shifted_position_stays_unnamed():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A detection 40 px from where the star should be is not that star."""
    wcs, reference_stars, spectral_stars = _solution_field()
    spectral_stars[5].star_data["xcentroid"] += 40.0
    identify_spectral_stars_via_solution(spectral_stars, reference_stars, wcs)
    assert spectral_stars[5].id == "Star_5"


def test_a_brighter_catalog_entry_wins_over_a_companion_a_few_pixels_away():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Regression for Navi: the companion entry sat nearer by 2 px and won."""
    wcs, reference_stars, spectral_stars = _solution_field()
    primary = reference_stars[0]
    primary.magnitude = 2.4
    companion = _reference_star("Companion", 0.0, 0.0)
    companion.magnitude = None
    companion.right_ascension = primary.right_ascension + 0.0016
    companion.declination = primary.declination
    reference_stars.append(companion)
    identify_spectral_stars_via_solution(spectral_stars, reference_stars, wcs)
    assert spectral_stars[0].id == "Bright"


def _add_companion(reference_stars, separation_px):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Add a fainter catalog star a given number of pixels from the bright one.

    Parameters
    ----------
    reference_stars : `list` [`StellarObject`]
        The catalog stars; the first is the bright one. The companion is
        added to the list.
    separation_px : `float`
        How far the companion sits from the bright star, in pixels (the
        solution's scale is 5e-4 degrees per pixel).

    Returns
    -------
    companion : `StellarObject`
        The new star.
    """
    bright = reference_stars[0]
    bright.magnitude = 3.1
    companion = _reference_star("Companion", 0.0, 0.0)
    companion.magnitude = 5.1
    # A step in right ascension is shorter on the sky by the cosine of the
    # declination, so it is divided out to get the separation in pixels.
    cosine_declination = np.cos(np.radians(bright.declination))
    companion.right_ascension = bright.right_ascension + separation_px * 5e-4 / cosine_declination
    companion.declination = bright.declination
    reference_stars.append(companion)
    return companion


def test_two_resolved_stars_sharing_one_detection_are_both_named_and_placed():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Regression for Albireo: A and B, 17 px apart, were one detection.

    The blob took the nearer star's name and the other star was left out. Both
    must now be named, each at its own projected position, with the second
    added as a new detection.
    """
    wcs, reference_stars, spectral_stars = _solution_field()
    _add_companion(reference_stars, 17.0)
    before = len(spectral_stars)

    identify_spectral_stars_via_solution(spectral_stars, reference_stars, wcs)

    names = [star.id for star in spectral_stars]
    assert len(spectral_stars) == before + 1
    assert names.count("Bright") == 1
    assert names.count("Companion") == 1
    bright = next(star for star in spectral_stars if star.id == "Bright")
    companion = next(star for star in spectral_stars if star.id == "Companion")
    separation = np.hypot(
        bright.star_data["xcentroid"] - companion.star_data["xcentroid"],
        bright.star_data["ycentroid"] - companion.star_data["ycentroid"],
    )
    assert separation == pytest.approx(17.0, abs=0.5)


def test_a_companion_closer_than_a_zero_order_stays_one_star():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Navi's primary and companion, 4 px apart, are not split into two."""
    wcs, reference_stars, spectral_stars = _solution_field()
    _add_companion(reference_stars, 4.0)
    before = len(spectral_stars)

    identify_spectral_stars_via_solution(spectral_stars, reference_stars, wcs)

    assert len(spectral_stars) == before
    assert [star.id for star in spectral_stars].count("Companion") == 0
