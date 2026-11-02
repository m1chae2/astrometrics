"""Measures how far a star's variability call sits from the cutoff.

Combines the star's own coefficient of variation (CV) with the
population's adaptive cutoff into a continuous margin, so a near-boundary
call is distinguishable from a confident one instead of surviving only
as a single pass/fail boolean.
"""

from astrometricslib.models.quality.photometry_quality import OutputQualityAssessment


def assess_output_quality(
    *,
    coefficient_of_variation: float,
    adaptive_cutoff: float,
    mad_cv: float,
) -> OutputQualityAssessment:
    """Build the output-quality assessment for one star's variability call.

    Parameters
    ----------
    coefficient_of_variation : `float`
        The star's own CV.
    adaptive_cutoff : `float`
        The population-wide cutoff it was compared against (see
        `_adaptive_cv_cutoff`).
    mad_cv : `float`
        The population's median absolute deviation of CV, the same
        scale `_adaptive_cv_cutoff` uses to build the cutoff from
        `sigma_threshold`.

    Returns
    -------
    assessment : `OutputQualityAssessment`
        The structured margin record; see that class for what each
        field means.
    """
    margin_in_mad_units = (coefficient_of_variation - adaptive_cutoff) / max(1e-4, mad_cv)
    return OutputQualityAssessment(
        coefficient_of_variation=coefficient_of_variation,
        adaptive_cutoff=adaptive_cutoff,
        margin_in_mad_units=margin_in_mad_units,
    )
