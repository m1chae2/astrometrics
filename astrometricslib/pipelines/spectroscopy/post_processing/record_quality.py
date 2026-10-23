"""Builds the gate record for one spectroscopy run.

A spectroscopy run extracts a spectrum for each star it can, then classifies
it, looks for absorption features and compares the result with the catalog.
Whether the run can be trusted depends on a few checks: did any star produce
a spectrum, was the bright zero-order image saturated, are the spectral types
well supported, do they agree with the catalog, were the feature p-values
calibrated against each spectrum's own noise, and was the instrument's
resolution measured rather than assumed.

The eighth check, ``wavelength_scale``, reads how much the spectra's
wavelength zero points scatter (see `wavelength_scale`). A ninth,
``gaia_xp_agreement``, compares the spectra with Gaia XP (see
`compare_to_gaia_xp`); the caller adds it.

Each check is returned as a `GateResult`. A check that could not look (no
zero-order measurement, no catalog type to compare with) is ``not_checked``;
it is not a pass. The failed gates' sentences are the run's flag reasons.

The run is summarised first as a few plain counts (`spectrum_facts`), so the
single-image runner and the parallel batch workers, which cannot pass whole
star objects between processes, build the same gates from the same numbers.

The module also rolls up the four per-spectrum quality checkpoints
(`StageQualityCheckpoint`) into one `StageQualityRollup` per stage for the run
summary. The workers pass each spectrum's checkpoints between processes as
plain dictionaries (`stage_quality_rows`), and `summarize_stage_quality` reads
them back.

The same module turns the run's summary numbers, roll-ups and gates into
quality measurements (`spectroscopy_run_measurements`) and stores them
(`record_spectroscopy_quality`). The gates and the summary stay as they are;
the measurements are written next to them.
"""

import statistics
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from astrometricslib.models.quality.gate_result import GateResult, failed_gate, passed_gate, unchecked_gate
from astrometricslib.models.quality.limit import LimitTier
from astrometricslib.models.quality.measurement import QualityMeasurement
from astrometricslib.models.quality.metric_spec import MetricSpec
from astrometricslib.models.quality.quality_summary import SpectroscopyPipelineQualityMetrics
from astrometricslib.models.quality.spectroscopy_quality import (
    STAGE_ORDER,
    StageQualityCheckpoint,
    StageQualityRollup,
)
from astrometricslib.models.stellar_source import (
    AMBIGUOUS_RMS_GAP,
    DIFFERS_FROM_CATALOG_SUBTYPES,
    NO_GOOD_MATCH_RMS,
)
from astrometricslib.models.wavelength_scale import WavelengthScaleSummary
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    GateLimitSource,
    PipelineRunContext,
    build_and_record_measurements,
    build_measurement,
    gate_measurement,
)
from astrometricslib.pipelines.shared.quality.saturation import (
    DEFAULT_SATURATION_FLAG_THRESHOLD,
    is_saturation_significant,
)
from astrometricslib.pipelines.spectroscopy.metrics import (
    GAIA_XP_BAND_EDGES_ANGSTROM,
    SPECTROSCOPY_AMBIGUOUS_CLASSIFICATION_COUNT,
    SPECTROSCOPY_CATALOG_MATCHED_STAR_COUNT,
    SPECTROSCOPY_DISPERSION_ANGLE_FIRST_STAR,
    SPECTROSCOPY_GAIA_XP_COMPARED_STAR_COUNT,
    SPECTROSCOPY_GAIA_XP_MEDIAN_ABSOLUTE_SLOPE,
    SPECTROSCOPY_GAIA_XP_MEDIAN_SLOPE,
    SPECTROSCOPY_GATE_CATALOG_AGREEMENT,
    SPECTROSCOPY_GATE_FEATURE_SIGNIFICANCE,
    SPECTROSCOPY_GATE_GAIA_XP_AGREEMENT,
    SPECTROSCOPY_GATE_PROCESSING_QUALITY,
    SPECTROSCOPY_GATE_RESOLUTION_MEASURED,
    SPECTROSCOPY_GATE_SPECTRA_EXTRACTED,
    SPECTROSCOPY_GATE_SPECTRAL_CLASSIFICATION,
    SPECTROSCOPY_GATE_WAVELENGTH_SCALE,
    SPECTROSCOPY_GATE_ZERO_ORDER_SATURATION,
    SPECTROSCOPY_METRICS,
    SPECTROSCOPY_POOR_MATCH_CLASSIFICATION_COUNT,
    SPECTROSCOPY_POSITION_ONLY_STAR_COUNT,
    SPECTROSCOPY_TRAIL_WIDTH_MEDIAN,
    SPECTROSCOPY_UNRESOLVED_STAR_COUNT,
    SPECTROSCOPY_WAVELENGTH_SCALE_APPLIED_COUNT,
    SPECTROSCOPY_WAVELENGTH_SCALE_CORRELATION_SPECTRUM_COUNT,
    SPECTROSCOPY_WAVELENGTH_SCALE_RMS_AFTER,
    SPECTROSCOPY_WAVELENGTH_SCALE_RMS_BEFORE,
    SPECTROSCOPY_WAVELENGTH_SCALE_SATURATION_CORRELATION,
    SPECTROSCOPY_WAVELENGTH_SCALE_SPECTRUM_COUNT,
    SPECTROSCOPY_ZERO_ORDER_SATURATED_FRACTION_MAX,
    STAGE_METRIC_KEYS,
    STAGE_NAMES,
    gaia_xp_band_metric_name,
    stage_metric_name,
    stage_spectrum_count_name,
)
from astrometricslib.pipelines.spectroscopy.post_processing.compare_to_gaia_xp import GAIA_XP_GATE_NAME
from astrometricslib.pipelines.spectroscopy.post_processing.wavelength_scale import (
    WAVELENGTH_SCALE_GATE_NAME,
    wavelength_scale_gate,
)

SPECTRA_GATE_NAME = "spectra_extracted"
ZERO_ORDER_GATE_NAME = "zero_order_saturation"
CLASSIFICATION_GATE_NAME = "spectral_classification"
CATALOG_GATE_NAME = "catalog_agreement"
FEATURE_GATE_NAME = "feature_significance"
RESOLUTION_GATE_NAME = "resolution_measured"
PROCESSING_QUALITY_GATE_NAME = "processing_quality"

# The largest share of classified spectra allowed to fail a processing-
# checkpoint metric before the `processing_quality` gate fails. A designed
# value, not a measured one: half the classified spectra failing means the
# processing stage is not working for most of the run. It has not been checked
# against real runs.
PROCESSING_QUALITY_MAXIMUM_FAILED_FRACTION = 0.5

# The counts `spectrum_facts` returns, so a caller can start from all zeros.
FACT_NAMES = (
    "spectra",
    "classified",
    "subtype_ambiguous",
    "catalog_compared",
    "catalog_disagree",
    "feature_stars_tested",
    "feature_stars_uncalibrated",
    "resolution_measured",
    "processing_checked",
    "processing_failed",
)

# A spectral type that was never guessed.
_UNCLASSIFIED_TYPES = ("", "Unknown")

# What the feature detector calls a p-value that could not be calibrated
# against the spectrum's own control windows and fell back to a Gaussian.
_UNCALIBRATED_P_VALUE_METHOD = "gaussian"


def spectrum_facts(stellar_objects: Iterable[Any]) -> dict[str, int]:
    """Count what the run's spectra offer to each check.

    Parameters
    ----------
    stellar_objects : `Iterable`
        Stars carrying a ``spectroscopy`` result (or `None`).

    Returns
    -------
    facts : `dict` [`str`, `int`]
        One count for each name in `FACT_NAMES`.
    """
    facts = dict.fromkeys(FACT_NAMES, 0)
    for star in stellar_objects:
        spectroscopy = getattr(star, "spectroscopy", None)
        if spectroscopy is None:
            continue
        facts["spectra"] += 1
        spectral_type = spectroscopy.self_determined_spectral_type
        if spectral_type and spectral_type not in _UNCLASSIFIED_TYPES:
            facts["classified"] += 1
            if getattr(spectroscopy, "is_ambiguous", None):
                facts["subtype_ambiguous"] += 1
        comparison = spectroscopy.catalog_comparison
        if comparison is not None and comparison.spectral_type_agrees is not None:
            facts["catalog_compared"] += 1
            if comparison.spectral_type_agrees is False:
                facts["catalog_disagree"] += 1
        methods = [
            feature.get("p_value_method")
            for feature in spectroscopy.probable_spectral_features or []
            if feature.get("p_value_method")
        ]
        if methods:
            facts["feature_stars_tested"] += 1
            if _UNCALIBRATED_P_VALUE_METHOD in methods:
                facts["feature_stars_uncalibrated"] += 1
        if spectroscopy.resolution_element_angstrom is not None:
            facts["resolution_measured"] += 1
        if spectral_type and spectral_type not in _UNCLASSIFIED_TYPES:
            processing = next(
                (
                    checkpoint
                    for checkpoint in getattr(spectroscopy, "stage_quality", None) or []
                    if checkpoint.stage == "processing"
                ),
                None,
            )
            if processing is not None:
                facts["processing_checked"] += 1
                if processing.has_failed_metric:
                    facts["processing_failed"] += 1
    return facts


def stage_quality_rows(stellar_objects: Iterable[Any]) -> list[list[dict[str, Any]]]:
    """Collect each spectrum's quality checkpoints as plain dictionaries.

    A batch worker returns these to the parent process, which cannot receive
    whole star objects.

    Parameters
    ----------
    stellar_objects : `Iterable`
        Stars carrying a ``spectroscopy`` result (or `None`).

    Returns
    -------
    rows : `list` [`list` [`dict`]]
        One list per spectrum that has checkpoints, each holding that
        spectrum's checkpoints in stage order.
    """
    rows = []
    for star in stellar_objects:
        spectroscopy = getattr(star, "spectroscopy", None)
        checkpoints = getattr(spectroscopy, "stage_quality", None) if spectroscopy is not None else None
        if checkpoints:
            rows.append([checkpoint.model_dump() for checkpoint in checkpoints])
    return rows


def summarize_stage_quality(
    rows: Iterable[Iterable[StageQualityCheckpoint | Mapping[str, Any]]],
) -> list[StageQualityRollup]:
    """Roll the per-spectrum checkpoints up into one record per stage.

    Parameters
    ----------
    rows : `Iterable` [`Iterable`]
        One entry per spectrum, each holding that spectrum's checkpoints,
        either as `StageQualityCheckpoint` objects or as the dictionaries
        from `stage_quality_rows`.

    Returns
    -------
    rollups : `list` [`StageQualityRollup`]
        One record for each stage that at least one spectrum has, in stage
        order. Each holds the number of spectra, how many have a failed
        metric, and the median of every metric that at least one spectrum
        measured. A spectrum counts once per stage, even if it fails several
        metrics there. Empty when no spectrum has a checkpoint.
    """
    spectra_by_stage: dict[str, list[StageQualityCheckpoint]] = {stage: [] for stage in STAGE_ORDER}
    for row in rows:
        for entry in row:
            checkpoint = (
                entry
                if isinstance(entry, StageQualityCheckpoint)
                else StageQualityCheckpoint.model_validate(entry)
            )
            spectra_by_stage[checkpoint.stage].append(checkpoint)
    rollups = []
    for stage in STAGE_ORDER:
        checkpoints = spectra_by_stage[stage]
        if not checkpoints:
            continue
        values_by_metric: dict[str, list[float]] = {}
        for checkpoint in checkpoints:
            for entry in checkpoint.metrics:
                if entry.value is not None:
                    values_by_metric.setdefault(entry.name, []).append(entry.value)
        rollups.append(
            StageQualityRollup(
                stage=stage,
                spectrum_count=len(checkpoints),
                failed_spectrum_count=sum(1 for checkpoint in checkpoints if checkpoint.has_failed_metric),
                metric_medians={
                    name: float(statistics.median(values)) for name, values in values_by_metric.items()
                },
            )
        )
    return rollups


def merge_spectrum_facts(parts: Iterable[Mapping[str, int] | None]) -> dict[str, int]:
    """Add up the counts of several frames.

    Parameters
    ----------
    parts : `Iterable` [`Mapping` or `None`]
        Counts from `spectrum_facts`; `None` and missing names count as zero.

    Returns
    -------
    facts : `dict` [`str`, `int`]
        The summed counts.
    """
    total = dict.fromkeys(FACT_NAMES, 0)
    for part in parts:
        for name in FACT_NAMES:
            total[name] += int((part or {}).get(name, 0))
    return total


def spectroscopy_run_gates(
    facts: Mapping[str, int],
    zero_order_fractions: Sequence[float],
    classification_concerns: Sequence[Mapping[str, Any]],
    wavelength_scale: WavelengthScaleSummary | None = None,
) -> list[GateResult]:
    """Build the gates for one spectroscopy run.

    Parameters
    ----------
    facts : `Mapping` [`str`, `int`]
        The run's counts (see `spectrum_facts`).
    zero_order_fractions : `Sequence` [`float`]
        The saturated share of each processed star's zero-order image.
    classification_concerns : `Sequence` [`Mapping`]
        The stars whose spectral type is a poor match or ambiguous at the
        class level (the best reference of another spectral class is nearly
        as close). A star ambiguous only between neighbouring subtypes is
        not a concern; it is counted in ``facts["subtype_ambiguous"]`` and
        reported in the gate's detail.
    wavelength_scale : `WavelengthScaleSummary`, optional
        The run's wavelength-scale scatter (see `summarize_wavelength_scale`).
        Without it the ``wavelength_scale`` gate reads ``not_checked``.

    Returns
    -------
    gates : `list` [`GateResult`]
        Eight gates, in a fixed order.
    """
    gates: list[GateResult] = []
    spectra = facts.get("spectra", 0)

    if spectra == 0:
        gates.append(failed_gate(SPECTRA_GATE_NAME, "no star produced a spectrum", 0.0, 1.0))
    else:
        gates.append(passed_gate(SPECTRA_GATE_NAME, float(spectra), 1.0))

    saturation_source = (
        "share of the zero-order image at the camera's saturation level; a guess, not validated"
    )
    if not zero_order_fractions:
        gates.append(
            unchecked_gate(
                ZERO_ORDER_GATE_NAME, "no zero-order saturation was measured for any star", saturation_source
            )
        )
    else:
        worst = max(zero_order_fractions)
        if is_saturation_significant(worst):
            gates.append(
                failed_gate(
                    ZERO_ORDER_GATE_NAME,
                    "zero-order saturated in at least one processed star",
                    worst,
                    DEFAULT_SATURATION_FLAG_THRESHOLD,
                    saturation_source,
                )
            )
        else:
            gates.append(
                passed_gate(ZERO_ORDER_GATE_NAME, worst, DEFAULT_SATURATION_FLAG_THRESHOLD, saturation_source)
            )

    classification_source = (
        f"best reference within {NO_GOOD_MATCH_RMS} relative RMS of the spectrum, and the best "
        f"reference of another spectral class at least {AMBIGUOUS_RMS_GAP} RMS further away "
        "(not a probability); neighbouring subtypes closer than that are reported, not failed"
    )
    classified = facts.get("classified", 0)
    subtype_note = (
        f"; {facts.get('subtype_ambiguous', 0)} of {classified} classifications ambiguous at subtype level"
        if classified
        else ""
    )
    if classification_concerns:
        # A concern proves the check ran, whatever the counts say.
        gates.append(
            failed_gate(
                CLASSIFICATION_GATE_NAME,
                f"spectral classification uncertain for {len(classification_concerns)} star(s)"
                + subtype_note,
                float(len(classification_concerns)),
                0.0,
                classification_source,
            )
        )
    elif facts.get("classified", 0) == 0:
        gates.append(
            unchecked_gate(
                CLASSIFICATION_GATE_NAME, "no star was given a spectral type", classification_source
            )
        )
    else:
        gates.append(
            passed_gate(
                CLASSIFICATION_GATE_NAME,
                0.0,
                0.0,
                classification_source,
                subtype_note.removeprefix("; "),
            )
        )

    catalog_source = (
        f"measured type within {DIFFERS_FROM_CATALOG_SUBTYPES:.0f} subtype steps of the catalog type "
        "(set on 29 stars)"
    )
    if facts.get("catalog_compared", 0) == 0:
        gates.append(
            unchecked_gate(
                CATALOG_GATE_NAME, "no star had a catalog spectral type to compare with", catalog_source
            )
        )
    elif facts.get("catalog_disagree", 0):
        gates.append(
            failed_gate(
                CATALOG_GATE_NAME,
                f"the measured spectral type disagrees with the catalog for {facts['catalog_disagree']} "
                f"of {facts['catalog_compared']} star(s)",
                float(facts["catalog_disagree"]),
                0.0,
                catalog_source,
            )
        )
    else:
        gates.append(passed_gate(CATALOG_GATE_NAME, 0.0, 0.0, catalog_source))

    feature_source = "p-values calibrated on at least 20 control windows of the spectrum itself"
    if facts.get("feature_stars_tested", 0) == 0:
        gates.append(
            unchecked_gate(FEATURE_GATE_NAME, "no star had its absorption features tested", feature_source)
        )
    elif facts.get("feature_stars_uncalibrated", 0):
        gates.append(
            failed_gate(
                FEATURE_GATE_NAME,
                f"feature p-values for {facts['feature_stars_uncalibrated']} star(s) could not be calibrated "
                "against the spectrum's own noise (too few control windows), so they assume Gaussian noise",
                float(facts["feature_stars_uncalibrated"]),
                0.0,
                feature_source,
            )
        )
    else:
        gates.append(passed_gate(FEATURE_GATE_NAME, 0.0, 0.0, feature_source))

    resolution_source = "line widths of the star's own spectrum, when enough lines are present"
    measured = facts.get("resolution_measured", 0)
    if spectra == 0 or measured == 0:
        gates.append(
            unchecked_gate(
                RESOLUTION_GATE_NAME,
                "the instrument resolution was assumed from its design for every spectrum",
                resolution_source,
            )
        )
    else:
        gates.append(
            passed_gate(
                RESOLUTION_GATE_NAME,
                float(measured),
                float(spectra),
                resolution_source,
                f"resolution measured for {measured} of {spectra} spectrum(s)",
            )
        )

    processing_source = (
        f"at most {PROCESSING_QUALITY_MAXIMUM_FAILED_FRACTION:.0%} of classified spectra fail a "
        "processing-checkpoint metric (designed share, not validated)"
    )
    processing_checked = facts.get("processing_checked", 0)
    processing_failed = facts.get("processing_failed", 0)
    if processing_checked == 0:
        gates.append(
            unchecked_gate(
                PROCESSING_QUALITY_GATE_NAME,
                "no classified spectrum had a processing checkpoint to judge",
                processing_source,
            )
        )
    else:
        failed_fraction = processing_failed / processing_checked
        detail = f"{processing_failed} of {processing_checked} classified spectra failed a processing metric"
        if failed_fraction > PROCESSING_QUALITY_MAXIMUM_FAILED_FRACTION:
            gates.append(
                failed_gate(
                    PROCESSING_QUALITY_GATE_NAME,
                    detail,
                    failed_fraction,
                    PROCESSING_QUALITY_MAXIMUM_FAILED_FRACTION,
                    processing_source,
                )
            )
        else:
            gates.append(
                passed_gate(
                    PROCESSING_QUALITY_GATE_NAME,
                    failed_fraction,
                    PROCESSING_QUALITY_MAXIMUM_FAILED_FRACTION,
                    processing_source,
                    detail,
                )
            )
    gates.append(wavelength_scale_gate(wavelength_scale))
    return gates


# The metric that stands for each gate, and how its limit was obtained. The
# history fill (`scripts/backfill_quality_measurements.py`) reads this table
# too, so a limit is described in one place.
#
# A count that must be zero, or the one spectrum a run needs, follows from
# what the check is for, so those limits are geometry. The other four are
# designed values that the validation register lists in the spectroscopy
# design-estimates table: the saturated share of the zero-order image (SPE-D11,
# 0.1 percent of pixels, which is a share chosen by guess; the saturation level
# itself comes from the camera profile), the share of failed spectra (SPE-D13),
# the wavelength-scale RMS of one dispersion pixel (SPE-D12) and the Gaia XP
# limits (SPE-28 holds the comparison and names the limits as design
# estimates). None has been measured as a limit.
GATE_LIMIT_SOURCES: dict[str, GateLimitSource] = {
    SPECTRA_GATE_NAME: GateLimitSource(SPECTROSCOPY_GATE_SPECTRA_EXTRACTED, LimitTier.GEOMETRY),
    ZERO_ORDER_GATE_NAME: GateLimitSource(
        SPECTROSCOPY_GATE_ZERO_ORDER_SATURATION, LimitTier.DESIGN_ESTIMATE, "SPE-D11"
    ),
    CLASSIFICATION_GATE_NAME: GateLimitSource(SPECTROSCOPY_GATE_SPECTRAL_CLASSIFICATION, LimitTier.GEOMETRY),
    CATALOG_GATE_NAME: GateLimitSource(SPECTROSCOPY_GATE_CATALOG_AGREEMENT, LimitTier.GEOMETRY),
    FEATURE_GATE_NAME: GateLimitSource(SPECTROSCOPY_GATE_FEATURE_SIGNIFICANCE, LimitTier.GEOMETRY),
    RESOLUTION_GATE_NAME: GateLimitSource(SPECTROSCOPY_GATE_RESOLUTION_MEASURED, LimitTier.GEOMETRY),
    PROCESSING_QUALITY_GATE_NAME: GateLimitSource(
        SPECTROSCOPY_GATE_PROCESSING_QUALITY, LimitTier.DESIGN_ESTIMATE, "SPE-D13"
    ),
    WAVELENGTH_SCALE_GATE_NAME: GateLimitSource(
        SPECTROSCOPY_GATE_WAVELENGTH_SCALE, LimitTier.DESIGN_ESTIMATE, "SPE-D12"
    ),
    GAIA_XP_GATE_NAME: GateLimitSource(
        SPECTROSCOPY_GATE_GAIA_XP_AGREEMENT, LimitTier.DESIGN_ESTIMATE, "SPE-28"
    ),
}

_SPEC_BY_NAME: dict[str, MetricSpec] = {spec.name: spec for spec in SPECTROSCOPY_METRICS}

_NO_STAGE_REASON = "No spectrum of the run had a {stage} quality checkpoint."
_NO_STAGE_VALUE_REASON = "No spectrum of the run had a value for this metric at the {stage} checkpoint."
_NO_WAVELENGTH_SCALE_REASON = "No spectrum of the run had a measured wavelength zero-point offset."
_NO_GAIA_XP_REASON = "No star of the run was compared with a Gaia XP spectrum."
_NOT_COUNTED_REASON = "This run path does not count star identification."


def _optional_float(value: float | int | None) -> float | None:
    """Cast a number to a plain `float`, keeping `None`.

    Parameters
    ----------
    value : `float`, `int` or `None`
        A number from a summary field.

    Returns
    -------
    number : `float` or `None`
        The same number as a built-in `float`.
    """
    return None if value is None else float(value)


def _summary_values(
    metrics: SpectroscopyPipelineQualityMetrics, star_identification_counted: bool
) -> list[tuple[str, float | None, str, int | None]]:
    """List the run totals and roll-up numbers of the summary.

    Parameters
    ----------
    metrics : `SpectroscopyPipelineQualityMetrics`
        The run's summary metrics.
    star_identification_counted : `bool`
        Whether this run path counted star identification. The session batch
        does not, and its zero counts are defaults, not measurements.

    Returns
    -------
    values : `list` [`tuple`]
        One ``(metric name, value, reason if missing, sample count)`` for
        each metric except the checkpoint medians and the gates.
    """
    identification_reason = _NOT_COUNTED_REASON
    rows: list[tuple[str, float | None, str, int | None]] = [
        (
            SPECTROSCOPY_ZERO_ORDER_SATURATED_FRACTION_MAX,
            _optional_float(metrics.zero_order_saturated_pixel_fraction),
            "No processed star had a zero-order saturation measurement.",
            None,
        ),
        (
            SPECTROSCOPY_DISPERSION_ANGLE_FIRST_STAR,
            _optional_float(metrics.dispersion_angle_deg),
            "No star had a measured dispersion angle.",
            None,
        ),
        (
            SPECTROSCOPY_TRAIL_WIDTH_MEDIAN,
            _optional_float(metrics.median_trail_width_px),
            "No star had a fitted trail width.",
            None,
        ),
        (
            SPECTROSCOPY_CATALOG_MATCHED_STAR_COUNT,
            float(metrics.catalog_matched_star_count) if star_identification_counted else None,
            identification_reason,
            None,
        ),
        (
            SPECTROSCOPY_POSITION_ONLY_STAR_COUNT,
            float(metrics.position_only_star_count) if star_identification_counted else None,
            identification_reason,
            None,
        ),
        (
            SPECTROSCOPY_UNRESOLVED_STAR_COUNT,
            float(metrics.unresolved_star_count) if star_identification_counted else None,
            identification_reason,
            None,
        ),
        (
            SPECTROSCOPY_POOR_MATCH_CLASSIFICATION_COUNT,
            float(metrics.poor_match_classification_count),
            "",
            None,
        ),
        (
            SPECTROSCOPY_AMBIGUOUS_CLASSIFICATION_COUNT,
            float(metrics.ambiguous_classification_count),
            "",
            None,
        ),
    ]
    scale = metrics.wavelength_scale_summary
    scale_rows = (
        (SPECTROSCOPY_WAVELENGTH_SCALE_SPECTRUM_COUNT, None if scale is None else scale.spectrum_count),
        (SPECTROSCOPY_WAVELENGTH_SCALE_APPLIED_COUNT, None if scale is None else scale.applied_count),
        (
            SPECTROSCOPY_WAVELENGTH_SCALE_RMS_BEFORE,
            None if scale is None else scale.rms_before_correction_angstrom,
        ),
        (
            SPECTROSCOPY_WAVELENGTH_SCALE_RMS_AFTER,
            None if scale is None else scale.rms_after_correction_angstrom,
        ),
        (
            SPECTROSCOPY_WAVELENGTH_SCALE_SATURATION_CORRELATION,
            None if scale is None else scale.offset_saturation_correlation,
        ),
        (
            SPECTROSCOPY_WAVELENGTH_SCALE_CORRELATION_SPECTRUM_COUNT,
            None if scale is None else scale.correlation_spectrum_count,
        ),
    )
    correlation_reason = (
        "Fewer than three spectra had both an offset and a saturation value, or one list did not vary."
    )
    for name, value in scale_rows:
        reason = (
            correlation_reason
            if scale is not None and name == SPECTROSCOPY_WAVELENGTH_SCALE_SATURATION_CORRELATION
            else _NO_WAVELENGTH_SCALE_REASON
        )
        # The RMS after correction is a statistic of spectra with an offset.
        sample_count = (
            scale.spectrum_count
            if scale is not None and name == SPECTROSCOPY_WAVELENGTH_SCALE_RMS_AFTER
            else None
        )
        rows.append((name, _optional_float(value), reason, sample_count))

    gaia = metrics.gaia_xp_summary
    rows.append((
        SPECTROSCOPY_GAIA_XP_COMPARED_STAR_COUNT,
        None if gaia is None else float(gaia.compared_star_count),
        _NO_GAIA_XP_REASON,
        None,
    ))
    rows.append((
        SPECTROSCOPY_GAIA_XP_MEDIAN_SLOPE,
        None if gaia is None else _optional_float(gaia.median_slope_percent_per_1000_angstrom),
        _NO_GAIA_XP_REASON if gaia is None else "No compared star had a measurable tilt.",
        None,
    ))
    rows.append((
        SPECTROSCOPY_GAIA_XP_MEDIAN_ABSOLUTE_SLOPE,
        None if gaia is None else _optional_float(gaia.median_absolute_slope_percent_per_1000_angstrom),
        _NO_GAIA_XP_REASON if gaia is None else "No compared star had a measurable tilt.",
        None,
    ))
    for band_index, (band_start, _band_end) in enumerate(GAIA_XP_BAND_EDGES_ANGSTROM):
        band = None
        if gaia is not None:
            band = next((entry for entry in gaia.bands if round(entry.start_angstrom) == band_start), None)
        band_reason = _NO_GAIA_XP_REASON if band is None else "No compared star had a value in this band."
        rows.append((
            gaia_xp_band_metric_name(band_index, "median_ratio"),
            None if band is None else _optional_float(band.median_ratio),
            band_reason,
            None if band is None else band.star_count,
        ))
        rows.append((
            gaia_xp_band_metric_name(band_index, "scatter"),
            None if band is None else _optional_float(band.scatter),
            band_reason if band is None or band.star_count < 2 else "The band's scatter was not computed.",
            None if band is None else band.star_count,
        ))
        rows.append((
            gaia_xp_band_metric_name(band_index, "star_count"),
            None if band is None else float(band.star_count),
            band_reason,
            None,
        ))
    return rows


def _stage_values(
    metrics: SpectroscopyPipelineQualityMetrics,
) -> list[tuple[str, float | None, str]]:
    """List the checkpoint spectrum counts and medians of the summary.

    Parameters
    ----------
    metrics : `SpectroscopyPipelineQualityMetrics`
        The run's summary metrics.

    Returns
    -------
    values : `list` [`tuple`]
        One ``(metric name, value, reason if missing)`` for each declared
        checkpoint count and median. A checkpoint metric the roll-up does not
        hold, such as a yes/no verdict, is not listed.
    """
    rollups = {rollup.stage: rollup for rollup in metrics.stage_quality_summary or []}
    values: list[tuple[str, float | None, str]] = []
    for stage in STAGE_NAMES:
        rollup = rollups.get(stage)
        values.append((
            stage_spectrum_count_name(stage),
            None if rollup is None else float(rollup.spectrum_count),
            _NO_STAGE_REASON.format(stage=stage),
        ))
    for stage, key in STAGE_METRIC_KEYS:
        rollup = rollups.get(stage)
        median = None if rollup is None else rollup.metric_medians.get(key)
        reason = (
            _NO_STAGE_REASON.format(stage=stage)
            if rollup is None
            else _NO_STAGE_VALUE_REASON.format(stage=stage)
        )
        values.append((stage_metric_name(stage, key), _optional_float(median), reason))
    return values


def spectroscopy_run_measurements(
    metrics: SpectroscopyPipelineQualityMetrics,
    gates: Sequence[GateResult],
    context: PipelineRunContext,
    *,
    star_identification_counted: bool = True,
) -> list[QualityMeasurement]:
    """Build every measurement the spectroscopy pipeline declares for one run.

    Parameters
    ----------
    metrics : `SpectroscopyPipelineQualityMetrics`
        The run's summary metrics, with its checkpoint, Gaia XP and
        wavelength-scale roll-ups.
    gates : `Sequence` [`GateResult`]
        The run's gates: the eight from `spectroscopy_run_gates` and the Gaia
        XP gate.
    context : `PipelineRunContext`
        The run's shared facts.
    star_identification_counted : `bool`, optional
        `False` for the session batch, which does not count star
        identification. The three identification counts are then
        ``not_measured`` instead of a default zero.

    Returns
    -------
    measurements : `list` [`QualityMeasurement`]
        One measurement for each declared metric, all about the run. A value
        the run could not make is ``not_measured`` with a reason. The gate
        metrics carry the gate's outcome and limit. Yes/no verdicts are not
        measurements and are not included.
    """
    subject = context.run_subject()
    measurements: list[QualityMeasurement] = []
    for name, value, reason, sample_count in _summary_values(metrics, star_identification_counted):
        measurements.append(
            build_measurement(
                _SPEC_BY_NAME[name], value, context, subject, reason=reason, sample_count=sample_count
            )
        )
    for name, value, reason in _stage_values(metrics):
        measurements.append(build_measurement(_SPEC_BY_NAME[name], value, context, subject, reason=reason))

    gates_by_name = {gate.name: gate for gate in gates}
    for gate_name, limit_source in GATE_LIMIT_SOURCES.items():
        gate = gates_by_name.get(gate_name)
        if gate is None:
            continue
        measurements.append(
            gate_measurement(
                gate,
                _SPEC_BY_NAME[limit_source.metric_name],
                context,
                subject,
                limit_tier=limit_source.tier,
                register_entry=limit_source.register_entry,
            )
        )
    return measurements


def record_spectroscopy_quality(
    metrics: SpectroscopyPipelineQualityMetrics,
    gates: Sequence[GateResult],
    context: PipelineRunContext,
    *,
    star_identification_counted: bool = True,
    config: object = None,
) -> int:
    """Store the run's measurements; no failure here ever fails the run.

    Parameters
    ----------
    metrics : `SpectroscopyPipelineQualityMetrics`
        The run's summary metrics.
    gates : `Sequence` [`GateResult`]
        The run's gates, the Gaia XP gate included.
    context : `PipelineRunContext`
        The run's shared facts.
    star_identification_counted : `bool`, optional
        `False` for the session batch (see `spectroscopy_run_measurements`).
    config : `AppConfiguration`, optional
        Where the quality store lives. The process-wide one if omitted.

    Returns
    -------
    new_count : `int`
        How many rows were new. It is 0 when anything went wrong, either in
        building the rows or in storing them (both are logged).
    """
    return build_and_record_measurements(
        lambda: spectroscopy_run_measurements(
            metrics, gates, context, star_identification_counted=star_identification_counted
        ),
        f"spectroscopy run on {context.target_id}",
        config=config,
    )
