"""Purpose: Compare each part of the sky with the equipment's typical night.

Description: Seeing, focus and tracking change from night to night much more
than they change between parts of the sky. A comparison of raw values would
mostly compare good nights with bad ones. So each measurement is first divided
by the typical value of the same metric on the same night, which removes the
night and leaves how a part of the sky compares with the rest of that night.

Each part of the sky (an altitude band, an azimuth sector, a pier side) then
gets one value per night, the median of that night's relative measurements
there. Those night values are the independent samples. A night counts toward a
dimension only if it observed at least two parts of the sky along it: a night
that stayed in one altitude band has a relative value of 1 there by
construction and cannot say whether the band is worse than another.

A part is judged only when at least `MINIMUM_NIGHTS_PER_BIN` nights have a
value, and it is poor only when two things are both true:

1. It is worse than the rest of the night by at least the blur tolerance, the
   same policy number the performance envelope uses (a 10 percent change is
   one the equipment's own budget treats as worth acting on).
2. The difference is at least 3 uncertainties large (`z_score`), so a few
   lucky nights cannot produce it. The uncertainty of a median is 1.2533 x the
   robust scatter of the night values divided by the square root of their
   number.

Altitude and time of night are linked within one night, because targets sink
as the night goes on. A poor low band can therefore be seeing, or a focus that
drifted since the last autofocus. The recommendation says so.
"""

import math
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from wayfindinglib.analytics.performance_envelope import (
    BASELINE_SPREAD_MULTIPLIER,
    MAD_TO_SIGMA,
    MINIMUM_FRAMES_PER_NIGHT,
)
from wayfindinglib.models.session.sky_quality import (
    SkyBinResult,
    SkyMetricResult,
    SkyPerformance,
    SkySample,
)
from wayfindinglib.session_analysis.sky.pre_processing.assess_sky_input_quality import MINIMUM_NIGHTS_PER_BIN
from wayfindinglib.session_analysis.sky.sky_bins import altitude_band_limits, bins_of, reachable_bins

_MEDIAN_ERROR_FACTOR = math.sqrt(math.pi / 2.0)
"""The standard error of a median is this factor (1.2533) times that of the
mean, for normally distributed data."""

_MINIMUM_SPREAD = 1e-9
"""A scatter below this is zero; a z score would divide by nothing."""


@dataclass(frozen=True)
class MetricSpec:
    """How one metric is compared.

    Attributes
    ----------
    higher_is_worse : `bool`
        Whether a larger value is worse (star width, guiding error) or a
        smaller one is (star roundness).
    minimum_samples_per_night : `int`
        Fewest measurements a night needs for its typical value to be known.
    minimum_samples_per_cell : `int`
        Fewest measurements a night needs in one part of the sky for that
        part to give a value for the night.
    """

    higher_is_worse: bool
    minimum_samples_per_night: int
    minimum_samples_per_cell: int


METRIC_SPECS = {
    "star_width": MetricSpec(True, MINIMUM_FRAMES_PER_NIGHT, 5),
    "star_roundness": MetricSpec(False, MINIMUM_FRAMES_PER_NIGHT, 5),
    "guiding_error": MetricSpec(True, 3, 1),
}
"""The metrics compared. A night's frames number in the tens, so its typical
value needs 10 and a part of the sky needs 5 (design estimates). A night has
only a few guiding runs, each a separate calibration and so independent, so a
night needs 3 and a part needs 1."""


def _relative_values(samples: Sequence[SkySample], spec: MetricSpec) -> list[tuple[SkySample, float]]:
    """Divide each measurement by its night's typical value.

    Returns
    -------
    relative : `list` [`tuple` [`SkySample`, `float`]]
        Each sample with its relative value. Nights with too few measurements
        are left out, as is a night whose typical value is not positive.
    """
    by_night: dict[str, list[SkySample]] = defaultdict(list)
    for sample in samples:
        by_night[sample.night].append(sample)
    relative = []
    for night_samples in by_night.values():
        if len(night_samples) < spec.minimum_samples_per_night:
            continue
        typical = statistics.median(sample.value for sample in night_samples)
        if typical <= 0:
            continue
        relative.extend((sample, sample.value / typical) for sample in night_samples)
    return relative


def _compare_metric(
    metric: str,
    samples: Sequence[SkySample],
    bins: Sequence[tuple[str, str]],
    blur_tolerance_fraction: float,
) -> SkyMetricResult | None:
    """Compare every part of the sky for one metric.

    Returns
    -------
    result : `SkyMetricResult` or `None`
        The comparison, or `None` if no night has enough measurements.
    """
    spec = METRIC_SPECS[metric]
    relative = _relative_values(samples, spec)
    if not relative:
        return None

    cells: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for sample, value in relative:
        for bin_key in bins_of(sample.altitude_degrees, sample.azimuth_degrees, sample.pier_side):
            cells[bin_key][sample.night].append(value)

    # A night gives one value per part of the sky it observed enough in. A
    # night that observed only one part along a dimension has a relative value
    # of 1 there by construction, so it says nothing about that dimension.
    night_values: dict[tuple[str, str], list[float]] = defaultdict(list)
    for dimension in {dimension for dimension, _ in bins}:
        per_night: dict[str, dict[str, float]] = defaultdict(dict)
        for (cell_dimension, label), nights in cells.items():
            if cell_dimension != dimension:
                continue
            for night, values in nights.items():
                if len(values) >= spec.minimum_samples_per_cell:
                    per_night[night][label] = statistics.median(values)
        for by_label in per_night.values():
            if len(by_label) < 2:
                continue
            for label, value in by_label.items():
                night_values[dimension, label].append(value)

    # The scatter is that of the night values around their own part's median,
    # so a real difference between parts does not inflate it.
    spreads: dict[str, float | None] = {}
    for dimension in {dimension for dimension, _ in bins}:
        residuals = [
            value - statistics.median(values)
            for (d, _), values in night_values.items()
            if d == dimension
            for value in values
        ]
        if len(residuals) >= MINIMUM_NIGHTS_PER_BIN:
            spreads[dimension] = MAD_TO_SIGMA * statistics.median(abs(value) for value in residuals)
        else:
            spreads[dimension] = None

    results = []
    for dimension, label in bins:
        values = night_values.get((dimension, label), [])
        sample_count = sum(len(v) for v in cells.get((dimension, label), {}).values())
        spread = spreads[dimension]
        result = SkyBinResult(
            dimension=dimension, label=label, samples=sample_count, nights=len(values), spread=spread
        )
        if len(values) >= MINIMUM_NIGHTS_PER_BIN and spread is not None:
            median_relative = statistics.median(values)
            worse_by = (median_relative - 1.0) if spec.higher_is_worse else (1.0 - median_relative)
            error = _MEDIAN_ERROR_FACTOR * spread / math.sqrt(len(values))
            result.judged = True
            result.median_relative_value = median_relative
            result.worse_by = worse_by
            result.z_score = worse_by / error if error > _MINIMUM_SPREAD else None
            result.is_poor = (
                worse_by >= blur_tolerance_fraction
                and result.z_score is not None
                and result.z_score >= BASELINE_SPREAD_MULTIPLIER
            )
        results.append(result)
    return SkyMetricResult(
        metric=metric,
        nights=len({sample.night for sample, _ in relative}),
        samples=len(relative),
        bins=results,
    )


def _suggested_minimum_altitude(metrics: Sequence[SkyMetricResult]) -> float | None:
    """Find the altitude below which stars were measurably wider.

    Returns
    -------
    altitude : `float` or `None`
        The top edge of the highest poor altitude band for star width, or
        `None` if no altitude band is poor.
    """
    for result in metrics:
        if result.metric != "star_width":
            continue
        poor_tops = [
            altitude_band_limits(bin_.label)[1]
            for bin_ in result.bins
            if bin_.dimension == "altitude" and bin_.is_poor
        ]
        return max(poor_tops) if poor_tops else None
    return None


def measure_sky_performance(
    samples: Sequence[SkySample],
    minimum_altitude_degrees: float,
    maximum_altitude_degrees: float,
    blur_tolerance_fraction: float,
) -> SkyPerformance:
    """Compare each part of the sky for every metric.

    Parameters
    ----------
    samples : `Sequence` [`SkySample`]
        Every measurement with a known position.
    minimum_altitude_degrees : `float`
        The lowest altitude the telescope is configured to observe at.
    maximum_altitude_degrees : `float`
        The highest.
    blur_tolerance_fraction : `float`
        The fraction by which a part of the sky must be worse than the rest
        of the night to count as poor.

    Returns
    -------
    performance : `SkyPerformance`
        One comparison per metric that has enough data, and the suggested
        minimum altitude.
    """
    bins = reachable_bins(minimum_altitude_degrees, maximum_altitude_degrees)
    by_metric: dict[str, list[SkySample]] = defaultdict(list)
    for sample in samples:
        by_metric[sample.metric].append(sample)
    metrics = []
    for metric in METRIC_SPECS:
        result = _compare_metric(metric, by_metric.get(metric, []), bins, blur_tolerance_fraction)
        if result is not None:
            metrics.append(result)
    return SkyPerformance(
        metrics=metrics, suggested_minimum_altitude_degrees=_suggested_minimum_altitude(metrics)
    )
