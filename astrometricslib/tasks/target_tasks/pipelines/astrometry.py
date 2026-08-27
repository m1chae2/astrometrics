"""Finds a target's stars and figures out where the image is pointing.

Runs `AstrometryPipeline` (star detection plus, when possible, plate
solving), records what happened as an `AstrometryQualitySummary`, and
saves the identified stars into the shared star catalog.
"""

import logging
import os
from typing import Any

from astrometricslib.models.quality_summary import (
    AstrometryPipelineQualityMetrics,
    AstrometryQualitySummary,
)
from astrometricslib.tasks.pipeline_contract import (
    AnalysisPipeline,
    InputScreening,
    PipelineRequest,
    RunOutcome,
    run_pipeline,
)
from astrometricslib.tasks.target_tasks.pipelines.star_persistence import (
    _drop_unresolved_stars,
    merge_astrometry_stellar_object,
    persist_pipeline_stars,
)
from astrometricslib.utilities.coordinate_parsing import parse_coordinate_string

logger = logging.getLogger(__name__)


def _backfill_target_ra_dec_from_wcs(target: Any, context: Any) -> None:
    """Fill in a target's RA/Dec from a solved WCS, if they were unset.

    A target created before its position was known starts with empty or
    all-zero coordinates. Once astrometry solves an image of it, that
    solve is a better answer than "unknown" -- so it gets written back
    onto the target record.
    """
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

    if not (is_ra_empty or is_dec_empty or is_zero):
        return
    if context.wcs is None:
        return

    try:
        from astropy import units as u
        from astropy.coordinates import SkyCoord

        ra_deg = float(context.wcs.wcs.crval[0])
        dec_deg = float(context.wcs.wcs.crval[1])
        solved_coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
        target.ra = solved_coord.ra.to_string(unit=u.hour, sep=" ", precision=2)
        target.dec = solved_coord.dec.to_string(unit=u.deg, sep=" ", precision=2)
        logger.info(f"Updated Target {target.id} RA/Dec from plate solver: RA={target.ra}, DEC={target.dec}")
    except Exception as wcs_error:
        logger.warning(f"Failed to extract center coordinate from WCS for target {target.id}: {wcs_error}")


def _write_solved_wcs_to_fits_header(path: str | None, context: Any) -> None:
    """Write a solved WCS back into the source FITS file's header.

    So the next tool that opens this file (Siril, another astrometry
    run, a human in a FITS viewer) sees the solved pointing without
    having to solve it again.
    """
    if context.wcs is None or not path or not os.path.exists(path):
        return
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


class AstrometryPipelineAdapter(AnalysisPipeline):
    """Adapts `AstrometryPipeline` to the shared `AnalysisPipeline` shape."""

    @property
    def pipeline_name(self) -> str:
        """See `AnalysisPipeline.pipeline_name`.

        Returns
        -------
        pipeline_name : `str`
            Always ``"astrometry"``.
        """
        return "astrometry"

    def screen_input(self, request: PipelineRequest) -> InputScreening:
        """Astrometry has no screening failure mode left to check.

        `analyze_target` already raises before dispatch if no image path
        can be resolved for this target, so by the time a request
        reaches here, `request.path` is always usable.

        Returns
        -------
        screening : `InputScreening`
            Always `can_proceed=True`.
        """
        return InputScreening(can_proceed=True)

    def run(self, request: PipelineRequest, screening: InputScreening) -> RunOutcome:
        """Detect stars, solve the pointing if possible, and save both.

        Returns
        -------
        outcome : `RunOutcome`
            Carries the `AnalysisContext`, the saved stars, and the
            counters `validate_output` needs.
        """
        from astrometricslib.tasks.stellar_tasks.astrometry_tasks.astrometry_pipeline import (
            AstrometryPipeline,
        )
        from astrometricslib.tasks.stellar_tasks.astrometry_tasks.plate_solver import (
            get_plate_solve_attempt_count,
            reset_plate_solve_statistics,
        )
        from astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier import (
            get_gaia_query_statistics,
            reset_gaia_query_statistics,
        )

        target = request.target
        path = request.path

        # Reset before this target's own solve/query work starts, so
        # the counts read back below describe this target alone --
        # both tallies are process-global and a worker handles many
        # targets in sequence. reset_gaia_query_statistics leaves the
        # circuit breaker itself untouched; see its docstring.
        reset_plate_solve_statistics()
        reset_gaia_query_statistics()

        pipeline = AstrometryPipeline()
        context = pipeline.process(
            path, attempt_plate_solving=True, target_ra=target.ra, target_dec=target.dec, **request.options
        )

        context.stellar_objects, star_id_breakdown = _drop_unresolved_stars(
            context.stellar_objects, target_id=target.id, pipeline_name="astrometry"
        )
        # Read from the identification and solver modules rather than
        # threaded through the call chain: both keep per-process
        # tallies precisely so a summary can record what the run
        # actually experienced against the remote services.
        simbad_matched_count = sum(
            1 for stellar_object in context.stellar_objects if stellar_object.spectral_type
        )
        gaia_statistics = get_gaia_query_statistics()
        plate_solve_attempts = get_plate_solve_attempt_count()

        _backfill_target_ra_dec_from_wcs(target, context)
        _write_solved_wcs_to_fits_header(path, context)

        context.stellar_objects, _ = persist_pipeline_stars(
            context.stellar_objects,
            butler=request.butler,
            target_id=target.id,
            merge_function=merge_astrometry_stellar_object,
            already_dropped=True,
        )

        return RunOutcome(
            context=context,
            stellar_objects=context.stellar_objects,
            payload={
                "star_id_breakdown": star_id_breakdown,
                "simbad_matched_count": simbad_matched_count,
                "gaia_statistics": gaia_statistics,
                "plate_solve_attempts": plate_solve_attempts,
            },
        )

    def validate_output(self, request: PipelineRequest, outcome: RunOutcome) -> AstrometryQualitySummary:
        """Build the quality summary, flagging a failed solve attempt.

        Returns
        -------
        summary : `AstrometryQualitySummary`
            Flagged with "plate solve failed" when no WCS was solved.
        """
        star_id_breakdown = outcome.payload["star_id_breakdown"]
        gaia_statistics = outcome.payload["gaia_statistics"]

        summary = AstrometryQualitySummary(
            target_id=request.target.id,
            astrometry_metrics=AstrometryPipelineQualityMetrics(
                sources_detected=outcome.context.sources_detected,
                solve_attempted=outcome.context.solve_attempted,
                astrometric_residual_rms_arcsec=outcome.context.astrometric_residual_rms_arcsec,
                plate_solve_succeeded=outcome.context.wcs is not None,
                simbad_matched_count=outcome.payload["simbad_matched_count"],
                remote_catalog_queries_attempted=int(gaia_statistics["attempted"]),
                remote_catalog_queries_failed=int(gaia_statistics["failed"]),
                remote_catalog_circuit_breaker_tripped=bool(gaia_statistics["circuit_breaker_tripped"]),
                plate_solve_attempts=outcome.payload["plate_solve_attempts"],
                catalog_matched_star_count=star_id_breakdown.catalog_matched,
                position_only_star_count=star_id_breakdown.position_only,
                unresolved_star_count=star_id_breakdown.unresolved,
            ),
        )
        if not summary.astrometry_metrics.plate_solve_succeeded:
            summary.flagged = True
            summary.flag_reasons.append("plate solve failed")
        return summary

    def to_result_dict(
        self, request: PipelineRequest, outcome: RunOutcome, summary: AstrometryQualitySummary
    ) -> dict[str, Any]:
        """Build the result dict astrometry's callers expect back.

        Returns
        -------
        result : `dict`
            Has ``"context"``, ``"stellar_objects"``, ``"wcs"``, and
            ``"image_stats"``.
        """
        context = outcome.context
        return {
            "context": context,
            "stellar_objects": outcome.stellar_objects,
            "wcs": context.wcs,
            "image_stats": context.image.get_stats() if hasattr(context.image, "get_stats") else {},
        }


def run_astrometry_analysis(
    target,  # ruff: ignore[missing-type-function-argument]
    frames,  # ruff: ignore[missing-type-function-argument] -- unused; astrometry always solves `path`
    filter_type,  # ruff: ignore[missing-type-function-argument] -- unused; astrometry has no filter concept
    butler,  # ruff: ignore[missing-type-function-argument]
    path: str | None,
    **kwargs,  # ruff: ignore[missing-type-kwargs]
) -> dict[str, Any]:
    """Detect stars in one image and, if possible, solve its pointing.

    A thin wrapper kept at this name and signature for
    `pipelines.PIPELINE_RUNNERS` -- the actual work is
    `AstrometryPipelineAdapter`, run through the shared
    screen/run/validate/report cycle in `run_pipeline`.

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
    request = PipelineRequest(
        target=target, butler=butler, frames=frames, filter_type=filter_type, path=path, options=kwargs
    )
    return run_pipeline(AstrometryPipelineAdapter(), request)
