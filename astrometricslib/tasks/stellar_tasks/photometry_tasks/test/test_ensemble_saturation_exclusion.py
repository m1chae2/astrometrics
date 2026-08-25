"""Purpose: Unit tests for per-frame saturated-comparison-star exclusion.

Description: Verifies normalize_light_curves excludes a comparison star from
the ensemble median only in the specific frame(s) where it's saturated,
leaving it eligible in every other frame, and that frame_ensemble_composition
correctly records the resulting per-frame ensemble size and exclusions.
"""

from datetime import datetime, timedelta

from astrometricslib.models.stellar_source import LightCurve, StellarObject
from astrometricslib.tasks.stellar_tasks.photometry_tasks.variability_analyzer import VariabilityAnalyzer

# normalize_light_curves' reference ensemble window is a percentile slice
# scaled to the detected population (skip brightest ~10%, take next
# ~60%), not a fixed [100:300] -- see that function's comments. Mirror
# the same formula here instead of hardcoding indices, so this test
# doesn't silently drift out of sync if the window's percentiles ever
# change.
_ENSEMBLE_STAR_COUNT = 105
_ENSEMBLE_START_INDEX = min(100, _ENSEMBLE_STAR_COUNT // 10)
_ENSEMBLE_END_INDEX = min(300, _ENSEMBLE_START_INDEX + max(1, int(_ENSEMBLE_STAR_COUNT * 0.6)))
_ENSEMBLE_WINDOW_SIZE = _ENSEMBLE_END_INDEX - _ENSEMBLE_START_INDEX
# A star safely inside that window, used as "the" comparison star under
# test below (any index in [_ENSEMBLE_START_INDEX, _ENSEMBLE_END_INDEX)
# would do).
_STAR_INDEX_IN_ENSEMBLE = _ENSEMBLE_START_INDEX + (_ENSEMBLE_WINDOW_SIZE // 2)


def _build_ensemble_stars(saturated_star_index: int, saturated_timestamp: datetime):  # ruff: ignore[missing-return-type-private-function]
    """Build descending-flux stars, one saturated on a single frame.

    Returns
    -------
    list[StellarObject]
        Descending-flux stars where only `saturated_star_index` is
        flagged saturated, and only on the first of two frames.
    """
    timestamps = [saturated_timestamp, saturated_timestamp + timedelta(minutes=5)]
    stars = []
    for i in range(_ENSEMBLE_STAR_COUNT):
        star = StellarObject(id=f"Star_{i}")
        star.flux = float(_ENSEMBLE_STAR_COUNT - i) * 100.0
        is_saturated_per_frame = [False, False]
        if i == saturated_star_index:
            is_saturated_per_frame[0] = True
        star.light_curve = LightCurve(
            timestamps=list(timestamps),
            fluxes=[1000.0, 1000.0],
            is_saturated=is_saturated_per_frame,
        )
        stars.append(star)
    return stars, timestamps


def test_saturated_comparison_star_excluded_only_in_its_saturated_frame():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify per-frame exclusion of a saturated comparison star."""
    saturated_timestamp = datetime(2026, 7, 20, 22, 0)
    stars, timestamps = _build_ensemble_stars(
        saturated_star_index=_STAR_INDEX_IN_ENSEMBLE, saturated_timestamp=saturated_timestamp
    )

    analyzer = VariabilityAnalyzer()
    analyzer.stellar_objects = stars
    analyzer.timestamp_to_path = {
        timestamps[0]: "frame_saturated.fits",
        timestamps[1]: "frame_clean.fits",
    }
    analyzer.normalize_light_curves()

    composition_by_path = {c.frame_path: c for c in analyzer.frame_ensemble_composition}

    saturated_frame = composition_by_path["frame_saturated.fits"]
    clean_frame = composition_by_path["frame_clean.fits"]

    star_id = f"Star_{_STAR_INDEX_IN_ENSEMBLE}"
    assert star_id in saturated_frame.excluded_comparison_star_ids
    assert star_id not in clean_frame.excluded_comparison_star_ids
    # The saturated frame's ensemble is one star smaller than the
    # clean frame's.
    assert saturated_frame.ensemble_size == clean_frame.ensemble_size - 1


def test_frame_ensemble_composition_tracks_full_ensemble_when_unsaturated():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify ensemble_size matches full reference-star count, unsaturated."""
    saturated_timestamp = datetime(2026, 7, 20, 22, 0)
    stars, timestamps = _build_ensemble_stars(
        saturated_star_index=-1, saturated_timestamp=saturated_timestamp
    )

    analyzer = VariabilityAnalyzer()
    analyzer.stellar_objects = stars
    analyzer.timestamp_to_path = {
        timestamps[0]: "frame_a.fits",
        timestamps[1]: "frame_b.fits",
    }
    analyzer.normalize_light_curves()

    for composition in analyzer.frame_ensemble_composition:
        assert composition.excluded_comparison_star_ids == []
        assert composition.ensemble_size == _ENSEMBLE_WINDOW_SIZE
