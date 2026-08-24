"""Tests for keeping a target's comparison ensemble stable across frames.

The ensemble was selected once by flux rank, but a star contributes to
a frame's median only where it has a positive, unsaturated flux. Faint
sources on structured background frequently net zero, so the median was
taken over a different set of stars in every frame and the zero point
moved with the membership rather than with the sky.

Measured over the 7 targets carrying a scatter metric on 2026-08-24,
the fraction of a target's frames holding a full ensemble predicted its
light-curve scatter at r = -0.977: 0.059 mag where every frame kept 50
or more comparison stars, against 0.50 mag where most kept fewer than
10.
"""

from astrometricslib.tasks.stellar_tasks.photometry_tasks.variability_analyzer import (
    MINIMUM_ENSEMBLE_COMPLETENESS,
    MINIMUM_FRAME_ENSEMBLE_SIZE,
    VariabilityAnalyzer,
)


class _LightCurve:
    """A light curve stand-in over a fixed timestamp grid."""

    def __init__(self, timestamps, fluxes, is_saturated=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.timestamps = timestamps
        self.fluxes = fluxes
        self.is_saturated = is_saturated if is_saturated is not None else [False] * len(fluxes)
        self.fluxes_normalized = []


class _Star:
    """A stellar object stand-in carrying a light curve and a flux."""

    def __init__(self, star_id, fluxes, timestamps, is_saturated=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.id = star_id
        self.flux = max(fluxes) if fluxes else 0
        self.light_curve = _LightCurve(timestamps, fluxes, is_saturated)


def _analyzer_with(stars) -> VariabilityAnalyzer:  # ruff: ignore[missing-type-function-argument]
    """Return an analyzer preloaded with `stars`.

    Returns
    -------
    analyzer : `VariabilityAnalyzer`
        An analyzer whose stellar objects are `stars`.
    """
    analyzer = VariabilityAnalyzer()
    analyzer.stellar_objects = stars
    return analyzer


def _complete_population(star_count=60, frame_count=20) -> list:  # ruff: ignore[missing-type-function-argument]
    """Return stars measurable in every frame.

    Returns
    -------
    stars : `list` [`_Star`]
        Stars with a positive, unsaturated flux in every frame.
    """
    timestamps = list(range(frame_count))
    return [
        _Star(f"complete-{index}", [1000.0 + index] * frame_count, timestamps) for index in range(star_count)
    ]


def test_a_star_missing_from_most_frames_is_not_a_comparison_star():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The regression: intermittent stars shift the median every frame."""
    timestamps = list(range(20))
    stars = _complete_population()
    # Mid-brightness deliberately: the old flux-rank window skipped only
    # the brightest, so it selected this star, and every frame the star
    # was absent from took its median over a different population.
    stars.append(_Star("intermittent", [1030.0, 1030.0] + [0.0] * 18, timestamps))

    reference_ids, _ = _analyzer_with(stars)._select_comparison_star_ids()

    assert "intermittent" not in reference_ids


def test_stars_measurable_throughout_are_kept():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The ensemble must still be built from the stable population."""
    stars = _complete_population()

    reference_ids, completeness = _analyzer_with(stars)._select_comparison_star_ids()

    assert len(reference_ids) >= MINIMUM_FRAME_ENSEMBLE_SIZE
    assert completeness == MINIMUM_ENSEMBLE_COMPLETENESS


def test_a_saturated_measurement_does_not_count_toward_completeness():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Saturated frames are dropped from the median, so they are absent."""
    timestamps = list(range(20))
    stars = _complete_population()
    stars.append(_Star("saturated", [90000.0] * 20, timestamps, is_saturated=[True] * 18 + [False, False]))

    reference_ids, _ = _analyzer_with(stars)._select_comparison_star_ids()

    assert "saturated" not in reference_ids


def test_a_sparse_field_relaxes_rather_than_selecting_nothing():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Nebula fields would otherwise lose normalization entirely.

    NGC 1499 and NGC 2244 hold 63% of their frames below 10 comparison
    stars, so an unrelaxed 80% requirement selects nothing there.
    """
    timestamps = list(range(20))
    # Every star is measurable in only half the frames.
    stars = [_Star(f"half-{index}", [1000.0] * 10 + [0.0] * 10, timestamps) for index in range(40)]

    reference_ids, completeness = _analyzer_with(stars)._select_comparison_star_ids()

    assert len(reference_ids) >= MINIMUM_FRAME_ENSEMBLE_SIZE
    assert completeness < MINIMUM_ENSEMBLE_COMPLETENESS


def test_the_brightest_stars_are_still_skipped():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The brightest sources remain the most saturation-prone."""
    timestamps = list(range(20))
    stars = _complete_population(star_count=100)
    brightest = _Star("brightest", [99000.0] * 20, timestamps)
    stars.insert(0, brightest)

    reference_ids, _ = _analyzer_with(stars)._select_comparison_star_ids()

    assert "brightest" not in reference_ids


def test_skipping_the_brightest_never_empties_a_small_ensemble():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """On a small field the skip must not cost more than it protects."""
    stars = _complete_population(star_count=MINIMUM_FRAME_ENSEMBLE_SIZE)

    reference_ids, _ = _analyzer_with(stars)._select_comparison_star_ids()

    assert len(reference_ids) == MINIMUM_FRAME_ENSEMBLE_SIZE


def test_a_frame_normalized_by_too_few_stars_is_rejected():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A one-star ensemble forces that star's curve flat by construction.

    C 2022 E3 ZTF reported 0.031 mag that way -- the catalog's lowest
    scatter, produced by 89% of frames normalizing against fewer than
    10 stars.
    """
    timestamps = list(range(3))
    # All but three stars drop out of the final frame, leaving a median
    # over three fluxes. A frame where *no* star is measurable never
    # reaches the median at all; the damaging case is the tiny ensemble
    # that still looks like one.
    stars = [_Star(f"s-{index}", [1000.0, 1000.0, 0.0], timestamps) for index in range(40)]
    # Not the first few, which the brightest-skip would drop anyway.
    for star in stars[10:13]:
        star.light_curve.fluxes = [1000.0, 1000.0, 1000.0]
    analyzer = _analyzer_with(stars)
    analyzer.timestamp_to_path = {t: f"/frame-{t}.fits" for t in timestamps}

    analyzer.normalize_light_curves()

    assert analyzer.frames_rejected_for_small_ensemble >= 1
    assert "/frame-2.fits" in analyzer.rejected_files


def test_a_rejected_frame_leaves_no_normalized_flux_behind():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A starved frame must not reach the variability search."""
    timestamps = list(range(3))
    stars = [_Star(f"s-{index}", [1000.0, 1000.0, 500.0], timestamps) for index in range(40)]
    for star in stars[5:]:
        star.light_curve.fluxes = [1000.0, 1000.0, 0.0]
    analyzer = _analyzer_with(stars)
    analyzer.timestamp_to_path = {t: f"/frame-{t}.fits" for t in timestamps}

    analyzer.normalize_light_curves()

    assert all(len(star.light_curve.fluxes_normalized) <= 2 for star in stars)


def test_a_healthy_target_keeps_every_frame():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The floor must not cost frames on a well-populated field.

    NGC 6992 held 50+ comparison stars in every frame and scattered at
    0.059 mag; it must come through untouched.
    """
    stars = _complete_population(star_count=60, frame_count=20)
    analyzer = _analyzer_with(stars)
    analyzer.timestamp_to_path = {t: f"/frame-{t}.fits" for t in range(20)}

    analyzer.normalize_light_curves()

    assert analyzer.frames_rejected_for_small_ensemble == 0
    assert len(analyzer.frame_reference_flux) == 20


def test_no_stars_at_all_is_not_an_error():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """An empty target must not raise."""
    analyzer = _analyzer_with([])

    analyzer.normalize_light_curves()

    assert analyzer.frames_rejected_for_small_ensemble == 0
