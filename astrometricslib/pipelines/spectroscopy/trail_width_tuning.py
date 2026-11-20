"""Tuner for the typical streak width of a setup's spectra (#175, Phase E3).

Before a spectroscopy run measures its own streak width, the values that
follow it use the setup's typical width, `run_bases.TYPICAL_TRAIL_WIDTH_PX`.
This tuner derives it from the stored star spectra: the median, over spectra,
of each spectrum's median streak width, turned from a Gaussian sigma into a
FWHM. The stored spectra do not say which camera took them, so every stored
spectrum counts; today they all come from one spectroscopy setup.
"""

import statistics
from collections.abc import Sequence

from astrometricslib._internal.drivers.tuning_support import finite_values
from astrometricslib.foundation.run_bases import TYPICAL_TRAIL_WIDTH_PX
from astrometricslib.foundation.tuning import Tuner, TuningInputs, TuningResult, TuningSkippedError
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import FWHM_PER_SIGMA
from astrometricslib.pipelines.spectroscopy.spectrum_tuning_data import (
    load_stored_star_spectra,
    require_spectral_filter,
)


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


TYPICAL_TRAIL_WIDTH_TUNER = Tuner(
    name="foundation.typical_trail_width",
    parameters=(TYPICAL_TRAIL_WIDTH_PX,),
    method="The median, over stored star spectra, of each spectrum's median streak FWHM.",
    tune=tune_typical_trail_width,
    per_filter=True,
)


__all__ = ["TYPICAL_TRAIL_WIDTH_TUNER", "median_trail_width_fwhm"]
