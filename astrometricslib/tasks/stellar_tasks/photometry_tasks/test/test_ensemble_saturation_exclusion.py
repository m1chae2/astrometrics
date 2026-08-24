"""Purpose: Unit tests for per-frame saturated-comparison-star exclusion.

Description: Verifies normalize_light_curves excludes a comparison star from
the ensemble median only in the specific frame(s) where it's saturated,
leaving it eligible in every other frame, and that frame_ensemble_composition
correctly records the resulting per-frame ensemble size and exclusions.
"""

from datetime import datetime, timedelta

from astrometricslib.models.stellar_source import LightCurve, StellarObject
from astrometricslib.tasks.stellar_tasks.photometry_tasks.variability_analyzer import VariabilityAnalyzer

# normalize_light_curves selects comparison stars by completeness first
# and brightness second: every star measurable in at least
# MINIMUM_ENSEMBLE_COMPLETENESS of frames, minus the brightest ~10% as
# the most saturation-prone. Mirror the same formula here instead of
# hardcoding indices, so this test doesn't silently drift out of sync.
_ENSEMBLE_STAR_COUNT = 105
_ENSEMBLE_SKIP_COUNT = min(_ENSEMBLE_STAR_COUNT // 10, 100)
_ENSEMBLE_WINDOW_SIZE = min(_ENSEMBLE_STAR_COUNT - _ENSEMBLE_SKIP_COUNT, 300)
# A star safely inside that window, used as "the" comparison star under
# test below (any index past _ENSEMBLE_SKIP_COUNT would do).
_STAR_INDEX_IN_ENSEMBLE = _ENSEMBLE_SKIP_COUNT + (_ENSEMBLE_WINDOW_SIZE // 2)

# Enough frames that one saturated frame is a small fraction of a
# star's measurements. With two frames, "saturated in one frame" is 50%
# completeness, which the ensemble now rejects outright -- and the
# per-frame exclusion this file exists to test would never be reached.
_FRAME_COUNT = 20


def _build_ensemble_stars(saturated_star_index: int, saturated_timestamp: datetime):  # ruff: ignore[missing-return-type-private-function]
    """Build descending-flux stars, one saturated on a single frame.

    Returns
    -------
    list[StellarObject]
        Descending-flux stars where only `saturated_star_index` is
        flagged saturated, and only on the first of two frames.
    """
    timestamps = [saturated_timestamp + timedelta(minutes=5 * n) for n in range(_FRAME_COUNT)]
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


def test_saturated_comparison_star_excluded_only_in_its_saturated_frame():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify per-frame exclusion of a saturated comparison star."""
    saturated_timestamp = datetime(2026, 7, 20, 22, 0)
    stars, timestamps = _build_ensemble_stars(
        saturated_star_index=_STAR_INDEX_IN_ENSEMBLE, saturated_timestamp=saturated_timestamp
    )

    analyzer = VariabilityAnalyzer()
    analyzer.stellar_objects = stars
    analyzer.timestamp_to_path = {timestamps[0]: "frame_saturated.fits"} | {
        timestamp: f"frame_clean_{n}.fits" for n, timestamp in enumerate(timestamps[1:])
    }
    analyzer.normalize_light_curves()

    composition_by_path = {c.frame_path: c for c in analyzer.frame_ensemble_composition}

    saturated_frame = composition_by_path["frame_saturated.fits"]
    clean_frame = composition_by_path["frame_clean_0.fits"]

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
    analyzer.timestamp_to_path = {timestamp: f"frame_{n}.fits" for n, timestamp in enumerate(timestamps)}
    analyzer.normalize_light_curves()

    for composition in analyzer.frame_ensemble_composition:
        assert composition.excluded_comparison_star_ids == []
        assert composition.ensemble_size == _ENSEMBLE_WINDOW_SIZE
