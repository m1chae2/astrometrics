"""Extracts a light spectrum for each star found in a spectral image.

Runs `AstrometryPipeline` first, in blind-detection-only mode, purely to
find where the stars are in the frame -- a spectral stack has no WCS of
its own, so this is the only way to locate them at all. Then runs
`SpectroscopyPipeline` to pull a spectrum out of each one, records the
result as a `SpectroscopyQualitySummary`, and saves the stars.
"""

import statistics
from typing import Any

from astrometricslib.models.target import Target
from astrometricslib.tasks.target_tasks.pipelines.star_persistence import (
    merge_spectroscopy_stellar_object,
    persist_pipeline_stars,
)


def run_spectroscopy_analysis(
    target: Target,
    frames,  # ruff: ignore[missing-type-function-argument] -- unused; spectroscopy always solves `path`
    filter_type,  # ruff: ignore[missing-type-function-argument] -- unused; spectroscopy has no filter concept
    butler,  # ruff: ignore[missing-type-function-argument]
    path: str | None,
    **kwargs,  # ruff: ignore[missing-type-kwargs]
) -> dict[str, Any]:
    """Detect stars in a spectral image and extract each one's spectrum.

    Parameters
    ----------
    target : `Target`
        The target this image belongs to. Its `spectroscopy_quality_summary`
        is set by this call.
    frames : `Any`
        Unused. Present so every pipeline runner shares one call signature.
    filter_type : `Any`
        Unused. Present so every pipeline runner shares one call signature.
    butler : `Any`
        Reads any existing catalog-identified stars for this target, for
        spectral-to-astrometric registration, and saves the stars this
        run found.
    path : `str`
        The spectral FITS image to analyze.

    Returns
    -------
    result : `dict`
        Has ``"context"`` (the `AnalysisContext` the astrometry pass
        built) and ``"stellar_objects"``.
    """
    from astrometricslib.tasks.stellar_tasks.astrometry_tasks.astrometry_pipeline import (
        AstrometryPipeline,
    )
    from astrometricslib.tasks.stellar_tasks.spectroscopy_tasks.spectroscopy_pipeline import (
        SpectroscopyPipeline,
    )

    # Use the AstrometryPipeline to identify the stars in the field
    astrometry = AstrometryPipeline()
    context = astrometry.process(path, attempt_plate_solving=False)

    # The spectral stack has no WCS of its own (see the module
    # docstring on spectral_star_registration), so these stars
    # would otherwise stay permanently unidentified. If this
    # target already has a plate-solved, catalog-identified
    # star field (from an earlier astrometry run), register the
    # two point sets purely by their geometry and carry each
    # matched star's real identity over -- automatically,
    # whenever a reference field is available, no caller opt-in
    # needed. Registered against the *full* blind detection set
    # (`context.stellar_objects`, up to ~100 stars) rather than
    # just the handful spectroscopy.process() below goes on to
    # extract a spectrum for -- astroalign's triangle-asterism
    # matching needs a reasonably dense point set to find a
    # reliable transform, and 10ish points was regularly too few
    # to converge at all in practice. Registration only sets
    # identification fields, and these are the same object
    # instances spectroscopy.process() mutates next, so it
    # doesn't matter that most of them won't end up with a
    # spectrum extracted.
    reference_stellar_objects = [
        stellar_object
        for stellar_object in butler.get("stellar_catalog", {})
        if target.id in stellar_object.target_ids
        and stellar_object.is_catalog_identified
        and not stellar_object.id.endswith("::spectroscopy")
    ]
    if reference_stellar_objects:
        from astrometricslib.tasks.stellar_tasks.astrometry_tasks.spectral_star_registration import (
            identify_spectral_stars_via_registration,
        )

        identify_spectral_stars_via_registration(context.stellar_objects, reference_stellar_objects)

    spectroscopy = SpectroscopyPipeline()
    limit = kwargs.get("limit", 10)
    stellar_objects, star_id_breakdown = persist_pipeline_stars(
        spectroscopy.process(context, limit=limit),
        butler=butler,
        target_id=target.id,
        merge_function=merge_spectroscopy_stellar_object,
        pipeline_name="spectroscopy",
    )

    from astrometricslib.models.quality_summary import (
        SpectroscopyPipelineQualityMetrics,
        SpectroscopyQualitySummary,
    )
    from astrometricslib.tasks.shared.saturation_analysis import is_saturation_significant

    zero_order_fractions = spectroscopy.last_run_zero_order_saturation_fractions
    max_zero_order_fraction = max(zero_order_fractions) if zero_order_fractions else None
    zero_order_flagged = (
        is_saturation_significant(max_zero_order_fraction) if max_zero_order_fraction is not None else False
    )
    dispersion_angles = [obj.dispersion_angle for obj in stellar_objects if obj.dispersion_angle is not None]
    all_trail_widths = [
        width
        for obj in stellar_objects
        if obj.trail_width_px
        for width in obj.trail_width_px
        if width > 0.0  # 0.0 marks a per-position fixed-box fallback, not a real fit
    ]
    trail_width_profile_available = bool(all_trail_widths)
    median_trail_width_px = statistics.median(all_trail_widths) if trail_width_profile_available else None

    target.spectroscopy_quality_summary = SpectroscopyQualitySummary(
        target_id=target.id,
        spectroscopy_metrics=SpectroscopyPipelineQualityMetrics(
            zero_order_saturated_pixel_fraction=max_zero_order_fraction,
            zero_order_saturation_flagged=zero_order_flagged,
            dispersion_angle_deg=dispersion_angles[0] if dispersion_angles else None,
            trail_width_profile_available=trail_width_profile_available,
            median_trail_width_px=median_trail_width_px,
            catalog_matched_star_count=star_id_breakdown.catalog_matched,
            position_only_star_count=star_id_breakdown.position_only,
            unresolved_star_count=star_id_breakdown.unresolved,
        ),
    )
    if zero_order_flagged:
        target.spectroscopy_quality_summary.flagged = True
        target.spectroscopy_quality_summary.flag_reasons.append(
            "zero-order saturated in at least one processed star"
        )

    return {"context": context, "stellar_objects": stellar_objects}
