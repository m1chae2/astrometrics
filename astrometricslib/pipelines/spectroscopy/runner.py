"""Extracts a light spectrum for each star found in a spectral image.

Runs `AstrometryPipeline` first, in blind-detection-only mode, purely to
find where the stars are in the frame -- a spectral stack has no WCS of
its own, so this is the only way to locate them at all. Then runs
`SpectroscopyPipeline` to pull a spectrum out of each one, records the
result as a `SpectroscopyQualitySummary`, and saves the stars.
"""

import statistics
from typing import Any

import numpy as np

from astrometricslib.models.quality_summary import (
    SpectroscopyPipelineQualityMetrics,
    SpectroscopyQualitySummary,
)
from astrometricslib.models.target import Target
from astrometricslib.pipelines.pipeline_base import (
    AnalysisPipeline,
    PipelineRequest,
    Result,
    run_pipeline,
)
from astrometricslib.pipelines.shared.star_recording import (
    merge_spectroscopy_stellar_object,
    record_pipeline_stars,
)
from astrometricslib.pipelines.shared.target_center_hint import (
    resolve_solved_stack_center_hint,
    resolve_solved_stack_wcs,
)

# How far from the frame centre, in degrees, a reference star may be and still
# be used to name the stars in a spectral frame. The frame is 3008 pixels of
# about 1.9 arcseconds (1.6 degrees) on a side, so its corners are 1.13
# degrees from the centre; 1.5 degrees allows for a mount pointing error of
# a few tenths of a degree and nothing more. Before this limit, a target with
# no stars of its own was registered against every named star in the catalog,
# and on 2026-09-24 25 of 69 stored spectra (Albireo, Alnath and M 57 fields)
# carried the name of a star 6 to 145 degrees away.
REGISTRATION_REFERENCE_FIELD_RADIUS_DEG = 1.5


def _recover_extended_source_hint(
    astrometry: Any,
    context: Any,
    target: Target,
    spectral_stack_path: str,
    reference_stellar_objects: list,
    hint_ra: float | None,
    hint_dec: float | None,
) -> None:
    """Find where an extended target (a nebula, say) sits in a spectral stack.

    A spectral stack has no plate solution, so `AstrometryPipeline.process`
    cannot turn the target's catalog position into a pixel position, and
    without one no extended target is extracted (an old stored record for it
    then keeps a position from some earlier run). The target's solved
    standard stack has a plate solution, and star registration has just
    measured how far the two stacks are shifted apart (against the reference
    stars' sky positions run through that same solution, since the target's
    stored stars may come from stacks with other pixel frames). Shifting the
    solution by that amount gives one for the spectral stack, good to a few
    pixels (on M 57 the ring nebula fell about 6 pixels from where its zero
    order really is).

    Does nothing when the hint already exists, there is no matching solved
    stack, or the star fields are not related by a single shift.

    Parameters
    ----------
    astrometry : `AstrometryPipeline`
        The pipeline that built `context`; it looks the target up.
    context : `AnalysisContext`
        The spectral stack's context. Its `extended_source_hint` is filled in.
    target : `Target`
        The target being analyzed.
    spectral_stack_path : `str`
        Path of the spectral stack.
    reference_stellar_objects : `list` [`StellarObject`]
        The named stars the spectral field was just registered against.
    hint_ra : `float` or `None`
        The position hint's right ascension in decimal degrees.
    hint_dec : `float` or `None`
        The position hint's declination in decimal degrees.
    """
    if context.extended_source_hint is not None:
        return

    from astrometricslib.pipelines.astrometry.spectral_star_registration import (
        estimate_registration_offset,
        shift_wcs_to_frame,
    )

    reference_wcs = resolve_solved_stack_wcs(target, spectral_stack_path)
    if reference_wcs is None:
        return
    offset = estimate_registration_offset(context.stellar_objects, reference_stellar_objects, reference_wcs)
    if offset is None:
        return
    spectral_wcs = shift_wcs_to_frame(reference_wcs, offset)
    context.extended_source_hint = astrometry.build_extended_source_hint(
        context.image, spectral_wcs, hint_ra, hint_dec
    )


def _field_center_for_registration(
    stack_path: str, hint_ra: float | None, hint_dec: float | None
) -> tuple[float, float] | None:
    """Find roughly where on the sky a spectral stack points.

    Prefers the plate-solved position hint; otherwise reads the mount's
    ``RA`` and ``DEC`` (decimal degrees) from the stack's FITS header.

    Parameters
    ----------
    stack_path : `str`
        The spectral stack.
    hint_ra : `float` or `None`
        The solved-stack right ascension in decimal degrees, if any.
    hint_dec : `float` or `None`
        The solved-stack declination in decimal degrees, if any.

    Returns
    -------
    center : `tuple` [`float`, `float`] or `None`
        The `(ra, dec)` in decimal degrees, or `None` when neither source
        has a usable position.
    """
    if hint_ra is not None and hint_dec is not None:
        return float(hint_ra), float(hint_dec)
    from astrometricslib.drivers.fits_access import read_header

    try:
        header = read_header(stack_path)
        return float(header["RA"]), float(header["DEC"])
    except OSError, KeyError, TypeError, ValueError:
        return None


def _within_field(stellar_object: Any, field_center: tuple[float, float] | None) -> bool:
    """Tell whether a catalog star lies inside the frame's part of the sky.

    Parameters
    ----------
    stellar_object : `StellarObject`
        The star to test.
    field_center : `tuple` [`float`, `float`] or `None`
        The frame centre `(ra, dec)` in decimal degrees. `None` means the
        centre is unknown, so no star is ruled out.

    Returns
    -------
    is_inside : `bool`
        `False` for a star with no sky position when a centre is known,
        because it cannot be checked.
    """
    if field_center is None:
        return True
    if stellar_object.right_ascension is None or stellar_object.declination is None:
        return False
    ra_star = np.radians(float(stellar_object.right_ascension))
    dec_star = np.radians(float(stellar_object.declination))
    ra_center, dec_center = np.radians(field_center[0]), np.radians(field_center[1])
    cosine_separation = np.sin(dec_star) * np.sin(dec_center) + np.cos(dec_star) * np.cos(
        dec_center
    ) * np.cos(ra_star - ra_center)
    separation_deg = float(np.degrees(np.arccos(np.clip(cosine_separation, -1.0, 1.0))))
    return separation_deg <= REGISTRATION_REFERENCE_FIELD_RADIUS_DEG


def _registration_reference_candidates(
    target: Target, catalog_access: Any, field_center: tuple[float, float] | None = None
) -> list:
    """Collect the stars a spectral field can register its identity against.

    `identify_spectral_stars_via_registration` needs a reference set of
    stars with known identities and pixel positions to match a
    spectroscopy image's blind detections against. Prefer this target's
    own catalog-identified stars -- a target with both standard and SPEC
    frames ("mixed frames") gets its own astrometry pass run first, so
    by the time spectroscopy runs, the catalog usually already has stars
    tagged with this exact target's id.

    A SPEC-only target (no standard frames of its own) never produces
    catalog-identified stars for its own id, and there is no field
    recording which imaging target's frames its exposures were taken
    alongside, so there is nothing narrower to filter to in that case --
    fall back to every catalog-identified star on record, regardless of
    which target found it.

    That fallback is safe against unrelated targets:
    `identify_spectral_stars_via_registration` requires several points
    to agree on one consistent pixel offset (see `_MIN_CONTROL_POINTS`
    there) before accepting a match, so a field with no real geometric
    relationship to this one just fails to register, exactly as an
    empty candidate list would have.

    Parameters
    ----------
    target : `Target`
        The target running spectroscopy; scopes the preferred candidate
        set to stars this target's own astrometry pass already found.
    catalog_access : `Any`
        Provides the read of `stellar_catalog`.
    field_center : `tuple` [`float`, `float`], optional
        Where the spectral frame points, `(ra, dec)` in decimal degrees.
        Only stars within `REGISTRATION_REFERENCE_FIELD_RADIUS_DEG` of it
        are kept, from both the own-target set and the fallback set: a
        target's recorded stars can include ones an earlier bad run
        attached to it, and registration matches by pixel geometry alone,
        so a star from another part of the sky can be handed to a spectral
        star that is really something else.

    Returns
    -------
    candidates : `list` [`StellarObject`]
        This target's own catalog-identified stars in the field if any
        exist, otherwise every catalog-identified star in the field. A star
        with no normal-image pixel position is skipped later by the
        registration itself.
    """
    catalog_identified = [
        stellar_object
        for stellar_object in catalog_access.get("stellar_catalog", {})
        if stellar_object.is_catalog_identified and _within_field(stellar_object, field_center)
    ]
    own_target_stars = [
        stellar_object for stellar_object in catalog_identified if target.id in stellar_object.target_ids
    ]
    return own_target_stars if own_target_stars else catalog_identified


class SpectroscopyPipelineAdapter(AnalysisPipeline):
    """Adapts `SpectroscopyPipeline` to the shared `AnalysisPipeline`."""

    @property
    def pipeline_name(self) -> str:
        """See `AnalysisPipeline.pipeline_name`.

        Returns
        -------
        pipeline_name : `str`
            Always ``"spectroscopy"``.
        """
        return "spectroscopy"

    def process_input(self, request: PipelineRequest) -> Result:
        """Spectroscopy has no "nothing to do" case left to check.

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
        """Locate stars, register against any known field, and extract spectra.

        Returns
        -------
        result : `Result`
            Carries the `AnalysisContext`, the saved stars, and the
            `SpectroscopyPipeline` instance `validate_output` reads its
            saturation fractions from.
        """
        from astrometricslib.pipelines.astrometry.pipeline import (
            AstrometryPipeline,
        )
        from astrometricslib.pipelines.spectroscopy.pipeline import (
            SpectroscopyPipeline,
        )

        target = request.target
        catalog_access = request.catalog_access

        # Use the AstrometryPipeline to identify the stars in the field
        # The stack's own FITS position is only the mount's report and can be
        # far off (see `resolve_solved_stack_center_hint`), so prefer the
        # centre of this target's plate-solved stack when there is one.
        hint_ra, hint_dec = resolve_solved_stack_center_hint(target, request.path)
        astrometry = AstrometryPipeline()
        context = astrometry.process(
            request.path, attempt_plate_solving=False, target_ra=hint_ra, target_dec=hint_dec
        )

        # The spectral stack has no WCS of its own (see the module
        # docstring on spectral_star_registration), so these stars
        # would otherwise stay permanently unidentified. If a
        # plate-solved, catalog-identified star field is available
        # (from an earlier astrometry run), register the two point
        # sets purely by their geometry and carry each matched star's
        # real identity over -- automatically, whenever a reference
        # field is available, no caller opt-in needed. Registered
        # against the *full* blind detection set
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
        field_center = _field_center_for_registration(request.path, hint_ra, hint_dec)
        reference_stellar_objects = _registration_reference_candidates(target, catalog_access, field_center)
        if reference_stellar_objects:
            from astrometricslib.pipelines.astrometry.spectral_star_registration import (
                identify_spectral_stars_via_registration,
            )

            identify_spectral_stars_via_registration(context.stellar_objects, reference_stellar_objects)
            _recover_extended_source_hint(
                astrometry, context, target, request.path, reference_stellar_objects, hint_ra, hint_dec
            )

        spectroscopy = SpectroscopyPipeline()
        limit = request.options.get("limit", 10)
        stellar_objects, star_id_breakdown = record_pipeline_stars(
            spectroscopy.process(context, limit=limit),
            catalog_access=catalog_access,
            target_id=target.id,
            merge_function=merge_spectroscopy_stellar_object,
            pipeline_name="spectroscopy",
        )

        return Result(
            context=context,
            stellar_objects=stellar_objects,
            payload={"star_id_breakdown": star_id_breakdown, "spectroscopy": spectroscopy},
        )

    def validate_output(self, request: PipelineRequest, result: Result) -> SpectroscopyQualitySummary:
        """Build the quality summary, flagging any significant saturation.

        Returns
        -------
        summary : `SpectroscopyQualitySummary`
            Flagged when any processed star's zero-order image was
            significantly saturated.
        """
        from astrometricslib.pipelines.shared.quality.saturation import is_saturation_significant
        from astrometricslib.pipelines.spectroscopy.spectral_classifier import (
            build_spectral_classification_concerns,
        )

        stellar_objects = result.stellar_objects
        star_id_breakdown = result.payload["star_id_breakdown"]
        spectroscopy = result.payload["spectroscopy"]

        zero_order_fractions = spectroscopy.last_run_zero_order_saturation_fractions
        max_zero_order_fraction = max(zero_order_fractions) if zero_order_fractions else None
        zero_order_flagged = (
            is_saturation_significant(max_zero_order_fraction)
            if max_zero_order_fraction is not None
            else False
        )
        dispersion_angles = [
            obj.spectroscopy.dispersion_angle
            for obj in stellar_objects
            if obj.spectroscopy and obj.spectroscopy.dispersion_angle is not None
        ]
        all_trail_widths = [
            width
            for obj in stellar_objects
            if obj.spectroscopy and obj.spectroscopy.trail_width_px
            for width in obj.spectroscopy.trail_width_px
            if width > 0.0  # 0.0 marks a per-position fixed-box fallback, not a real fit
        ]
        trail_width_profile_available = bool(all_trail_widths)
        median_trail_width_px = statistics.median(all_trail_widths) if trail_width_profile_available else None

        flagged_spectral_classifications = build_spectral_classification_concerns(stellar_objects)
        low_confidence_count = sum(
            1 for concern in flagged_spectral_classifications if "low_confidence" in concern["reason"]
        )
        ambiguous_count = sum(
            1 for concern in flagged_spectral_classifications if "ambiguous" in concern["reason"]
        )

        summary = SpectroscopyQualitySummary(
            target_id=request.target.id,
            spectroscopy_metrics=SpectroscopyPipelineQualityMetrics(
                zero_order_saturated_pixel_fraction=max_zero_order_fraction,
                zero_order_saturation_flagged=zero_order_flagged,
                dispersion_angle_deg=dispersion_angles[0] if dispersion_angles else None,
                trail_width_profile_available=trail_width_profile_available,
                median_trail_width_px=median_trail_width_px,
                catalog_matched_star_count=star_id_breakdown.catalog_matched,
                position_only_star_count=star_id_breakdown.position_only,
                unresolved_star_count=star_id_breakdown.unresolved,
                low_confidence_classification_count=low_confidence_count,
                ambiguous_classification_count=ambiguous_count,
                flagged_spectral_classifications=flagged_spectral_classifications,
            ),
        )
        if zero_order_flagged:
            summary.flagged = True
            summary.flag_reasons.append("zero-order saturated in at least one processed star")
        if flagged_spectral_classifications:
            summary.flagged = True
            summary.flag_reasons.append(
                f"spectral classification uncertain for {len(flagged_spectral_classifications)} star(s)"
            )

        from astrometricslib.pipelines.shared.applied_camera_profile import record_camera_profile

        record_camera_profile(summary, spectroscopy.config.camera.name)
        return summary

    def to_result_dict(
        self, request: PipelineRequest, result: Result, summary: SpectroscopyQualitySummary
    ) -> dict[str, Any]:
        """Build the result dict spectroscopy's callers expect back.

        Returns
        -------
        result_dict : `dict`
            Has ``"context"`` and ``"stellar_objects"``.
        """
        return {"context": result.context, "stellar_objects": result.stellar_objects}


def run_spectroscopy_analysis(
    target,  # ruff: ignore[missing-type-function-argument]
    frames,  # ruff: ignore[missing-type-function-argument] -- unused; spectroscopy always solves `path`
    filter_type,  # ruff: ignore[missing-type-function-argument] -- unused; spectroscopy has no filter concept
    catalog_access,  # ruff: ignore[missing-type-function-argument]
    path: str | None,
    **kwargs,  # ruff: ignore[missing-type-kwargs]
) -> dict[str, Any]:
    """Detect stars in a spectral image and extract each one's spectrum.

    A thin wrapper kept at this name and signature for
    `dispatch.PIPELINE_RUNNERS` -- the actual work is
    `SpectroscopyPipelineAdapter`, run through the shared
    input/main/output processing cycle in `run_pipeline`.

    Parameters
    ----------
    target : `Target`
        The target this image belongs to. Its `spectroscopy_quality_summary`
        is set by this call.
    frames : `Any`
        Unused. Present so every pipeline runner shares one call signature.
    filter_type : `Any`
        Unused. Present so every pipeline runner shares one call signature.
    catalog_access : `Any`
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
    request = PipelineRequest(
        target=target,
        catalog_access=catalog_access,
        frames=frames,
        filter_type=filter_type,
        path=path,
        options=kwargs,
    )
    return run_pipeline(SpectroscopyPipelineAdapter(), request)
