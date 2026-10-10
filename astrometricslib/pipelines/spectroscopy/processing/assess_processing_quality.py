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

Two cross-checks sit beside the classification. The dereddening step removes
the reddening a catalog E(B-V) predicts before the classifier runs, and the
metrics `ebv_used` and `dereddening_type_shift_steps` report what it did.
Neither has a limit, because a type shift is expected when dust is present.
The line-index estimate is a second opinion on the type that does not depend
on the continuum slope. `index_vs_template_type_steps` measures how far it is
from the template-fit type. It uses `DIFFERS_FROM_CATALOG_SUBTYPES`, the
limit this repository already uses for two types that disagree, and the
checkpoint raises the `slope_and_lines_disagree` flag when the distance
passes it.

The metrics built from propagated errors are also reported only: the median
relative error of the equivalent widths, the equivalent widths of H-beta and
H-alpha, and the reduced chi-square of the best reference. No measurement
says what value of any of them is too poor, so none has a limit. They are
`None`-valued for a spectrum whose samples carry no errors.
"""

from collections.abc import Mapping, Sequence

import numpy as np

from astrometricslib.models.spectral_cross_checks import LineIndexClassification, ReddeningRecord
from astrometricslib.models.spectroscopy_quality import StageQualityCheckpoint, StageQualityMetric, metric
from astrometricslib.models.stellar_source import (
    AMBIGUOUS_RMS_GAP,
    DIFFERS_FROM_CATALOG_SUBTYPES,
    NO_GOOD_MATCH_RMS,
)
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


def _cross_check_metrics(
    reddening: ReddeningRecord | None, line_index_classification: LineIndexClassification | None
) -> list[StageQualityMetric]:
    """Build the metrics for the dereddening and line-index cross-checks.

    Parameters
    ----------
    reddening : `ReddeningRecord`, optional
        The dereddening applied before the classification, or `None` when no
        E(B-V) was available.
    line_index_classification : `LineIndexClassification`, optional
        The line-index estimate, or `None` when it could not be made.

    Returns
    -------
    metrics : `list` [`StageQualityMetric`]
        ``ebv_used``, ``dereddening_type_shift_steps`` and
        ``index_vs_template_type_steps``. A metric is `None`-valued when its
        input is missing.
    """
    shift = (
        None if reddening is None or reddening.type_shift_steps is None else abs(reddening.type_shift_steps)
    )
    index_steps = (
        None if line_index_classification is None else line_index_classification.steps_from_template_fit
    )
    return [
        metric(
            "ebv_used",
            None if reddening is None else reddening.ebv,
            "magnitude",
            note=(
                f"report-only; removed from the spectrum before classifying; source: {reddening.ebv_source}"
                if reddening is not None
                else "report-only; no catalog E(B-V) was available, so the observed spectrum was classified"
            ),
        ),
        metric(
            "dereddening_type_shift_steps",
            shift,
            "subtype steps",
            note=(
                "report-only; steps between the best type before and after dereddening, "
                "ten steps to a letter class"
            ),
        ),
        metric(
            "index_vs_template_type_steps",
            index_steps,
            "subtype steps",
            limit=DIFFERS_FROM_CATALOG_SUBTYPES,
            higher_is_better=False,
            note=(
                "limit is DIFFERS_FROM_CATALOG_SUBTYPES; steps between the type the line indices "
                "point to and the template-fit type, ten steps to a letter class"
            ),
        ),
    ]


# The feature names whose equivalent widths get their own metric, matched
# against the detector's feature names (``"Hydrogen Balmer series (H-beta)"``).
_HBETA_FEATURE = "H-beta"
_HALPHA_FEATURE = "H-alpha"


def _find_feature(features: Sequence[Mapping[str, object]], short_name: str) -> Mapping[str, object] | None:
    """Find a named feature by the short name in its label.

    Parameters
    ----------
    features : `Sequence` [`Mapping`]
        The results of `detect_named_features`.
    short_name : `str`
        A fragment of the feature's name, such as ``"H-beta"``.

    Returns
    -------
    feature : `Mapping` or `None`
        The first feature whose name contains `short_name`, or `None`.
    """
    return next((feature for feature in features if short_name in str(feature.get("feature", ""))), None)


def _equivalent_width_metrics(features: Sequence[Mapping[str, object]]) -> list[StageQualityMetric]:
    """Build the metrics that describe the equivalent widths.

    Parameters
    ----------
    features : `Sequence` [`Mapping`]
        The results of `detect_named_features`, each with its equivalent
        width and error when the spectrum carried sample errors.

    Returns
    -------
    metrics : `list` [`StageQualityMetric`]
        The median relative error over the features with a detected or
        possible verdict, then the H-beta and H-alpha equivalent widths in
        Angstroms. A value is `None` when no feature has a width.
    """
    relative_errors = []
    for feature in features:
        width = _optional_float(feature.get("equivalent_width_angstrom"))
        error = _optional_float(feature.get("equivalent_width_error_angstrom"))
        if (
            feature.get("verdict") in (VERDICT_DETECTED, VERDICT_POSSIBLE)
            and width is not None
            and error is not None
            and abs(width) > 0.0
        ):
            relative_errors.append(error / abs(width))
    metrics = [
        metric(
            "median_equivalent_width_relative_error",
            float(np.median(relative_errors)) if relative_errors else None,
            "fraction",
            note=(
                "median of error / |equivalent width| over features with a detected or possible verdict; "
                "reported only, no measured limit"
            ),
        )
    ]
    for short_name, name in ((_HBETA_FEATURE, "hbeta"), (_HALPHA_FEATURE, "halpha")):
        feature = _find_feature(features, short_name)
        width = _optional_float(feature.get("equivalent_width_angstrom")) if feature else None
        error = _optional_float(feature.get("equivalent_width_error_angstrom")) if feature else None
        if feature is None or width is None:
            note = f"{short_name} equivalent width not measured (not covered, or no sample errors)"
        else:
            kind = str(feature.get("kind", ""))
            note = (
                f"{short_name} equivalent width {width:.1f} +/- "
                f"{error if error is not None else float('nan'):.1f} A, verdict {feature.get('verdict')}, "
                f"{kind}; positive means a dip below the continuum; reported only"
            )
        metrics.append(metric(f"{name}_equivalent_width_angstrom", width, "angstrom", note=note))
    return metrics


def assess_processing_quality(
    *,
    classification: Mapping[str, object],
    features: Sequence[Mapping[str, object]],
    synthetic_b_minus_v: float | None,
    emission_lines: Sequence[Mapping[str, object]],
    is_emission_line_source: bool,
    second_order_blue_to_red_ratio: Sequence[float] | np.ndarray | None,
    reddening: ReddeningRecord | None = None,
    line_index_classification: LineIndexClassification | None = None,
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
    reddening : `ReddeningRecord`, optional
        The dereddening applied before the classification (see
        `analyze_spectrum`). Leave out when none was applied.
    line_index_classification : `LineIndexClassification`, optional
        The line-index estimate of the type, compared with the reported
        template-fit type (see `classify_by_line_indices`).

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
        *_equivalent_width_metrics(features),
        metric(
            "best_template_reduced_chi_square",
            _optional_float(classification.get("reduced_chi_square")) if is_classified else None,
            "reduced chi-square",
            note=(
                "chi-square per degree of freedom of the best reference by RMS, with the propagated sample "
                "errors; near 1 means the reference fits within the noise, far above 1 means the mismatch "
                "(reference and response errors) exceeds the noise; reported only, it decides nothing"
            ),
        ),
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
    cross_check_metrics = _cross_check_metrics(reddening, line_index_classification)
    metrics.extend(cross_check_metrics)

    flags = []
    if not is_classified:
        flags.append("unclassified")
    if is_emission_line_source:
        flags.append("emission_line_source")
    if risky_fraction:
        flags.append("second_order_risk")
    if any(
        entry.name == "index_vs_template_type_steps" and entry.passed is False
        for entry in cross_check_metrics
    ):
        flags.append("slope_and_lines_disagree")
    return StageQualityCheckpoint(stage=STAGE, metrics=metrics, flags=flags)
