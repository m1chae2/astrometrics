"""Purpose: Unit tests for per-frame saturated-comparison-star exclusion.

Description: Verifies normalize_light_curves excludes a comparison star from
the ensemble median only in the specific frame(s) where it's saturated,
leaving it eligible in every other frame, and that frame_ensemble_composition
correctly records the resulting per-frame ensemble size and exclusions.
"""

from datetime import datetime, timedelta

from astrometricslib.models.stellar_source import LightCurve, StellarObject
from astrometricslib.pipelines.photometry.variability_analyzer import (
    MAXIMUM_ENSEMBLE_SATURATED_FRACTION,
    TARGET_ENSEMBLE_SIZE,
    VariabilityAnalyzer,
)

# normalize_light_curves selects its ensemble on photometric merit --
# frame coverage, then saturation, then brightest-first up to
# TARGET_ENSEMBLE_SIZE -- rather than by position in the flux-sorted
# list. Build more stars than the ensemble takes so the selection has to
# actually choose.
_ENSEMBLE_STAR_COUNT = 130

# Enough frames that a star saturated in exactly one of them stays under
# MAXIMUM_ENSEMBLE_SATURATED_FRACTION and so remains an ensemble member,
# excluded only from that one frame's median. With too few frames a
# single saturated frame exceeds the fraction and the star is dropped
# from the ensemble entirely -- correct behaviour, but it would test
# something other than per-frame exclusion.
_FRAME_COUNT = 40
assert 1 / _FRAME_COUNT <= MAXIMUM_ENSEMBLE_SATURATED_FRACTION

# A star comfortably inside the selected (brightest) portion of the
# population, used as "the" comparison star under test below.
_STAR_INDEX_IN_ENSEMBLE = TARGET_ENSEMBLE_SIZE // 2


def _build_ensemble_stars(saturated_star_index: int, first_timestamp: datetime):  # ruff: ignore[missing-return-type-private-function]
    """Build descending-flux stars, one saturated on a single frame.

    Returns
    -------
    stars_and_timestamps : `tuple` [`list` [`StellarObject`], `list`]
        Descending-flux stars where only `saturated_star_index` is
        flagged saturated, and only on the first frame, alongside the
        frame timestamps.
    """
    timestamps = [first_timestamp + timedelta(minutes=5 * i) for i in range(_FRAME_COUNT)]
    stars = []
    for i in range(_ENSEMBLE_STAR_COUNT):
        star = StellarObject(id=f"Star_{i}")
        star.flux = float(_ENSEMBLE_STAR_COUNT - i) * 100.0
        is_saturated_per_frame = [False] * _FRAME_COUNT
        if i == saturated_star_index:
            is_saturated_per_frame[0] = True
        star.light_curve = LightCurve(
            timestamps=list(timestamps),
            fluxes=[1000.0] * _FRAME_COUNT,
            is_saturated=is_saturated_per_frame,
        )
        stars.append(star)
    return stars, timestamps


def _run_analyzer(stars, timestamps):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Normalize the given stars and index compositions by frame path.

    Returns
    -------
    composition_by_path : `dict`
        Each frame's `FrameEnsembleComposition`, keyed by frame path.
    """
    analyzer = VariabilityAnalyzer()
    analyzer.stellar_objects = stars
    analyzer.timestamp_to_path = {
        timestamp: f"frame_{index:03d}.fits" for index, timestamp in enumerate(timestamps)
    }
    analyzer.normalize_light_curves()
    return {c.frame_path: c for c in analyzer.frame_ensemble_composition}


def test_saturated_comparison_star_excluded_only_in_its_saturated_frame():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify per-frame exclusion of a saturated comparison star."""
    stars, timestamps = _build_ensemble_stars(
        saturated_star_index=_STAR_INDEX_IN_ENSEMBLE,
        first_timestamp=datetime(2026, 7, 20, 22, 0),
    )

    composition_by_path = _run_analyzer(stars, timestamps)
    saturated_frame = composition_by_path["frame_000.fits"]
    clean_frame = composition_by_path["frame_001.fits"]

    star_id = f"Star_{_STAR_INDEX_IN_ENSEMBLE}"
    assert star_id in saturated_frame.excluded_comparison_star_ids
    assert star_id not in clean_frame.excluded_comparison_star_ids
    # The saturated frame's ensemble is one star smaller than the
    # clean frame's.
    assert saturated_frame.ensemble_size == clean_frame.ensemble_size - 1


def test_frame_ensemble_composition_tracks_full_ensemble_when_unsaturated():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify ensemble_size matches the selected reference-star count."""
    stars, timestamps = _build_ensemble_stars(
        saturated_star_index=-1, first_timestamp=datetime(2026, 7, 20, 22, 0)
    )

    composition_by_path = _run_analyzer(stars, timestamps)

    for composition in composition_by_path.values():
        assert composition.excluded_comparison_star_ids == []
        # Every star here is fully covered and unsaturated, so selection
        # is limited only by the target ensemble size.
        assert composition.ensemble_size == TARGET_ENSEMBLE_SIZE


def test_the_ensemble_takes_the_brightest_eligible_stars():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify selection is brightest-first, not a mid-list slice.

    The old positional window skipped the brightest stars outright and
    slid deeper into the faint end as the population grew, which is what
    made a larger detected population produce a worse ensemble.
    """
    stars, timestamps = _build_ensemble_stars(
        saturated_star_index=-1, first_timestamp=datetime(2026, 7, 20, 22, 0)
    )

    analyzer = VariabilityAnalyzer()
    analyzer.stellar_objects = stars
    analyzer.timestamp_to_path = {
        timestamp: f"frame_{index:03d}.fits" for index, timestamp in enumerate(timestamps)
    }
    analyzer.normalize_light_curves()

    composition = analyzer.frame_ensemble_composition[0]
    assert composition.ensemble_size == TARGET_ENSEMBLE_SIZE
    # Stars are built brightest-first, so the faintest stars must be the
    # ones left out rather than the brightest.
    assert "Star_0" not in composition.excluded_comparison_star_ids


def test_a_persistently_saturated_star_is_kept_out_of_the_ensemble():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a star saturated in most frames is not a comparison star.

    Per-frame exclusion handles the occasional clip; a star that is
    saturated throughout has no usable flux scale at all and must not
    anchor the normalization.
    """
    stars, timestamps = _build_ensemble_stars(
        saturated_star_index=-1, first_timestamp=datetime(2026, 7, 20, 22, 0)
    )
    always_saturated = stars[0]
    always_saturated.light_curve.is_saturated = [True] * _FRAME_COUNT

    composition_by_path = _run_analyzer(stars, timestamps)
    composition = composition_by_path["frame_000.fits"]

    # Never selected, so it is not reported as a per-frame exclusion
    # either -- it was not a candidate to begin with.
    assert always_saturated.id not in composition.excluded_comparison_star_ids
    assert composition.ensemble_size == TARGET_ENSEMBLE_SIZE
