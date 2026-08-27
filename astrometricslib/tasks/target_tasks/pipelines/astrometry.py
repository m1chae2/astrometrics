"""Finds a target's stars and figures out where the image is pointing.

Runs `AstrometryPipeline` (star detection plus, when possible, plate
solving), records what happened as an `AstrometryQualitySummary`, and
saves the identified stars into the shared star catalog.
"""

import logging
import os
from typing import Any

from astrometricslib.models.target import Target
from astrometricslib.tasks.target_tasks.pipelines.star_persistence import (
    _drop_unresolved_stars,
    merge_astrometry_stellar_object,
    persist_pipeline_stars,
)
from astrometricslib.utilities.coordinate_parsing import parse_coordinate_string

logger = logging.getLogger(__name__)


def run_astrometry_analysis(
    target: Target,
    frames,  # ruff: ignore[missing-type-function-argument] -- unused; astrometry always solves `path`
    filter_type,  # ruff: ignore[missing-type-function-argument] -- unused; astrometry has no filter concept
    butler,  # ruff: ignore[missing-type-function-argument]
    path: str | None,
    **kwargs,  # ruff: ignore[missing-type-kwargs]
) -> dict[str, Any]:
    """Detect stars in one image and, if possible, solve its pointing.

    Parameters
    ----------
    target : `Target`
        The target this image belongs to. Its `astrometry_quality_summary`
        is set by this call, and its `ra`/`dec` are filled in if they were
        previously unset and the plate solve succeeds.
    frames : `Any`
        Unused. Present so every pipeline runner shares one call signature.
    filter_type : `Any`
        Unused. Present so every pipeline runner shares one call signature.
    butler : `Any`
        Reads the existing star catalog for id reconciliation and saves
        the stars this run found.
    path : `str`
        The FITS image to analyze.

    Returns
    -------
    result : `dict`
        Has ``"context"`` (the `AnalysisContext` the pipeline built),
        ``"stellar_objects"``, ``"wcs"``, and ``"image_stats"``.
    """
    from astrometricslib.tasks.stellar_tasks.astrometry_tasks.astrometry_pipeline import (
        AstrometryPipeline,
    )
    from astrometricslib.tasks.stellar_tasks.astrometry_tasks.plate_solver import (
        reset_plate_solve_statistics,
    )
    from astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier import (
        reset_gaia_query_statistics,
    )

    # Reset before this target's own solve/query work starts, so
    # the counts read back below describe this target alone --
    # both tallies are process-global and a worker handles many
    # targets in sequence. reset_gaia_query_statistics leaves the
    # circuit breaker itself untouched; see its docstring.
    reset_plate_solve_statistics()
    reset_gaia_query_statistics()

    pipeline = AstrometryPipeline()
    context = pipeline.process(
        path, attempt_plate_solving=True, target_ra=target.ra, target_dec=target.dec, **kwargs
    )

    from astrometricslib.models.quality_summary import (
        AstrometryPipelineQualityMetrics,
        AstrometryQualitySummary,
    )

    context.stellar_objects, star_id_breakdown = _drop_unresolved_stars(
        context.stellar_objects, target_id=target.id, pipeline_name="astrometry"
    )
    simbad_matched_count = sum(
        1 for stellar_object in context.stellar_objects if stellar_object.spectral_type
    )
    # Read from the identification and solver modules rather than
    # threaded through the call chain: both keep per-process
    # tallies precisely so a summary can record what the run
    # actually experienced against the remote services.
    from astrometricslib.tasks.stellar_tasks.astrometry_tasks.plate_solver import (
        get_plate_solve_attempt_count,
    )
    from astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier import (
        get_gaia_query_statistics,
    )

    gaia_statistics = get_gaia_query_statistics()
    target.astrometry_quality_summary = AstrometryQualitySummary(
        target_id=target.id,
        astrometry_metrics=AstrometryPipelineQualityMetrics(
            sources_detected=context.sources_detected,
            solve_attempted=context.solve_attempted,
            astrometric_residual_rms_arcsec=context.astrometric_residual_rms_arcsec,
            plate_solve_succeeded=context.wcs is not None,
            simbad_matched_count=simbad_matched_count,
            remote_catalog_queries_attempted=int(gaia_statistics["attempted"]),
            remote_catalog_queries_failed=int(gaia_statistics["failed"]),
            remote_catalog_circuit_breaker_tripped=bool(gaia_statistics["circuit_breaker_tripped"]),
            plate_solve_attempts=get_plate_solve_attempt_count(),
            catalog_matched_star_count=star_id_breakdown.catalog_matched,
            position_only_star_count=star_id_breakdown.position_only,
            unresolved_star_count=star_id_breakdown.unresolved,
        ),
    )
    if not target.astrometry_quality_summary.astrometry_metrics.plate_solve_succeeded:
        target.astrometry_quality_summary.flagged = True
        target.astrometry_quality_summary.flag_reasons.append("plate solve failed")

    # If target RA and DEC are not populated or zero, pull
    # coordinates from plate solver
    is_ra_empty = not target.ra or target.ra.strip() in ("", "0", "0.0", "0h 0m 0s")
    is_dec_empty = not target.dec or target.dec.strip() in ("", "0", "0.0", "0° 0′ 0′′")
    is_zero = False
    if not (is_ra_empty or is_dec_empty):
        try:
            resolved_ra_deg = parse_coordinate_string(str(target.ra), is_ra=True)
            resolved_dec_deg = parse_coordinate_string(str(target.dec), is_ra=False)
            # ruff: ignore[float-equality-comparison] -- exact
            # sentinel check against the unset-coordinate
            # default ("0h 0m 0s" / "0(deg) 0' 0''"), not a
            # measured/computed value comparison.
            if resolved_ra_deg == 0.0 and resolved_dec_deg == 0.0:
                is_zero = True
        except Exception:
            is_zero = True

    if is_ra_empty or is_dec_empty or is_zero:
        if context.wcs is not None:
            try:
                from astropy import units as u
                from astropy.coordinates import SkyCoord

                ra_deg = float(context.wcs.wcs.crval[0])
                dec_deg = float(context.wcs.wcs.crval[1])
                solved_coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
                target.ra = solved_coord.ra.to_string(unit=u.hour, sep=" ", precision=2)
                target.dec = solved_coord.dec.to_string(unit=u.deg, sep=" ", precision=2)
                logger.info(
                    f"Updated Target {target.id} RA/Dec from plate solver: RA={target.ra}, DEC={target.dec}"
                )
            except Exception as wcs_error:
                logger.warning(
                    f"Failed to extract center coordinate from WCS for target {target.id}: {wcs_error}"
                )

    if context.wcs is not None and path:
        if os.path.exists(path):
            try:
                from astropy.io import fits

                with fits.open(path, mode="update") as hdul:
                    wcs_header = context.wcs.to_header()
                    for card in wcs_header.cards:
                        if not card.keyword:
                            continue
                        hdul[0].header[card.keyword] = (card.value, card.comment)
                    hdul.flush()
                logger.info(f"Updated FITS file {path} header with solved WCS keywords.")
            except Exception as wcs_error:
                logger.warning(f"Failed to update FITS file header with WCS: {wcs_error}")

    context.stellar_objects, _ = persist_pipeline_stars(
        context.stellar_objects,
        butler=butler,
        target_id=target.id,
        merge_function=merge_astrometry_stellar_object,
        already_dropped=True,
    )

    return {
        "context": context,
        "stellar_objects": context.stellar_objects,
        "wcs": context.wcs,
        "image_stats": context.image.get_stats() if hasattr(context.image, "get_stats") else {},
    }
