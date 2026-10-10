"""Purpose: Unit tests for how saturation shapes the fixed comparison set.

Description: Verifies that `normalize_light_curves` chooses one comparison set
for the whole session. A star that is saturated in even one frame is never a
candidate, so it is left out of the set for every frame and not only for the
frame where it clipped. The tests also check that the per-frame composition
records the same set size in every frame, that the brightest 2 percent of the
candidates are skipped, and that the set size cap can be changed.
"""

from datetime import datetime, timedelta
from typing import Any

from astrometricslib.models.stellar_source import PhotometryResult, StellarObject
from astrometricslib.pipelines.photometry.processing.comparison_ensemble import (
    DEFAULT_MAXIMUM_COMPARISON_STARS,
)
from astrometricslib.pipelines.photometry.processing.variability_analyzer import VariabilityAnalyzer

# More stars than the set can hold, so the selection has to choose.
_STAR_COUNT = 130

_FRAME_COUNT = 40

# The brightest 2 percent of 130 candidates is 2 stars (floor of 2.6).
_SKIPPED_BRIGHTEST_COUNT = 2

# A star well inside the band of candidates, used as the star under test.
_STAR_INDEX_IN_BAND = 10


def _build_stars(saturated_star_index: int, first_timestamp: datetime) -> tuple[list[StellarObject], list]:
    """Build stars of falling brightness, one saturated on a single frame.

    Parameters
    ----------
    saturated_star_index : `int`
        The star flagged saturated in the first frame. A negative value
        flags none.
    first_timestamp : `datetime.datetime`
        The exposure start of the first frame.

    Returns
    -------
    stars_and_timestamps : `tuple` [`list` [`StellarObject`], `list`]
        Stars ``Star_0`` (brightest) to ``Star_129`` (faintest), each
        constant in time, and the frame timestamps.
    """
    timestamps = [first_timestamp + timedelta(minutes=5 * i) for i in range(_FRAME_COUNT)]
    stars = []
    for i in range(_STAR_COUNT):
        star = StellarObject(id=f"Star_{i}")
        star.flux = float(_STAR_COUNT - i) * 100.0
        is_saturated_per_frame = [False] * _FRAME_COUNT
        if i == saturated_star_index:
            is_saturated_per_frame[0] = True
        star.photometry = PhotometryResult(
            timestamps=list(timestamps),
            fluxes=[star.flux] * _FRAME_COUNT,
            is_saturated=is_saturated_per_frame,
        )
        stars.append(star)
    return stars, timestamps


def _run_analyzer(stars: list[StellarObject], timestamps: list, **options: Any) -> VariabilityAnalyzer:
    """Normalize the given stars with a fresh analyzer.

    Returns
    -------
    analyzer : `VariabilityAnalyzer`
        The analyzer after `normalize_light_curves` has run.
    """
    analyzer = VariabilityAnalyzer(**options)
    analyzer.stellar_objects = stars
    analyzer.timestamp_to_path = {
        timestamp: f"frame_{index:03d}.fits" for index, timestamp in enumerate(timestamps)
    }
    analyzer.normalize_light_curves()
    return analyzer


def test_a_star_saturated_in_one_frame_is_left_out_of_the_set_for_every_frame() -> None:
    """Verify one saturated frame removes the star from the whole session."""
    stars, timestamps = _build_stars(_STAR_INDEX_IN_BAND, datetime(2026, 7, 20, 22, 0))

    analyzer = _run_analyzer(stars, timestamps)

    saturated_id = f"Star_{_STAR_INDEX_IN_BAND}"
    assert analyzer.comparison_set is not None
    assert saturated_id not in analyzer.comparison_set.star_ids
    for composition in analyzer.frame_ensemble_composition:
        assert saturated_id not in composition.excluded_comparison_star_ids


def test_every_frame_records_the_same_comparison_set_size() -> None:
    """Verify the per-frame composition shows a set that does not change."""
    stars, timestamps = _build_stars(_STAR_INDEX_IN_BAND, datetime(2026, 7, 20, 22, 0))

    analyzer = _run_analyzer(stars, timestamps)

    sizes = {composition.ensemble_size for composition in analyzer.frame_ensemble_composition}
    assert sizes == {DEFAULT_MAXIMUM_COMPARISON_STARS}
    assert len(analyzer.frame_ensemble_composition) == _FRAME_COUNT
    assert all(
        composition.excluded_comparison_star_ids == [] for composition in analyzer.frame_ensemble_composition
    )
    assert analyzer.comparison_set is not None
    assert analyzer.comparison_set.is_fixed


def test_the_set_skips_the_brightest_two_percent_and_takes_the_next_brightest() -> None:
    """Verify the band starts below the brightest 2 percent of the candidates.

    The brightest stars come closest to saturation and to the camera's
    non-linear range, so they are not used as comparison stars.
    """
    stars, timestamps = _build_stars(-1, datetime(2026, 7, 20, 22, 0))

    analyzer = _run_analyzer(stars, timestamps)

    assert analyzer.comparison_set is not None
    chosen = set(analyzer.comparison_set.star_ids)
    for skipped in range(_SKIPPED_BRIGHTEST_COUNT):
        assert f"Star_{skipped}" not in chosen
    assert f"Star_{_SKIPPED_BRIGHTEST_COUNT}" in chosen


def test_a_persistently_saturated_star_is_kept_out_of_the_set() -> None:
    """Verify a star saturated throughout is never a comparison star.

    A star saturated in every frame has no usable flux scale. It is also
    not counted as a rejected candidate, because it never was one.
    """
    stars, timestamps = _build_stars(-1, datetime(2026, 7, 20, 22, 0))
    always_saturated = stars[_SKIPPED_BRIGHTEST_COUNT]
    always_saturated.photometry.is_saturated = [True] * _FRAME_COUNT

    analyzer = _run_analyzer(stars, timestamps)

    assert analyzer.comparison_set is not None
    assert always_saturated.id not in analyzer.comparison_set.star_ids
    assert always_saturated.id not in analyzer.comparison_set.rejected_ids
    assert len(analyzer.comparison_set.star_ids) == DEFAULT_MAXIMUM_COMPARISON_STARS


def test_the_set_size_cap_can_be_changed() -> None:
    """Verify the analyzer's cap sets the number of comparison stars."""
    stars, timestamps = _build_stars(-1, datetime(2026, 7, 20, 22, 0))

    analyzer = _run_analyzer(stars, timestamps, maximum_comparison_stars=8)

    assert analyzer.comparison_set is not None
    assert len(analyzer.comparison_set.star_ids) == 8
    assert {composition.ensemble_size for composition in analyzer.frame_ensemble_composition} == {8}
