"""Tuners for the typical star and trail widths of a setup (#175, Phase E3).

Before a run measures its own star width (for example inside the star-width
estimator, which cannot use the width it is about to measure), the values
that follow the star width use the setup's typical width,
`run_bases.TYPICAL_STAR_WIDTH_PX`. The same holds for the width of a
spectrum's streak, `run_bases.TYPICAL_TRAIL_WIDTH_PX`. These tuners derive
both from this observatory's stored data:

- the typical star width is the median star width (FWHM) of the setup's
  stored stacks, read from the stacking quality rows;
- the typical trail width is the median, over the stored star spectra, of
  each spectrum's median streak width, turned from a Gaussian sigma into a
  FWHM. The stored spectra do not say which camera took them, so every
  stored spectrum counts; today they all come from one spectroscopy setup.
"""

import statistics
from collections.abc import Iterable, Sequence
from typing import Any

from astrometricslib._internal.drivers.tuning_support import camera_rows, finite_values
from astrometricslib.foundation.run_bases import TYPICAL_STAR_WIDTH_PX, TYPICAL_TRAIL_WIDTH_PX
from astrometricslib.foundation.tuning import Tuner, TuningInputs, TuningResult, TuningSkippedError
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import FWHM_PER_SIGMA
from astrometricslib.pipelines.spectroscopy.spectrum_tuning_data import (
    load_stored_star_spectra,
    require_spectral_filter,
)
from astrometricslib.pipelines.stacking.metrics import STACKING_STACKED_FWHM_PX

# A stack's optic matches the setup's when its stored focal length is within
# this share of the optic's: the same 2% tolerance a frame's optic is chosen
# with (`frame_optics.FOCAL_LENGTH_TOLERANCE_FRACTION`).
OPTIC_FOCAL_LENGTH_TOLERANCE = TunedParameter(
    name="foundation.tuning_optic_focal_length_tolerance",
    default=0.02,
    unit="fraction of the focal length",
    description="How close a stored focal length must be to the setup optic's to count as that optic.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="AST-17",
)


def stack_widths_of_optic(
    rows: Iterable[tuple[Any, Any]], optic_name: str, focal_length_mm: float | None, tolerance: float
) -> list[float]:
    """Keep the newest stack width of each stack taken through one optic.

    Parameters
    ----------
    rows : `Iterable` [`tuple`]
        ``(measurement, capture setting)`` pairs, oldest first.
    optic_name : `str`
        The setup's optic.
    focal_length_mm : `float` or `None`
        The optic's focal length, for settings that record no optic name.
    tolerance : `float`
        The share of the focal length within which a setting matches.

    Returns
    -------
    widths : `list` [`float`]
        One width per stack (its newest row), in pixels.
    """
    newest: dict[str, float] = {}
    for measurement, setting in rows:
        same_name = setting.telescope is not None and setting.telescope == optic_name
        same_focal = (
            focal_length_mm is not None
            and setting.focal_length_mm is not None
            and abs(setting.focal_length_mm - focal_length_mm) <= tolerance * focal_length_mm
        )
        if same_name or same_focal:
            newest[measurement.subject.subject_id] = float(measurement.value)
    return finite_values(newest.values())


def median_trail_width_fwhm(trail_widths_sigma: Sequence[Sequence[float]]) -> float | None:
    """Turn stored streak sigmas into one typical streak FWHM.

    Parameters
    ----------
    trail_widths_sigma : `Sequence` [`Sequence` [`float`]]
        For each spectrum, its streak widths as Gaussian sigmas, in pixels.

    Returns
    -------
    width : `float` or `None`
        The median over spectra of each spectrum's median sigma, times
        `FWHM_PER_SIGMA`; `None` when no spectrum has a positive width.
    """
    per_spectrum = [
        statistics.median(positive)
        for widths in trail_widths_sigma
        if (positive := [width for width in finite_values(widths) if width > 0.0])
    ]
    return FWHM_PER_SIGMA * statistics.median(per_spectrum) if per_spectrum else None


def tune_typical_star_width(inputs: TuningInputs) -> TuningResult:
    """Derive the setup's typical star width from its stored stacks.

    Parameters
    ----------
    inputs : `TuningInputs`
        The setup and config.

    Returns
    -------
    result : `TuningResult`
        The median stack FWHM, resting on the number of stacks.

    Raises
    ------
    TuningSkippedError
        If the setup has no stored stack widths.
    """
    from astrometricslib.parameters import resolve_parameter

    optic = inputs.configuration.get_observatory_setups().optic_named(inputs.setup.optic_name)
    rows = camera_rows(inputs.configuration, inputs.setup.camera_name, STACKING_STACKED_FWHM_PX)
    widths = stack_widths_of_optic(
        rows,
        inputs.setup.optic_name,
        optic.focal_length_mm if optic is not None else None,
        float(resolve_parameter(OPTIC_FOCAL_LENGTH_TOLERANCE).value),
    )
    if not widths:
        raise TuningSkippedError("no stored stack star widths for this camera and optic")
    return TuningResult(
        values={TYPICAL_STAR_WIDTH_PX.name: statistics.median(widths)},
        sample=len(widths),
        source=f"Median star width (FWHM) of {len(widths)} stored stacks of this camera and optic",
    )


def tune_typical_trail_width(inputs: TuningInputs) -> TuningResult:
    """Derive the typical streak width of the setup's spectra.

    Parameters
    ----------
    inputs : `TuningInputs`
        The setup, the filter and the ``"astrometrics"`` service.

    Returns
    -------
    result : `TuningResult`
        The typical streak FWHM, resting on the number of spectra.

    Raises
    ------
    TuningSkippedError
        If the filter is not the grating or no spectrum has a streak width.
    """
    require_spectral_filter(inputs)
    spectra = load_stored_star_spectra(inputs.service("astrometrics"))
    widths = [list(spectrum.trail_width_px) for spectrum in spectra if spectrum.trail_width_px is not None]
    width = median_trail_width_fwhm(widths)
    if width is None:
        raise TuningSkippedError("no stored spectrum has a streak width")
    count = sum(1 for spectrum in widths if any(value > 0.0 for value in spectrum))
    return TuningResult(
        values={TYPICAL_TRAIL_WIDTH_PX.name: width},
        sample=count,
        source=(
            f"Median streak FWHM of {count} stored star spectra (all stored spectra; none records its camera)"
        ),
    )


TYPICAL_STAR_WIDTH_TUNER = Tuner(
    name="foundation.typical_star_width",
    parameters=(TYPICAL_STAR_WIDTH_PX,),
    method="The median star width (FWHM) of the setup's stored stacks.",
    tune=tune_typical_star_width,
)

TYPICAL_TRAIL_WIDTH_TUNER = Tuner(
    name="foundation.typical_trail_width",
    parameters=(TYPICAL_TRAIL_WIDTH_PX,),
    method="The median, over stored star spectra, of each spectrum's median streak FWHM.",
    tune=tune_typical_trail_width,
    per_filter=True,
)

__all__ = [
    "TYPICAL_STAR_WIDTH_TUNER",
    "TYPICAL_TRAIL_WIDTH_TUNER",
    "median_trail_width_fwhm",
    "stack_widths_of_optic",
]
