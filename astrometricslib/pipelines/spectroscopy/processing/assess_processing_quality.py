"""Quality checkpoint 2: how the core processing of one spectrum went.

Processing finds named features, classifies the spectrum, measures its
colour, tests for emission lines and flags second-order risk. This module
judges those results on their own, before post-processing compares them with
the catalog. It never changes a result. It gathers the numbers into the common
`StageQualityCheckpoint` shape so processing can be measured like every other
stage.

The limits for the classification (`NO_GOOD_MATCH_RMS` and
`AMBIGUOUS_RMS_GAP`) are defined once, in
`astrometricslib.models.stellar_source`. This module imports them and
defines none of its own. Metrics without a limit (feature counts,
second-order risk, emission lines) are reported for measurement only,
because no validated limit exists for them yet.
"""

from collections.abc import Mapping, Sequence

import numpy as np

from astrometricslib.models.spectroscopy_quality import StageQualityCheckpoint, StageQualityMetric, metric
from astrometricslib.models.stellar_source import AMBIGUOUS_RMS_GAP, NO_GOOD_MATCH_RMS
from astrometricslib.pipelines.spectroscopy.processing.emission_line_detector import (
    VERDICT_DETECTED as EMISSION_LINE_DETECTED,
)
from astrometricslib.pipelines.spectroscopy.processing.second_order_risk import is_second_order_risky
from astrometricslib.pipelines.spectroscopy.processing.spectral_feature_detector import (
    VERDICT_DETECTED,
    VERDICT_POSSIBLE,
)

STAGE = "processing"

# What the feature detector calls a p-value that fell back to a Gaussian
# because too few control windows were available (see
# `spectral_feature_detector.MINIMUM_CONTROL_POSITIONS`).
_UNCALIBRATED_P_VALUE_METHOD = "gaussian"

# The spectral types that mean no classification was made.
_UNCLASSIFIED_TYPES = ("", "Unknown")


def _optional_float(value: object) -> float | None:
    """Read a number from a loosely typed value.

    Parameters
    ----------
    value : `object`
        A number, or `None`, or anything else.

    Returns
    -------
    number : `float` or `None`
        The value as a `float` if it is a number, otherwise `None`.
    """
    return float(value) if isinstance(value, int | float | np.integer | np.floating) else None


def _feature_metrics(features: Sequence[Mapping[str, object]]) -> list[StageQualityMetric]:
    """Build the metrics that describe the absorption-feature tests.

    Parameters
    ----------
    features : `Sequence` [`Mapping`]
        The results of `detect_named_features`.

    Returns
    -------
    metrics : `list` [`StageQualityMetric`]
        The significant-feature count, the best p-value and the number of
        p-values that could not be calibrated. All three are `None`-valued
        when no feature was tested (for example for an extended target).
    """
    tested = [feature for feature in features if _optional_float(feature.get("p_value")) is not None]
    if not tested:
        return [
            metric("significant_feature_count", None, "features"),
            metric("best_feature_p_value", None, "probability"),
            metric("uncalibrated_feature_count", None, "features"),
        ]
    significant = [
        feature for feature in tested if feature.get("verdict") in (VERDICT_DETECTED, VERDICT_POSSIBLE)
    ]
    p_values = [value for feature in tested if (value := _optional_float(feature.get("p_value"))) is not None]
    uncalibrated = [
        feature for feature in tested if feature.get("p_value_method") == _UNCALIBRATED_P_VALUE_METHOD
    ]
    return [
        metric(
            "significant_feature_count",
            float(len(significant)),
            "features",
            note="features whose verdict is detected or possible",
        ),
        metric(
            "best_feature_p_value",
            min(p_values),
            "probability",
            note="chance that noise like this spectrum's gives a feature this strong; lower is stronger",
        ),
        metric(
            "uncalibrated_feature_count",
            float(len(uncalibrated)),
            "features",
            limit=0.0,
            higher_is_better=False,
            note="p-values that assumed Gaussian noise because too few control windows were available",
        ),
    ]


def assess_processing_quality(
    *,
    classification: Mapping[str, object],
    features: Sequence[Mapping[str, object]],
    synthetic_b_minus_v: float | None,
    emission_lines: Sequence[Mapping[str, object]],
    is_emission_line_source: bool,
    second_order_blue_to_red_ratio: Sequence[float] | np.ndarray | None,
) -> StageQualityCheckpoint:
    """Build quality checkpoint 2 for one spectrum's processing result.

    Parameters
    ----------
    classification : `Mapping`
        The result of `classify_spectral_type` (or an "Unknown" result).
        Its ``classification_rms``, ``rms_gap_to_second_best`` and
        ``rms_gap_to_next_class`` entries are read.
    features : `Sequence` [`Mapping`]
        The results of `detect_named_features`.
    synthetic_b_minus_v : `float`, optional
        The spectrum's own B-V colour, or `None` when it could not be measured.
    emission_lines : `Sequence` [`Mapping`]
        The results of `detect_emission_lines`.
    is_emission_line_source : `bool`
        Whether at least two emission lines were detected.
    second_order_blue_to_red_ratio : `Sequence` [`float`], optional
        The per-sample ratio from `compute_second_order_blue_to_red_ratio`.

    Returns
    -------
    checkpoint : `StageQualityCheckpoint`
        The ``processing`` checkpoint. For an unclassified spectrum the
        classification and colour metrics are `None`-valued and the
        checkpoint carries the ``unclassified`` flag.
    """
    is_classified = str(classification.get("spectral_type", "")) not in _UNCLASSIFIED_TYPES
    rms = _optional_float(classification.get("classification_rms")) if is_classified else None
    gap_second = _optional_float(classification.get("rms_gap_to_second_best")) if is_classified else None
    gap_class = _optional_float(classification.get("rms_gap_to_next_class")) if is_classified else None

    metrics = [
        metric(
            "classification_rms",
            rms,
            "relative RMS",
            limit=NO_GOOD_MATCH_RMS,
            higher_is_better=False,
            note="limit is NO_GOOD_MATCH_RMS; lower means the best reference fits more closely",
        ),
        metric(
            "rms_gap_to_second_best",
            gap_second,
            "relative RMS",
            note=(
                "reported only; below AMBIGUOUS_RMS_GAP the subtype is uncertain, which is common at "
                "this resolution, so only the class-level gap carries a limit"
            ),
        ),
        metric(
            "rms_gap_to_next_class",
            gap_class,
            "relative RMS",
            limit=AMBIGUOUS_RMS_GAP,
            note="limit is AMBIGUOUS_RMS_GAP; a smaller gap means the class letter is uncertain",
        ),
        metric(
            "synthetic_colour_measured",
            (1.0 if synthetic_b_minus_v is not None else 0.0) if is_classified else None,
            "flag",
            limit=1.0,
            note="1 when a B-V colour could be measured from the spectrum",
        ),
        metric("synthetic_b_minus_v", synthetic_b_minus_v, "magnitude"),
        *_feature_metrics(features),
    ]

    risky_fraction: float | None = None
    max_ratio: float | None = None
    if second_order_blue_to_red_ratio is not None and len(second_order_blue_to_red_ratio) > 0:
        ratio = np.asarray(second_order_blue_to_red_ratio, dtype=float)
        risky_fraction = float(np.mean(is_second_order_risky(ratio)))
        max_ratio = float(np.max(ratio))
    metrics.append(
        metric(
            "second_order_risky_fraction",
            risky_fraction,
            "fraction",
            note="share of samples where second-order light could add a tenth of the signal",
        )
    )
    metrics.append(
        metric(
            "second_order_max_blue_to_red_ratio",
            max_ratio,
            "ratio",
            note="largest ratio of brightness at half the wavelength to brightness at the wavelength",
        )
    )

    detected_lines = sum(1 for line in emission_lines if line.get("verdict") == EMISSION_LINE_DETECTED)
    metrics.append(
        metric(
            "emission_lines_detected",
            float(detected_lines),
            "lines",
            note="named emission lines or blends with a detected verdict",
        )
    )

    flags = []
    if not is_classified:
        flags.append("unclassified")
    if is_emission_line_source:
        flags.append("emission_line_source")
    if risky_fraction:
        flags.append("second_order_risk")
    return StageQualityCheckpoint(stage=STAGE, metrics=metrics, flags=flags)
