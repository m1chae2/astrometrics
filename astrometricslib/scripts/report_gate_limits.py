r"""Show how often each quality limit fires across the whole library.

Every quality gate with a number in it has a limit. A limit that fires on most
stacks is probably set too tight, and one that never fires may be set too
loose or may never have had anything to catch. This read-only script reads the
quality summaries saved on every target and reports, for each limit, how many
targets have a value, the spread of those values, and the share beyond the
limit. It also shows the spread of numbers that have no limit yet (such as the
astrometric residual), which is what a limit would have to be derived from.

    python -m astrometricslib.scripts.report_gate_limits

A share beyond a limit is not an error rate: a stack that fires a limit may be
truly poor. It says where the limits sit against the data they judge. The
values are the latest saved for each target, one per target per pipeline.
"""

import json
import os
import sqlite3
import statistics
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from astrometricslib import Astrometrics
from astrometricslib.pipelines.shared.quality.saturation import DEFAULT_SATURATION_FLAG_THRESHOLD
from astrometricslib.pipelines.stacking.post_processing.stack_quality import (
    DEFAULT_FWHM_DEGRADATION_RATIO,
    DEFAULT_REJECTED_FRACTION_FLAG_THRESHOLD,
    DEFAULT_ZERO_FRACTION_FLAG_THRESHOLD,
)
from astrometricslib.pipelines.stacking.pre_processing.flat_calibration import MAXIMUM_FLAT_NOISE_FRACTION

Document = dict[str, Any]


def dig(document: Any, *path: str) -> Any:
    """Follow a path of keys into nested dictionaries.

    Parameters
    ----------
    document : `Any`
        A dictionary, or anything else.
    *path : `str`
        The keys to follow.

    Returns
    -------
    value : `Any`
        The value found, or `None` if any step is missing or not a dictionary.
    """
    for key in path:
        if not isinstance(document, dict):
            return None
        document = document.get(key)
    return document


def ratio(numerator: Any, denominator: Any) -> float | None:
    """Divide two stored numbers, tolerating gaps.

    Returns
    -------
    value : `float` or `None`
        The quotient, or `None` if either is missing or the divisor is zero.
    """
    if numerator is None or not denominator:
        return None
    return float(numerator) / float(denominator)


@dataclass(frozen=True)
class Metric:
    """One stored number and the limit that judges it, if any.

    Attributes
    ----------
    pipeline : `str`
        Which pipeline produced it.
    name : `str`
        What it is.
    read : `Callable`
        Reads the number from a target's saved document, or `None`.
    limit : `float` or `None`
        The limit a gate applies, or `None` for a number with no limit yet.
    fires_above : `bool`
        True when a value above the limit fires it (false: below).
    """

    pipeline: str
    name: str
    read: Callable[[Document], float | None]
    limit: float | None = None
    fires_above: bool = True


def _stack(*path: str) -> Callable[[Document], float | None]:
    """Build a reader for the image stack's stored metrics.

    Returns
    -------
    reader : `Callable`
        Reads the named stacking metric.
    """
    return lambda target: dig(target, "stacking", "qualitySummary", "stackingMetrics", *path)


def _quality(pipeline: str, metrics_key: str, key: str) -> Callable[[Document], float | None]:
    """Build a reader for a pipeline's stored metric.

    Returns
    -------
    reader : `Callable`
        Reads the named metric of the named pipeline.
    """
    return lambda target: dig(target, "quality", pipeline, metrics_key, key)


METRICS: tuple[Metric, ...] = (
    Metric(
        "stacking",
        "rejected pixel fraction",
        _stack("rejectedPixelFraction"),
        DEFAULT_REJECTED_FRACTION_FLAG_THRESHOLD,
    ),
    Metric(
        "stacking",
        "stacked FWHM / expected FWHM",
        lambda t: ratio(
            dig(t, "stacking", "qualitySummary", "stackingMetrics", "stackedFwhmPx"),
            dig(t, "stacking", "qualitySummary", "stackingMetrics", "expectedStackFwhmPx"),
        ),
        DEFAULT_FWHM_DEGRADATION_RATIO,
    ),
    Metric(
        "stacking",
        "saturated pixel fraction",
        _stack("saturatedPixelFraction"),
        DEFAULT_SATURATION_FLAG_THRESHOLD,
    ),
    Metric(
        "stacking", "zero pixel fraction", _stack("zeroPixelFraction"), DEFAULT_ZERO_FRACTION_FLAG_THRESHOLD
    ),
    Metric(
        "stacking", "master flat noise fraction", _stack("flatNoiseFraction"), MAXIMUM_FLAT_NOISE_FRACTION
    ),
    Metric(
        "stacking",
        "frames set aside / frames submitted",
        lambda t: ratio(
            (dig(t, "stacking", "qualitySummary", "stackingMetrics", "framesSubmitted") or 0)
            - (dig(t, "stacking", "qualitySummary", "stackingMetrics", "framesStacked") or 0),
            dig(t, "stacking", "qualitySummary", "stackingMetrics", "framesSubmitted"),
        ),
    ),
    Metric(
        "astrometry",
        "plate-solve residual RMS (arcsec)",
        _quality("astrometry", "astrometryMetrics", "astrometricResidualRmsArcsec"),
    ),
    Metric("astrometry", "sources detected", _quality("astrometry", "astrometryMetrics", "sourcesDetected")),
    Metric(
        "astrometry",
        "catalog lookups failed / attempted",
        lambda t: ratio(
            dig(t, "quality", "astrometry", "astrometryMetrics", "remoteCatalogQueriesFailed"),
            dig(t, "quality", "astrometry", "astrometryMetrics", "remoteCatalogQueriesAttempted"),
        ),
        0.5,
    ),
    Metric(
        "photometry",
        "median light-curve scatter (mag)",
        _quality("photometry", "photometryMetrics", "lightCurveScatterRmsMag"),
    ),
    Metric(
        "photometry",
        "frames rejected as outliers / frames",
        lambda t: ratio(
            len(dig(t, "quality", "photometry", "photometryMetrics", "rejectedFrames") or []),
            dig(t, "quality", "photometry", "photometryMetrics", "framesProcessed"),
        ),
        0.25,
    ),
    Metric(
        "spectroscopy",
        "zero-order saturated fraction",
        _quality("spectroscopy", "spectroscopyMetrics", "zeroOrderSaturatedPixelFraction"),
        DEFAULT_SATURATION_FLAG_THRESHOLD,
    ),
)


@dataclass(frozen=True)
class MetricSummary:
    """How one metric is spread across the library.

    Attributes
    ----------
    metric : `Metric`
        The metric.
    count : `int`
        How many targets have a value.
    minimum, median, ninetieth_percentile, maximum : `float` or `None`
        The spread of the values.
    beyond_limit : `int`
        How many values fire the limit (zero when there is none).
    """

    metric: Metric
    count: int
    minimum: float | None
    median: float | None
    ninetieth_percentile: float | None
    maximum: float | None
    beyond_limit: int


def summarize(metric: Metric, targets: Iterable[Document]) -> MetricSummary:
    """Summarize one metric over the targets that have it.

    Parameters
    ----------
    metric : `Metric`
        The metric to read.
    targets : `Iterable` [`dict`]
        The saved target documents.

    Returns
    -------
    summary : `MetricSummary`
        The count, spread and number beyond the limit.
    """
    values = []
    for target in targets:
        value = metric.read(target)
        try:
            if value is not None:
                values.append(float(value))
        except TypeError, ValueError:
            continue
    if not values:
        return MetricSummary(metric, 0, None, None, None, None, 0)
    ordered = sorted(values)
    beyond = 0
    if metric.limit is not None:
        beyond = sum(
            1 for value in values if (value >= metric.limit if metric.fires_above else value <= metric.limit)
        )
    ninetieth = ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))]
    return MetricSummary(
        metric, len(values), ordered[0], statistics.median(ordered), ninetieth, ordered[-1], beyond
    )


def format_report(summaries: Sequence[MetricSummary], target_count: int) -> str:
    """Write the summaries as a table.

    Parameters
    ----------
    summaries : `Sequence` [`MetricSummary`]
        From `summarize`.
    target_count : `int`
        How many targets were read.

    Returns
    -------
    text : `str`
        The report.
    """

    def number(value: float | None) -> str:
        """Write a number compactly.

        Returns
        -------
        text : `str`
            ``--`` for `None`.
        """
        return "--" if value is None else f"{value:.4g}"

    lines = [
        f"{target_count} targets read.",
        "",
        f"{'pipeline':13s}{'metric':40s}{'n':>4s}"
        f"{'min':>11s}{'median':>11s}{'p90':>11s}{'max':>11s}{'limit':>11s}  fires",
    ]
    for summary in summaries:
        metric = summary.metric
        fires = "" if metric.limit is None else f"{summary.beyond_limit}/{summary.count}"
        lines.append(
            f"{metric.pipeline:13s}{metric.name:40s}{summary.count:4d}"
            f"{number(summary.minimum):>11s}{number(summary.median):>11s}"
            f"{number(summary.ninetieth_percentile):>11s}{number(summary.maximum):>11s}"
            f"{number(metric.limit):>11s}  {fires}"
        )
    lines += [
        "",
        "'fires' counts targets whose latest saved value is beyond the limit. Metrics with no limit",
        "show the spread a limit would have to be derived from.",
    ]
    return "\n".join(lines)


def load_targets(connection: sqlite3.Connection) -> list[Document]:
    """Read every saved target document.

    Parameters
    ----------
    connection : `sqlite3.Connection`
        An open connection to the catalog database.

    Returns
    -------
    targets : `list` [`dict`]
        The documents that parse.
    """
    documents = []
    for (data_json,) in connection.execute("SELECT data_json FROM targets"):
        try:
            document = json.loads(data_json)
        except TypeError, ValueError:
            continue
        if isinstance(document, dict):
            documents.append(document)
    return documents


def main() -> int:
    """Run the report from the command line.

    Returns
    -------
    exit_code : `int`
        ``0`` on success, ``1`` if the catalog database cannot be found.
    """
    database = os.path.join(str(Astrometrics().config.get_library_path()), "astrometrics.db")
    if not os.path.exists(database):
        print(f"No catalog database at {database}.")
        return 1
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        targets = load_targets(connection)
    finally:
        connection.close()
    print(format_report([summarize(metric, targets) for metric in METRICS], len(targets)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
