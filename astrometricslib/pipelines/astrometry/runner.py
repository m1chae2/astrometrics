"""Finds a target's stars and figures out where the image is pointing.

Runs `AstrometryPipeline` (star detection plus, when possible, plate
solving), records what happened as an `AstrometryQualitySummary`, and
saves the identified stars into the shared star catalog.
"""

import logging
from pathlib import Path
from typing import Any

from astrometricslib.drivers.fits_access import FITS_READ_ERRORS
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.quality.gate_result import failed_gate, passed_gate
from astrometricslib.models.quality.quality_summary import (
    ASTROMETRY_PIPELINE_VERSION,
    AstrometryPipelineQualityMetrics,
    AstrometryQualitySummary,
)
from astrometricslib.models.target import Target
from astrometricslib.pipelines.astrometry.post_processing.record_quality import (
    PLATE_SOLVE_GATE_NAME,
    astrometry_run_gates,
    record_astrometry_quality,
    record_target_clipping_quality,
)
from astrometricslib.pipelines.pipeline_base import (
    AnalysisPipeline,
    PipelineRequest,
    Result,
    run_pipeline,
)
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.shared.session_identification import write_wcs_to_fits_header
from astrometricslib.pipelines.shared.star_recording import (
    _drop_unresolved_stars,
    merge_astrometry_stellar_object,
    record_pipeline_stars,
)
from astrometricslib.pipelines.shared.target_center_hint import resolve_target_center_hint
from astrometricslib.utilities.coordinate_parsing import parse_coordinate_string
from astrometricslib.utilities.exceptions import DATA_ERRORS

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
        except InvalidArgumentError:
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
        logger.info(
            "Updated Target %s RA/Dec from plate solver: RA=%s, DEC=%s", target.id, target.ra, target.dec
        )
    except DATA_ERRORS as wcs_error:
        logger.warning("Failed to extract center coordinate from WCS for target %s: %s", target.id, wcs_error)


def _write_solved_wcs_to_fits_header(path: str | None, context: Any) -> None:
    """Write a solved WCS back into the source FITS file's header.

    So the next tool that opens this file (Siril, another astrometry
    run, a human in a FITS viewer) sees the solved pointing without
    having to solve it again. The write keeps the SIP distortion terms
    and replaces any sky-map keywords from an earlier solve. See
    `write_wcs_to_fits_header` for how.

    Parameters
    ----------
    path : `str` or `None`
        The FITS file to update. `None` or a missing file does nothing.
    context : `Any`
        The finished pipeline context. Its `wcs` attribute is the solved
        sky map, or `None` when the image was not solved.
    """
    if context.wcs is None or not path:
        return
    write_wcs_to_fits_header(path, context.wcs)


def _refinement_metrics(refinement: Any) -> dict[str, Any]:
    """Read the quality-summary fields out of a Gaia refit result.

    Parameters
    ----------
    refinement : `WcsRefinement` or `None`
        What `StarIdentifier._refine_wcs_against_gaia` found, or `None` when
        no refit was tried.

    Returns
    -------
    fields : `dict`
        Values for the ``gaia_fit_*`` and ``wcs_refinement_shift_arcsec``
        fields of `AstrometryPipelineQualityMetrics`. All `None` without a
        fit.
    """
    if refinement is None or not refinement.star_count:
        return {
            "gaia_fit_rms_arcsec": None,
            "gaia_fit_rms_before_arcsec": None,
            "gaia_fit_star_count": None,
            "wcs_refinement_shift_arcsec": None,
        }
    return {
        # The RMS under the solution that was kept: the refined one when it
        # was applied, else the solver's own.
        "gaia_fit_rms_arcsec": refinement.rms_after_arcsec
        if refinement.applied
        else refinement.rms_before_arcsec,
        "gaia_fit_rms_before_arcsec": refinement.rms_before_arcsec,
        "gaia_fit_star_count": refinement.star_count,
        "wcs_refinement_shift_arcsec": refinement.shift_arcsec if refinement.applied else 0.0,
    }


def _plate_scale_arcsec_per_pixel(wcs: Any) -> float | None:
    """Read the pixel size on the sky from a solved coordinate system.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS` or `None`
        The solved coordinates.

    Returns
    -------
    scale : `float` or `None`
        Arcseconds per pixel, or `None` when there is no usable solution.
    """
    if wcs is None:
        return None
    try:
        from astropy.wcs.utils import proj_plane_pixel_scales

        return float(sum(proj_plane_pixel_scales(wcs)) / 2.0 * 3600.0)
    except ValueError, TypeError, AttributeError:
        return None


def _stored_stack_fwhm_px(path: str, target: Any) -> float | None:
    """Look up the star width the stacking step stored for the same file.

    Parameters
    ----------
    path : `str`
        Path of the image being analyzed.
    target : `Target` or `None`
        The image's target.

    Returns
    -------
    fwhm : `float` or `None`
        The stored width, or `None` when there is no target, the image is not
        the target's stacked image, or no width was stored.
    """
    try:
        stacking = target.stacking
        stacked_image = stacking.stacked_image
        stored = stacking.quality_summary.stacking_metrics.stacked_fwhm_px
    except AttributeError:
        return None
    if not stacked_image or stored is None:
        return None
    if Path(stacked_image).resolve() != Path(path).resolve():
        return None
    return float(stored)


def _measure_star_fwhm_px(image: Any, target: Any = None) -> float | None:
    """Measure how wide the stars of an image are.

    Parameters
    ----------
    image : `AstrometricsImage`
        The analyzed image.
    target : `Target`, optional
        The image's target. When the image is the target's stacked image and
        the stacking quality summary already holds its star width, that
        stored width is reused instead of measuring the file again.

    Returns
    -------
    fwhm : `float` or `None`
        The median star width in pixels, or `None` if it could not be measured.
    """
    from astrometricslib.pipelines.astrometry.pre_processing.fwhm import measure_image_fwhm

    path = getattr(image, "path", None)
    if not path:
        return None
    stored_fwhm = _stored_stack_fwhm_px(path, target)
    if stored_fwhm is not None:
        return stored_fwhm
    try:
        return measure_image_fwhm(path)
    except (*FITS_READ_ERRORS, *DATA_ERRORS):
        return None


class AstrometryPipelineAdapter(AnalysisPipeline):
    """Adapts `AstrometryPipeline` to the shared `AnalysisPipeline`."""

    @property
    def pipeline_name(self) -> str:
        """See `AnalysisPipeline.pipeline_name`.

        Returns
        -------
        pipeline_name : `str`
            Always ``"astrometry"``.
        """
        return "astrometry"

    def process_input(self, request: PipelineRequest) -> Result:
        """Astrometry has no "nothing to do" case left to check.

        `analyze_target` already raises before dispatch if no image path
        can be resolved for this target, so by the time a request
        reaches here, `request.path` is always usable.

        Returns
        -------
        result : `Result`
            Always `has_work=True`.
        """
        return Result()

    def run(self, request: PipelineRequest, result: Result) -> Result:
        """Detect stars, solve the pointing if possible, and save both.

        Returns
        -------
        result : `Result`
            Carries the `AnalysisContext`, the saved stars, and the
            counters `validate_output` needs.
        """
        from astrometricslib.drivers.astrometry_net_driver import (
            get_plate_solve_attempt_count,
            reset_plate_solve_statistics,
        )
        from astrometricslib.pipelines.astrometry.pipeline import (
            AstrometryPipeline,
        )
        from astrometricslib.pipelines.astrometry.processing.star_identifier import (
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

        pipeline = AstrometryPipeline(drivers=request.options.get("drivers"))
        context = pipeline.process(
            path, attempt_plate_solving=True, target_ra=target.ra, target_dec=target.dec
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

        # The target's catalog position as the record held it before this run:
        # the backfill below may fill an empty position from the solve, which
        # is the image center and not the target (see `target_clipping.py`).
        target_center_deg = resolve_target_center_hint(target)

        _backfill_target_ra_dec_from_wcs(target, context)
        _write_solved_wcs_to_fits_header(path, context)

        context.stellar_objects, _ = record_pipeline_stars(
            context.stellar_objects,
            catalog_access=request.catalog_access,
            target_id=target.id,
            merge_function=merge_astrometry_stellar_object,
            already_dropped=True,
        )

        # The stars solved and saved from the stacked image are the ones
        # this target "has"; photometry later finds more, but only from
        # per-frame detection, so they do not describe the stack.
        target.number_of_stars = len(context.stellar_objects)

        return Result(
            context=context,
            stellar_objects=context.stellar_objects,
            payload={
                "star_id_breakdown": star_id_breakdown,
                "simbad_matched_count": simbad_matched_count,
                "gaia_statistics": gaia_statistics,
                "plate_solve_attempts": plate_solve_attempts,
                "target_center_deg": target_center_deg,
            },
        )

    def validate_output(self, request: PipelineRequest, result: Result) -> AstrometryQualitySummary:
        """Build the quality summary, flagging a failed solve attempt.

        Returns
        -------
        summary : `AstrometryQualitySummary`
            Flagged with "plate solve failed" when no WCS was solved.
        """
        star_id_breakdown = result.payload["star_id_breakdown"]
        gaia_statistics = result.payload["gaia_statistics"]

        summary = AstrometryQualitySummary(
            target_id=request.target.id,
            astrometry_metrics=AstrometryPipelineQualityMetrics(
                sources_detected=result.context.sources_detected,
                solve_attempted=result.context.solve_attempted,
                catalog_match_separation_rms_arcsec=result.context.catalog_match_separation_rms_arcsec,
                plate_solve_fit_residual_rms_arcsec=result.context.plate_solve_fit_residual_rms_arcsec,
                plate_solve_matched_star_count=result.context.plate_solve_matched_star_count,
                **_refinement_metrics(result.context.wcs_refinement),
                astrometry_flags=list(result.context.astrometry_flags),
                plate_scale_arcsec_per_pixel=_plate_scale_arcsec_per_pixel(result.context.wcs),
                star_fwhm_px=_measure_star_fwhm_px(result.context.image, request.target),
                plate_solve_succeeded=result.context.wcs is not None,
                simbad_matched_count=result.payload["simbad_matched_count"],
                remote_catalog_queries_attempted=int(gaia_statistics["attempted"]),
                remote_catalog_queries_failed=int(gaia_statistics["failed"]),
                remote_catalog_circuit_breaker_tripped=bool(gaia_statistics["circuit_breaker_tripped"]),
                plate_solve_attempts=result.payload["plate_solve_attempts"],
                catalog_matched_star_count=star_id_breakdown.catalog_matched,
                position_only_star_count=star_id_breakdown.position_only,
                unresolved_star_count=star_id_breakdown.unresolved,
            ),
        )
        if summary.astrometry_metrics.plate_solve_succeeded:
            summary.record_gate(
                passed_gate(PLATE_SOLVE_GATE_NAME, detail="the image was solved to sky coordinates")
            )
        else:
            # An image with no solution has no sky coordinates, whether or
            # not a solve was tried, so both cases flag the run. The attempt
            # flag stays in the metrics for anyone who needs to tell them
            # apart.
            summary.record_gate(failed_gate(PLATE_SOLVE_GATE_NAME, "plate solve failed"))
        for gate in astrometry_run_gates(summary.astrometry_metrics):
            summary.record_gate(gate)

        # The same numbers and gates, stored as measurements next to the
        # summary. The context is None when the caller gave no time; then
        # nothing is stored (a warning says so). A storage failure is logged
        # inside and never fails the run.
        measurement_context = PipelineRunContext.from_options(
            request.target.id, self.pipeline_name, ASTROMETRY_PIPELINE_VERSION, request.options
        )
        if measurement_context is not None:
            # Values read from the stacked image go on the stack's subject.
            image_path = getattr(result.context.image, "path", None)
            record_astrometry_quality(
                summary.astrometry_metrics,
                list(summary.gates),
                measurement_context,
                Path(image_path).name if image_path else None,
            )
            # The share of the target's own pixels that are clipped in each
            # frame of the stack. It needs this run's own plate solution (not
            # the header's) and the registration record the stacking run wrote.
            if image_path:
                record_target_clipping_quality(
                    image_path,
                    list(request.target.frames),
                    measurement_context,
                    solved_wcs=None if result.context.wcs_is_header_fallback else result.context.wcs,
                    target_center_deg=result.payload["target_center_deg"],
                )

        from astrometricslib.pipelines.shared.applied_camera_profile import (
            camera_name_from_header,
            most_common_camera_name,
            record_camera_profile,
        )

        # The image's own header names its camera; the target's frames are the
        # fallback for an image whose header does not.
        camera_name = camera_name_from_header(result.context.image.header) or most_common_camera_name(
            request.frames or request.target.frames
        )
        record_camera_profile(summary, camera_name)
        return summary

    def to_result_dict(
        self, request: PipelineRequest, result: Result, summary: AstrometryQualitySummary
    ) -> dict[str, Any]:
        """Build the result dict astrometry's callers expect back.

        Returns
        -------
        result_dict : `dict`
            Has ``"context"``, ``"stellar_objects"``, ``"wcs"``, and
            ``"image_stats"``.
        """
        context = result.context
        return {
            "context": context,
            "stellar_objects": result.stellar_objects,
            "wcs": context.wcs,
            "image_stats": context.image.get_stats() if hasattr(context.image, "get_stats") else {},
        }


def run_astrometry_analysis(
    target: Target,
    frames: Any,  # unused; astrometry always solves `path`
    filter_type: Any,  # unused; astrometry has no filter concept
    catalog_access: Any,
    path: str | None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Detect stars in one image and, if possible, solve its pointing.

    A thin wrapper kept at this name and signature for
    `dispatch.PIPELINE_RUNNERS` -- the actual work is
    `AstrometryPipelineAdapter`, run through the shared
    input/main/output processing cycle in `run_pipeline`.

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
    catalog_access : `Any`
        Reads the existing star catalog for id reconciliation and saves
        the stars this run found.
    path : `str`
        The FITS image to analyze.
    **kwargs
        Run options. ``drivers`` (a `Drivers`) chooses the plate solver and
        SIMBAD driver; any left out is the built-in one.

    Returns
    -------
    result : `dict`
        Has ``"context"`` (the `AnalysisContext` the pipeline built),
        ``"stellar_objects"``, ``"wcs"``, and ``"image_stats"``.
    """
    from astrometricslib.pipelines.shared.provenance_recording import note_stacked_image_upstream

    note_stacked_image_upstream(kwargs, target.id, target.stacking.stacked_image, "input_image")

    request = PipelineRequest(
        target=target,
        catalog_access=catalog_access,
        frames=frames,
        filter_type=filter_type,
        path=path,
        options=kwargs,
    )
    return run_pipeline(AstrometryPipelineAdapter(), request)
