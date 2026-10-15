"""Generates the scientific review guides for the analysis pipelines.

The guides live in ``documentation/library_design/pipelines/``. Each one
describes one pipeline: what it is for, which FITS header cards it reads, the
maths it uses, how a run flows through the code, its tuned limits, how it
behaves when data are missing, and the fields of its quality summary.

Three kinds of content come straight from the code, so they cannot drift:

* The quality gates. Every module-level constant named ``GATE_NAME`` or
  ``*_GATE_NAME`` in a pipeline's package is found by reading the source
  (`discover_gate_names`). The gate tables and the gate counts are built from
  that list. A gate with no entry in `GATE_DESCRIPTIONS` is printed as "not
  yet described", and the doc-parity test fails on it.
* The live values of the tuned limits, read by importing the constant
  (`require_symbol`). A renamed constant stops the build instead of printing
  "None".
* The quality-summary field tables, read from the pydantic models.

The prose around them is written by hand here and checked against the code
when the code changes. Run this file directly to rewrite the guides; the
Sphinx build also runs it through `setup`.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The date and version written under each guide's title.
DOC_VERSION = "1.1"
DOC_DATE = "2026-10-14"
VERSION_LINE = f"*Version {DOC_VERSION} · {DOC_DATE} · Status: current*"

# The repository root, and the folder that holds the pipeline packages.
REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINES_ROOT = REPO_ROOT / "astrometricslib" / "pipelines"

# A gate-name constant: ``GATE_NAME`` or anything ending in ``_GATE_NAME``.
GATE_CONSTANT_PATTERN = re.compile(r"^(?:[A-Z0-9_]+_)?GATE_NAME$")

# The pipeline packages that have a review guide, keyed by guide name.
PIPELINE_PACKAGES: dict[str, str] = {
    "astrometry": "astrometry",
    "photometry": "photometry",
    "spectroscopy": "spectroscopy",
    "asteroid_detection": "asteroid_detection",
    "stacking": "stacking",
}

# Gates that are not recorded on the run's quality summary, by the module
# that defines them. Every other gate is a run gate.
GATE_ROLES: dict[str, str] = {
    "astrometricslib.pipelines.photometry.processing.period_checks": "period check",
}
RUN_GATE_ROLE = "run gate"


@dataclass(frozen=True)
class GateDefinition:
    """One quality-gate name found in a pipeline's source.

    Attributes
    ----------
    name : `str`
        The gate name recorded in the gate list, such as ``"plate_solve"``.
    constant : `str`
        The constant that holds it, such as ``"PLATE_SOLVE_GATE_NAME"``.
    module : `str`
        The dotted module that defines the constant.
    role : `str`
        ``"run gate"`` for a gate recorded on the run's quality summary, or
        the role named in `GATE_ROLES`.
    """

    name: str
    constant: str
    module: str
    role: str

    @property
    def relative_path(self) -> str:
        """Give the defining module as a path inside its pipeline package.

        Returns
        -------
        path : `str`
            For example ``"post_processing/run_gates.py"``.
        """
        parts = self.module.split(".")[3:]
        return "/".join(parts) + ".py"


def discover_gate_names(pipeline: str) -> list[GateDefinition]:
    """Find every gate-name constant in one pipeline package.

    The source files are parsed, not imported. A constant counts when it is
    assigned a string at the top level of a module and its name matches
    `GATE_CONSTANT_PATTERN`. Test folders are skipped.

    Parameters
    ----------
    pipeline : `str`
        A key of `PIPELINE_PACKAGES`.

    Returns
    -------
    gates : `list` [`GateDefinition`]
        The gates, ordered by file path and then by line.
    """
    package_dir = PIPELINES_ROOT / PIPELINE_PACKAGES[pipeline]
    gates: list[GateDefinition] = []
    for path in sorted(package_dir.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT)
        if any(part in ("test", "tests") for part in relative.parts):
            continue
        module = ".".join(relative.with_suffix("").parts)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                target, value = node.target, node.value
            else:
                continue
            if not isinstance(target, ast.Name) or not GATE_CONSTANT_PATTERN.match(target.id):
                continue
            if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
                continue
            gates.append(
                GateDefinition(
                    name=value.value,
                    constant=target.id,
                    module=module,
                    role=GATE_ROLES.get(module, RUN_GATE_ROLE),
                )
            )
    return gates


# One sentence per gate: what makes it fail, pass or go unchecked. Keyed by
# the gate name in the code. The doc-parity test checks that every gate the
# code defines has an entry, and that no entry is left over.
GATE_DESCRIPTIONS: dict[str, str] = {
    # Astrometry
    "plate_solve": (
        "Fails when the image ends the run with no sky solution (pixel-to-sky map, or WCS), "
        "from the solver or from the image's own header."
    ),
    "source_detection": "Fails when no star was detected.",
    "catalog_matches": (
        "Fails when fewer than `MINIMUM_MATCHED_STARS` stars were matched to a catalog. Not "
        "checked when the image was not solved."
    ),
    "astrometric_residual": (
        "Fails when the solution misses its stars by more than "
        "`MAXIMUM_RESIDUAL_FRACTION_OF_FWHM` of a star's width (FWHM, Eq. 5). Not checked when "
        "the residual (position error), plate scale (sky angle per pixel) or star width is missing."
    ),
    "catalog_lookup": (
        "Fails when the remote-catalog circuit breaker (a switch that stops online catalog queries "
        "after repeated failures) tripped during the run, or when at least "
        "`MAXIMUM_FAILED_LOOKUP_FRACTION` of lookups failed. Not checked when no lookup was made."
    ),
    # Photometry run gates
    "ensemble_frame_rejection": (
        "Fails when at least `MINIMUM_ENSEMBLE_REJECTION_COUNT_TO_FLAG` frames and at least "
        "`MINIMUM_ENSEMBLE_REJECTION_FRACTION_TO_FLAG` of the frames were rejected as ensemble "
        "outliers (frames whose comparison-star level is out of line with the rest)."
    ),
    "capture_timestamps": (
        "Fails when any frame was left out for a missing or unreadable capture time. The "
        "detail names the time basis of the light curves (Section 3.1)."
    ),
    "session_content": "Fails when a session produced no light curves.",
    "session_plate_solve": (
        "Fails when a session could not be plate-solved (given a pixel-to-sky map) for matching "
        "stars across sessions. Not checked for a single session."
    ),
    "photometry_work": (
        "Fails when stage 1 found nothing to measure (no frame in the filter, or no frame "
        "with a capture time)."
    ),
    "comparison_ensemble": (
        "Fails when a session has fewer than `MINIMUM_COMPARISON_STARS` comparison stars, a set "
        "that changes between frames, or comparison stars that scatter more than "
        "`MAXIMUM_ENSEMBLE_SCATTER_OVER_PREDICTED` times their predicted error."
    ),
    "registration_drift": (
        "Fails when the field shifts by more than `UNSTABLE_TRACKING_DRIFT_PX`, when rotation and "
        "scale change move a corner by more than `MAXIMUM_CORNER_DISPLACEMENT_PX` (95th "
        "percentile), or when more than `MAXIMUM_CENTROID_FALLBACK_FRACTION` of centroids "
        "(brightness-weighted star centers) were untrustworthy and fell back to the aligned "
        "position."
    ),
    "scatter_population": (
        "Not checked when too few stars have a measured scatter, or too few to fit the noise "
        "model (expected scatter against brightness), so the variable-candidate cutoff is not "
        "reliable."
    ),
    "variability_discrimination": (
        "Fails when the variability score does not rank catalogued variables above unlisted "
        "stars (AUC test: the chance that a random catalogued variable scores above a random "
        "unlisted star). Not checked with too few of either."
    ),
    "detectable_amplitude": (
        "Fails when a sinusoidal variable would need more than `MAXIMUM_DETECTABLE_AMPLITUDE_MAG` "
        "peak to peak to clear the run's noise, so the run is blind to smaller variables."
    ),
    "flux_uncertainty": (
        "Cannot fail. Reports the median per-point error. Not checked when the errors assumed "
        "a gain of 1 e-/ADU (electrons per camera count), because the camera's gain was unknown."
    ),
    # Photometry period checks
    "holdout_nights": (
        "Period check. Leaves each night out in turn and asks whether the period found from the "
        "other nights predicts it. A failure lowers the period verdict one level."
    ),
    "alias_ambiguity": (
        "Period check. Fails when an alias of the best period (a false period created by "
        "observing only at night, once a day) is nearly as strong as the best peak. A failure "
        "lowers the period verdict one level."
    ),
    # Spectroscopy
    "spectra_extracted": "Fails when no star produced a spectrum.",
    "zero_order_saturation": (
        "Fails when any star's zero-order image (its undispersed point image beside the "
        "spectrum) holds a significant share of saturated pixels (pixels clipped at the "
        "sensor's maximum)."
    ),
    "spectral_classification": (
        "Fails when a classification is a poor match (best misfit, the relative RMS of Eq. 4, "
        "above `NO_GOOD_MATCH_RMS`) or ambiguous at the class level (misfit gap to the next "
        "reference below `AMBIGUOUS_RMS_GAP`)."
    ),
    "catalog_agreement": (
        "Fails when a measured spectral type is more than `DIFFERS_FROM_CATALOG_SUBTYPES` "
        "subtype steps from the type a catalog lists for the star."
    ),
    "feature_significance": (
        "Fails when a star's feature p-values (the chance that noise alone makes a feature this "
        "strong) could not be calibrated against control windows (stretches of its own spectrum "
        "used as a noise reference)."
    ),
    "resolution_measured": (
        "Not checked when no spectrum had its resolution (smallest wavelength detail it can "
        "separate) measured."
    ),
    "processing_quality": (
        "Fails when more than `PROCESSING_QUALITY_MAXIMUM_FAILED_FRACTION` of classified spectra "
        "fail a metric of the processing checkpoint (one of four quality checkpoints on each "
        "spectrum)."
    ),
    "wavelength_scale": (
        "Fails when the run's wavelength zero points (offsets of each spectrum's wavelength "
        "scale) scatter by more than `WAVELENGTH_SCALE_LIMIT_ANGSTROM` after correction."
    ),
    "gaia_xp_agreement": (
        "Fails when the median tilt of observed / Gaia XP spectra (low-resolution spectra from "
        "the Gaia space mission) is above `GAIA_XP_SLOPE_LIMIT_PERCENT_PER_1000_ANGSTROM`. Not "
        "checked with too few stars."
    ),
    # Asteroid detection
    "search_frames": (
        "Not checked when fewer frames could be searched than a track (a chain of detections of "
        "one object) needs, so no mover could have been found."
    ),
    "pointing_metadata": "Fails when any frame was left out for missing `RA`, `DEC` or `NAXIS` cards.",
    "ephemeris_cross_match": (
        "Fails when SkyBoT (the online service listing known solar-system bodies) could not be "
        "reached for confirmed movers. Not checked when no mover reached it."
    ),
    "unmatched_movers": (
        "Fails when a confirmed straight-line mover matched no known solar-system body: a lead "
        "worth a manual look."
    ),
    "track_residuals": (
        "Reports how the straight-line test went and whether it used measured or assumed position errors."
    ),
    # Stacking
    "gain_homogeneity": (
        "Frames taken at a less common gain (camera amplification) setting are set aside, which "
        "corrects the stack. Not checked when no frame records its gain."
    ),
    "frame_quarantine": (
        "Fails when a batch had more bad frames (too few stars, or stars not round) than "
        "`MAXIMUM_QUARANTINED_FRACTION`, so none were moved."
    ),
    "background_homogeneity": (
        "Fails when the frames of one exposure length split into groups with different sky "
        "background levels. Not checked when turned off or with too few measured frames."
    ),
    "flat_level": (
        "Fails when the level of the master flat (the averaged image of an evenly lit surface) "
        "is outside the allowed share of the sensor's full scale."
    ),
    "flat_noise": ("Fails when the master flat (the averaged image of an evenly lit surface) is too noisy."),
    "calibration_metadata": (
        "Fails when a calibration frame (dark, bias or flat) was applied with a different gain, "
        "offset or temperature, or refused for a binning mismatch."
    ),
    "calibration_frame_count": (
        "Fails when a master bias, dark or flat was built from fewer frames than the configured minimum."
    ),
    "rejected_pixel_fraction": (
        "Fails when the share of pixels rejected as outliers in the stack is above "
        "`DEFAULT_REJECTED_FRACTION_FLAG_THRESHOLD`."
    ),
    "stack_sharpness": (
        "Fails when the stack's star width (FWHM) over the expected width is above "
        "`DEFAULT_FWHM_DEGRADATION_RATIO` (Eq. 3), meaning the stack is blurrier than its inputs "
        "predict."
    ),
    "spectral_registration": (
        "Fails when the registration (alignment) check of a spectral stack raised a concern. "
        "Not checked for an imaging stack."
    ),
    "saturated_pixel_fraction": "Fails when the stack holds a significant share of saturated pixels.",
    "zero_pixel_fraction": "Fails when too many stack pixels are exactly zero (over-subtraction).",
    "negative_pixels": "Fails when the stacking engine (Siril) reports too high a share of negative pixels.",
    "exposure_group_linearity": (
        "Fails when the bright end of an exposure group (the frames of one exposure length) is "
        "not one scale factor of the reference group; that group is left out of the combination."
    ),
    "raw_frame_clipping": "Fails when raw frames of an exposure group are clipped at zero.",
    "exposure_groups_combined": (
        "Fails when an exposure group with frames is missing from the combined stack."
    ),
}

# What the guide calls each role in prose, singular and plural.
ROLE_WORDS: dict[str, tuple[str, str]] = {
    RUN_GATE_ROLE: ("run gate", "run gates"),
    "period check": ("period check", "period checks"),
}


def gate_count_text(gates: list[GateDefinition]) -> str:
    """Describe how many gates of each role a pipeline has.

    Parameters
    ----------
    gates : `list` [`GateDefinition`]
        The pipeline's gates.

    Returns
    -------
    text : `str`
        For example ``"11 run gates + 2 period checks"``.
    """
    counts: dict[str, int] = {}
    for gate in gates:
        counts[gate.role] = counts.get(gate.role, 0) + 1
    parts = []
    for role in sorted(counts, key=lambda r: (r != RUN_GATE_ROLE, r)):
        singular, plural = ROLE_WORDS.get(role, (role, role + "s"))
        parts.append(f"{counts[role]} {singular if counts[role] == 1 else plural}")
    return " + ".join(parts)


def gate_table(gates: list[GateDefinition]) -> str:
    """Format a pipeline's gates as a Markdown table.

    Parameters
    ----------
    gates : `list` [`GateDefinition`]
        The pipeline's gates.

    Returns
    -------
    table : `str`
        One row per gate: name, role, defining module and description.
    """
    rows = [
        [
            f"`{gate.name}`",
            gate.role,
            f"`{gate.relative_path}`",
            GATE_DESCRIPTIONS.get(gate.name, "*Not yet described.*"),
        ]
        for gate in gates
    ]
    return make_table(["Gate", "Role", "Defined in", "What it checks"], rows)


def format_docstring_summary(obj: Any) -> str:
    """Extract and format the top summary lines of a docstring.

    Parameters
    ----------
    obj : `Any`
        The object whose docstring will be extracted.

    Returns
    -------
    summary : `str`
        Cleaned summary paragraph or an empty string.
    """
    doc = inspect.getdoc(obj)
    if not doc:
        return ""
    paragraphs = doc.split("\n\n")
    return paragraphs[0].strip().replace("\n", " ")


def make_table(headers: list[str], rows: list[list[str]]) -> str:
    """Format a list of rows as a Markdown table.

    Parameters
    ----------
    headers : `list` [`str`]
        Column headers.
    rows : `list` [`list` [`str`]]
        Table rows.

    Returns
    -------
    table : `str`
        Formatted Markdown table.
    """
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join([":---"] * len(headers)) + " |",
    ]
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def fill(template: str, values: dict[str, Any]) -> str:
    """Replace each ``<<key>>`` placeholder in a template.

    Parameters
    ----------
    template : `str`
        Text with ``<<key>>`` placeholders. Placeholders avoid braces, which
        LaTeX uses.
    values : `dict` [`str`, `Any`]
        The value for each key. Floats are written with ``:g``.

    Returns
    -------
    text : `str`
        The filled-in text.

    Raises
    ------
    KeyError
        If a placeholder has no value.
    """
    missing = sorted(set(re.findall(r"<<(\w+)>>", template)) - set(values))
    if missing:
        raise KeyError(f"no value for placeholder(s): {', '.join(missing)}")

    def _replace(match: re.Match[str]) -> str:
        """Format the value of one placeholder.

        Parameters
        ----------
        match : `re.Match` [`str`]
            The placeholder match.

        Returns
        -------
        text : `str`
            The value as text.
        """
        value = values[match.group(1)]
        return f"{value:g}" if isinstance(value, float) else str(value)

    return re.sub(r"<<(\w+)>>", _replace, template)


# Curated descriptions for fields that lack Field(description=...) in models
KNOWN_FIELD_DESCRIPTIONS: dict[str, str] = {
    "pipeline_name": "Which pipeline made this summary (for example astrometry or photometry).",
    "pipeline_version": "Version number of the pipeline's processing rules.",
    "target_id": "Identifier of the target (the sky object being studied).",
    "target_session_ids": "Identifiers of the observing sessions (usually one night each) used in the run.",
    "target_session_breakdown": "For each session, how many frames it had and how many the run used.",
    "upstream_quality_summary_reference": "Name of the pipeline that had to run first, if any.",
    "resolved_parameters": "The settings the run actually used, after defaults were filled in.",
    "camera_profile": "The camera settings applied, such as gain and the level at which pixels clip.",
    "quality_processing_applied": "Whether the quality gates and checks were run.",
    "flagged": "True if one or more quality gates failed, so a person should look at the run.",
    "flag_reasons": "One plain sentence for each reason the run was flagged.",
    "gates": "Every quality gate result: passed, failed or not checked.",
    "created_at": "Time the quality summary was made, in UTC.",
    "provenance_activity_id": (
        "Identifier of this run in the provenance record (the IVOA-standard log of which run made which "
        "data)."
    ),
    "upstream_entity_id": "Identifier of the exact database record this run read as its input.",
    "sources_detected": "Number of star-like sources found above the sky background threshold.",
    "solve_attempted": "Whether a plate solve (finding the pixel-to-sky map) was tried.",
    "plate_solve_succeeded": "Whether the image ended the run with a WCS (pixel-to-sky map).",
    "simbad_matched_count": (
        "Number of detected stars matched to an entry in SIMBAD (an online database of named objects)."
    ),
    "catalog_match_separation_rms_arcsec": (
        "Typical (RMS) distance, in arcsec, from each star to its nearest SIMBAD or Gaia DR3 catalog star."
    ),
    "plate_solve_fit_residual_rms_arcsec": (
        "The Astrometry.net solver's own measure of its fit error, in arcsec."
    ),
    "plate_solve_matched_star_count": "Number of stars the solver used to build the WCS (pixel-to-sky map).",
    "astrometry_flags": "Warnings about conditions that limit the accuracy of the sky positions.",
    "plate_scale_arcsec_per_pixel": "Plate scale: the sky angle one pixel covers, in arcsec.",
    "star_fwhm_px": "Median star width (FWHM, full width at half maximum) across the image, in pixels.",
    "remote_catalog_queries_attempted": "Number of online catalog requests started.",
    "remote_catalog_queries_failed": "Number of online catalog requests that timed out or failed.",
    "remote_catalog_circuit_breaker_tripped": (
        "True if repeated network failures switched off further online catalog requests."
    ),
    "plate_solve_attempts": "Number of plate-solve tries.",
    "stars_processed": "Number of stars tracked through the light curves (brightness over time).",
    "stars_found": "Number of reference stars found in the reference exposure.",
    "frames_processed": "Number of calibrated frames measured.",
    "rejected_frames": "Frames rejected because their comparison-star level was out of line with the rest.",
    "frame_ensemble_composition": "For each frame, which comparison stars were used.",
    "variable_candidate_count": "Number of stars flagged as short-term variable candidates.",
    "light_curve_scatter_rms_mag": (
        "Median scatter (RMS, in magnitudes) of the light curves of non-variable field stars."
    ),
    "cross_session_match_count": "Number of stars matched across sessions from different nights.",
    "noise_model_curve": (
        "The noise model: typical measured scatter (RMS) against instrumental magnitude, in brightness bins."
    ),
    "sessions_missing_wcs": "Sessions with no plate-solved WCS (pixel-to-sky map).",
    "long_term_variable_candidate_count": "Number of stars flagged as long-term variable candidates.",
    "astrometry_identified_star_count": "Number of stars matched to catalog stars from the astrometry seed.",
    "sessions_with_reused_header_wcs": (
        "Sessions whose existing header WCS (pixel-to-sky map) was checked and used."
    ),
    "sessions_with_replaced_header_wcs": "Sessions whose WCS (pixel-to-sky map) was worked out again.",
    "zero_order_saturated_pixel_fraction": (
        "Share of saturated (clipped) pixels in the zero order (the star's undispersed point image beside"
        " its spectrum)."
    ),
    "zero_order_saturation_flagged": (
        "True if the zero order went beyond the range where the sensor responds in proportion to light."
    ),
    "dispersion_angle_deg": (
        "Angle of the spectra (the direction light is spread by colour) relative to the sensor rows."
    ),
    "trail_width_profile_available": "Whether the width profile across the spectrum trail was measured.",
    "median_trail_width_px": "Median Gaussian sigma (not FWHM) of the trail cross-section, in pixels.",
    "poor_match_classification_count": (
        "Number of stars whose classification misfit (RMS) is above the rejection limit."
    ),
    "ambiguous_classification_count": (
        "Number of stars whose classification is ambiguous between spectral classes."
    ),
    "flagged_spectral_classifications": "Details of each star whose classification is in doubt.",
    "stage_quality_summary": "Summary of the quality checks at each extraction stage (checkpoint).",
    "gaia_xp_summary": (
        "Comparison with Gaia DR3 synthetic XP spectra (low-resolution spectra from the Gaia space mission)."
    ),
    "wavelength_scale_summary": (
        "Scatter and drift of the wavelength zero points (offsets of the wavelength scale)."
    ),
    "frames_with_wcs_estimate": (
        "Frames given a WCS (pixel-to-sky map) from the mount's RA/DEC and the stack's WCS."
    ),
    "frames_excluded_missing_pointing_metadata": "Frames left out for missing RA, DEC or NAXIS header cards.",
    "candidates_detected": "Total chains of detections (one object linked across frames), before any test.",
    "candidates_persistence_confirmed": "Chains seen in at least `min_frames_for_persistence` frames.",
    "candidates_rate_linearity_confirmed": "Candidates whose apparent motion is a straight line.",
    "candidates_ephemeris_matched": (
        "Candidates matched to known asteroids by SkyBoT (an online list of known solar-system bodies)."
    ),
    "ephemeris_queries_attempted": "Number of SkyBoT requests sent.",
    "ephemeris_queries_failed": "Number of SkyBoT requests that failed or timed out.",
    "is_spectral": (
        "True if the image is a slitless spectrum image (light spread by a grating) rather than a normal "
        "image."
    ),
    "frames_submitted": "Number of frames offered for calibration and stacking.",
    "frames_stacked": "Number of light frames (exposures of the target) aligned and stacked.",
    "excluded_frames": "Input frames set aside before stacking, each with its reason.",
    "rejected_pixel_fraction": "Share of pixel values rejected (clipped) while building the stack.",
    "rejected_fraction_flagged": "True if pixel rejection was above the warning threshold.",
    "background_split_detected": "True if a gradient or a jump in the sky background was found.",
    "background_split_detail": "Description of the sky-background jump.",
    "calibration_mismatch_flags": (
        "Soft flags for calibration frames applied with a different gain, offset or temperature."
    ),
    "flat_frame_count": "Number of flat exposures (images of an evenly lit surface) in the master flat.",
    "flat_noise_fraction": "Relative noise level measured in the master flat.",
    "flat_smoothing_sigma_px": (
        "Gaussian smoothing (sigma, in pixels) applied to the master flat if it was noisy."
    ),
    "flat_calibration_issues": "Messages about problems with the flat calibration.",
    "saturated_pixel_fraction": (
        "Share of saturated pixels (clipped at the sensor's maximum) in the final stack."
    ),
    "saturation_flagged": "True if the stack holds a significant share of saturated pixels.",
    "exposure_groups": "One sub-stack per exposure length, with its details.",
    "recommended_exposure_seconds": "Estimated exposure, in seconds, that keeps bright stars below clipping.",
    "zero_pixel_fraction": "Share of stack pixels that are exactly zero.",
    "zero_fraction_flagged": "True if over-subtraction clipped pixels at zero ADU (zero camera counts).",
    "negative_pixel_max_percent": "Largest percentage of negative pixels the stacking engine reported.",
    "negative_pixels_flagged": "True if the count of negative pixels was above the tolerance.",
    "stacked_fwhm_px": "Measured star width (FWHM, full width at half maximum) of the stacked image.",
    "median_input_fwhm_px": "Median star width (FWHM) of all light frames that were kept.",
    "expected_fwhm_px": "Predicted stack star width (FWHM): the RMS of a sample of input-frame FWHMs.",
    "fwhm_degradation_ratio": "Measured stack FWHM over the expected FWHM; 1 means no extra blur.",
    "fwhm_degradation_flagged": "True if the final image is clearly blurrier than its inputs.",
    "stacking_engine": "Program that did the pixel processing (for example Siril).",
    "stacking_engine_version": "Version of the stacking program.",
    "registration_reference_frame": "File name of the frame the others were aligned (registered) to.",
    "registration_reference_star_count": "Number of stars found in that frame to align on.",
    "astrometry_metrics": "The astrometry measurements (Section 7.2).",
    "photometry_metrics": "The photometry measurements (Section 7.2).",
    "spectroscopy_metrics": "The spectroscopy measurements (Section 7.2).",
    "asteroid_detection_metrics": "The asteroid-detection measurements (Section 7.2).",
    "stacking_metrics": "The stacking measurements (Section 7.2).",
    "catalog_matched_star_count": "Stars matched to a SIMBAD or Gaia DR3 entry.",
    "position_only_star_count": "Stars named only by their sky position.",
    "unresolved_star_count": "Stars with no catalog match and no sky position; not saved.",
    "gaia_fit_rms_arcsec": "Typical (RMS) distance to Gaia DR3 stars under the kept solution.",
    "gaia_fit_rms_before_arcsec": (
        "Typical (RMS) distance to the same Gaia stars under the solver's solution."
    ),
    "gaia_fit_star_count": "Stars used in the Gaia refit.",
    "wcs_refinement_shift_arcsec": "Largest move of the field made by the Gaia refit.",
    "expected_stack_fwhm_px": "Expected stack star width (FWHM): the RMS of a sample of input-frame FWHMs.",
    "fwhm_degraded": (
        "True if the stack FWHM over the expected FWHM is above the limit (the stack is blurrier than "
        "predicted)."
    ),
    "spectral_registration_flags": (
        "Frames the spectral registration (alignment) check raised a concern about."
    ),
    "spectral_registration_checked": "Whether the spectral registration (alignment) check ran.",
    "stacking_duration_seconds": "How long the stacking run took, in seconds.",
    "timed_out": "True if the stacking run timed out.",
    "debayer_applied": (
        "Whether colour frames were debayered (turned from the sensor's colour-filter mosaic into full-"
        "colour pixels)."
    ),
    "input_quality": "Structured assessment of the raw frames' quality.",
    "output_quality": "Structured assessment of the final stack's quality.",
}


def get_model_fields_table(model_cls: Any) -> str:
    """Format Pydantic model fields as a Markdown table.

    Parameters
    ----------
    model_cls : `Any`
        Pydantic model class.

    Returns
    -------
    table : `str`
        Markdown table of field names, types, and descriptions.
    """
    if not hasattr(model_cls, "model_fields"):
        return "*No Pydantic model fields available.*"

    headers = ["Field Name", "Type", "Description"]
    rows: list[list[str]] = []
    for name, field_info in model_cls.model_fields.items():
        type_str = str(field_info.annotation).replace("typing.", "")
        type_str = type_str.replace("<class '", "").replace("'>", "")
        type_str = "`" + type_str.replace("|", "\\|") + "`"
        desc = field_info.description or KNOWN_FIELD_DESCRIPTIONS.get(name) or "Quality metric attribute."
        desc = desc.replace("\n", " ").replace("|", "\\|").strip()
        rows.append([f"`{name}`", type_str, desc])
    return make_table(headers, rows)


def get_symbol(module_path: str, symbol_name: str) -> Any:
    """Dynamically resolve a symbol from a module path.

    Parameters
    ----------
    module_path : `str`
        The dotted module path.
    symbol_name : `str`
        The name of the attribute or class.

    Returns
    -------
    symbol : `Any`
        The resolved object or None.
    """
    try:
        mod = importlib.import_module(module_path)
        return getattr(mod, symbol_name)
    except ImportError, AttributeError:
        return None


def require_symbol(module_path: str, symbol_name: str) -> Any:
    """Resolve a symbol that a guide quotes, failing loudly if it is gone.

    Parameters
    ----------
    module_path : `str`
        The dotted module path.
    symbol_name : `str`
        The name of the attribute or class.

    Returns
    -------
    symbol : `Any`
        The resolved object.

    Raises
    ------
    LookupError
        If the module or the name no longer exists, so the guide would quote
        a value the code does not have.
    """
    module = importlib.import_module(module_path)
    if not hasattr(module, symbol_name):
        raise LookupError(f"{module_path}.{symbol_name} no longer exists; update the review guide generator")
    return getattr(module, symbol_name)


def default_of(function: Any, parameter: str) -> Any:
    """Read the default value of one parameter of a function.

    Parameters
    ----------
    function : `Any`
        The function or method.
    parameter : `str`
        The parameter name.

    Returns
    -------
    default : `Any`
        The parameter's default value.
    """
    return inspect.signature(function).parameters[parameter].default


def parameter_table(rows: list[list[str]]) -> str:
    """Format the tuned-parameter rows of a guide.

    Parameters
    ----------
    rows : `list` [`list` [`str`]]
        One row per parameter: name, live value, status and notes.

    Returns
    -------
    table : `str`
        The Markdown table.
    """
    return make_table(["Parameter", "Live Value", "Empirical Status", "Notes"], rows)


def header_table(rows: list[list[str]]) -> str:
    """Format the FITS-header rows of a guide.

    Parameters
    ----------
    rows : `list` [`list` [`str`]]
        One row per keyword: keyword, requirement, type, use and fallback.

    Returns
    -------
    table : `str`
        The Markdown table.
    """
    return make_table(
        ["FITS Keyword", "Requirement", "Data Type", "What It Is Used For", "When Missing"], rows
    )


def quality_section(summary_name: str, metrics_name: str) -> str:
    """Build section 7, the quality-summary field tables.

    Parameters
    ----------
    summary_name : `str`
        The quality-summary class in `astrometricslib.models.quality_summary`.
    metrics_name : `str`
        The metrics class in the same module.

    Returns
    -------
    section : `str`
        The Markdown of section 7.
    """
    module = "astrometricslib.models.quality_summary"
    envelope = get_model_fields_table(require_symbol(module, summary_name))
    metrics = get_model_fields_table(require_symbol(module, metrics_name))
    return (
        "## 7. Quality Summary Data Models\n\n"
        "Every run saves a quality summary: a record of what the run measured and how "
        "each quality gate came out. Section 7.1 lists the fields every pipeline shares. "
        "Section 7.2 lists the measurements that belong to this pipeline. The tables are "
        "read from the data models in the code.\n\n"
        f"### 7.1 Pipeline Execution Envelope (`{summary_name}`)\n\n{envelope}\n\n"
        f"### 7.2 Domain Scientific Metrics Schema (`{metrics_name}`)\n\n{metrics}\n"
    )


ANALYSIS_STAGES_NOTE = """`run_pipeline` (`pipeline_base.py`) calls the adapter's four methods in
order. It skips `run` only when `process_input` returns a `Result` with
`has_work=False`. After `validate_output` it stores the quality summary on the
target and records the run's provenance."""

# The relative link from a guide to the shared glossary.
GLOSSARY_LINK = "../Astrometrics_Library_Architecture.md#appendix-b-glossary"

# The sentence near the top of every guide that points to the glossary.
GLOSSARY_LINE = (
    f"Terms are defined in the [glossary]({GLOSSARY_LINK}). This guide also defines each term "
    "where it first appears, so it can be read on its own."
)

# Words that open section 2 of every guide, before the header table.
FITS_INTRO = """A FITS file (the standard file format for astronomy images) starts with a
header: a list of named cards, each a keyword and a value, that describe the
exposure. The table lists the cards this pipeline reads. "When Missing" says
what the pipeline does if a card is absent."""

# Words that open section 4.1 of every guide, before the gate table.
GATE_INTRO = """A quality gate is an automatic check on a run. It passes, fails, or is
"not checked" when the data it needs are missing. A failed gate marks the run
as flagged (the `flagged` field in Section 7) so a person can look at it. The
list below is read from the code when this guide is generated."""

# Words that open section 5 of every guide, before the parameter table.
PARAM_INTRO = """These are the fixed limits and settings the pipeline uses. "Live Value" is
read from the code when this guide is generated. "Empirical Status" says where
the value came from: measured on real data (the data set is named in
brackets), or set by design."""

# Placeholder values shared by every guide.
COMMON_VALUES: dict[str, str] = {
    "version_line": VERSION_LINE,
    "glossary_line": GLOSSARY_LINE,
    "fits_intro": FITS_INTRO,
    "gate_intro": GATE_INTRO,
    "param_intro": PARAM_INTRO,
}


def build_astrometry_doc() -> str:
    """Build the Astrometry Pipeline review documentation.

    Returns
    -------
    content : `str`
        Markdown content for astrometry.md.
    """
    gates = discover_gate_names("astrometry")
    run_gates_module = "astrometricslib.pipelines.astrometry.post_processing.run_gates"
    identifier_module = "astrometricslib.pipelines.astrometry.processing.star_identifier"
    refine_module = "astrometricslib.pipelines.astrometry.processing.wcs_refinement"
    detector = require_symbol(
        "astrometricslib.pipelines.astrometry.pre_processing.source_detection", "SourceDetector"
    )
    values = {
        "version_line": VERSION_LINE,
        "max_res": require_symbol(run_gates_module, "MAXIMUM_RESIDUAL_FRACTION_OF_FWHM"),
        "min_match": require_symbol(run_gates_module, "MINIMUM_MATCHED_STARS"),
        "max_fail": require_symbol(run_gates_module, "MAXIMUM_FAILED_LOOKUP_FRACTION"),
        "match_radius": require_symbol(
            "astrometricslib.pipelines.shared.catalog_star_identity", "CATALOG_MATCH_RADIUS_ARCSEC"
        ),
        "solver_sources": require_symbol(identifier_module, "MAXIMUM_PLATE_SOLVE_SOURCES"),
        "gaia_epoch": require_symbol(identifier_module, "GAIA_DR3_REFERENCE_EPOCH"),
        "threshold": float(default_of(detector.__init__, "threshold_sigma")),
        "refit_min_stars": require_symbol(refine_module, "MINIMUM_MATCHED_STARS"),
        "refit_sip": require_symbol(refine_module, "SIP_DEGREE"),
        "refit_clip": require_symbol(refine_module, "CLIP_SIGMAS"),
        "refit_shift": require_symbol(refine_module, "MAXIMUM_SHIFT_ARCSEC"),
        "refit_r1": require_symbol(refine_module, "INITIAL_MATCH_RADIUS_PIXELS"),
        "refit_r2": require_symbol(refine_module, "REFINED_MATCH_RADIUS_PIXELS"),
        "gate_count": gate_count_text(gates),
        "gate_table": gate_table(gates),
    }
    values["fits_table"] = header_table([
        [
            "`DATE-OBS`",
            "Recommended",
            "`str` (ISO-8601, UTC)",
            "Time the shutter opened. Used to move Gaia DR3 star positions from the catalog "
            "date to the image date (Eq. 3).",
            "Gaia positions stay at the catalog date (epoch 2016.0) and the run is flagged.",
        ],
        [
            "`FOCALLEN`",
            "Recommended",
            "`float` (mm)",
            "Telescope focal length, used for the pixel-scale hint (Eq. 2).",
            "The focal length set in the configuration.",
        ],
        [
            "`XPIXSZ` / `YPIXSZ`",
            "Recommended",
            "`float` (μm)",
            "Pixel size after binning (binning joins neighbouring sensor pixels into one), "
            "used as written. If the two values differ, they are averaged and the run is flagged.",
            "`PIXSIZE1` / `PIXSIZE2` times `XBINNING` / `YBINNING`.",
        ],
        [
            "`PIXSIZE1` / `PIXSIZE2`",
            "Optional",
            "`float` (μm)",
            "Pixel size before binning; multiplied by the binning factor.",
            "`PIXSCAL`.",
        ],
        [
            "`XBINNING` / `YBINNING`",
            "Optional",
            "`int`",
            "Binning factor: how many sensor pixels are joined along each axis. Applied to "
            "`PIXSIZE1` / `PIXSIZE2`.",
            "1.",
        ],
        [
            "`PIXSCAL`",
            "Optional",
            "`float` (arcsec/px)",
            "Pixel scale (the sky angle one pixel covers), read only when no pixel size is given.",
            "No scale hint: the solver searches all scales.",
        ],
        [
            "`RA` / `DEC` (or `OBJCTRA` / `OBJCTDEC`)",
            "Optional",
            "`float` or sexagesimal `str` (base-60, such as hours, minutes, seconds)",
            "Where the telescope pointed, as right ascension and declination (the sky's longitude "
            "and latitude). A search hint when the target record has no coordinates.",
            "No position hint.",
        ],
        [
            "`OBJECT`",
            "Optional",
            "`str`",
            "Target name, used for the extended-source hint (whether the target is spread out, "
            "like a galaxy, rather than a point).",
            "Guessed from the file name.",
        ],
    ])
    values["param_table"] = parameter_table([
        [
            "`MAXIMUM_RESIDUAL_FRACTION_OF_FWHM`",
            f"`{values['max_res']}`",
            "Design estimate",
            "Largest allowed residual (position error of the sky map), as a share of a star's "
            "width (FWHM). 8 saved solves gave 0.14 to 0.44.",
        ],
        [
            "`MINIMUM_MATCHED_STARS` (`run_gates`)",
            f"`{values['min_match']}`",
            "Design estimate",
            "Fewest stars matched to a catalog for the residual to mean anything.",
        ],
        [
            "`MAXIMUM_FAILED_LOOKUP_FRACTION`",
            f"`{values['max_fail']}`",
            "Design estimate",
            "Share of failed online catalog lookups at which `catalog_lookup` fails.",
        ],
        [
            "`CATALOG_MATCH_RADIUS_ARCSEC`",
            f"`{values['match_radius']}` arcsec",
            "Designed",
            "Largest distance at which a detected star is paired with a SIMBAD or Gaia DR3 star.",
        ],
        [
            "`MAXIMUM_PLATE_SOLVE_SOURCES`",
            f"`{values['solver_sources']}`",
            "Designed",
            "How many of the brightest detected stars are sent to the solver.",
        ],
        [
            "Solve minimum",
            "4 sources",
            "Designed",
            "Fewest detections for a solve to be tried (`process_image`).",
        ],
        [
            "`SourceDetector` threshold",
            f"`{values['threshold']:g}` σ",
            "Designed",
            "How far a peak must stand above the sky, in standard deviations of the sky noise (Eq. 1).",
        ],
        [
            "`MINIMUM_MATCHED_STARS` (`wcs_refinement`)",
            f"`{values['refit_min_stars']}`",
            "Design value",
            "Fewest Gaia star matches needed for the refit with distortion terms (SIP).",
        ],
        [
            "`MAXIMUM_SHIFT_ARCSEC`",
            f"`{values['refit_shift']}` arcsec",
            "Designed",
            "Largest move of the whole field a refit may make and still be kept.",
        ],
    ])
    template = r"""# Astrometry and Plate Solving Pipeline Review Guide

<<version_line>>

## Abstract

This document reviews the plate solving and astrometric calibration pipeline
(`astrometricslib.pipelines.astrometry`). It covers the header cards the
pipeline reads, its detection and solving methods, the Gaia DR3 refit of the
solution, how stars are named, the run flow, the tuned limits, the fallback
paths and the quality-summary fields.

<<glossary_line>>

## 1. Overview & Scientific Objective

### 1.1 In Plain Words

A telescope image shows stars, but it does not say where on the sky it
points. This pipeline works that out. For every pixel it finds the matching
sky direction, given as right ascension and declination (the sky's longitude
and latitude). Finding this pixel-to-sky map is called a plate solve.

The image carries no labels, so the map must come from the stars themselves.
The mount's reported pointing can be off, the optics bend the field slightly,
and stars drift slowly across the sky over the years. The pipeline finds the
stars in the image and matches their pattern against a star catalog. It then
fine-tunes the map against Gaia DR3 (the third data release of the Gaia
space mission, the most accurate star positions available).

The result is the map, written into the image file, a name for each star, and
a quality summary that says how accurate the map is.

### 1.2 Statement of Need

Astrometry (the measurement of star positions) finds the World Coordinate
System (WCS) of an image: the map from detector pixels $(x, y)$ to sky
coordinates $(\alpha, \delta)$ in the International Celestial Reference
System (ICRS, the standard sky coordinate frame). Photometry needs it to match
stars across sessions. Spectroscopy needs it to name the stars whose spectra
it extracts. Asteroid detection needs it to place every frame on the sky.

The diagram shows one run, top to bottom.

```mermaid
flowchart TD
    A["Image file (usually the target's stacked image)"] --> B["Find stars against the sky background"]
    B --> C["Gather scale and position hints (image file, then settings)"]
    C --> D{"At least 4 stars found?"}
    D -- yes --> E["Match the star pattern to a catalog (Astrometry.net)"]
    D -- no --> F["Use the sky map already in the file, if any"]
    E --> G["Fine-tune the map against Gaia star positions"]
    G --> H["Name stars: SIMBAD, then Gaia, then sky position"]
    F --> H
    H --> I["Quality checks"]
    I --> J["Quality summary"]
```

Astrometry.net is an open-source plate solver. SIMBAD is an online database of
named stars and other objects. The fine-tuning step fits SIP distortion terms
of order 2 (Section 3.3), and the quality summary is an
`AstrometryQualitySummary` (Section 7).

## 2. Input Requirements & FITS Header Specification

<<fits_intro>>

No card is strictly required: a missing card weakens a hint or a correction,
and the run says so in its flags.

<<fits_table>>

The pixel-scale hint is the scale from Eq. 2 with a window of ±5%. The solver
driver widens that window to 0.8 times its lower edge and 1.2 times its upper
edge before calling `solve-field`. With no focal length or no pixel size, no
scale hint is sent.

## 3. Mathematical & Algorithmic Formulation

This section covers, in order: finding stars, estimating how much sky a pixel
covers, solving and refining the map, moving catalog stars to the image date,
naming stars, and judging the map's accuracy.

### 3.1 Point Source Detection

A point source is anything in the image that looks like a single star. To find
them, the pipeline first estimates the sky background: the faint glow behind
the stars. The background is a 2-D map (photutils `Background2D`, median
estimator, 3σ clipping). Sigma clipping repeatedly drops pixels more than
3 standard deviations from the median, so stars do not inflate the sky
estimate. A small image falls back to one sigma-clipped median.

The test below asks how many noise units a pixel stands above the local sky.
A large value means a real source; a value near zero means only sky. A source
is kept when its peak stands above the background $B$ by the threshold $k$,
where $\sigma_B$ is the noise of the background:

$$\frac{I(x, y) - B(x, y)}{\sigma_B} \ge k = <<threshold>> \tag{1}$$

`DAOStarFinder` (a standard star finder from photutils) also checks shape.
Sharpness compares a peak's height with its spread; roundness compares its
width along the two axes, with 0 meaning round. It keeps sharpness in
$[0.2, 1.0]$ and roundness in $[-0.6, +0.6]$, which rejects hot pixels
(single pixels that always read high) and filament structure.

The finder looks for blobs of a set width, the kernel width. That width is the
measured blob width: the second moment (the spread) of the light in a
30-pixel box around the brightest sources. It counts faint wings and
neighbours, so it reads about twice the stellar FWHM (full width at half
maximum: the width of a star's light profile at half its peak height).
Detections closer than 15 px are merged. Colour frames are block-averaged
before detection.

### 3.2 Pixel-Scale Hint

The plate scale is the sky angle one pixel covers. A longer focal length or a
smaller pixel gives a smaller (finer) scale. The solver runs much faster when
it knows the scale in advance:

$$s = 206.265 \, \frac{p}{f} \tag{2}$$

where $s$ is the scale in arcsec per pixel (1 arcsec is 1/3600 of a degree),
$p$ the binned pixel size in μm and $f$ the focal length in mm.

### 3.3 Plate Solve and Gaia DR3 Refit

**What it does.** The brightest <<solver_sources>> detections go to
Astrometry.net. The solver fits only the bright stars of its index files, so
its solution can be a pixel or more off. The pipeline then refits the
solution to the field's Gaia DR3 stars (`wcs_refinement.py`).

**How.** Each detection is matched to the nearest Gaia star within
<<refit_r1>> px, then <<refit_r2>> px on later passes. The map is fitted with
`astropy.wcs.utils.fit_wcs_from_points` as a tangent-plane solution (the sky
projected onto a flat plane touching it at the field center) plus SIP
distortion of order <<refit_sip>>. SIP (Simple Imaging Polynomial) adds small
polynomial terms that model the bending of the field by the optics. Stars more
than <<refit_clip>> robust standard deviations off are dropped, and the fit
repeats until the matched set stops changing. A robust standard deviation is a
measure of spread that a few wild points cannot inflate.

**When it is kept.** The refit needs at least <<refit_min_stars>> matches. It
is kept only when it fits Gaia better than the solver's solution and moves the
field by at most <<refit_shift>> arcsec.

### 3.4 Proper Motion Epoch Propagation

Stars move slowly across the sky; this drift is their proper motion. Gaia DR3
gives each position at one date, the epoch <<gaia_epoch>>. Before matching,
each position is moved to the image time with astropy's
`SkyCoord.apply_space_motion`. The move grows with the star's speed and with
the years since 2016. To first order it is

$$\alpha(t) = \alpha_0 + \frac{\mu_{\alpha*} \, \Delta t}{\cos \delta_0}, \qquad
\delta(t) = \delta_0 + \mu_\delta \, \Delta t, \qquad \Delta t = t_\text{obs} - 2016.0 \tag{3}$$

where $\mu_{\alpha*}$ is Gaia's `pmra` (which already includes
$\cos \delta$) and $\mu_\delta$ is `pmdec`.

### 3.5 Star Naming

With a solution, each star is named from SIMBAD first, within
<<match_radius>> arcsec. Stars SIMBAD does not know are matched against a
Gaia DR3 cone (all catalog stars within a circle around the field). The
cone's faint limit is set from the frame's own zero point (the brightness
that one count per second corresponds to, which sets how faint the frame
reaches). Stars neither catalog knows get a position-based name such as
`FIELD_J123.4+45.6`.

Separations are great-circle distances (the shortest angle between two points
on the sky) from `SkyCoord.separation`. The catalog match separation is the
typical distance between a detected star and its catalog match. Small means
the map is accurate:

$$\text{RMS}_\text{sep} = \sqrt{\frac{1}{N} \sum_{i=1}^N \Delta \theta_i^2} \tag{4}$$

over the $N$ stars matched within the radius. RMS (root mean square) is the
square root of the mean squared distance.

### 3.6 Residual Gate

The residual $r$ (arcsec) is the map's typical position error. A position
error matters in proportion to the size of a star in the image, so the gate
divides it by the star width in arcsec. A ratio of 0.1 means the error is a
tenth of a star width; above <<max_res>> the map is too loose:

$$\frac{r}{s \cdot \text{FWHM}} \le <<max_res>> \tag{5}$$

The residual is the first of these that was measured: the RMS against Gaia
from the refit, then the solver's own fit residual, then the catalog match
separation of Eq. 4. The gate detail names the one used.

## 4. Processing Flow

`run_astrometry_analysis` (`runner.py`) runs `AstrometryPipelineAdapter`
through `run_pipeline`. <<analysis_note>>

- **Stage 1 (`process_input`):** A no-op that returns an empty `Result`.
  `analyze_target` has already checked that an image path exists.
- **Stage 2 (`run`):** Runs `AstrometryPipeline.process` (detection, solve,
  refit, header-WCS fallback, naming). It writes the solved WCS into the FITS
  header, fills in the target's RA/Dec when they were unset, and saves the
  stars to the shared catalog.
- **Stage 3 (`validate_output`):** Builds the `AstrometryQualitySummary`. The
  runner records `plate_solve`, then `astrometry_run_gates` adds the rest
  (<<gate_count>> in all).
- **Stage 4 (`to_result_dict`):** Returns `context`, `stellar_objects`, `wcs`
  and `image_stats`.

### 4.1 Quality Gates

<<gate_intro>>

<<gate_table>>

## 5. Tuned Calibration Parameters

<<param_intro>>

<<param_table>>

## 6. Degraded States & Fallback Pathways

1. **Missing optical metadata.** `FOCALLEN` falls back to the configured focal
   length. Pixel size falls back from `XPIXSZ` to `PIXSIZE1` times
   `XBINNING`, then to `PIXSCAL`. With none of these, the solve runs with no
   scale hint.
2. **Too few detections (fewer than 4).** No solve is tried. A celestial WCS
   already in the image header is used instead. Without one, `plate_solve`
   fails.
3. **Solver failure.** A tried solve that finds no solution raises
   `PlateSolveFailedError`, and the adapter does not catch it.
4. **No Gaia stars.** The refit is skipped with a flag and the solver's
   solution is kept. Without `DATE-OBS`, or with a cache that has no proper
   motions, Gaia positions stay at epoch 2016.0 with a flag.
5. **Catalog outages.** Repeated failures trip a process-wide circuit
   breaker: a shared switch that stops further online catalog queries.
   Unnamed stars keep position-based names, and `catalog_lookup`
   reports the outage.

<<quality_section>>"""
    values["analysis_note"] = ANALYSIS_STAGES_NOTE
    values["quality_section"] = quality_section(
        "AstrometryQualitySummary", "AstrometryPipelineQualityMetrics"
    )
    return fill(template, {**COMMON_VALUES, **values})


def build_photometry_doc() -> str:
    """Build the Photometry Pipeline review documentation.

    Returns
    -------
    content : `str`
        Markdown content for photometry.md.
    """
    gates = discover_gate_names("photometry")
    run_gates_module = "astrometricslib.pipelines.photometry.post_processing.run_gates"
    ensemble_module = "astrometricslib.pipelines.photometry.processing.comparison_ensemble"
    indices_module = "astrometricslib.pipelines.photometry.processing.variability_indices"
    analyzer_module = "astrometricslib.pipelines.photometry.processing.variability_analyzer"
    aperture = require_symbol(
        "astrometricslib.pipelines.photometry.pre_processing.frame_photometry", "measure_aperture_photometry"
    )
    values = {
        "version_line": VERSION_LINE,
        "rej_frac": require_symbol(run_gates_module, "MINIMUM_ENSEMBLE_REJECTION_FRACTION_TO_FLAG"),
        "rej_cnt": require_symbol(run_gates_module, "MINIMUM_ENSEMBLE_REJECTION_COUNT_TO_FLAG"),
        "max_amp": require_symbol(run_gates_module, "MAXIMUM_DETECTABLE_AMPLITUDE_MAG"),
        "corner": require_symbol(run_gates_module, "MAXIMUM_CORNER_DISPLACEMENT_PX"),
        "ens_scatter": require_symbol(run_gates_module, "MAXIMUM_ENSEMBLE_SCATTER_OVER_PREDICTED"),
        "scatter_pop": require_symbol(run_gates_module, "MINIMUM_STARS_FOR_SCATTER_POPULATION"),
        "drift": require_symbol(
            "astrometricslib.pipelines.photometry.pre_processing.assess_input_quality",
            "UNSTABLE_TRACKING_DRIFT_PX",
        ),
        "min_comp": require_symbol(ensemble_module, "MINIMUM_COMPARISON_STARS"),
        "max_comp": require_symbol(ensemble_module, "DEFAULT_MAXIMUM_COMPARISON_STARS"),
        "noise_model_stars": require_symbol(indices_module, "MINIMUM_STARS_FOR_NOISE_MODEL"),
        "fdr": require_symbol(indices_module, "FALSE_DISCOVERY_LEVEL"),
        "excess": require_symbol(indices_module, "EXCESS_SCATTER_THRESHOLD"),
        "cal_sigmas": require_symbol(indices_module, "CALIBRATION_SIGMAS"),
        "cv_sigma": require_symbol(analyzer_module, "DEFAULT_VARIABILITY_SIGMA_THRESHOLD"),
        "fap_detect": require_symbol(
            "astrometricslib.pipelines.photometry.processing.periodicity_search", "FALSE_ALARM_FOR_DETECTION"
        ),
        "radius": float(default_of(aperture, "radius")),
        "r_in": float(default_of(aperture, "annulus_inner")),
        "r_out": float(default_of(aperture, "annulus_outer")),
        "gate_count": gate_count_text(gates),
        "gate_table": gate_table(gates),
    }
    values["fits_table"] = header_table([
        [
            "`DATE-OBS`",
            "Mandatory (per frame)",
            "`str` (ISO-8601, UTC)",
            "Time the shutter opened. Turned into the light-curve time, mid-exposure BJD_TDB "
            "(a time measured at the solar system's center of mass; Section 3.1).",
            "That frame alone is rejected with a reason; `capture_timestamps` fails.",
        ],
        [
            "`EXPTIME` (or `EXPOSURE`)",
            "Recommended",
            "`float` (s)",
            "Exposure length. Gives the mid-exposure time and turns counts into flux per second.",
            "1.0 s is assumed.",
        ],
        [
            "`EGAIN`",
            "Optional",
            "`float` (e-/ADU)",
            "Gain: electrons per ADU (analog-to-digital unit, one camera count). Used for the "
            "flux errors when the camera profile has none. `GAIN` is never read.",
            "1 e-/ADU is assumed; `flux_uncertainty` is not checked.",
        ],
        [
            "`RDNOISE`",
            "Optional",
            "`float` (e-)",
            "Read noise (electronic noise added each time the sensor is read out). Used for the "
            "flux errors when the camera profile has none.",
            "0 e- is assumed.",
        ],
        [
            "`AIRMASS` (or `CENTAIRM`, `AIRM`, or altitude `CENTALT`, `ALTITUDE`, `ALT`)",
            "Optional",
            "`float`",
            "Airmass (how much atmosphere the light passed through; 1 straight overhead), for the "
            "optional airmass correction of the comparison-star level.",
            "1.0.",
        ],
    ])
    values["param_table"] = parameter_table([
        [
            "`MINIMUM_ENSEMBLE_REJECTION_FRACTION_TO_FLAG`",
            f"`{values['rej_frac']}`",
            "Empirical (7.2% baseline)",
            "Share of frames rejected as ensemble outliers (frames whose comparison-star level "
            "is out of line with the rest) that fails the gate.",
        ],
        [
            "`MINIMUM_ENSEMBLE_REJECTION_COUNT_TO_FLAG`",
            f"`{values['rej_cnt']}`",
            "Designed",
            "Fewest rejected frames that can fail the gate, so one frame of a short run cannot.",
        ],
        [
            "Aperture radius",
            f"`{values['radius']:g}` px",
            "Designed",
            "Radius of the circle in which a star's light is summed. Default of "
            "`measure_aperture_photometry`.",
        ],
        [
            "Sky annulus radii",
            f"`{values['r_in']:g}` to `{values['r_out']:g}` px",
            "Designed",
            "Inner and outer radius of the sky ring (annulus) around each star, where the sky "
            "level is measured.",
        ],
        [
            "`MINIMUM_COMPARISON_STARS`",
            f"`{values['min_comp']}`",
            "Design choice",
            "Comparison-star vetting stops at this size; fewer fails `comparison_ensemble`.",
        ],
        [
            "`DEFAULT_MAXIMUM_COMPARISON_STARS`",
            f"`{values['max_comp']}`",
            "Design choice",
            "Most comparison stars kept in the set.",
        ],
        [
            "`MINIMUM_STARS_FOR_NOISE_MODEL`",
            f"`{values['noise_model_stars']}`",
            "Designed",
            "Fewest stars for a noise model (expected scatter against brightness). With fewer, "
            "the CV cutoff (Eq. 9) picks candidates.",
        ],
        [
            "`FALSE_DISCOVERY_LEVEL`",
            f"`{values['fdr']}`",
            "Design value (synthetic fields)",
            "Largest Benjamini-Hochberg q-value a candidate may have. The q-value is the smallest "
            "expected share of false alarms among the picks at which the star would be picked.",
        ],
        [
            "`EXCESS_SCATTER_THRESHOLD`",
            f"`{values['excess']}`",
            "Design value",
            "Fixed threshold on the excess scatter: measured scatter over the scatter the noise "
            "model predicts (Eq. 7).",
        ],
        [
            "`DEFAULT_VARIABILITY_SIGMA_THRESHOLD`",
            f"`{values['cv_sigma']}`",
            "Measured (flags 5.2%)",
            "Multiplier of the MAD (median absolute deviation, a spread measure) in the CV cutoff (Eq. 9).",
        ],
        [
            "`MAXIMUM_DETECTABLE_AMPLITUDE_MAG`",
            f"`{values['max_amp']}` mag",
            "Design estimate",
            "If the smallest detectable peak-to-peak amplitude (in magnitudes, a logarithmic "
            "brightness scale) is larger, the run is called blind to most variables.",
        ],
        [
            "`MAXIMUM_CORNER_DISPLACEMENT_PX`",
            f"`{values['corner']}` px",
            "Designed",
            "Largest shift of an image corner caused by rotation and scale change between frames "
            "(95th percentile).",
        ],
        [
            "`MAXIMUM_ENSEMBLE_SCATTER_OVER_PREDICTED`",
            f"`{values['ens_scatter']}`",
            "Design value (3 fields)",
            "Largest ratio of the comparison stars' measured scatter to the scatter their error "
            "bars predict.",
        ],
    ])
    template = r"""# Differential Photometry and Light Curve Pipeline Review Guide

<<version_line>>

## Abstract

This document reviews the differential aperture photometry, ensemble
normalization, variability and period-search pipeline
(`astrometricslib.pipelines.photometry`).

<<glossary_line>>

## 1. Overview & Scientific Objective

### 1.1 In Plain Words

This pipeline measures how the brightness of each star in a field changes over
a night or over many nights, and picks out the stars whose changes are real. A
plot of a star's brightness against time is its light curve.

The difficulty is that the air changes too. Thin cloud, haze and a star
sinking toward the horizon dim every star at once, often by more than the
real changes. The main idea is to compare each star with a set of steady
stars in the same image. The shared dimming cancels in the ratio. Statistical
tests then separate real variation from noise, and a period search looks for
patterns that repeat.

The result is a light curve for every star, a list of candidate variable
stars, any periods found, and a quality summary.

### 1.2 Statement of Need

Time-domain photometry (brightness measurement repeated over time) measures
how a star's brightness changes. It finds pulsating, eclipsing and rotating
variables and planet transits (the small dip when a planet crosses its star).
From the ground, cloud, haze and airmass (how much atmosphere the light passed
through; 1 straight overhead) change every star's flux together. Differential
photometry removes that shared change by dividing each star by an ensemble (a
combined set) of constant comparison stars in the same frames.

The diagram shows one run, top to bottom.

```mermaid
flowchart TD
    A["Frames in the requested filter (spectral frames left out)"] --> B["Group into observing sessions"]
    B --> C["Per session: pick reference stars, optionally seeded from a catalog"]
    C --> D["Per frame: read capture time, align to the reference, find star centers"]
    D --> E["Sum each star's light in a circle, subtract sky, estimate the error"]
    E --> F["Convert times to a common solar-system clock"]
    F --> G["Divide by a fixed set of steady comparison stars"]
    G --> H["Model the noise and score each star's variability"]
    H --> I["Pick variable candidates, limiting false alarms"]
    I --> J["Merge sessions (two or more)"]
    J --> K["Label known variables from catalogs"]
    K --> L["Search for periods and test for false alarms"]
    L --> M["Quality checks"]
    M --> N["Quality summary"]
```

Section 3 gives the details: the solar-system clock is $\text{BJD}_\text{TDB}$
(Section 3.1), the error estimate is the CCD equation (Section 3.2), and the
candidate tests are the chi-square, Stetson J, excess-scatter and q-value
tests (Section 3.4). The quality summary is a `PhotometryQualitySummary`
(Section 7).

## 2. Input Requirements & FITS Header Specification

<<fits_intro>>

The filter comes from each frame's record, and the observatory site comes from
the configuration. The target's sky position comes from the target record.

<<fits_table>>

## 3. Mathematical & Algorithmic Formulation

This section follows a measurement from the time stamp, through the
brightness of each star and its division by the comparison stars, to the tests
that call a star variable and the search for a period.

### 3.1 Capture Time ($\text{BJD}_\text{TDB}$)

Earth moves around the Sun, so light from a star reaches Earth up to about
8 minutes earlier or later than it reaches the Sun. Times are therefore moved
to the solar system's center of mass (the barycenter), where this effect
vanishes.

`DATE-OBS` is the shutter-open time in UTC. The pipeline adds half the
exposure, converts UTC to TDB (Barycentric Dynamical Time, a uniform time
scale for the solar system), and adds the light-travel time to the
solar-system barycenter for the target's direction (up to ±499 s). The result
is the mid-exposure Barycentric Julian Date, $\text{BJD}_\text{TDB}$ (a day
count measured at the barycenter).

With no site configured, the observer is placed at Earth's center. With no
target position, the times stay as UTC start times. The time basis is quoted
in the `capture_timestamps` gate.

### 3.2 Centroid and Aperture Photometry

**Placing each star.** Each star is placed at its reference position moved by
the frame's fitted shift, rotation and scale change. It is then re-centered on
its centroid (the brightness-weighted center of its light, photutils
`centroid_com`). When the centroid is not trustworthy, the aligned position is
kept and the reason is recorded. Apertures are never rounded to whole pixels.

**Measuring its light.** Aperture photometry sums the light inside a circle
(the aperture) and subtracts the sky that falls in the same circle. The net
flux in a circle of radius <<radius>> px is

$$F_\text{net} = \sum_{i \in \text{ap}} I_i - n_\text{pix} \bar{B} \tag{1}$$

where $I_i$ are the pixel values in ADU (analog-to-digital units, raw camera
counts), $n_\text{pix}$ the number of pixels in the circle, and $\bar{B}$ the
median of a sky ring (annulus) from <<r_in>> to <<r_out>> px.

**Its error.** The CCD equation adds up every noise source in electrons: the
star's own photon noise, the sky, the read noise and the dark current. A
large $\sigma_F$ compared with $F_\text{net}$ means a noisy measurement. Its
1σ error (one standard deviation) is

$$\sigma_F = \frac{1}{g}\sqrt{F_e + n_\text{pix}\left(S_e + R^2 + D_e\right)
  + \frac{n_\text{pix}^2}{n_\text{sky}}\left(S_e + R^2\right)} \tag{2}$$

Here $g$ is the gain (electrons per ADU), $F_e = g F_\text{net}$,
$S_e = g \bar{B}$ is the sky per pixel, $R$ the read noise (e-, the
electronic noise of reading the sensor), $D_e$ the dark current per pixel (a
signal the sensor builds up from heat), and $n_\text{sky}$ the number of sky
pixels. The last term is the error of the sky level itself. $\sigma_F$ is in
ADU, like $F_\text{net}$.

### 3.3 Differential Ensemble Normalization

**Choosing the comparison stars.** A fixed comparison set is chosen per
session. Candidates are measured, unsaturated (not clipped at the sensor's
maximum) and positive in every usable frame and not listed as variable. The
brightest 2% are skipped, the band reaches the brightest 30%, and candidates
that scatter more than their errors allow are dropped until <<min_comp>>
remain. At most <<max_comp>> are kept.

**The ensemble level.** Each member's flux is divided by its own session mean,
so a value of 1 means "normal". The frame's ensemble level $L_k$ is the
average of those ratios in frame $k$; a value of 0.9 means the comparison
stars were 10% fainter than their session average in that frame. The average
is inverse-variance weighted: each star
counts in proportion to $1/\sigma^2$, so precise stars count more than noisy
ones:

$$L_k = \frac{\sum_j w_j \, F_{j,k} / \langle F_j \rangle}{\sum_j w_j}, \qquad
w_j = \frac{1}{\sigma_j^2}, \qquad \sigma_{L} = \frac{1}{\sqrt{\sum_j w_j}} \tag{3}$$

**Normalizing a star.** A comparison star is divided by the level built from
the other members only (leave one out), so its own changes never enter its
divisor. A star's normalized flux is $f_k = F_k / N_k$, with $N_k$ the level
in ADU per second. Its error combines the star's own error and the level's
error; either one being large makes the result noisy:

$$\sigma_{f} = \sqrt{\left(\frac{\sigma_F}{N}\right)^2 + \left(\frac{F \sigma_N}{N^2}\right)^2} \tag{4}$$

Normalization needs at least 2 comparison stars. With fewer, the raw light
curves are kept.

### 3.4 Variability Indices and Candidate Rule

**Why several tests.** Every light curve scatters a little from noise alone.
A variable star scatters more than its error bars allow, and its changes are
smooth from one point to the next. Three indices each catch one of these
signs. Brightness here is in magnitudes $m$ (a logarithmic scale where a
larger number means fainter; 1 mag is a factor of about 2.5).

**The noise model.** A noise model gives the scatter a constant star of
magnitude $m$ shows in this field, $\sigma_\text{model}(m)$, from the median
scatter in brightness bins. It needs at least <<noise_model_stars>> stars.

**The three indices.** In each, $x_i$ are the star's $n$ measurements,
$\sigma_i$ their errors and $\bar{x}_w$ their weighted mean.

The reduced chi-square $\chi^2_\nu$ asks whether the points scatter more than
their error bars allow. A value near 1 means noise only; well above 1 means
extra variation:

$$\chi^2_\nu = \frac{1}{n-1}\sum_{i=1}^{n}\frac{(x_i - \bar{x}_w)^2}{\sigma_i^2} \tag{5}$$

The Stetson J index asks whether neighbouring points in time stray to the same
side of the mean together. Noise gives $J$ near 0; a smooth real change gives
a clearly positive $J$:

$$\delta_i = \sqrt{\frac{n}{n-1}}\,\frac{x_i - \bar{x}_w}{\sigma_i}, \qquad
J = \frac{1}{n-1}\sum_{i=1}^{n-1}\operatorname{sgn}(P_i)\sqrt{|P_i|}, \qquad
P_i = \delta_i \, \delta_{i+1} \tag{6}$$

The excess scatter $E$ compares the star's measured scatter with what the
noise model expects for a star of its brightness. 1 means typical; 2 means
twice the usual scatter:

$$E = \frac{\sigma_\text{measured}}{\sigma_\text{model}(m)} \tag{7}$$

Eq. 6 is the single-band Stetson J with consecutive pairs and unit weights
(Stetson 1996, PASP 108, 851).

**Thresholds and score.** The thresholds on $\chi^2_\nu$ and $J$ are the
field median plus <<cal_sigmas>> robust standard deviations (a spread measure
that a few wild values cannot inflate). The threshold on $E$ is fixed at
<<excess>>. The ranking score divides each index by its threshold, so a score
above 1 means the star clears its tests. Without a high $J$, the score is
capped at 1, so it cannot pass:

$$S = \begin{cases}\min\left(\chi^2_\nu / \chi^2_\text{thr},\; E / E_\text{thr}\right) & J > J_\text{thr}\\
\min\left(\chi^2_\nu / \chi^2_\text{thr},\; E / E_\text{thr},\; 1\right) & \text{otherwise}
\end{cases} \tag{8}$$

**Limiting false alarms.** Testing thousands of stars makes some pass by
chance. A star is a candidate when $S > 1$ and its q-value is below <<fdr>>.
The chi-square false-alarm probability is the chance that noise alone gives a
$\chi^2$ this large (the upper tail of the chi-square distribution with
$n - 1$ degrees of freedom). The Benjamini-Hochberg method turns these
probabilities into q-values that control the false discovery rate: the
expected share of false alarms among all stars picked. A q-value of 0.05 means
about 1 in 20 such picks is expected to be noise.

**Small fields.** A field too small for a noise model falls back to a cutoff
on the coefficient of variation (CV, standard deviation over mean). The cutoff
is the typical CV plus a multiple of its spread, and never below 2%:

$$\text{CV}_\text{cut} = \max\left(0.02,\;
  \text{median}(\text{CV}) + <<cv_sigma>> \times \text{MAD}(\text{CV})\right) \tag{9}$$

where MAD is the unscaled median absolute deviation (the median distance of
the values from their median, a spread measure that ignores outliers).

### 3.5 Period Search

**Methods.** The target's star and the brightest stars are searched with a
Lomb-Scargle periodogram and box least squares. Lomb-Scargle fits sine waves
of many trial periods to unevenly spaced data and finds smooth cycles. Box
least squares fits a flat-bottomed dip that repeats, and finds eclipses and
transits.

**False alarms.** The false-alarm probability (FAP) is the share of
block-shuffled copies of the light curve (the data cut into blocks and put
back in random order, which destroys any real period) that do as well. A
"detected" verdict needs a FAP below <<fap_detect>>.

**Many searches.** Searching many stars raises the chance that one passes by
luck. The Šidák correction, $1 - (1 - p)^m$ for $m$ searches, is the chance
of at least one false pass among $m$ tries. It keeps the chance of a false
detection across the whole run at that level (`family_wise_correction.py`).
Two period checks (Section 4.1) can lower the verdict.

### 3.6 Cross-Session Merge and Labels

With two or more sessions, each star's light curves are matched by sky
position and merged (`batch.py`). Each session keeps its own normalized level.
Stars whose level changes between sessions become long-term candidates.
Candidates are then labelled from SIMBAD (a database of named objects),
Gaia DR3 (the Gaia space mission's third catalog) and VSX (the International
Variable Star Index) as listed, not listed, or unknown
(`known_variability_labels.py`).

## 4. Processing Flow

`run_photometry_analysis` (`runner.py`) runs `PhotometryPipelineAdapter`
through `run_pipeline`. <<analysis_note>>

- **Stage 1 (`process_input`):** Keeps frames in the requested filter, leaves
  out spectral frames, and groups frames with a capture time into sessions.
  With no frame or no session it returns `has_work=False`, and
  `photometry_work` fails.
- **Stage 2 (`run`):** Runs one `VariabilityAnalyzer` pass per session, merges
  sessions, labels candidates, saves the stars, and runs the period search in
  a step whose failure cannot cost the light curves.
- **Stage 3 (`validate_output`):** Fits the run's noise model and records
  the run gates from `photometry_run_gates`. The period checks were already
  recorded on each period result in stage 2. The package defines
  <<gate_count>>.
- **Stage 4 (`to_result_dict`):** Returns run counts, the rejected frames,
  the short-term and long-term variable candidates and the cross-session
  match count.

### 4.1 Quality Gates

<<gate_intro>>

Run gates are recorded on the quality summary. Period checks are recorded on
each period result.

<<gate_table>>

## 5. Tuned Calibration Parameters

<<param_intro>>

<<param_table>>

## 6. Degraded States & Fallback Pathways

1. **Sparse fields.** Below <<noise_model_stars>> usable stars there is no
   noise model. Candidates come from the CV cutoff of Eq. 9, and
   `scatter_population` is not checked. Below <<scatter_pop>> stars with a
   scatter, the cutoff itself is called unreliable.
2. **Few comparison stars.** With fewer than 2, raw light curves are kept.
   With fewer than <<min_comp>>, `comparison_ensemble` fails.
3. **Missing or bad `DATE-OBS`.** Only that frame is rejected, with a reason.
   It is never given the computer's clock time instead.
4. **No site configured.** BJD_TDB is computed with the observer at Earth's
   center, and the time basis says so.
5. **Unknown gain.** Errors assume 1 e-/ADU. The fluxes do not change, but
   `flux_uncertainty` is not checked.
6. **Noisy runs.** `detectable_amplitude` reports how large a variable must
   be to clear the noise. A run that needs more than <<max_amp>> mag is blind
   to most variables.

<<quality_section>>"""
    values["analysis_note"] = ANALYSIS_STAGES_NOTE
    values["quality_section"] = quality_section(
        "PhotometryQualitySummary", "PhotometryPipelineQualityMetrics"
    )
    return fill(template, {**COMMON_VALUES, **values})


def build_spectroscopy_doc() -> str:
    """Build the Spectroscopy Pipeline review documentation.

    Returns
    -------
    content : `str`
        Markdown content for spectroscopy.md.
    """
    gates = discover_gate_names("spectroscopy")
    source_module = "astrometricslib.models.stellar_source"
    extractor_module = "astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor"
    zero_point_module = "astrometricslib.pipelines.spectroscopy.pre_processing.wavelength_zero_point"
    values = {
        "version_line": VERSION_LINE,
        "no_good": require_symbol(source_module, "NO_GOOD_MATCH_RMS"),
        "unreliable": require_symbol(source_module, "UNRELIABLE_MATCH_RMS"),
        "gap": require_symbol(source_module, "AMBIGUOUS_RMS_GAP"),
        "subtypes": require_symbol(source_module, "DIFFERS_FROM_CATALOG_SUBTYPES"),
        "box": require_symbol(extractor_module, "APERTURE_SIGMA_MULTIPLIER"),
        "sky_gap": require_symbol(extractor_module, "SKY_BAND_GAP_PX"),
        "sky_width": require_symbol(extractor_module, "SKY_BAND_WIDTH_PX"),
        "zp_lines": require_symbol(zero_point_module, "MINIMUM_LINES_TO_APPLY"),
        "zp_sig": require_symbol(zero_point_module, "MINIMUM_LINE_SIGNIFICANCE"),
        "wl_limit": require_symbol(
            "astrometricslib.pipelines.spectroscopy.post_processing.wavelength_scale",
            "WAVELENGTH_SCALE_LIMIT_ANGSTROM",
        ),
        "xp_limit": require_symbol(
            "astrometricslib.pipelines.spectroscopy.post_processing.compare_to_gaia_xp",
            "GAIA_XP_SLOPE_LIMIT_PERCENT_PER_1000_ANGSTROM",
        ),
        "gate_count": gate_count_text(gates),
        "gate_table": gate_table(gates),
    }
    values["fits_table"] = header_table([
        [
            "`DATE-OBS`",
            "Optional",
            "`str` (ISO-8601, UTC)",
            "Exposure start, used for the mid-exposure time of the refraction correction (the "
            "shift caused by the air bending each colour by a different amount).",
            "The refraction correction is skipped, with the reason recorded.",
        ],
        [
            "`DATE-END`",
            "Optional",
            "`str` (ISO-8601, UTC)",
            "Exposure end, for the mid-exposure time.",
            "`DATE-OBS` plus half of `EXPTIME`.",
        ],
        [
            "`EXPTIME`",
            "Recommended",
            "`float` (s)",
            "Exposure length. Turns counts into counts per second and gives the mid-exposure time.",
            "The intensity scale is left unknown, never guessed.",
        ],
        [
            "`BITPIX` / `STACKCNT`",
            "Optional",
            "`int`",
            "`BITPIX` is the pixel data type (negative means decimal numbers) and `STACKCNT` the "
            "number of stacked frames. Together they mark a Siril float stack, whose mean frame "
            "exposure is `EXPTIME` / `STACKCNT`.",
            "Treated as a raw frame when `BITPIX` > 0 and there is no `STACKCNT`.",
        ],
        [
            "`AIRMASS`",
            "Recommended",
            "`float`",
            "Airmass (how much atmosphere the light passed through; 1 straight overhead), for "
            "the extinction correction (Eq. 3) that undoes dimming by the air.",
            "The extinction correction is skipped, and the skip is recorded.",
        ],
        [
            "`RA` / `DEC`",
            "Optional",
            "`float` (deg)",
            "Field center (right ascension and declination, the sky's longitude and latitude). "
            "Used to match stars when there is no solved stack (a stack with a sky map).",
            "No registration reference by position.",
        ],
    ])
    values["param_table"] = parameter_table([
        [
            "`NO_GOOD_MATCH_RMS`",
            f"`{values['no_good']}`",
            "Measured (14 stars)",
            "Misfit to the best reference spectrum (relative RMS, Eq. 4) above which a "
            "classification is a poor match.",
        ],
        [
            "`UNRELIABLE_MATCH_RMS`",
            f"`{values['unreliable']}`",
            "Measured (55 stars)",
            "Misfit (relative RMS, Eq. 4) above which no spectral type is given at all.",
        ],
        [
            "`AMBIGUOUS_RMS_GAP`",
            f"`{values['gap']}`",
            "Measured (reference ladder spacing)",
            "Smallest misfit gap between the best reference and the next one; a smaller gap "
            "makes the match ambiguous.",
        ],
        [
            "`DIFFERS_FROM_CATALOG_SUBTYPES`",
            f"`{values['subtypes']}`",
            "Measured (29 stars)",
            "Subtype steps (the finer divisions of a spectral class) beyond which the measured "
            "type disagrees with the catalog type.",
        ],
        [
            "`APERTURE_SIGMA_MULTIPLIER`",
            f"`{values['box']}`",
            "Designed",
            "Half-width of the extraction box, in units of the trail's Gaussian sigma (its "
            "width measure, Eq. 2).",
        ],
        [
            "`SKY_BAND_GAP_PX` / `SKY_BAND_WIDTH_PX`",
            f"`{values['sky_gap']}` px / `{values['sky_width']}` px",
            "Measured (Vega, one frame)",
            "Gap between the box and each sky band, and the band's width, on each side of the box.",
        ],
        [
            "`WAVELENGTH_SCALE_LIMIT_ANGSTROM`",
            f"`{values['wl_limit']}` Å",
            "Designed",
            "Largest scatter of the run's wavelength zero points (offsets of the wavelength "
            "scale) after correction.",
        ],
        [
            "`GAIA_XP_SLOPE_LIMIT_PERCENT_PER_1000_ANGSTROM`",
            f"`{values['xp_limit']}`",
            "Designed",
            "Largest median tilt of the ratio observed / Gaia XP (the Gaia mission's own "
            "low-resolution spectra), in percent per 1000 Å.",
        ],
        [
            "Flux calibration",
            "Relative F_λ",
            "Calibrated to a constant",
            "Energy flux per unit wavelength (F_λ), corrected for sensor sensitivity (QE), "
            "instrument response and atmospheric dimming, up to one unknown scale factor.",
        ],
    ])
    template = r"""# Slitless Spectroscopy Extraction and Classification Pipeline Review Guide

<<version_line>>

## Abstract

This document reviews the slitless spectroscopy pipeline
(`astrometricslib.pipelines.spectroscopy`): extraction, wavelength
calibration, refraction, zero-point, response and extinction corrections,
dereddening, classification, the Gaia XP comparison, and the quality
checkpoints.

<<glossary_line>>

## 1. Overview & Scientific Objective

### 1.1 In Plain Words

This pipeline spreads each star's light into its colours, a spectrum, and
uses the spectrum to classify the star. The class, or spectral type, mainly
tells the star's surface temperature. The types run O, B, A, F, G, K, M from
hottest to coolest, each split into finer subtypes.

There is no slit, so every star in the field makes its own short rainbow
trail at once. Trails can overlap, the sky adds light, and the camera, the
optics and the air each dim some colours more than others. The pipeline
traces each trail, sums its light, and turns position along the trail into
wavelength. It then removes each known colour effect in turn and finds the
reference spectrum that matches best.

The result is a corrected spectrum and a spectral type for each star, with
checks against star catalogs and against spectra from the Gaia space mission.

### 1.2 Statement of Need

A transmission grating (a clear plate ruled with many fine parallel lines) in
front of the camera, for example a Star Analyser 100 or 200, spreads each
star's light into a first-order spectrum on the sensor. The spectrum lies next
to the star's undispersed point image, the zero order. The pipeline turns
those trails into calibrated spectra and classifies them against the Pickles
(1998) stellar library (a set of reference spectra of known types).

The diagram shows one run, top to bottom.

```mermaid
flowchart TD
    A["Stacked spectral image"] --> B["Find stars (detection only, no sky map)"]
    B --> C["Name stars from the matching normal image's sky map, else by geometry"]
    C --> D["Find the spectrum direction and trace each trail"]
    D --> E["Sum light across each trail; subtract sky from two side bands"]
    E --> F["Remove light spilling over from bright neighbours"]
    F --> G["Turn trail position into wavelength"]
    G --> H["Correct the shift from the air bending light"]
    H --> I["Correct the offset of the wavelength scale"]
    I --> J["Correct for the sensor's sensitivity at each colour"]
    J --> K["Correct for the instrument and for dimming by the air"]
    K --> L["Remove reddening by interstellar dust"]
    L --> M["Classify against reference spectra"]
    M --> N["Compare with Gaia spectra"]
    N --> O["Four checkpoints and quality checks"]
    O --> P["Quality summary"]
```

Section 3 names the steps in the usual terms: the grating equation, differential
refraction and the zero-point shift (Section 3.1); QE, instrument response,
airmass extinction and dereddening (Section 3.3); and the Pickles and Gaia XP
comparisons (Sections 3.4 and 3.5). The normal image is the target's solved
standard stack, whose WCS (pixel-to-sky map) names the stars. The quality
summary is a `SpectroscopyQualitySummary` (Section 7).

## 2. Input Requirements & FITS Header Specification

<<fits_intro>>

The dispersion direction is measured from the image. `DISPAXIS` and `GRATROT`
are not read. The observatory site comes from the configuration.

<<fits_table>>

## 3. Mathematical & Algorithmic Formulation

This section follows one spectrum: turning trail position into wavelength,
summing the light, correcting the brightness at each colour, and matching the
result to reference spectra.

### 3.1 Wavelength Model

**The grating equation.** The grating bends each colour by a different angle,
so distance along the trail measures wavelength: the farther from the zero
order, the redder the light. The dispersion is this spreading of light by
wavelength, and its direction on the sensor is measured from the image. A
sample's distance $x$ from the zero-order center, along the dispersion,
becomes a wavelength through the first-order grating equation
(`optics_physics.py`):

$$\lambda(x) = d \, \sin\left(\arctan\frac{x \, p}{L}\right) \tag{1}$$

where $d$ is the groove spacing (1 / lines per mm), $p$ the pixel pitch and
$L$ the grating-to-sensor distance, the one fitted number. A larger $L$
spreads the spectrum over more pixels.

**Refraction shift.** The air bends light slightly, and bends blue light more
than red. Differential atmospheric refraction is this colour-dependent
bending. It moves each wavelength along the trail by
$R(\lambda) - R(\lambda_\text{eff})$, projected onto the dispersion
direction, where $R(\lambda)$ is the bending at wavelength $\lambda$ and
$\lambda_\text{eff}$ is a reference (effective) wavelength.
It needs the site, a WCS (a pixel-to-sky map) and the exposure time.

**Zero-point shift.** If the zero-order center is misplaced, the whole
wavelength scale slides by a fixed amount, its zero point. A zero-point shift
corrects that offset. It is measured from known absorption lines (dark lines
where the star's atmosphere absorbs light), each at least <<zp_sig>> noise
widths deep, and is applied when at least <<zp_lines>> lines agree.

### 3.2 Extraction and Background Subtraction

**The box.** The trail is traced step by step. At each step a Gaussian (a
bell-shaped curve) is fitted across the trail. Its sigma is a width measure:
a wider trail gets a wider box, so the box holds the same share of the light
everywhere. The box half-width is

$$w(x) = <<box>> \, \sigma_\text{trail}(x) \tag{2}$$

where $\sigma_\text{trail}$ is the fitted Gaussian sigma, smoothed over 61
steps. `median_trail_width_px` in the summary is this sigma too, not a FWHM
(full width at half maximum, a different width measure about 2.35 times
sigma). Edge pixels count for the fraction inside the box.

**The sky.** Two sky bands, each <<sky_width>> px wide and <<sky_gap>> px
from the box, are sigma-clipped (pixels far from the median, such as stars,
are dropped). The sky level is the mean of their medians, or the lower band
alone when they disagree by more than the noise allows.

**Neighbours.** A bright neighbour's wings (the faint outer edges of its
trail) are removed with a blur profile measured from an isolated star
(`neighbor_trail_deblending.py`, `neighbor_wing_correction.py`).

### 3.3 Flux Calibration

**What is corrected.** The raw counts at each wavelength are not the star's
true brightness at that colour. The sensor, the optics and the air each pass
some colours better than others. Three corrections undo this in turn.

- **QE.** The counts are divided by the sensor's quantum efficiency (QE: the
  share of arriving light the sensor records, which depends on colour).
- **Instrument response.** The counts are then divided by the instrument
  response: everything else about the optics and grating that favours some
  colours. The response is Vega's observed spectrum over the Pickles A0V
  template (a reference spectrum for Vega's type).
- **Airmass extinction.** The air dims light more at blue wavelengths, and
  more when the star is low. The extinction curve $k(\lambda)$ gives the
  dimming in magnitudes per airmass (a magnitude is a logarithmic brightness
  unit; 1 mag is a factor of about 2.5). Only the airmass difference $X -
  X_\text{ref}$ from the response's reference star is removed, because the
  response already holds the reference star's own dimming:

$$F_\text{corr}(\lambda) = F(\lambda)\; 10^{0.4\, k(\lambda)\, (X - X_\text{ref})} \tag{3}$$

The result is a relative $F_\lambda$ (energy flux per unit wavelength) up to
one constant: the shape of the spectrum is right, its overall level is not
known.

**Dereddening.** Dust between the stars dims blue light more than red, so a
star seen through dust looks redder, and cooler, than it is. $E(B-V)$ is the
size of this reddening, given as the extra difference between blue and visual
magnitudes. When a catalog gives the star's $E(B-V)$, interstellar reddening
is removed with the Cardelli, Clayton and Mathis (1989) law with $R_V = 3.1$
($R_V$ sets how the dimming changes with wavelength).

### 3.4 Classification

Each reference spectrum is blurred to the instrument's resolution (the
smallest wavelength detail it can separate) and scaled to the observation by a
best-fit factor $a$. The match score is the relative RMS (root mean square)
difference between the observed spectrum $F$ and the scaled reference $T$,
over $M$ wavelengths, divided by the mean flux $\bar{F}$. 0 is a perfect
match; 0.1 means a typical misfit of 10% of the flux:

$$\text{RMS} = \frac{1}{\bar{F}}
  \sqrt{\frac{1}{M}\sum_{i=1}^{M}\left[F(\lambda_i) - a\,T(\lambda_i)\right]^2} \tag{4}$$

The references are ranked by this score, and the lowest wins. Above
<<no_good>> the match is poor. Above <<unreliable>> no type is given. A gap
below <<gap>> to the next reference makes the match ambiguous.

### 3.5 Gaia XP Comparison

The Gaia space mission measured low-resolution spectra, called XP spectra, for
many stars, independently of this instrument. Where Gaia DR3 (its third data
release) has an XP spectrum for the star, both spectra are brought to the same
resolution and divided by their median over 5400-5600 Å (1 Å, an angstrom, is
0.1 nm). If the calibration is right, their ratio is flat. The tilt of their
ratio tests the response, the extinction correction and the wavelength scale
together.

## 4. Processing Flow

`run_spectroscopy_analysis` (`runner.py`) runs `SpectroscopyPipelineAdapter`
through `run_pipeline`. <<analysis_note>>

- **Stage 1 (`process_input`):** A no-op that returns an empty `Result`.
- **Stage 2 (`run`):** Finds the stars with `AstrometryPipeline` (no solve),
  names them from the target's solved stack, and runs
  `SpectroscopyPipeline.process`. Each spectrum carries four quality
  checkpoints, in order: `raw_frame`, `pre_processing`, `processing` and
  `post_processing`.
- **Stage 3 (`validate_output`):** Builds the summary, records the eight
  `spectroscopy_run_gates`, then `gaia_xp_agreement` (<<gate_count>> in all).
- **Stage 4 (`to_result_dict`):** Returns only `context` and
  `stellar_objects`.

A session of raw frames runs through `batch.py` instead. It names the stars
in one reference frame, follows them through the session's other frames in
parallel workers, and builds the same gates from the same plain counts.

### 4.1 Quality Gates

<<gate_intro>>

<<gate_table>>

## 5. Tuned Calibration Parameters

<<param_intro>>

<<param_table>>

## 6. Degraded States & Fallback Pathways

1. **Zero-order saturation.** Saturated pixels are clipped at the sensor's
   maximum value, so their true brightness is lost. When a star's zero order
   holds a significant share of them, `zero_order_saturation` fails. A
   saturated center can offset the wavelength scale; the zero-point shift
   corrects it
   when enough lines agree.
2. **Missing header data.** No `DATE-OBS`, site or WCS skips the refraction
   correction. No `AIRMASS` skips the extinction correction. Each skip is
   recorded on the spectrum.
3. **Ambiguous or poor matches.** They are kept with a flag and counted in
   the summary. `spectral_classification` fails only on a class-level
   ambiguity or a poor match.
4. **Reddened hot stars.** With no catalog $E(B-V)$, an O, B or A star tends
   to read about one subtype cool.
5. **No solved stack.** Stars are named by matching point patterns, which can
   fail on sparse fields.

<<quality_section>>"""
    values["analysis_note"] = ANALYSIS_STAGES_NOTE
    values["quality_section"] = quality_section(
        "SpectroscopyQualitySummary", "SpectroscopyPipelineQualityMetrics"
    )
    return fill(template, {**COMMON_VALUES, **values})


def build_asteroid_detection_doc() -> str:
    """Build the Asteroid Detection Pipeline review documentation.

    Returns
    -------
    content : `str`
        Markdown content for asteroid_detection.md.
    """
    gates = discover_gate_names("asteroid_detection")
    config_cls = require_symbol("astrometricslib.models.moving_object_config", "MovingObjectConfig")
    defaults = {name: field.default for name, field in config_cls.model_fields.items()}
    values = {
        "version_line": VERSION_LINE,
        "pointing_radius": require_symbol(
            "astrometricslib.pipelines.asteroid_detection.pipeline",
            "_POINTING_CORRECTION_SEARCH_RADIUS_ARCSEC",
        ),
        "gate_count": gate_count_text(gates),
        "gate_table": gate_table(gates),
        **{f"cfg_{name}": value for name, value in defaults.items()},
    }
    values["fits_table"] = header_table([
        [
            "`DATE-OBS`",
            "Mandatory (per frame)",
            "`str` (ISO-8601, UTC)",
            "Time the shutter opened, read into the frame record's timestamp.",
            "The frame is not searched.",
        ],
        [
            "`RA` / `DEC`",
            "Mandatory (per frame)",
            "`float` (deg)",
            "The mount's pointing (right ascension and declination, the sky's longitude and "
            "latitude), which sets the center of the frame's WCS (pixel-to-sky map).",
            "The frame is excluded; `pointing_metadata` fails.",
        ],
        [
            "`NAXIS1` / `NAXIS2`",
            "Mandatory (per frame)",
            "`int`",
            "Frame width and height in pixels; the WCS reference pixel is the frame's center.",
            "The frame is excluded; `pointing_metadata` fails.",
        ],
        [
            "Stack WCS",
            "Mandatory",
            "WCS cards of the stacked image",
            "The stacked image's sky map. Gives the rotation and scale for every frame's WCS.",
            "The run cannot start without a stacked image.",
        ],
        [
            "`EXPTIME`",
            "Optional",
            "`float` (s)",
            "Recorded with each detection.",
            "Left empty.",
        ],
    ])

    def cfg(name: str) -> str:
        """Format one configuration default as inline code.

        Parameters
        ----------
        name : `str`
            The `MovingObjectConfig` field.

        Returns
        -------
        text : `str`
            The default between backticks.
        """
        return f"`{defaults[name]:g}`" if isinstance(defaults[name], float) else f"`{defaults[name]}`"

    values["param_table"] = parameter_table([
        [
            "`min_frames_for_persistence`",
            cfg("min_frames_for_persistence"),
            "Designed",
            "Fewest detections in a chain (detections linked across frames as one object).",
        ],
        [
            "`pixel_match_tolerance_px`",
            cfg("pixel_match_tolerance_px"),
            "Designed",
            "Pixel spread below which a chain is a hot pixel (a sensor pixel that always reads high).",
        ],
        [
            "`sky_match_tolerance_arcsec`",
            cfg("sky_match_tolerance_arcsec"),
            "Designed",
            "Sky spread below which a chain is a fixed star (Eq. 2).",
        ],
        [
            "`rate_min_arcsec_per_hour` / `rate_max_arcsec_per_hour`",
            f"{cfg('rate_min_arcsec_per_hour')} / {cfg('rate_max_arcsec_per_hour')} arcsec/h",
            "Designed",
            "Allowed range of the total speed across the sky.",
        ],
        [
            "`chain_match_radius_max_arcsec`",
            f"{cfg('chain_match_radius_max_arcsec')} arcsec",
            "Designed",
            "Cap on how far a chain may reach for its next detection (Eq. 1).",
        ],
        [
            "`residual_rms_max_multiple`",
            cfg("residual_rms_max_multiple"),
            "Designed",
            "Largest RMS (root mean square) distance from the fitted straight line on each axis, "
            "in units of the position error.",
        ],
        [
            "`min_displacement_error_multiple`",
            cfg("min_displacement_error_multiple"),
            "Designed",
            "Smallest total distance moved, in units of the position error.",
        ],
        [
            "`astrometric_error_default_arcsec`",
            f"{cfg('astrometric_error_default_arcsec')} arcsec",
            "Designed",
            "Position error assumed for a detection with none measured.",
        ],
        [
            "`ephemeris_cross_match_radius_arcsec`",
            f"{cfg('ephemeris_cross_match_radius_arcsec')} arcsec",
            "Designed",
            "Match radius for a known body from SkyBoT (an online service that lists known "
            "solar-system bodies), at both the first and last detection.",
        ],
        [
            "`_POINTING_CORRECTION_SEARCH_RADIUS_ARCSEC`",
            f"`{values['pointing_radius']:g}` arcsec",
            "Unvalidated (NGC 2403)",
            "Pairing radius between stack stars and frame detections in the per-frame "
            "pointing-offset vote (Section 3.1).",
        ],
    ])
    template = r"""# Asteroid and Moving Object Detection Pipeline Review Guide

<<version_line>>

## Abstract

This document reviews the moving-object detection pipeline
(`astrometricslib.pipelines.asteroid_detection`): per-frame pointing,
detection, per-night chaining, the stationary and straight-line tests, and the
SkyBoT cross-match.

<<glossary_line>>

## 1. Overview & Scientific Objective

### 1.1 In Plain Words

This pipeline looks for asteroids and comets in a series of images of the
same patch of sky. Stars stay put from image to image. A nearby body in the
solar system moves a little between images.

Many things appear in one image and not the next without being asteroids:
noise, hot pixels (sensor pixels that always read high) and faint stars near
the detection limit. Each image's pointing also differs slightly. The
pipeline lines up every image on the stars and links detections from image to
image into chains. It drops chains that do not move, and keeps chains that
move in a straight line at a plausible, steady speed. It then asks an online
service which known bodies were at those places at those times.

The result is a list of moving objects, each matched to a known body or marked
as unknown and worth a closer look.

### 1.2 Statement of Need

Asteroids and comets move against the stars, so they show up as point sources
(star-like dots) that shift from frame to frame. The pipeline finds those
sources, links them into straight-line tracks and asks the IMCCE SkyBoT
service (an online list of where every known solar-system body is at a given
time) which known bodies were there. A confirmed mover with no match is a lead
for a person to check.

The diagram shows one run. The left branch prepares the reference stars; the
right branch prepares each frame.

```mermaid
flowchart TD
    A["Stacked image with a sky map (run astrometry first)"] --> B["Reference stars from the stack"]
    D["Sky map per frame: rotation and scale from the stack, center from the mount"]
    C["Single frames with capture times"] --> D
    B --> E["Correct each frame's pointing (offset vote)"]
    D --> E
    E --> F["Find star-like sources"]
    F --> G["Link detections into chains within each night"]
    G --> H["Drop chains that do not move (hot pixels, stars)"]
    H --> I["Keep straight-line movers at a plausible speed"]
    I --> J["Look up known bodies (SkyBoT)"]
    J --> K["Quality checks"]
    K --> L["Quality summary"]
```

A sky map is a WCS (World Coordinate System): the map from pixels to sky
positions. A plate-solved stack is a stacked image whose WCS has been found by
the astrometry pipeline. The quality summary is an
`AsteroidDetectionQualitySummary` (Section 7).

## 2. Input Requirements & FITS Header Specification

<<fits_intro>>

The target must have a plate-solved stack: run astrometry first. Without a
stacked image the run raises `ConflictError`.

<<fits_table>>

## 3. Mathematical & Algorithmic Formulation

This section follows the frames from pointing, through linking detections into
chains, to the tests a chain must pass and the look-up of known bodies.
Angles on the sky are in arcseconds (arcsec, 1/3600 of a degree).

### 3.1 Per-Frame Pointing

**Why.** Siril (the stacking program) does not save each frame's alignment, so
each frame's WCS takes its rotation and scale from the stack and its center
from the mount's `RA`/`DEC` (`frame_wcs_composer.py`). A mount is often tens
of arcseconds off.

**How.** The stack's own stars are therefore matched to each frame's
detections within <<pointing_radius>> arcsec. Every pair votes for an offset
in 5-arcsec bins, and the winning offset is removed from the frame's
positions. True pairs agree on one offset; chance pairs scatter their votes.

### 3.2 Chaining

Detections are linked only within one observing night. Frames are taken in
time order, and each open chain may claim the nearest detection in the next
frame. The search radius is the distance the fastest allowed mover could
travel in the time between frames $\Delta t$, capped so it never grows too
large:

$$r = \min\left(v_\text{max}\, \Delta t,\; r_\text{cap}\right) \tag{1}$$

with $v_\text{max}$ = <<cfg_rate_max_arcsec_per_hour>> arcsec/h and
$r_\text{cap}$ = <<cfg_chain_match_radius_max_arcsec>> arcsec. Claims go
nearest-first over all chains. A chain may skip frames; it is not limited to
consecutive ones. A chain needs at least <<cfg_min_frames_for_persistence>>
detections.

### 3.3 Stationary Tests

A chain that does not move is not an asteroid. If it stays put on the sensor,
it is a hot pixel; if it stays put on the sky, it is a star. Both spreads are
the largest distance of any detection from the chain's mean position. A small
spread means the chain did not move:

$$\max_i \left\lVert \mathbf{p}_i - \bar{\mathbf{p}} \right\rVert
  < <<cfg_pixel_match_tolerance_px>>\ \text{px} \;\Rightarrow\; \text{hot pixel}, \qquad
\max_i \theta\left(\mathbf{s}_i, \bar{\mathbf{s}}\right)
  < <<cfg_sky_match_tolerance_arcsec>>'' \;\Rightarrow\; \text{fixed star} \tag{2}$$

where $\mathbf{p}_i$ is a pixel position and $\mathbf{s}_i$ a sky position
on the tangent plane (the sky projected onto a flat plane that touches it at
the field center), and $\theta$ is the angle between two sky positions.

### 3.4 Straight-Line and Rate Tests

Over one night, an asteroid moves in a nearly straight line at a nearly steady
speed. Noise linked by chance does not. RA and Dec (right ascension and
declination, the sky's longitude and latitude) offsets on the tangent plane
are each fitted against time. The slopes $\dot{\alpha}$ and $\dot{\delta}$
are the speeds along the two axes:

$$\alpha(t) = \alpha_0 + \dot{\alpha}\,(t - t_0), \qquad
  \delta(t) = \delta_0 + \dot{\delta}\,(t - t_0) \tag{3}$$

A chain passes when all of these hold:

- The total rate $\sqrt{\dot\alpha^2 + \dot\delta^2}$ is between
  <<cfg_rate_min_arcsec_per_hour>> and <<cfg_rate_max_arcsec_per_hour>>
  arcsec/h.
- On each axis, the RMS distance from the line is at most
  <<cfg_residual_rms_max_multiple>> times that axis's position error.
- The total displacement is at least <<cfg_min_displacement_error_multiple>>
  times the position error.
- The positions never move back along the fitted direction by more than the
  position error (otherwise `REJECTED_NON_MONOTONIC`, meaning the motion is
  not one-way).

### 3.5 Known-Body Match

SkyBoT's prediction of where a known body is at a given time is its
ephemeris. SkyBoT is asked twice per mover: at its first and at its last
detection. Checking both ends makes sure the known body also moves the same
way. A known body matches only if it is within
<<cfg_ephemeris_cross_match_radius_arcsec>> arcsec at both moments.

## 4. Processing Flow

`run_asteroid_detection_analysis` (`runner.py`) runs
`AsteroidDetectionPipelineAdapter` through `run_pipeline`. <<analysis_note>>

- **Stage 1 (`process_input`):** A no-op that returns an empty `Result`.
- **Stage 2 (`run`):** Runs `AsteroidDetectionPipeline.process` on the light
  frames that have a timestamp. Only straight-line movers and matched bodies
  are kept on the target.
- **Stage 3 (`validate_output`):** Builds the summary and records
  `asteroid_run_gates` (<<gate_count>>).
- **Stage 4 (`to_result_dict`):** Returns the status, stage counts and
  surviving candidates.

`mpc_report.py` can write Minor Planet Center (MPC, the clearing house for
asteroid observations) reports from detections, in its 80-column and ADES
text formats, but no pipeline step calls it yet. A run does not produce MPC
records. `rotation_period.py` builds a light curve from a track for the
period search; the pipeline does not call it either.

### 4.1 Quality Gates

<<gate_intro>>

<<gate_table>>

## 5. Tuned Calibration Parameters

<<param_intro>>

The values below are the defaults of `MovingObjectConfig`. The configuration
can change them.

<<param_table>>

## 6. Degraded States & Fallback Pathways

1. **Too few frames.** With fewer searchable frames than a chain needs,
   `search_frames` is not checked, rather than reporting a clean null result.
2. **Missing pointing cards.** Frames without `RA`, `DEC`, `NAXIS1` or
   `NAXIS2` are excluded and `pointing_metadata` fails.
3. **Weak pointing vote.** When too few pairs agree on an offset, the frame
   keeps its raw mount pointing.
4. **SkyBoT outages.** Confirmed movers stay as candidates and
   `ephemeris_cross_match` fails.
5. **No measured position error.** The default error is assumed and
   `track_residuals` says how many detections used it.

<<quality_section>>"""
    values["analysis_note"] = ANALYSIS_STAGES_NOTE
    values["quality_section"] = quality_section(
        "AsteroidDetectionQualitySummary", "AsteroidDetectionPipelineQualityMetrics"
    )
    return fill(template, {**COMMON_VALUES, **values})


def build_stacking_doc() -> str:
    """Build the Stacking & Calibration Pipeline review documentation.

    Returns
    -------
    content : `str`
        Markdown content for stacking.md.
    """
    gates = discover_gate_names("stacking")
    quality_module = "astrometricslib.pipelines.stacking.post_processing.stack_quality"
    values = {
        "version_line": VERSION_LINE,
        "dark_tol": require_symbol(
            "astrometricslib.drivers.calibration_library", "DEFAULT_DARK_TEMPERATURE_TOLERANCE_C"
        ),
        "degrade": require_symbol(quality_module, "DEFAULT_FWHM_DEGRADATION_RATIO"),
        "rej": require_symbol(quality_module, "DEFAULT_REJECTED_FRACTION_FLAG_THRESHOLD"),
        "min_surviving": require_symbol(
            "astrometricslib.utilities.stack_filter_floor", "DEFAULT_MINIMUM_SURVIVING_FRAMES"
        ),
        "quarantine": require_symbol(
            "astrometricslib.pipelines.stacking.pre_processing.frame_quarantine",
            "MAXIMUM_QUARANTINED_FRACTION",
        ),
        "penalty": require_symbol(
            "astrometricslib.pipelines.stacking.pre_processing.exposure_weighting",
            "MINIMUM_PENALTY_TO_WEIGHT",
        ),
        "registered": require_symbol(
            "astrometricslib.pipelines.stacking.stack_runner", "MINIMUM_REGISTERED_FRACTION"
        ),
        "gate_count": gate_count_text(gates),
        "gate_table": gate_table(gates),
    }
    values["fits_table"] = header_table([
        [
            "`EXPTIME`",
            "Mandatory",
            "`float` (s)",
            "Exposure length. Frames are stacked one exposure length at a time. Darks (frames "
            "taken with the shutter closed) are filed by it and matched within 0.1 s.",
            "A dark with no `EXPTIME` is filed as 30 s.",
        ],
        [
            "`CCD-TEMP` (or `SET-TEMP`)",
            "Recommended",
            "`float` (°C)",
            "Sensor temperature, for filing darks in temperature slots and judging how well a dark matches.",
            "The dark's temperature is not judged.",
        ],
        [
            "`GAIN` (or `ISOSPEED`)",
            "Recommended",
            "`float`",
            "Gain setting (the camera's amplification). Used for the minority-gain check and to "
            "file calibration frames.",
            "Frames fall into one unknown-gain group; `gain_homogeneity` is not checked.",
        ],
        [
            "`OFFSET` (or `BLKLEVEL`)",
            "Recommended",
            "`int`",
            "Camera offset (a fixed level added to every pixel), used to file calibration frames.",
            "Offset 0 is assumed.",
        ],
        [
            "`XBINNING` / `YBINNING`",
            "Optional",
            "`int`",
            "Binning (sensor pixels joined into one); calibration frames of another binning are "
            "never applied.",
            "1x1.",
        ],
        [
            "`FILTER`",
            "Recommended",
            "`str`",
            "Filter name. Flats (images of an evenly lit surface) are filed and picked by filter; "
            "the stack's file name carries it.",
            "Filter type `NONE`.",
        ],
        [
            "`INSTRUME` (or `CAMERA`)",
            "Recommended",
            "`str`",
            "Camera name; frames from two cameras are never stacked together.",
            "The camera of the frame record.",
        ],
    ])
    values["param_table"] = parameter_table([
        [
            "`DEFAULT_DARK_TEMPERATURE_TOLERANCE_C`",
            f"`{values['dark_tol']}` °C",
            "Designed",
            "Width of a dark temperature slot, and the largest temperature gap before a soft "
            "flag (a warning that does not stop the dark being used).",
        ],
        [
            "`DEFAULT_MINIMUM_SURVIVING_FRAMES`",
            f"`{values['min_surviving']}` frames",
            "Measured (M 13)",
            "Fewest frames that must be left after Siril's percentage-based frame filters.",
        ],
        [
            "`DEFAULT_FWHM_DEGRADATION_RATIO`",
            f"`{values['degrade']}`",
            "Measured (6 stacks)",
            "Largest ratio of the stack's star width (FWHM) to the expected width (Eq. 3).",
        ],
        [
            "`DEFAULT_REJECTED_FRACTION_FLAG_THRESHOLD`",
            f"`{values['rej']}`",
            "Measured (3 sessions)",
            "Share of pixels rejected as outliers during stacking above which "
            "`rejected_pixel_fraction` fails.",
        ],
        [
            "`MAXIMUM_QUARANTINED_FRACTION`",
            f"`{values['quarantine']}`",
            "Designed",
            "Largest share of a batch the quarantine (setting bad frames aside) may move.",
        ],
        [
            "`MINIMUM_PENALTY_TO_WEIGHT`",
            f"`{values['penalty']}`",
            "Measured (M 27)",
            "Noise penalty of giving every frame equal weight (Eq. 4), above which noise weighting is used.",
        ],
        [
            "`MINIMUM_REGISTERED_FRACTION`",
            f"`{values['registered']}`",
            "Unvalidated (Vega)",
            "Share of spectral frames that must register (align on each other); fewer triggers a retry.",
        ],
    ])
    template = r"""# Astronomical Stacking and Image Calibration Pipeline Review Guide

<<version_line>>

## Abstract

This document reviews the stacking pipeline
(`astrometricslib.pipelines.stacking`): frame checks, calibration-frame
selection, the Siril run, exposure groups, stack quality gates and the steps
after the stack.

<<glossary_line>>

## 1. Overview & Scientific Objective

### 1.1 In Plain Words

This pipeline combines many exposures of the same target into one deeper,
cleaner image, called a stack.

Each raw exposure carries flaws from the camera and the optics: a fixed
electronic level, a glow that builds up with exposure time, and darker corners
and dust shadows. Each exposure is also noisy, and some catch satellite trails
or cosmic-ray hits. The pipeline first removes the camera and optics flaws
using reference exposures taken for the purpose (calibration frames). It then
lines the exposures up on their stars and averages them, rejecting pixels that
disagree with the rest.

The result is the stacked image, a preview picture, and a quality summary that
checks both the inputs and the result.

### 1.2 Statement of Need

Raw frames carry dark current (a signal the sensor builds up from heat, which
grows with exposure time), a bias level (the fixed electronic level of an
empty exposure), and vignetting (darker corners from the optics) and dust
shadows. Master darks, biases and flats (averaged calibration frames taken
with the shutter closed, with the shortest exposure, and of an evenly lit
surface) remove them. Stacking many aligned frames raises the signal-to-noise
ratio by about $\sqrt{N}$ for $N$ frames and rejects satellite trails and
cosmic rays.

The diagram shows one run, top to bottom.

```mermaid
flowchart TD
    A["Start a stack for a target"] --> B["Select frames: one camera, no mix of spectra and normal images"]
    B --> C["Set aside frames taken at a less common gain"]
    C --> D["Set bad frames aside (too few stars, stars not round)"]
    D --> E["Check the sky background for each exposure length"]
    E --> F["Choose weighting"]
    F --> G{"Inputs unchanged?"}
    G -- yes --> H["Keep the stack on disk"]
    G -- no --> I["Stage the current stack as the previous one"]
    I --> J["Split by exposure length (and night, for spectra)"]
    J --> K["Siril: calibrate, align, stack"]
    K --> L["Combine exposure groups"]
    L --> M["Trim noisy edges"]
    M --> N["Quality summary and quality checks"]
    N --> O["Preview picture and inputs record"]
```

In code terms the run starts at `stage.stack_frames(target)`, splits frames in
`stack_runner.run_stack`, and stacks through a `StackingDriver`, whose Siril
version runs `ImageProcessing` (Section 4). Siril is an open-source
astronomy image processor. Setting bad frames aside is the frame quarantine,
and the quality summary is a `StackQualitySummary` (Section 7).

## 2. Input Requirements & FITS Header Specification

<<fits_intro>>

<<fits_table>>

## 3. Mathematical & Algorithmic Formulation

This section covers choosing calibration frames, predicting how sharp the
stack should be, weighting frames, and combining frames of different exposure
lengths.

### 3.1 Calibration-Frame Selection

A dark only cancels the heat signal if it was taken at about the same sensor
temperature as the light frames (the exposures of the target). The
calibration library files each frame under its gain, offset and binning, and
each dark also under a temperature slot <<dark_tol>> °C wide. Lights use the
darks of their own slot. With none, the nearest-temperature dark is used.

The master's temperature is the mean of its darks. A soft flag (a warning
that does not stop the dark being used) is raised when the gap between the
light's temperature and the master's is too large:

$$\left|T_\text{light} - \bar{T}_\text{dark}\right| > <<dark_tol>>\,^\circ\text{C} \tag{1}$$

The dark is still applied; the flag makes `calibration_metadata` fail. When
no dark records a temperature, the match is not judged. A gain or offset
mismatch falls back the same way. A binning mismatch never falls back.

### 3.2 Expected Stack FWHM

FWHM (full width at half maximum) is the width of a star's light profile at
half its peak height; a smaller FWHM means a sharper image. Averaging aligned
frames adds no blur of its own, so the stack's star width follows from the
input widths. The pipeline predicts that width and checks the stack against
it. For round Gaussian (bell-shaped) stars of widths $w_1, \dots, w_N$, the
stacked star's FWHM is the root mean square of the inputs:

$$\text{FWHM}_\text{expected} = \sqrt{\frac{1}{N}\sum_{i=1}^{N}\text{FWHM}_i^2} \tag{2}$$

The ratio $R$ of measured to expected width is 1 for a perfect stack. A ratio
well above 1 means the alignment or the combination blurred the stars:

$$R = \frac{\text{FWHM}_\text{stack}}{\text{FWHM}_\text{expected}} \le <<degrade>> \tag{3}$$

### 3.3 Weighting and Rejection

**Weighting.** Frames of different exposure lengths have different noise, so
giving them equal weight can waste the better frames. The configured weighting
always wins. With none configured, an imaging stack uses Siril's noise
weighting when equal weights would cost at least <<penalty>> in noise. The
penalty $P$ is 1 when all exposures are equal and grows as they differ:

$$P = \sqrt{\overline{(1/t)}\;\cdot\;\overline{t}} \ge <<penalty>> \tag{4}$$

where $t$ are the frame exposures and a bar is the mean over frames. Frames
of one exposure length give $P = 1$, so such a stack is unweighted unless a
weighting is configured.

**Rejection.** Siril stacks with `-norm=addscale` (each frame's level and
spread are matched to the reference before combining) and sigma rejection
(`rej`). Sigma rejection drops, at each pixel, values more than a set number
of standard deviations from the rest, which removes satellite trails and
cosmic rays. The low and high limits are either fixed or set from the frame
count.

### 3.4 Exposure Groups

Every stack with more than one exposure length is stacked one length at a
time. Each group's stack is turned into counts per second and put on the
reference group's brightness scale using mid-range pixels. A group whose
bright end disagrees is left out, because its brightest pixels no longer grow
in step with exposure. The rest are combined with inverse-variance weights:
each group counts in proportion to $1/\sigma^2$, so less noisy groups count
more. All groups are used, not only the largest one.

## 4. Processing Flow

Stacking has no `AnalysisPipeline` adapter. The entry point is
`stage.stack_frames`:

1. **Frame selection and checks.** One camera only, no mix of spectral and
   imaging frames, minority-gain frames set aside, frames set aside by an
   earlier run dropped, the quarantine (imaging only), and the background
   check.
2. **Skip unchanged.** `stack_inputs.py` takes a fingerprint of the frames,
   calibration frames, settings and stacking code: a short code that changes
   whenever any of them changes. A match with the last run keeps the stack on
   disk, unless `force=True`.
3. **Previous-stack staging.** The current stack moves to a staging folder.
   On success it becomes the single `_previous` version
   (`previous_stack.py`), which a person can swap back or discard.
4. **Stack.** `stack_runner.run_stack` splits the frames by exposure length
   (and by night for spectra) and calls the `StackingDriver`. The Siril driver
   (`siril_stacking_driver.py`) runs `ImageProcessing` in `siril_interface.py`:
   masters, calibration, registration (alignment) and stacking. Spectral registration that
   loses too many frames is retried with relaxed detection, then with phase
   correlation (aligning whole images by their overall pattern rather than by
   single stars).
5. **After the stack.** Noisy edges are trimmed (`stack_crop.py`). The
   quality summary and its gates are built (<<gate_count>>). A preview
   picture is made with the GraXpert and Cosmic Clarity image tools and star
   toning, and the inputs record is written last.

### 4.1 Quality Gates

<<gate_intro>>

<<gate_table>>

## 5. Tuned Calibration Parameters

<<param_intro>>

<<param_table>>

## 6. Degraded States & Fallback Pathways

1. **Temperature gap above <<dark_tol>> °C.** The nearest dark is applied
   with a soft flag; `calibration_metadata` fails. A missing `CCD-TEMP` means
   the dark is not judged.
2. **Bad frames.** Frames with too few stars or non-round stars move to
   `_excluded`. A batch whose share of bad frames is above <<quarantine>>
   moves nothing and `frame_quarantine` fails.
3. **Registration failure.** Spectral stacks are retried. On an imaging stack,
   blur shows as $R > <<degrade>>$ in `stack_sharpness`.
4. **Over-subtraction.** `zero_pixel_fraction` and `negative_pixels` catch
   darks or biases that remove more than the sky, which pushes pixels to zero
   or below.
5. **A dropped exposure group.** The combined stack is still written, and
   `exposure_groups_combined` fails.
6. **Restack failure.** The staged stack moves back, so the old stack is
   kept.

<<quality_section>>"""
    values["quality_section"] = quality_section("StackQualitySummary", "StackingPipelineQualityMetrics")
    return fill(template, {**COMMON_VALUES, **values})


# The class each pipeline's run goes through, for the index table.
PIPELINE_ENTRY_POINTS: dict[str, tuple[str, str, str]] = {
    "astrometry": ("Astrometry", "`AstrometryPipelineAdapter`", "`AstrometryQualitySummary`"),
    "photometry": ("Photometry", "`PhotometryPipelineAdapter`", "`PhotometryQualitySummary`"),
    "spectroscopy": ("Spectroscopy", "`SpectroscopyPipelineAdapter`", "`SpectroscopyQualitySummary`"),
    "asteroid_detection": (
        "Asteroid Detection",
        "`AsteroidDetectionPipelineAdapter`",
        "`AsteroidDetectionQualitySummary`",
    ),
    "stacking": ("Image Stacking", "`stage.stack_frames` (no adapter)", "`StackQualitySummary`"),
}


def build_index_doc() -> str:
    """Build the parent index documentation.

    Returns
    -------
    content : `str`
        Markdown content for index.md.
    """
    rows = []
    for key, (title, entry, summary) in PIPELINE_ENTRY_POINTS.items():
        rows.append([
            f"**{title}**",
            f"[{title}]({key}.md)",
            entry,
            summary,
            gate_count_text(discover_gate_names(key)),
        ])
    overview_table = make_table(["Pipeline", "Review Guide", "Entry Point", "Quality Summary", "Gates"], rows)
    stage_rows = make_table(
        ["Pipeline", "Stage 1 (`process_input`)"],
        [
            ["Astrometry", "No-op: returns an empty `Result`."],
            ["Photometry", "Filters frames, builds sessions; may return `has_work=False`."],
            ["Spectroscopy", "No-op: returns an empty `Result`."],
            ["Asteroid Detection", "No-op: returns an empty `Result`."],
        ],
    )
    template = r"""# Scientific Analysis Pipelines Review Index

<<version_line>>

## Abstract

This index is the entry point for scientific review of the Astrometrics
processing pipelines. Each guide covers the header cards a pipeline reads, its
maths, its processing flow, its tuned limits, its fallback paths and its
quality gates. The gate lists and counts are read from the code when the
guides are generated.

A pipeline here is a fixed series of processing steps that turns telescope
images into a scientific result. A quality gate is an automatic check on a
run that passes, fails, or is not checked when its data are missing.

Terms are defined in the [glossary](<<glossary_link>>). Each guide also
defines its terms where they first appear, so it can be read on its own.

## 1. Pipeline Architecture

Four analysis pipelines share the `AnalysisPipeline` interface
(`pipeline_base.py`). `run_pipeline` calls four methods in order:

1. **`process_input`:** decides whether there is work to do.
2. **`run`:** does the science.
3. **`validate_output`:** builds the quality summary and records the gates.
4. **`to_result_dict`:** returns the result the caller expects.

`run` is skipped only when `process_input` returns `has_work=False`. Stage 1
does real work only in photometry:

<<stage_rows>>

Stacking does not use this interface. It runs from `stage.stack_frames`
through `stack_runner.run_stack` to a `StackingDriver`, whose Siril
implementation runs `ImageProcessing` in `siril_interface.py`.

```mermaid
flowchart LR
    A["1. process_input: is there work?"] -- "work to do" --> B["2. run: do the science"]
    A -- "nothing to do" --> C["3. validate_output: build the quality summary"]
    B --> C
    C --> D["4. to_result_dict: return the result"]
```

## 2. Pipeline Portfolio Overview

In plain words, the five pipelines do this:

- **Astrometry** finds where an image points on the sky, pixel by pixel, and
  names its stars.
- **Photometry** measures how each star's brightness changes over time and
  picks out the variable stars.
- **Spectroscopy** spreads each star's light into its colours and classifies
  the star from that spectrum.
- **Asteroid Detection** finds objects that move between images and checks
  them against known asteroids and comets.
- **Image Stacking** removes camera flaws from many exposures and combines
  them into one deeper image.

<<overview_table>>

A run gate is recorded on the run's quality summary. A period check is
recorded on each period-search result and can lower its verdict.

## 3. Reviewer Checklist

When auditing the pipelines, check:

1. **Input metadata contracts.** Which header cards each pipeline reads and
   what happens when one is missing.
2. **Mathematical formulations.** The CCD equation (the error of a measured
   brightness), $\text{BJD}_\text{TDB}$ times (times measured at the solar
   system's center of mass), Stetson $J$ (a test for smooth, real brightness
   changes), the grating equation (position along a spectrum to wavelength)
   and the stack FWHM prediction (how sharp the stars of a stack should be).
3. **Degraded states.** Sparse fields, tracking drift (the field sliding
   across the sensor during a run), saturated zero orders (clipped point
   images beside the spectra) and catalog outages.
4. **Empirical status.** Which limits were measured on real data and which are
   design estimates.

```{toctree}
:maxdepth: 1
:hidden:

astrometry
photometry
spectroscopy
asteroid_detection
stacking
```
"""
    return fill(
        template,
        {
            **COMMON_VALUES,
            "glossary_link": GLOSSARY_LINK,
            "stage_rows": stage_rows,
            "overview_table": overview_table,
        },
    )


def generate_pipeline_review_docs(output_dir: Path) -> list[Path]:
    """Generate all modular pipeline review markdown documents.

    Parameters
    ----------
    output_dir : `Path`
        Target directory to write review docs into.

    Returns
    -------
    generated_files : `list` [`Path`]
        List of generated file paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    docs = {
        "index.md": build_index_doc(),
        "astrometry.md": build_astrometry_doc(),
        "photometry.md": build_photometry_doc(),
        "spectroscopy.md": build_spectroscopy_doc(),
        "asteroid_detection.md": build_asteroid_detection_doc(),
        "stacking.md": build_stacking_doc(),
    }

    generated: list[Path] = []
    for filename, content in docs.items():
        filepath = output_dir / filename
        filepath.write_text(content.strip() + "\n", encoding="utf-8")
        generated.append(filepath)

    return generated


def main() -> int:
    """Run CLI generation of pipeline review documentation.

    Returns
    -------
    exit_code : `int`
        0 if successful.
    """
    target_dir = REPO_ROOT / "documentation" / "library_design" / "pipelines"
    print(f"Generating pipeline review documents in {target_dir}...")
    files = generate_pipeline_review_docs(target_dir)
    for f in files:
        print(f"  Generated: {f.name} ({f.stat().st_size} bytes)")
    print("Pipeline review documentation generated successfully.")
    return 0


def generate_pipeline_review_docs_sphinx(app: Any) -> None:
    """Generate pipeline review docs during the Sphinx build.

    Parameters
    ----------
    app : `Any`
        Sphinx application instance.
    """
    doc_dir = Path(app.srcdir)
    target_dir = doc_dir / "library_design" / "pipelines"
    generate_pipeline_review_docs(target_dir)


def setup(app: Any) -> dict[str, Any]:
    """Register Sphinx extension hooks.

    Parameters
    ----------
    app : `Any`
        Sphinx application instance.

    Returns
    -------
    metadata : `dict` [`str`, `Any`]
        Sphinx extension metadata.
    """
    app.connect("builder-inited", generate_pipeline_review_docs_sphinx)
    return {
        "version": "0.1",
        "parallel_read_safe": True,
        "parallel_write_safe": True,
    }


if __name__ == "__main__":
    import sys

    sys.exit(main())
