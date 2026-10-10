"""Builds the gate record for one astrometry run.

Astrometry finds the stars in an image, works out where the image points
(a "plate solve") and names the stars by looking them up in online catalogs.
Three checks say whether the run can be trusted: were any stars found, did the
plate solve succeed, and did the catalog lookups work.

The residual check judges the plate solver's own fit residual (how far the
fitted position of each star it matched sits from the reference star). When the
solver reported none, as the online solver does, the check falls back to the
catalog match separation (the distance from each detected star to the nearest
SIMBAD or Gaia star). The gate's detail says which number it used.

Each check is returned as a `GateResult`. The plate-solve gate is built by the
runner as before; the other two are built here. A catalog lookup that was
never attempted is ``not_checked``; it is not a pass.
"""

from astrometricslib.models.gate_result import GateResult, failed_gate, passed_gate, unchecked_gate
from astrometricslib.models.quality_summary import AstrometryPipelineQualityMetrics

SOURCE_DETECTION_GATE_NAME = "source_detection"
RESIDUAL_GATE_NAME = "astrometric_residual"
MATCHED_STARS_GATE_NAME = "catalog_matches"
CATALOG_LOOKUP_GATE_NAME = "catalog_lookup"

# How large the plate-solve residual may be, as a fraction of the stars' own
# width (FWHM). Centroid noise on a well-measured star is a few percent of its
# width, so a residual near half a width means the fit is dominated by
# something else: a wrong match, a bad distortion model or a wrong field.
# A design estimate. On the 8 saved solves whose stacked image carries a
# solved plate scale (2026-10-09) the residual was 0.14 to 0.44 of the width
# (median 0.25), so none fires; it is there to catch a failed solve, not to
# rank good ones. Those 8 numbers are catalog match separations (see
# `AstrometryPipelineQualityMetrics.catalog_match_separation_rms_arcsec`), not
# plate-solver fit residuals. A fit residual is expected to be the smaller of
# the two, so this limit is the more lenient one for it. It has not been
# re-measured on fit residuals.
MAXIMUM_RESIDUAL_FRACTION_OF_FWHM = 0.5

# The fewest catalog-matched stars for the residual to mean anything. The
# residual is a root mean square over the matched stars; with a handful of
# them it is not a reliable measure of the fit. A design estimate. On the 42
# saved solves, 4 had fewer than 20 matches (4, 6, 11 and 19), and they are
# among the five with the largest residuals (5.8, 5.9, 9.0 and 7.1 arcsec).
MINIMUM_MATCHED_STARS = 20

# The share of catalog lookups that may fail before the star names cannot be
# trusted. A design estimate: the first failures usually come from one slow
# or unreachable service, and half failing means most stars went unnamed.
MAXIMUM_FAILED_LOOKUP_FRACTION = 0.5


def _residual_and_basis(metrics: AstrometryPipelineQualityMetrics) -> tuple[float | None, str]:
    """Pick the residual the gate judges and say where it came from.

    Parameters
    ----------
    metrics : `AstrometryPipelineQualityMetrics`
        What the run measured.

    Returns
    -------
    residual : `float` or `None`
        The plate solver's fit residual in arcseconds when it reported one.
        Otherwise the catalog match separation RMS, or `None` when neither
        was measured.
    basis : `str`
        A short phrase naming the number used, for the gate's detail.
    """
    fit_residual = metrics.plate_solve_fit_residual_rms_arcsec
    if fit_residual is not None:
        matched = metrics.plate_solve_matched_star_count
        stars = f" over {matched} matched stars" if matched is not None else ""
        return fit_residual, f"plate-solve fit residual RMS{stars}"
    separation = metrics.catalog_match_separation_rms_arcsec
    if separation is None:
        separation = metrics.astrometric_residual_rms_arcsec
    return separation, "catalog match separation RMS, because the plate solver reported no fit residual"


def astrometry_run_gates(metrics: AstrometryPipelineQualityMetrics) -> list[GateResult]:
    """Build the source-detection and catalog-lookup gates.

    Parameters
    ----------
    metrics : `AstrometryPipelineQualityMetrics`
        What the run measured.

    Returns
    -------
    gates : `list` [`GateResult`]
        The ``catalog_matches``, ``astrometric_residual``, ``source_detection``
        and ``catalog_lookup`` gates.
    """
    gates: list[GateResult] = []
    matched = metrics.catalog_matched_star_count
    matches_source = f"at least {MINIMUM_MATCHED_STARS} catalog-matched stars (design estimate)"
    if not metrics.plate_solve_succeeded:
        gates.append(
            unchecked_gate(
                MATCHED_STARS_GATE_NAME, "the image was not solved, so no stars were matched", matches_source
            )
        )
    elif matched < MINIMUM_MATCHED_STARS:
        gates.append(
            failed_gate(
                MATCHED_STARS_GATE_NAME,
                f"only {matched} star(s) were matched to a catalog, too few for the fit to be reliable",
                float(matched),
                float(MINIMUM_MATCHED_STARS),
                matches_source,
            )
        )
    else:
        gates.append(
            passed_gate(MATCHED_STARS_GATE_NAME, float(matched), float(MINIMUM_MATCHED_STARS), matches_source)
        )

    residual_source = f"at most {MAXIMUM_RESIDUAL_FRACTION_OF_FWHM:g} of a star's width (design estimate)"
    residual, basis = _residual_and_basis(metrics)
    scale = metrics.plate_scale_arcsec_per_pixel
    fwhm = metrics.star_fwhm_px
    if residual is None or not scale or not fwhm:
        gates.append(
            unchecked_gate(
                RESIDUAL_GATE_NAME,
                "the residual, the plate scale or the star width was not measured, "
                "so the fit cannot be judged",
                residual_source,
            )
        )
    else:
        fraction = residual / (scale * fwhm)
        if fraction > MAXIMUM_RESIDUAL_FRACTION_OF_FWHM:
            gates.append(
                failed_gate(
                    RESIDUAL_GATE_NAME,
                    f"the plate solution misses its stars by {residual:.1f} arcsec ({basis}), "
                    f"{fraction:.0%} of a star's width, so it may be wrong",
                    fraction,
                    MAXIMUM_RESIDUAL_FRACTION_OF_FWHM,
                    residual_source,
                )
            )
        else:
            gates.append(
                passed_gate(
                    RESIDUAL_GATE_NAME,
                    fraction,
                    MAXIMUM_RESIDUAL_FRACTION_OF_FWHM,
                    residual_source,
                    f"{residual:.2f} arcsec ({basis})",
                )
            )

    if metrics.sources_detected <= 0:
        gates.append(
            failed_gate(
                SOURCE_DETECTION_GATE_NAME,
                "no stars were detected in the image",
                float(metrics.sources_detected),
                1.0,
            )
        )
    else:
        gates.append(passed_gate(SOURCE_DETECTION_GATE_NAME, float(metrics.sources_detected), 1.0))

    lookup_source = (
        f"under {MAXIMUM_FAILED_LOOKUP_FRACTION:.0%} of lookups failed and the circuit "
        "breaker did not trip (design estimate)"
    )
    attempted = metrics.remote_catalog_queries_attempted
    failed = metrics.remote_catalog_queries_failed
    if metrics.remote_catalog_circuit_breaker_tripped and attempted == 0:
        # The breaker was already open when the run began (it is shared by the
        # whole process), so no lookup was made. The run did not cause this and
        # did not check the catalogs.
        gates.append(
            unchecked_gate(
                CATALOG_LOOKUP_GATE_NAME,
                "the catalog service was already marked unreachable, so no lookup was made",
                lookup_source,
            )
        )
    elif metrics.remote_catalog_circuit_breaker_tripped:
        gates.append(
            failed_gate(
                CATALOG_LOOKUP_GATE_NAME,
                "catalog lookups kept failing, so the service was dropped and some stars are unnamed",
                float(failed),
                limit_source=lookup_source,
            )
        )
    elif attempted == 0:
        gates.append(
            unchecked_gate(CATALOG_LOOKUP_GATE_NAME, "no catalog lookup was attempted", lookup_source)
        )
    elif failed / attempted >= MAXIMUM_FAILED_LOOKUP_FRACTION:
        gates.append(
            failed_gate(
                CATALOG_LOOKUP_GATE_NAME,
                f"{failed} of {attempted} catalog lookup(s) failed; many stars may be unnamed",
                failed / attempted,
                MAXIMUM_FAILED_LOOKUP_FRACTION,
                lookup_source,
            )
        )
    else:
        gates.append(
            passed_gate(
                CATALOG_LOOKUP_GATE_NAME,
                failed / attempted,
                MAXIMUM_FAILED_LOOKUP_FRACTION,
                lookup_source,
                f"{failed} of {attempted} catalog lookup(s) failed",
            )
        )
    return gates
