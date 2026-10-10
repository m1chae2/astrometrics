"""Rolls the wavelength zero points of a run up into one scatter figure.

Each spectrum measures its own wavelength zero-point offset in
pre-processing (see `pre_processing.wavelength_zero_point`). A single offset
says how far one spectrum's wavelength scale was off. The spread of the
offsets across a run says whether the scale can be trusted at all: if the
zero-order image of many stars is badly placed, the run's wavelengths
disagree from star to star.

This module reads those numbers back out of each spectrum's pre-processing
checkpoint, so it works the same for the single-image runner and for the batch
workers, which pass checkpoints between processes as plain dictionaries. It
builds a `WavelengthScaleSummary` and the run-level gate `wavelength_scale`.
"""

import math
from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np

from astrometricslib.models.gate_result import GateResult, failed_gate, passed_gate, unchecked_gate
from astrometricslib.models.spectroscopy_quality import StageQualityCheckpoint
from astrometricslib.models.wavelength_scale import WavelengthScaleSummary

WAVELENGTH_SCALE_GATE_NAME = "wavelength_scale"

# The largest RMS of the offsets left after correction, in Angstroms, before
# the gate fails: one pixel of dispersion. The camera setup covers 11.0 to 11.4
# A per pixel (see `spectral_resolution`). A designed limit, not a measured
# one: a scale that is off by more than one pixel puts a line in the wrong
# sample.
WAVELENGTH_SCALE_LIMIT_ANGSTROM = 11.0

# The fewest spectra with a measured offset needed before the gate judges the
# run. The RMS of one or two numbers says nothing about scatter.
MINIMUM_SPECTRA_FOR_WAVELENGTH_SCALE = 3

_OFFSET_METRIC = "wavelength_zero_point_offset_angstrom"
_UNCERTAINTY_METRIC = "wavelength_zero_point_uncertainty_angstrom"
_APPLIED_METRIC = "wavelength_zero_point_applied"
_SATURATION_METRIC = "zero_order_saturated_fraction"


def _pre_processing_values(
    row: Iterable[StageQualityCheckpoint | Mapping[str, Any]],
) -> dict[str, float | None] | None:
    """Read the metric values from one spectrum's pre-processing checkpoint.

    Parameters
    ----------
    row : `Iterable`
        One spectrum's checkpoints, as `StageQualityCheckpoint` objects or as
        the dictionaries from `stage_quality_rows`.

    Returns
    -------
    values : `dict` [`str`, `float` or `None`] or `None`
        The value of each metric by name, or `None` when the row has no
        pre-processing checkpoint.
    """
    for entry in row:
        checkpoint = (
            entry
            if isinstance(entry, StageQualityCheckpoint)
            else StageQualityCheckpoint.model_validate(entry)
        )
        if checkpoint.stage == "pre_processing":
            return {metric.name: metric.value for metric in checkpoint.metrics}
    return None


def _correlation(first: list[float], second: list[float]) -> float | None:
    """Give the Pearson correlation of two lists, if it is defined.

    Parameters
    ----------
    first : `list` [`float`]
        The first variable.
    second : `list` [`float`]
        The second variable, the same length.

    Returns
    -------
    correlation : `float` or `None`
        The correlation, from -1 to 1. `None` when there are fewer than three
        pairs or either list is constant.
    """
    if len(first) < MINIMUM_SPECTRA_FOR_WAVELENGTH_SCALE:
        return None
    first_values = np.asarray(first, dtype=float)
    second_values = np.asarray(second, dtype=float)
    if np.ptp(first_values) <= 0.0 or np.ptp(second_values) <= 0.0:
        return None
    return float(np.corrcoef(first_values, second_values)[0, 1])


def summarize_wavelength_scale(
    rows: Iterable[Iterable[StageQualityCheckpoint | Mapping[str, Any]]],
) -> WavelengthScaleSummary | None:
    """Summarize the wavelength zero points of a run's spectra.

    Only spectra with a measured offset count. For each one the "before"
    value is the size of its offset, and the "after" value is what is left
    once the pipeline's correction is made: the offset's uncertainty when the
    pipeline applied the offset, and the whole offset when it did not. The
    uncertainty is used rather than zero because a correction cannot be more
    exact than the measurement behind it.

    The correlation is between a spectrum's absolute offset and its zero-order
    saturated fraction. The absolute value is used because saturation can
    pull the centre of the zero order either way, so signed offsets would
    cancel.

    Parameters
    ----------
    rows : `Iterable` [`Iterable`]
        One entry per spectrum, each holding that spectrum's checkpoints (see
        `summarize_stage_quality`).

    Returns
    -------
    summary : `WavelengthScaleSummary` or `None`
        The summary, or `None` when no spectrum has a measured offset.
    """
    before: list[float] = []
    after: list[float] = []
    applied_count = 0
    offsets_with_saturation: list[float] = []
    saturations: list[float] = []
    for row in rows:
        values = _pre_processing_values(row)
        offset_value = None if values is None else values.get(_OFFSET_METRIC)
        if values is None or offset_value is None:
            continue
        offset = abs(float(offset_value))
        is_applied = bool(values.get(_APPLIED_METRIC))
        uncertainty = values.get(_UNCERTAINTY_METRIC)
        before.append(offset)
        after.append(float(uncertainty or 0.0) if is_applied else offset)
        applied_count += int(is_applied)
        saturation = values.get(_SATURATION_METRIC)
        if saturation is not None:
            offsets_with_saturation.append(offset)
            saturations.append(float(saturation))
    if not before:
        return None
    return WavelengthScaleSummary(
        spectrum_count=len(before),
        applied_count=applied_count,
        rms_before_correction_angstrom=math.sqrt(float(np.mean(np.square(before)))),
        rms_after_correction_angstrom=math.sqrt(float(np.mean(np.square(after)))),
        offset_saturation_correlation=_correlation(offsets_with_saturation, saturations),
        correlation_spectrum_count=len(saturations),
    )


def wavelength_scale_gate(summary: WavelengthScaleSummary | None) -> GateResult:
    """Build the run-level gate on the wavelength-scale scatter.

    Parameters
    ----------
    summary : `WavelengthScaleSummary` or `None`
        The run's summary from `summarize_wavelength_scale`.

    Returns
    -------
    gate : `GateResult`
        ``failed`` when the RMS after correction is over
        `WAVELENGTH_SCALE_LIMIT_ANGSTROM`, ``not_checked`` when fewer than
        `MINIMUM_SPECTRA_FOR_WAVELENGTH_SCALE` spectra had an offset, and
        ``passed`` otherwise.
    """
    source = (
        f"RMS of the per-spectrum wavelength offsets left after correction, at most "
        f"{WAVELENGTH_SCALE_LIMIT_ANGSTROM:g} A (one pixel of dispersion; designed, not measured)"
    )
    if summary is None or summary.spectrum_count < MINIMUM_SPECTRA_FOR_WAVELENGTH_SCALE:
        found = 0 if summary is None else summary.spectrum_count
        return unchecked_gate(
            WAVELENGTH_SCALE_GATE_NAME,
            f"only {found} spectrum(s) had a measured wavelength offset; "
            f"at least {MINIMUM_SPECTRA_FOR_WAVELENGTH_SCALE} are needed",
            source,
        )
    detail = (
        f"RMS {summary.rms_before_correction_angstrom:.1f} A before and "
        f"{summary.rms_after_correction_angstrom:.1f} A after correction over "
        f"{summary.spectrum_count} spectra; the offset was applied to {summary.applied_count}"
    )
    if summary.rms_after_correction_angstrom > WAVELENGTH_SCALE_LIMIT_ANGSTROM:
        return failed_gate(
            WAVELENGTH_SCALE_GATE_NAME,
            "the wavelength scale scatters between spectra: " + detail,
            summary.rms_after_correction_angstrom,
            WAVELENGTH_SCALE_LIMIT_ANGSTROM,
            source,
        )
    return passed_gate(
        WAVELENGTH_SCALE_GATE_NAME,
        summary.rms_after_correction_angstrom,
        WAVELENGTH_SCALE_LIMIT_ANGSTROM,
        source,
        detail,
    )
