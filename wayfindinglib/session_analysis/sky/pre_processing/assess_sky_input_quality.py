"""Purpose: Judge whether the data can answer the sky-position question.

Description: A comparison between parts of the sky needs measurements from
several nights in each part. One night's seeing can make a whole part of the
sky look poor, and the nights are the independent samples, not the frames
within them. So this stage counts nights, not just frames, and lists the parts
of the sky that too few nights reached. Those parts are not judged later, and
the gaps themselves are worth reporting: a region with no data is a region the
telescope has not been tried in.
"""

from collections import defaultdict
from collections.abc import Sequence

from wayfindinglib.analytics.performance_envelope import MINIMUM_BASELINE_SESSIONS
from wayfindinglib.models.session.sky_quality import SkyCoverageBin, SkyInputQuality, SkySample
from wayfindinglib.session_analysis.sky.sky_bins import bins_of, reachable_bins

MINIMUM_NIGHTS_PER_BIN = 3
"""Fewest nights a part of the sky needs before it is judged.

The nights are the independent samples: frames within a night share its
seeing and its focus. With two nights, one unusual night decides the result.
Three is the smallest number for which the middle value is not one of the two
extremes.
"""

_COVERAGE_METRICS = ("star_width", "guiding_error")
"""Metrics counted for coverage. Star roundness comes from the same frames as
star width, so counting it too would count each frame twice."""


def assess_sky_input_quality(
    samples: Sequence[SkySample],
    samples_without_position: int,
    guiding_nights_excluded: int,
    minimum_altitude_degrees: float,
    maximum_altitude_degrees: float,
) -> SkyInputQuality:
    """Assess how much of the sky the recorded nights cover.

    Parameters
    ----------
    samples : `Sequence` [`SkySample`]
        Every measurement with a known position.
    samples_without_position : `int`
        Measurements left out because they have no position.
    guiding_nights_excluded : `int`
        Nights whose guiding runs were left out as unreliable.
    minimum_altitude_degrees : `float`
        The lowest altitude the telescope is configured to observe at.
    maximum_altitude_degrees : `float`
        The highest.

    Returns
    -------
    input_quality : `SkyInputQuality`
        Coverage, gaps and whether there is enough data to analyse at all.
    """
    nights = sorted({sample.night for sample in samples})
    by_metric: dict[str, int] = defaultdict(int)
    for sample in samples:
        by_metric[sample.metric] += 1
    altitudes = [sample.altitude_degrees for sample in samples if sample.altitude_degrees is not None]

    sample_counts: dict[tuple[str, str], int] = defaultdict(int)
    night_sets: dict[tuple[str, str], set[str]] = defaultdict(set)
    for sample in samples:
        if sample.metric not in _COVERAGE_METRICS:
            continue
        for bin_key in bins_of(sample.altitude_degrees, sample.azimuth_degrees, sample.pier_side):
            sample_counts[bin_key] += 1
            night_sets[bin_key].add(sample.night)

    coverage = [
        SkyCoverageBin(
            dimension=dimension,
            label=label,
            samples=sample_counts.get((dimension, label), 0),
            nights=len(night_sets.get((dimension, label), ())),
        )
        for dimension, label in reachable_bins(minimum_altitude_degrees, maximum_altitude_degrees)
    ]
    return SkyInputQuality(
        nights=len(nights),
        first_night=nights[0] if nights else None,
        last_night=nights[-1] if nights else None,
        samples_by_metric=dict(by_metric),
        samples_without_position=samples_without_position,
        guiding_nights_excluded=guiding_nights_excluded,
        altitude_range_degrees=[min(altitudes), max(altitudes)] if altitudes else [],
        coverage=coverage,
        gaps=[f"{bin_.dimension} {bin_.label}" for bin_ in coverage if bin_.nights < MINIMUM_NIGHTS_PER_BIN],
        minimum_nights_per_bin=MINIMUM_NIGHTS_PER_BIN,
        configured_minimum_altitude_degrees=minimum_altitude_degrees,
        has_enough_data=len(nights) >= MINIMUM_BASELINE_SESSIONS,
    )
