"""Builds the gate record for one spectroscopy run.

A spectroscopy run extracts a spectrum for each star it can, then classifies
it, looks for absorption features and compares the result with the catalog.
Whether the run can be trusted depends on a few checks: did any star produce
a spectrum, was the bright zero-order image saturated, are the spectral types
well supported, do they agree with the catalog, were the feature p-values
calibrated against each spectrum's own noise, and was the instrument's
resolution measured rather than assumed.

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
"""

import statistics
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from astrometricslib.models.gate_result import GateResult, failed_gate, passed_gate, unchecked_gate
from astrometricslib.models.spectroscopy_quality import (
    STAGE_ORDER,
    StageQualityCheckpoint,
    StageQualityRollup,
)
from astrometricslib.models.stellar_source import (
    AMBIGUOUS_RMS_GAP,
    DIFFERS_FROM_CATALOG_SUBTYPES,
    NO_GOOD_MATCH_RMS,
)
from astrometricslib.pipelines.shared.quality.saturation import (
    DEFAULT_SATURATION_FLAG_THRESHOLD,
    is_saturation_significant,
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

    Returns
    -------
    gates : `list` [`GateResult`]
        Seven gates, in a fixed order.
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
    return gates
