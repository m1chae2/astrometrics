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
        "Fails when the image ends the run with no sky solution, from the solver or from the "
        "image's own header WCS."
    ),
    "source_detection": "Fails when no star was detected.",
    "catalog_matches": (
        "Fails when fewer than `MINIMUM_MATCHED_STARS` stars were matched to a catalog. Not "
        "checked when the image was not solved."
    ),
    "astrometric_residual": (
        "Fails when the solution misses its stars by more than "
        "`MAXIMUM_RESIDUAL_FRACTION_OF_FWHM` of a star's width (Eq. 5). Not checked when the "
        "residual, plate scale or star width is missing."
    ),
    "catalog_lookup": (
        "Fails when the remote-catalog circuit breaker tripped during the run, or when at least "
        "`MAXIMUM_FAILED_LOOKUP_FRACTION` of lookups failed. Not checked when no lookup was made."
    ),
    # Photometry run gates
    "ensemble_frame_rejection": (
        "Fails when at least `MINIMUM_ENSEMBLE_REJECTION_COUNT_TO_FLAG` frames and at least "
        "`MINIMUM_ENSEMBLE_REJECTION_FRACTION_TO_FLAG` of the frames were rejected as ensemble "
        "outliers."
    ),
    "capture_timestamps": (
        "Fails when any frame was left out for a missing or unreadable capture time. The "
        "detail names the time basis of the light curves (Section 3.1)."
    ),
    "session_content": "Fails when a session produced no light curves.",
    "session_plate_solve": (
        "Fails when a session could not be plate-solved for cross-session star matching. Not "
        "checked for a single session."
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
        "percentile), or when more than `MAXIMUM_CENTROID_FALLBACK_FRACTION` of centroids fell "
        "back to the aligned position."
    ),
    "scatter_population": (
        "Not checked when too few stars have a measured scatter, or too few to fit the noise "
        "model, so the candidate cutoff is not reliable."
    ),
    "variability_discrimination": (
        "Fails when the variability score does not rank catalogued variables above unlisted "
        "stars (AUC test). Not checked with too few of either."
    ),
    "detectable_amplitude": (
        "Fails when a sinusoidal variable would need more than `MAXIMUM_DETECTABLE_AMPLITUDE_MAG` "
        "peak to peak to clear the run's noise, so the run is blind to smaller variables."
    ),
    "flux_uncertainty": (
        "Cannot fail. Reports the median per-point error. Not checked when the errors assumed "
        "a gain of 1 e-/ADU."
    ),
    # Photometry period checks
    "holdout_nights": (
        "Period check. Leaves each night out in turn and asks whether the period found from the "
        "other nights predicts it. A failure lowers the period verdict one level."
    ),
    "alias_ambiguity": (
        "Period check. Fails when a nightly-schedule alias of the best period is nearly as strong "
        "as the best peak. A failure lowers the period verdict one level."
    ),
    # Spectroscopy
    "spectra_extracted": "Fails when no star produced a spectrum.",
    "zero_order_saturation": (
        "Fails when any star's zero-order image holds a significant share of saturated pixels."
    ),
    "spectral_classification": (
        "Fails when a classification is a poor match (best RMS above `NO_GOOD_MATCH_RMS`) or "
        "ambiguous at the class level (gap below `AMBIGUOUS_RMS_GAP`)."
    ),
    "catalog_agreement": (
        "Fails when a measured type is more than `DIFFERS_FROM_CATALOG_SUBTYPES` subtype steps "
        "from the catalog type."
    ),
    "feature_significance": (
        "Fails when a star's feature p-values could not be calibrated against control windows "
        "of its own spectrum."
    ),
    "resolution_measured": "Not checked when no spectrum had its resolution measured.",
    "processing_quality": (
        "Fails when more than `PROCESSING_QUALITY_MAXIMUM_FAILED_FRACTION` of classified spectra "
        "fail a processing-checkpoint metric."
    ),
    "wavelength_scale": (
        "Fails when the run's wavelength zero points scatter by more than "
        "`WAVELENGTH_SCALE_LIMIT_ANGSTROM` after correction."
    ),
    "gaia_xp_agreement": (
        "Fails when the median tilt of observed / Gaia XP spectra is above "
        "`GAIA_XP_SLOPE_LIMIT_PERCENT_PER_1000_ANGSTROM`. Not checked with too few stars."
    ),
    # Asteroid detection
    "search_frames": (
        "Not checked when fewer frames could be searched than a track needs, so no mover could "
        "have been found."
    ),
    "pointing_metadata": "Fails when any frame was left out for missing `RA`, `DEC` or `NAXIS` cards.",
    "ephemeris_cross_match": (
        "Fails when SkyBoT could not be reached for confirmed movers. Not checked when no mover reached it."
    ),
    "unmatched_movers": (
        "Fails when a confirmed straight-line mover matched no known body: a lead worth a manual look."
    ),
    "track_residuals": (
        "Reports how the straight-line test went and whether it used measured or assumed position errors."
    ),
    # Stacking
    "gain_homogeneity": (
        "Frames of a minority gain are set aside, which corrects the stack. Not checked when "
        "no frame records its gain."
    ),
    "frame_quarantine": (
        "Fails when a batch had more bad frames (too few stars, or stars not round) than "
        "`MAXIMUM_QUARANTINED_FRACTION`, so none were moved."
    ),
    "background_homogeneity": (
        "Fails when an exposure length shows a split in sky background. Not checked when "
        "turned off or with too few measured frames."
    ),
    "flat_level": "Fails when the master flat's level is outside the allowed share of full scale.",
    "flat_noise": "Fails when the master flat is too noisy.",
    "calibration_metadata": (
        "Fails when a calibration frame was applied with a different gain, offset or "
        "temperature, or refused for a binning mismatch."
    ),
    "calibration_frame_count": (
        "Fails when a master bias, dark or flat was built from fewer frames than the configured minimum."
    ),
    "rejected_pixel_fraction": (
        "Fails when the share of pixels rejected in the stack is above "
        "`DEFAULT_REJECTED_FRACTION_FLAG_THRESHOLD`."
    ),
    "stack_sharpness": (
        "Fails when the stack's FWHM over the expected FWHM is above "
        "`DEFAULT_FWHM_DEGRADATION_RATIO` (Eq. 3)."
    ),
    "spectral_registration": (
        "Fails when the registration check of a spectral stack raised a concern. Not checked "
        "for an imaging stack."
    ),
    "saturated_pixel_fraction": "Fails when the stack holds a significant share of saturated pixels.",
    "zero_pixel_fraction": "Fails when too many stack pixels are exactly zero (over-subtraction).",
    "negative_pixels": "Fails when the engine reports too high a share of negative pixels.",
    "exposure_group_linearity": (
        "Fails when an exposure group's bright end is not one scale factor of the reference "
        "group; that group is left out of the combination."
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
    "pipeline_name": "Unique pipeline domain identifier (e.g., astrometry, photometry).",
    "pipeline_version": "Semantic version of the pipeline processing contract.",
    "target_id": "Target celestial object identifier.",
    "target_session_ids": "List of observing session IDs included in the analysis.",
    "target_session_breakdown": "Per-session frame counts and contribution breakdown.",
    "upstream_quality_summary_reference": "Category name of prerequisite pipeline.",
    "resolved_parameters": "Effective configuration parameters used during execution.",
    "camera_profile": "Camera sensor parameters and clipping thresholds applied.",
    "quality_processing_applied": "Indicates whether QA gates and checks were executed.",
    "flagged": "True if one or more quality gates failed, indicating data concerns.",
    "flag_reasons": "Human-readable sentences describing reasons for quality flags.",
    "gates": "Complete collection of passed, failed, and unchecked GateResults.",
    "created_at": "UTC timestamp when the quality summary was generated.",
    "provenance_activity_id": "IVOA provenance activity / job tracking identifier.",
    "upstream_entity_id": "Exact upstream database entity consumed by this run.",
    "sources_detected": "Total point sources detected above background threshold.",
    "solve_attempted": "Whether a plate solve was attempted on the frame.",
    "plate_solve_succeeded": "Whether the image ended the run with a WCS solution.",
    "simbad_matched_count": "Count of detected stars matched with SIMBAD catalog entries.",
    "catalog_match_separation_rms_arcsec": "RMS separation to the nearest SIMBAD or Gaia DR3 star.",
    "plate_solve_fit_residual_rms_arcsec": "Internal solver fit residual from Astrometry.net.",
    "plate_solve_matched_star_count": "Number of stars used by solver to compute WCS.",
    "astrometry_flags": "Warning flags for conditions that limit coordinate accuracy.",
    "plate_scale_arcsec_per_pixel": "Calculated plate scale in arcseconds per detector pixel.",
    "star_fwhm_px": "Median stellar Full-Width at Half-Maximum across the image in pixels.",
    "remote_catalog_queries_attempted": "Number of remote catalog requests initiated.",
    "remote_catalog_queries_failed": "Number of remote catalog queries that timed out or failed.",
    "remote_catalog_circuit_breaker_tripped": "True if network outages halted catalog queries.",
    "plate_solve_attempts": "Number of plate solve iterations attempted.",
    "stars_processed": "Total stars tracked across light curve time series.",
    "stars_found": "Reference stars initialized in reference exposure.",
    "frames_processed": "Number of calibrated time-series frames analyzed.",
    "rejected_frames": "Frames rejected as outliers of the comparison-ensemble level.",
    "frame_ensemble_composition": "Per-frame record of comparison stars utilized.",
    "variable_candidate_count": "Number of stars flagged as short-term variable candidates.",
    "light_curve_scatter_rms_mag": "Median light curve scatter across non-variable field stars.",
    "cross_session_match_count": "Stars successfully matched across multi-night sessions.",
    "noise_model_curve": "Binned instrumental magnitude vs empirical RMS scatter curve.",
    "sessions_missing_wcs": "Sessions lacking plate-solved WCS coordinate maps.",
    "long_term_variable_candidate_count": "Stars flagged as candidate long-term variables.",
    "astrometry_identified_star_count": "Stars matched to catalog astrometric seeds.",
    "sessions_with_reused_header_wcs": "Sessions where pre-existing WCS was validated and used.",
    "sessions_with_replaced_header_wcs": "Sessions where WCS was recalculated.",
    "zero_order_saturated_pixel_fraction": "Fraction of saturated pixels in zero-order anchor.",
    "zero_order_saturation_flagged": "True if zero-order anchor exceeded sensor linearity.",
    "dispersion_angle_deg": "Angle of spectral dispersion relative to detector rows.",
    "trail_width_profile_available": "Whether cross-dispersion spatial profile was measured.",
    "median_trail_width_px": "Median Gaussian sigma (not FWHM) of the trail cross-section, in pixels.",
    "poor_match_classification_count": "Stars with classification RMS above rejection limit.",
    "ambiguous_classification_count": "Stars with ambiguous classification between classes.",
    "flagged_spectral_classifications": "Detailed records of stars with doubtful classifications.",
    "stage_quality_summary": "Rollup of quality checks across extraction stages.",
    "gaia_xp_summary": "Comparison against Gaia DR3 synthetic XP spectra.",
    "wavelength_scale_summary": "Scatter and drift of wavelength calibration zero-points.",
    "frames_with_wcs_estimate": "Frames given a WCS from the mount's RA/DEC and the stack's WCS.",
    "frames_excluded_missing_pointing_metadata": "Frames left out for missing RA, DEC or NAXIS cards.",
    "candidates_detected": "Total chains of detections, before any test.",
    "candidates_persistence_confirmed": "Chains seen in at least `min_frames_for_persistence` frames.",
    "candidates_rate_linearity_confirmed": "Candidates exhibiting linear apparent motion vectors.",
    "candidates_ephemeris_matched": "Candidates matched to known asteroids in SkyBoT database.",
    "ephemeris_queries_attempted": "SkyBoT API requests submitted.",
    "ephemeris_queries_failed": "Failed or timed-out SkyBoT queries.",
    "is_spectral": "True if image represents dispersed slitless spectroscopy.",
    "frames_submitted": "Total input frames provided for calibration and stacking.",
    "frames_stacked": "Number of light frames successfully aligned and integrated.",
    "excluded_frames": "Input frames set aside before stacking, each with its reason.",
    "rejected_pixel_fraction": "Fraction of pixels clipped during master stacking.",
    "rejected_fraction_flagged": "True if pixel rejection exceeded warning threshold.",
    "background_split_detected": "True if gradient or background discontinuity was found.",
    "background_split_detail": "Diagnostic description of background discontinuity.",
    "calibration_mismatch_flags": (
        "Soft flags for calibration frames applied with a different gain, offset or temperature."
    ),
    "flat_frame_count": "Number of individual flat exposures in master flat.",
    "flat_noise_fraction": "Relative noise level measured in master flat.",
    "flat_smoothing_sigma_px": "Gaussian smoothing applied to master flat if noisy.",
    "flat_calibration_issues": "Diagnostic messages regarding flat field quality.",
    "saturated_pixel_fraction": "Fraction of saturated pixels in final stacked image.",
    "saturation_flagged": "True if stacked image contains significant saturation.",
    "exposure_groups": "Breakdown of sub-stacks by individual exposure duration.",
    "recommended_exposure_seconds": "Estimated exposure to keep bright stars below clipping.",
    "zero_pixel_fraction": "Fraction of pixels in stack with exact zero value.",
    "zero_fraction_flagged": "True if over-subtraction caused clipping at zero ADU.",
    "negative_pixel_max_percent": "Maximum percentage of negative pixels reported by engine.",
    "negative_pixels_flagged": "True if negative pixel count exceeded tolerance.",
    "stacked_fwhm_px": "Measured stellar FWHM of the integrated master image.",
    "median_input_fwhm_px": "Median stellar FWHM across all surviving input light frames.",
    "expected_fwhm_px": "Predicted stack FWHM: the RMS of a sample of input-frame FWHMs.",
    "fwhm_degradation_ratio": "Ratio of stacked FWHM to expected FWHM.",
    "fwhm_degradation_flagged": "True if final image is significantly blurrier than inputs.",
    "stacking_engine": "Software engine performing pixel processing (e.g., Siril).",
    "stacking_engine_version": "Version string of the stacking engine.",
    "registration_reference_frame": "Filename of chosen reference registration sub-frame.",
    "registration_reference_star_count": "Number of alignment anchor stars detected.",
    "astrometry_metrics": "The astrometry measurements (Section 7.2).",
    "photometry_metrics": "The photometry measurements (Section 7.2).",
    "spectroscopy_metrics": "The spectroscopy measurements (Section 7.2).",
    "asteroid_detection_metrics": "The asteroid-detection measurements (Section 7.2).",
    "stacking_metrics": "The stacking measurements (Section 7.2).",
    "catalog_matched_star_count": "Stars matched to a SIMBAD or Gaia DR3 entry.",
    "position_only_star_count": "Stars named only by their sky position.",
    "unresolved_star_count": "Stars with no catalog match and no sky position; not saved.",
    "gaia_fit_rms_arcsec": "RMS distance to Gaia DR3 stars under the kept solution.",
    "gaia_fit_rms_before_arcsec": "RMS distance to the same Gaia stars under the solver's solution.",
    "gaia_fit_star_count": "Stars used in the Gaia refit.",
    "wcs_refinement_shift_arcsec": "Largest move of the field made by the Gaia refit.",
    "expected_stack_fwhm_px": "Expected stack FWHM: the RMS of a sample of input-frame FWHMs.",
    "fwhm_degraded": "True if the stack FWHM over the expected FWHM is above the limit.",
    "spectral_registration_flags": "Frames the spectral registration check raised a concern about.",
    "spectral_registration_checked": "Whether the spectral registration check ran.",
    "stacking_duration_seconds": "How long the stacking run took, in seconds.",
    "timed_out": "True if the stacking run timed out.",
    "debayer_applied": "Whether colour frames were debayered.",
    "input_quality": "Structured assessment of raw frame quality.",
    "output_quality": "Structured assessment of final stacked image.",
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
        f"### 7.1 Pipeline Execution Envelope (`{summary_name}`)\n\n{envelope}\n\n"
        f"### 7.2 Domain Scientific Metrics Schema (`{metrics_name}`)\n\n{metrics}\n"
    )


ANALYSIS_STAGES_NOTE = """`run_pipeline` (`pipeline_base.py`) calls the adapter's four methods in
order. It skips `run` only when `process_input` returns a `Result` with
`has_work=False`. After `validate_output` it stores the quality summary on the
target and records the run's provenance."""


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
            "Time the shutter opened. Moves Gaia DR3 positions to the image date (Eq. 3).",
            "Gaia positions stay at epoch 2016.0 and the run is flagged.",
        ],
        [
            "`FOCALLEN`",
            "Recommended",
            "`float` (mm)",
            "Focal length for the pixel-scale hint (Eq. 2).",
            "The configured focal length.",
        ],
        [
            "`XPIXSZ` / `YPIXSZ`",
            "Recommended",
            "`float` (μm)",
            "Binned pixel size, used as written. Two different values are averaged and flagged.",
            "`PIXSIZE1` / `PIXSIZE2` times `XBINNING` / `YBINNING`.",
        ],
        [
            "`PIXSIZE1` / `PIXSIZE2`",
            "Optional",
            "`float` (μm)",
            "Unbinned pixel size, multiplied by the binning.",
            "`PIXSCAL`.",
        ],
        [
            "`XBINNING` / `YBINNING`",
            "Optional",
            "`int`",
            "Binning factor applied to `PIXSIZE1` / `PIXSIZE2`.",
            "1.",
        ],
        [
            "`PIXSCAL`",
            "Optional",
            "`float` (arcsec/px)",
            "Pixel scale, read only when no pixel size is given.",
            "No scale hint: the solver searches all scales.",
        ],
        [
            "`RA` / `DEC` (or `OBJCTRA` / `OBJCTDEC`)",
            "Optional",
            "`float` or sexagesimal `str`",
            "Pointing hint when the target record has no coordinates.",
            "No position hint.",
        ],
        [
            "`OBJECT`",
            "Optional",
            "`str`",
            "Target name, used for the extended-source hint.",
            "Guessed from the file name.",
        ],
    ])
    values["param_table"] = parameter_table([
        [
            "`MAXIMUM_RESIDUAL_FRACTION_OF_FWHM`",
            f"`{values['max_res']}`",
            "Design estimate",
            "Largest residual, as a share of the star FWHM. 8 saved solves gave 0.14 to 0.44.",
        ],
        [
            "`MINIMUM_MATCHED_STARS` (`run_gates`)",
            f"`{values['min_match']}`",
            "Design estimate",
            "Fewest catalog-matched stars for the residual to mean anything.",
        ],
        [
            "`MAXIMUM_FAILED_LOOKUP_FRACTION`",
            f"`{values['max_fail']}`",
            "Design estimate",
            "Share of failed catalog lookups that fails `catalog_lookup`.",
        ],
        [
            "`CATALOG_MATCH_RADIUS_ARCSEC`",
            f"`{values['match_radius']}` arcsec",
            "Designed",
            "Radius for pairing a detected star with a SIMBAD or Gaia DR3 star.",
        ],
        [
            "`MAXIMUM_PLATE_SOLVE_SOURCES`",
            f"`{values['solver_sources']}`",
            "Designed",
            "The brightest detections sent to the solver.",
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
            "Detection threshold above the background RMS.",
        ],
        [
            "`MINIMUM_MATCHED_STARS` (`wcs_refinement`)",
            f"`{values['refit_min_stars']}`",
            "Design value",
            "Fewest Gaia matches for the SIP refit.",
        ],
        [
            "`MAXIMUM_SHIFT_ARCSEC`",
            f"`{values['refit_shift']}` arcsec",
            "Designed",
            "Largest field shift a refit may make and still be kept.",
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

## 1. Overview & Scientific Objective

**Statement of need.** Astrometry finds the World Coordinate System (WCS) of
an image: the map from detector pixels $(x, y)$ to sky coordinates
$(\alpha, \delta)$ in the International Celestial Reference System (ICRS).
Photometry needs it to match stars across sessions. Spectroscopy needs it to
name the stars whose spectra it extracts. Asteroid detection needs it to place
every frame on the sky.

```mermaid
flowchart TD
    A["FITS image (usually the target's stack)"] --> B["Star detection (DAOStarFinder, 2-D background)"]
    B --> C["Scale and position hints (header, then configuration)"]
    C --> D{"At least 4 detections?"}
    D -- yes --> E["Astrometry.net solve"]
    D -- no --> F["Image header WCS, if it has one"]
    E --> G["Refit to Gaia DR3 stars (SIP, order 2)"]
    G --> H["Name stars: SIMBAD, then Gaia DR3, then position"]
    F --> H
    H --> I["Quality gates"]
    I --> J["AstrometryQualitySummary"]
```

## 2. Input Requirements & FITS Header Specification

No card is strictly required: a missing card weakens a hint or a correction,
and the run says so in its flags.

<<fits_table>>

The pixel-scale hint is the scale from Eq. 2 with a window of ±5%. The solver
driver widens that window to 0.8 times its lower edge and 1.2 times its upper
edge before calling `solve-field`. With no focal length or no pixel size, no
scale hint is sent.

## 3. Mathematical & Algorithmic Formulation

### 3.1 Point Source Detection

The background is a 2-D map (photutils `Background2D`, median estimator,
3σ clipping). A small image falls back to one sigma-clipped median. A source
is kept when its peak stands above the background by the threshold $k$:

$$\frac{I(x, y) - B(x, y)}{\sigma_B} \ge k = <<threshold>> \tag{1}$$

`DAOStarFinder` keeps sharpness in $[0.2, 1.0]$ and roundness in
$[-0.6, +0.6]$, which rejects hot pixels and filament structure. The kernel
width is the measured blob width: the second moment of the light in a 30-pixel
box around the brightest sources. It counts faint wings and neighbours, so it
reads about twice the stellar FWHM (full width at half maximum). Detections
closer than 15 px are merged. Colour frames are block-averaged before
detection.

### 3.2 Pixel-Scale Hint

$$s = 206.265 \, \frac{p}{f} \tag{2}$$

where $s$ is the scale in arcsec per pixel, $p$ the binned pixel size in μm
and $f$ the focal length in mm.

### 3.3 Plate Solve and Gaia DR3 Refit

The brightest <<solver_sources>> detections go to Astrometry.net. The
solver fits only the bright stars of its index files, so its solution can be
a pixel or more off. The pipeline then refits the solution to the field's
Gaia DR3 stars (`wcs_refinement.py`). Each detection is matched to the nearest
Gaia star within <<refit_r1>> px, then <<refit_r2>> px on later passes. A
tangent-plane solution with SIP distortion of order <<refit_sip>> is fitted
with `astropy.wcs.utils.fit_wcs_from_points`. Stars more than
<<refit_clip>> robust standard deviations off are dropped, and the fit repeats
until the matched set stops changing. The refit needs at least
<<refit_min_stars>> matches. It is kept only when it fits Gaia better than
the solver's solution and moves the field by at most <<refit_shift>> arcsec.

### 3.4 Proper Motion Epoch Propagation

Gaia DR3 positions are given at epoch <<gaia_epoch>>. Before matching, they
are moved to the image time with astropy's `SkyCoord.apply_space_motion`. To
first order this is

$$\alpha(t) = \alpha_0 + \frac{\mu_{\alpha*} \, \Delta t}{\cos \delta_0}, \qquad
\delta(t) = \delta_0 + \mu_\delta \, \Delta t, \qquad \Delta t = t_\text{obs} - 2016.0 \tag{3}$$

where $\mu_{\alpha*}$ is Gaia's `pmra` (which already includes
$\cos \delta$) and $\mu_\delta$ is `pmdec`.

### 3.5 Star Naming

With a solution, each star is named from SIMBAD first, within
<<match_radius>> arcsec. Stars SIMBAD does not know are matched against a
Gaia DR3 cone, whose faint limit is set from the frame's own zero point. Stars
neither catalog knows get a position-based name such as
`FIELD_J123.4+45.6`. Separations are great-circle distances from
`SkyCoord.separation`. The catalog match separation is

$$\text{RMS}_\text{sep} = \sqrt{\frac{1}{N} \sum_{i=1}^N \Delta \theta_i^2} \tag{4}$$

over the $N$ stars matched within the radius.

### 3.6 Residual Gate

The residual $r$ (arcsec) is judged against the star width:

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

<<gate_table>>

## 5. Tuned Calibration Parameters

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
5. **Catalog outages.** Repeated failures trip a process-wide circuit breaker.
   Unnamed stars keep position-based names, and `catalog_lookup` reports the
   outage.

<<quality_section>>"""
    values["analysis_note"] = ANALYSIS_STAGES_NOTE
    values["quality_section"] = quality_section(
        "AstrometryQualitySummary", "AstrometryPipelineQualityMetrics"
    )
    return fill(template, values)


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
            "Time the shutter opened. The light-curve time is mid-exposure BJD_TDB (Section 3.1).",
            "That frame alone is rejected with a reason; `capture_timestamps` fails.",
        ],
        [
            "`EXPTIME` (or `EXPOSURE`)",
            "Recommended",
            "`float` (s)",
            "Mid-exposure time and flux per second.",
            "1.0 s is assumed.",
        ],
        [
            "`EGAIN`",
            "Optional",
            "`float` (e-/ADU)",
            "Gain for the flux errors, when the camera profile has none. `GAIN` is never read.",
            "1 e-/ADU is assumed; `flux_uncertainty` is not checked.",
        ],
        [
            "`RDNOISE`",
            "Optional",
            "`float` (e-)",
            "Read noise for the flux errors, when the camera profile has none.",
            "0 e- is assumed.",
        ],
        [
            "`AIRMASS` (or `CENTAIRM`, `AIRM`, or altitude `CENTALT`, `ALTITUDE`, `ALT`)",
            "Optional",
            "`float`",
            "Airmass for the optional ensemble airmass correction.",
            "1.0.",
        ],
    ])
    values["param_table"] = parameter_table([
        [
            "`MINIMUM_ENSEMBLE_REJECTION_FRACTION_TO_FLAG`",
            f"`{values['rej_frac']}`",
            "Empirical (7.2% baseline)",
            "Share of frames rejected as ensemble outliers that fails the gate.",
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
            "Default of `measure_aperture_photometry`.",
        ],
        [
            "Sky annulus radii",
            f"`{values['r_in']:g}` to `{values['r_out']:g}` px",
            "Designed",
            "Inner and outer radius of the sky ring.",
        ],
        [
            "`MINIMUM_COMPARISON_STARS`",
            f"`{values['min_comp']}`",
            "Design choice",
            "Vetting stops at this size; fewer fails `comparison_ensemble`.",
        ],
        [
            "`DEFAULT_MAXIMUM_COMPARISON_STARS`",
            f"`{values['max_comp']}`",
            "Design choice",
            "Cap on the comparison set.",
        ],
        [
            "`MINIMUM_STARS_FOR_NOISE_MODEL`",
            f"`{values['noise_model_stars']}`",
            "Designed",
            "Fewer stars: no noise model, and the CV cutoff (Eq. 9) picks candidates.",
        ],
        [
            "`FALSE_DISCOVERY_LEVEL`",
            f"`{values['fdr']}`",
            "Design value (synthetic fields)",
            "Largest Benjamini-Hochberg q-value of a candidate.",
        ],
        [
            "`EXCESS_SCATTER_THRESHOLD`",
            f"`{values['excess']}`",
            "Design value",
            "Fixed threshold on the excess scatter.",
        ],
        [
            "`DEFAULT_VARIABILITY_SIGMA_THRESHOLD`",
            f"`{values['cv_sigma']}`",
            "Measured (flags 5.2%)",
            "MAD multiplier of the CV cutoff.",
        ],
        [
            "`MAXIMUM_DETECTABLE_AMPLITUDE_MAG`",
            f"`{values['max_amp']}` mag",
            "Design estimate",
            "If the smallest detectable peak-to-peak amplitude is larger, the run is called blind "
            "to most variables.",
        ],
        [
            "`MAXIMUM_CORNER_DISPLACEMENT_PX`",
            f"`{values['corner']}` px",
            "Designed",
            "Largest 95th-percentile corner displacement from rotation and scale change.",
        ],
        [
            "`MAXIMUM_ENSEMBLE_SCATTER_OVER_PREDICTED`",
            f"`{values['ens_scatter']}`",
            "Design value (3 fields)",
            "Largest comparison-star scatter over the scatter their errors predict.",
        ],
    ])
    template = r"""# Differential Photometry and Light Curve Pipeline Review Guide

<<version_line>>

## Abstract

This document reviews the differential aperture photometry, ensemble
normalization, variability and period-search pipeline
(`astrometricslib.pipelines.photometry`).

## 1. Overview & Scientific Objective

**Statement of need.** Time-domain photometry measures how a star's
brightness changes. It finds pulsating, eclipsing and rotating variables and
planet transits. From the ground, cloud, haze and airmass change every star's
flux together. Differential photometry removes that shared change by dividing
each star by an ensemble of constant comparison stars in the same frames.

```mermaid
flowchart TD
    A["Frames in the requested filter (spectral frames left out)"] --> B["Group into observing sessions"]
    B --> C["Per session: reference stars and optional catalog seed"]
    C --> D["Per frame: DATE-OBS, alignment (shift, rotation, scale), centroid_com"]
    D --> E["Aperture and sky ring, CCD-equation error"]
    E --> F["Mid-exposure BJD_TDB"]
    F --> G["Fixed comparison set, leave-one-out ensemble"]
    G --> H["Noise model and variability indices"]
    H --> I["Candidate rule (chi-square, Stetson J, excess scatter, q-value)"]
    I --> J["Cross-session merge (two or more sessions)"]
    J --> K["Known-variable labels"]
    K --> L["Period search with false-alarm checks"]
    L --> M["Run gates"]
    M --> N["PhotometryQualitySummary"]
```

## 2. Input Requirements & FITS Header Specification

The filter comes from each frame's record, and the observatory site comes from
the configuration. The target's sky position comes from the target record.

<<fits_table>>

## 3. Mathematical & Algorithmic Formulation

### 3.1 Capture Time ($\text{BJD}_\text{TDB}$)

`DATE-OBS` is the shutter-open time in UTC. The pipeline adds half the
exposure, converts UTC to TDB (Barycentric Dynamical Time), and adds the
light-travel time to the solar-system barycenter for the target's direction
(up to ±499 s). The result is the mid-exposure Barycentric Julian Date,
$\text{BJD}_\text{TDB}$. With no site configured, the observer is placed at
Earth's center. With no target position, the times stay as UTC start times.
The time basis is quoted in the `capture_timestamps` gate.

### 3.2 Centroid and Aperture Photometry

Each star is placed at its reference position moved by the frame's fitted
shift, rotation and scale change. It is then re-centered on its
brightness-weighted centroid (photutils `centroid_com`). When the centroid is
not trustworthy, the aligned position is kept and the reason is recorded.
Apertures are never rounded to whole pixels.

The net flux in a circle of radius <<radius>> px is

$$F_\text{net} = \sum_{i \in \text{ap}} I_i - n_\text{pix} \bar{B} \tag{1}$$

where $\bar{B}$ is the median of a sky ring from <<r_in>> to <<r_out>> px.
Its 1σ error follows the CCD equation in electrons:

$$\sigma_F = \frac{1}{g}\sqrt{F_e + n_\text{pix}\left(S_e + R^2 + D_e\right)
  + \frac{n_\text{pix}^2}{n_\text{sky}}\left(S_e + R^2\right)} \tag{2}$$

Here $g$ is the gain (e-/ADU), $F_e = g F_\text{net}$, $S_e = g \bar{B}$ is
the sky per pixel, $R$ the read noise (e-), $D_e$ the dark current per pixel,
and $n_\text{sky}$ the number of sky pixels. The last term is the error of the
sky level itself. $\sigma_F$ is in ADU, like $F_\text{net}$.

### 3.3 Differential Ensemble Normalization

A fixed comparison set is chosen per session. Candidates are measured,
unsaturated and positive in every usable frame and not listed as variable.
The brightest 2% are skipped, the band reaches the brightest 30%, and
candidates that scatter more than their errors allow are dropped until
<<min_comp>> remain. At most <<max_comp>> are kept.

Each member's flux is divided by its own session mean. The frame's ensemble
level is the inverse-variance weighted mean of those ratios:

$$L_k = \frac{\sum_j w_j \, F_{j,k} / \langle F_j \rangle}{\sum_j w_j}, \qquad
w_j = \frac{1}{\sigma_j^2}, \qquad \sigma_{L} = \frac{1}{\sqrt{\sum_j w_j}} \tag{3}$$

A comparison star is divided by the level built from the other members only
(leave one out), so its own changes never enter its divisor. A star's
normalized flux is $f_k = F_k / N_k$, with $N_k$ the level in ADU per second,
and its error is

$$\sigma_{f} = \sqrt{\left(\frac{\sigma_F}{N}\right)^2 + \left(\frac{F \sigma_N}{N^2}\right)^2} \tag{4}$$

Normalization needs at least 2 comparison stars. With fewer, the raw light
curves are kept.

### 3.4 Variability Indices and Candidate Rule

A noise model gives the scatter a constant star of magnitude $m$ shows in this
field, $\sigma_\text{model}(m)$, from the median scatter in brightness bins. It
needs at least <<noise_model_stars>> stars. Three indices are measured per
star:

$$\chi^2_\nu = \frac{1}{n-1}\sum_{i=1}^{n}\frac{(x_i - \bar{x}_w)^2}{\sigma_i^2} \tag{5}$$

$$\delta_i = \sqrt{\frac{n}{n-1}}\,\frac{x_i - \bar{x}_w}{\sigma_i}, \qquad
J = \frac{1}{n-1}\sum_{i=1}^{n-1}\operatorname{sgn}(P_i)\sqrt{|P_i|}, \qquad
P_i = \delta_i \, \delta_{i+1} \tag{6}$$

$$E = \frac{\sigma_\text{measured}}{\sigma_\text{model}(m)} \tag{7}$$

Eq. 6 is the single-band Stetson J with consecutive pairs and unit weights
(Stetson 1996, PASP 108, 851). The thresholds on $\chi^2_\nu$ and $J$ are the
field median plus <<cal_sigmas>> robust standard deviations. The threshold on
$E$ is fixed at <<excess>>. The ranking score is

$$S = \begin{cases}\min\left(\chi^2_\nu / \chi^2_\text{thr},\; E / E_\text{thr}\right) & J > J_\text{thr}\\
\min\left(\chi^2_\nu / \chi^2_\text{thr},\; E / E_\text{thr},\; 1\right) & \text{otherwise}
\end{cases} \tag{8}$$

A star is a candidate when $S > 1$ and its q-value is below <<fdr>>. The
q-value is the Benjamini-Hochberg false discovery rate of the chi-square
false-alarm probability (the upper tail of the chi-square distribution with
$n - 1$ degrees of freedom).

A field too small for a noise model falls back to a cutoff on the
coefficient of variation (CV, standard deviation over mean):

$$\text{CV}_\text{cut} = \max\left(0.02,\;
  \text{median}(\text{CV}) + <<cv_sigma>> \times \text{MAD}(\text{CV})\right) \tag{9}$$

where MAD is the unscaled median absolute deviation.

### 3.5 Period Search

The target's star and the brightest stars are searched with a Lomb-Scargle
periodogram (smooth cycles) and box least squares (repeating dips). The
false-alarm probability (FAP) is the share of block-shuffled copies of the
light curve that do as well. A "detected" verdict needs a FAP below
<<fap_detect>>. The Šidák correction, $1 - (1 - p)^m$ for $m$ searches,
keeps the chance of a false detection across the whole run at that level
(`family_wise_correction.py`). Two period checks (Section 4.1) can lower the
verdict.

### 3.6 Cross-Session Merge and Labels

With two or more sessions, each star's light curves are matched by sky
position and merged (`batch.py`). Each session keeps its own normalized level.
Stars whose level changes between sessions become long-term candidates.
Candidates are then labelled from SIMBAD, Gaia DR3 and VSX as listed,
not listed, or unknown (`known_variability_labels.py`).

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

Run gates are recorded on the quality summary. Period checks are recorded on
each period result.

<<gate_table>>

## 5. Tuned Calibration Parameters

<<param_table>>

## 6. Degraded States & Fallback Pathways

1. **Sparse fields.** Below <<noise_model_stars>> usable stars there is no
   noise model. Candidates come from the CV cutoff of Eq. 9, and
   `scatter_population` is not checked. Below <<scatter_pop>> stars with a
   scatter, the cutoff itself is called unreliable.
2. **Few comparison stars.** With fewer than 2, raw light curves are kept.
   With fewer than <<min_comp>>, `comparison_ensemble` fails.
3. **Missing or bad `DATE-OBS`.** Only that frame is rejected, with a reason.
   It never gets the wall-clock time.
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
    return fill(template, values)


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
            "Exposure start, for the refraction correction's mid-exposure time.",
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
            "Counts-per-second scale of the spectrum; mid-exposure time.",
            "The intensity scale is left unknown, never guessed.",
        ],
        [
            "`BITPIX` / `STACKCNT`",
            "Optional",
            "`int`",
            "Mark a Siril float stack, whose mean frame exposure is `EXPTIME` / `STACKCNT`.",
            "Treated as a raw frame when `BITPIX` > 0 and there is no `STACKCNT`.",
        ],
        [
            "`AIRMASS`",
            "Recommended",
            "`float`",
            "Airmass for the extinction correction (Eq. 3).",
            "The extinction correction is skipped, and the skip is recorded.",
        ],
        [
            "`RA` / `DEC`",
            "Optional",
            "`float` (deg)",
            "Field center for star registration when there is no solved stack.",
            "No registration reference by position.",
        ],
    ])
    values["param_table"] = parameter_table([
        [
            "`NO_GOOD_MATCH_RMS`",
            f"`{values['no_good']}`",
            "Measured (14 stars)",
            "Best-reference relative RMS above which a classification is a poor match.",
        ],
        [
            "`UNRELIABLE_MATCH_RMS`",
            f"`{values['unreliable']}`",
            "Measured (55 stars)",
            "Above this, no spectral type is given at all.",
        ],
        [
            "`AMBIGUOUS_RMS_GAP`",
            f"`{values['gap']}`",
            "Measured (reference ladder spacing)",
            "Smallest RMS gap between the best reference and the next one.",
        ],
        [
            "`DIFFERS_FROM_CATALOG_SUBTYPES`",
            f"`{values['subtypes']}`",
            "Measured (29 stars)",
            "Subtype steps beyond which the type disagrees with the catalog.",
        ],
        [
            "`APERTURE_SIGMA_MULTIPLIER`",
            f"`{values['box']}`",
            "Designed",
            "Box half-width in units of the trail's Gaussian sigma.",
        ],
        [
            "`SKY_BAND_GAP_PX` / `SKY_BAND_WIDTH_PX`",
            f"`{values['sky_gap']}` px / `{values['sky_width']}` px",
            "Measured (Vega, one frame)",
            "Sky-band geometry on each side of the box.",
        ],
        [
            "`WAVELENGTH_SCALE_LIMIT_ANGSTROM`",
            f"`{values['wl_limit']}` Å",
            "Designed",
            "Largest scatter of the run's corrected wavelength zero points.",
        ],
        [
            "`GAIA_XP_SLOPE_LIMIT_PERCENT_PER_1000_ANGSTROM`",
            f"`{values['xp_limit']}`",
            "Designed",
            "Largest median tilt of observed / Gaia XP.",
        ],
        [
            "Flux calibration",
            "Relative F_λ",
            "Calibrated to a constant",
            "QE-, response- and extinction-corrected energy flux, up to one unknown scale factor.",
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

## 1. Overview & Scientific Objective

**Statement of need.** A transmission grating in front of the camera (for
example a Star Analyser 100 or 200) spreads each star's light into a
first-order spectrum on the sensor, next to the star's undispersed image (the
zero order). The pipeline turns those trails into calibrated spectra and
classifies them against the Pickles (1998) stellar library.

```mermaid
flowchart TD
    A["Spectral stack"] --> B["Find stars (astrometry detection, no solve)"]
    B --> C["Name stars from the solved standard stack (WCS, else geometry)"]
    C --> D["Dispersion angle and trail tracing"]
    D --> E["Box extraction with two sky bands"]
    E --> F["Neighbour wing correction"]
    F --> G["Grating-equation wavelengths"]
    G --> H["Differential refraction shift"]
    H --> I["Wavelength zero-point shift"]
    I --> J["QE correction"]
    J --> K["Instrument response and airmass extinction"]
    K --> L["Dereddening (catalog E(B-V))"]
    L --> M["Classification against Pickles references"]
    M --> N["Gaia XP comparison"]
    N --> O["Four checkpoints and run gates"]
    O --> P["SpectroscopyQualitySummary"]
```

## 2. Input Requirements & FITS Header Specification

The dispersion direction is measured from the image. `DISPAXIS` and `GRATROT`
are not read. The observatory site comes from the configuration.

<<fits_table>>

## 3. Mathematical & Algorithmic Formulation

### 3.1 Wavelength Model

A sample's distance $x$ from the zero-order center, along the dispersion,
becomes a wavelength through the first-order grating equation
(`optics_physics.py`):

$$\lambda(x) = d \, \sin\left(\arctan\frac{x \, p}{L}\right) \tag{1}$$

where $d$ is the groove spacing (1 / lines per mm), $p$ the pixel pitch and
$L$ the grating-to-sensor distance, the one fitted number. Two shifts follow.
First, differential atmospheric refraction moves each wavelength along the
trail by $R(\lambda) - R(\lambda_\text{eff})$, projected onto the dispersion
direction. It needs the site, a WCS and the exposure time. Second, a
zero-point shift corrects an offset zero-order center. It is measured from
known absorption lines, each at least <<zp_sig>> noise widths deep, and is
applied when at least <<zp_lines>> lines agree.

### 3.2 Extraction and Background Subtraction

The trail is traced step by step. At each step a Gaussian is fitted across
the trail, and the box half-width is

$$w(x) = <<box>> \, \sigma_\text{trail}(x) \tag{2}$$

where $\sigma_\text{trail}$ is the fitted Gaussian sigma, smoothed over 61
steps. `median_trail_width_px` in the summary is this sigma too, not a FWHM.
Edge pixels count for the fraction inside the box. Two sky bands, each
<<sky_width>> px wide and <<sky_gap>> px from the box, are sigma-clipped. The
sky level is the mean of their medians, or the lower band alone when they
disagree by more than the noise allows. A bright neighbour's wings are
removed with a blur profile measured from an isolated star
(`neighbor_trail_deblending.py`, `neighbor_wing_correction.py`).

### 3.3 Flux Calibration

The counts are divided by the sensor's quantum efficiency (QE), then by the
instrument response. The response is Vega's observed spectrum over the Pickles
A0V template. The airmass difference from the response's reference star is
removed with the extinction curve $k(\lambda)$:

$$F_\text{corr}(\lambda) = F(\lambda)\; 10^{0.4\, k(\lambda)\, (X - X_\text{ref})} \tag{3}$$

The result is a relative $F_\lambda$ (energy flux per unit wavelength) up to
one constant. When a catalog gives the star's $E(B-V)$, interstellar
reddening is removed with the Cardelli, Clayton and Mathis (1989) law with
$R_V = 3.1$.

### 3.4 Classification

Each reference spectrum is blurred to the instrument's resolution and scaled
to the observation by a best-fit factor $a$. The match score is the relative
RMS

$$\text{RMS} = \frac{1}{\bar{F}}
  \sqrt{\frac{1}{M}\sum_{i=1}^{M}\left[F(\lambda_i) - a\,T(\lambda_i)\right]^2} \tag{4}$$

The references are ranked by this score, and the lowest wins. Above
<<no_good>> the match is poor. Above <<unreliable>> no type is given. A gap
below <<gap>> to the next reference makes the match ambiguous.

### 3.5 Gaia XP Comparison

Where Gaia DR3 has an XP spectrum for the star, both spectra are brought to the
same resolution and divided by their median over 5400-5600 Å. The tilt of
their ratio tests the response, the extinction correction and the wavelength
scale together.

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

<<gate_table>>

## 5. Tuned Calibration Parameters

<<param_table>>

## 6. Degraded States & Fallback Pathways

1. **Zero-order saturation.** `zero_order_saturation` fails. A saturated
   center can offset the wavelength scale; the zero-point shift corrects it
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
    return fill(template, values)


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
            "The mount's pointing, which centers the frame's WCS.",
            "The frame is excluded; `pointing_metadata` fails.",
        ],
        [
            "`NAXIS1` / `NAXIS2`",
            "Mandatory (per frame)",
            "`int`",
            "Frame size; the WCS reference pixel is its center.",
            "The frame is excluded; `pointing_metadata` fails.",
        ],
        [
            "Stack WCS",
            "Mandatory",
            "WCS cards of the stacked image",
            "Rotation and scale for every frame's WCS.",
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
            "Fewest detections in a chain.",
        ],
        [
            "`pixel_match_tolerance_px`",
            cfg("pixel_match_tolerance_px"),
            "Designed",
            "Pixel spread below which a chain is a hot pixel.",
        ],
        [
            "`sky_match_tolerance_arcsec`",
            cfg("sky_match_tolerance_arcsec"),
            "Designed",
            "Sky spread below which a chain is a fixed star.",
        ],
        [
            "`rate_min_arcsec_per_hour` / `rate_max_arcsec_per_hour`",
            f"{cfg('rate_min_arcsec_per_hour')} / {cfg('rate_max_arcsec_per_hour')} arcsec/h",
            "Designed",
            "Allowed range of the total rate.",
        ],
        [
            "`chain_match_radius_max_arcsec`",
            f"{cfg('chain_match_radius_max_arcsec')} arcsec",
            "Designed",
            "Cap on the chaining search radius.",
        ],
        [
            "`residual_rms_max_multiple`",
            cfg("residual_rms_max_multiple"),
            "Designed",
            "Largest per-axis RMS from the line, in units of the position error.",
        ],
        [
            "`min_displacement_error_multiple`",
            cfg("min_displacement_error_multiple"),
            "Designed",
            "Smallest total displacement, in units of the position error.",
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
            "SkyBoT match radius, at both the first and last detection.",
        ],
        [
            "`_POINTING_CORRECTION_SEARCH_RADIUS_ARCSEC`",
            f"`{values['pointing_radius']:g}` arcsec",
            "Unvalidated (NGC 2403)",
            "Pairing radius for the per-frame pointing-offset vote.",
        ],
    ])
    template = r"""# Asteroid and Moving Object Detection Pipeline Review Guide

<<version_line>>

## Abstract

This document reviews the moving-object detection pipeline
(`astrometricslib.pipelines.asteroid_detection`): per-frame pointing,
detection, per-night chaining, the stationary and straight-line tests, and the
SkyBoT cross-match.

## 1. Overview & Scientific Objective

**Statement of need.** Asteroids and comets move against the stars, so they
show up as point sources that shift from frame to frame. The pipeline finds
those sources, links them into straight-line tracks and asks the IMCCE SkyBoT
service which known bodies were there. A confirmed mover with no match is a
lead for a person to check.

```mermaid
flowchart TD
    A["Plate-solved stack (prerequisite)"] --> B["Reference stars from the stack"]
    C["Light frames with timestamps"] --> D["Frame WCS: stack rotation and scale, mount RA/DEC"]
    B --> E["Per-frame pointing correction (offset vote)"]
    D --> E
    E --> F["Point-source detection"]
    F --> G["Chain detections within each night"]
    G --> H["Stationary tests (pixel, sky)"]
    H --> I["Straight-line and rate tests"]
    I --> J["SkyBoT cross-match"]
    J --> K["Run gates"]
    K --> L["AsteroidDetectionQualitySummary"]
```

## 2. Input Requirements & FITS Header Specification

The target must have a plate-solved stack: run astrometry first. Without a
stacked image the run raises `ConflictError`.

<<fits_table>>

## 3. Mathematical & Algorithmic Formulation

### 3.1 Per-Frame Pointing

Siril does not save each frame's alignment, so each frame's WCS takes its
rotation and scale from the stack and its center from the mount's `RA`/`DEC`
(`frame_wcs_composer.py`). A mount is often tens of arcseconds off. The stack's
own stars are therefore matched to each frame's detections within
<<pointing_radius>> arcsec, every pair votes for an offset in 5-arcsec bins,
and the winning offset is removed from the frame's positions.

### 3.2 Chaining

Detections are linked only within one observing night. Frames are taken in
time order, and each open chain may claim the nearest detection in the next
frame within

$$r = \min\left(v_\text{max}\, \Delta t,\; r_\text{cap}\right) \tag{1}$$

with $v_\text{max}$ = <<cfg_rate_max_arcsec_per_hour>> arcsec/h and
$r_\text{cap}$ = <<cfg_chain_match_radius_max_arcsec>> arcsec. Claims go
nearest-first over all chains. A chain may skip frames; it is not limited to
consecutive ones. A chain needs at least <<cfg_min_frames_for_persistence>>
detections.

### 3.3 Stationary Tests

Both spreads are measured from the chain's mean position:

$$\max_i \left\lVert \mathbf{p}_i - \bar{\mathbf{p}} \right\rVert
  < <<cfg_pixel_match_tolerance_px>>\ \text{px} \;\Rightarrow\; \text{hot pixel}, \qquad
\max_i \theta\left(\mathbf{s}_i, \bar{\mathbf{s}}\right)
  < <<cfg_sky_match_tolerance_arcsec>>'' \;\Rightarrow\; \text{fixed star} \tag{2}$$

where $\mathbf{p}_i$ is a pixel position and $\mathbf{s}_i$ a sky position
on the tangent plane.

### 3.4 Straight-Line and Rate Tests

RA and Dec offsets on the tangent plane are each fitted against time:

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
  position error (otherwise `REJECTED_NON_MONOTONIC`).

### 3.5 Known-Body Match

SkyBoT is asked twice per mover: at its first and at its last detection. A
known body matches only if it is within
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

`mpc_report.py` can write Minor Planet Center 80-column and ADES text from
detections, but no pipeline step calls it yet. A run does not produce MPC
records. `rotation_period.py` builds a light curve from a track for the
period search; the pipeline does not call it either.

### 4.1 Quality Gates

<<gate_table>>

## 5. Tuned Calibration Parameters

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
    return fill(template, values)


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
            "Frames are stacked one exposure length at a time. Darks are filed by it and "
            "matched within 0.1 s.",
            "A dark with no `EXPTIME` is filed as 30 s.",
        ],
        [
            "`CCD-TEMP` (or `SET-TEMP`)",
            "Recommended",
            "`float` (°C)",
            "Sensor temperature, for filing darks in temperature slots and judging the match.",
            "The dark's temperature is not judged.",
        ],
        [
            "`GAIN` (or `ISOSPEED`)",
            "Recommended",
            "`float`",
            "Gain setting: the minority-gain check and calibration filing.",
            "Frames fall into one unknown-gain group; `gain_homogeneity` is not checked.",
        ],
        [
            "`OFFSET` (or `BLKLEVEL`)",
            "Recommended",
            "`int`",
            "Camera offset for calibration filing.",
            "Offset 0 is assumed.",
        ],
        [
            "`XBINNING` / `YBINNING`",
            "Optional",
            "`int`",
            "Binning; calibration frames of another binning are never applied.",
            "1x1.",
        ],
        [
            "`FILTER`",
            "Recommended",
            "`str`",
            "Flats are filed and picked by filter; the stack's file name carries it.",
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
            "Width of a dark temperature slot and the largest gap before a soft flag.",
        ],
        [
            "`DEFAULT_MINIMUM_SURVIVING_FRAMES`",
            f"`{values['min_surviving']}` frames",
            "Measured (M 13)",
            "Floor on frames left after Siril's percentage frame filters.",
        ],
        [
            "`DEFAULT_FWHM_DEGRADATION_RATIO`",
            f"`{values['degrade']}`",
            "Measured (6 stacks)",
            "Largest stack FWHM over the expected FWHM (Eq. 3).",
        ],
        [
            "`DEFAULT_REJECTED_FRACTION_FLAG_THRESHOLD`",
            f"`{values['rej']}`",
            "Measured (3 sessions)",
            "Largest share of rejected pixels.",
        ],
        [
            "`MAXIMUM_QUARANTINED_FRACTION`",
            f"`{values['quarantine']}`",
            "Designed",
            "Largest share of a batch the quarantine may move.",
        ],
        [
            "`MINIMUM_PENALTY_TO_WEIGHT`",
            f"`{values['penalty']}`",
            "Measured (M 27)",
            "Equal-weight noise penalty (Eq. 4) above which noise weighting is used.",
        ],
        [
            "`MINIMUM_REGISTERED_FRACTION`",
            f"`{values['registered']}`",
            "Unvalidated (Vega)",
            "Share of spectral frames that must register before a retry.",
        ],
    ])
    template = r"""# Astronomical Stacking and Image Calibration Pipeline Review Guide

<<version_line>>

## Abstract

This document reviews the stacking pipeline
(`astrometricslib.pipelines.stacking`): frame checks, calibration-frame
selection, the Siril run, exposure groups, stack quality gates and the steps
after the stack.

## 1. Overview & Scientific Objective

**Statement of need.** Raw frames carry dark current, a bias level, and
vignetting and dust shadows. Master darks, biases and flats remove them.
Stacking many aligned frames raises the signal-to-noise ratio by about
$\sqrt{N}$ and rejects satellite trails and cosmic rays.

```mermaid
flowchart TD
    A["stage.stack_frames(target)"] --> B["Select frames: one camera, no spectral and imaging mix"]
    B --> C["Minority-gain frames set aside"]
    C --> D["Frame quarantine (star count, roundness)"]
    D --> E["Background homogeneity per exposure length"]
    E --> F["Choose weighting"]
    F --> G{"Inputs unchanged?"}
    G -- yes --> H["Keep the stack on disk"]
    G -- no --> I["Stage the current stack as the previous one"]
    I --> J["stack_runner.run_stack: split by exposure (and night, for spectra)"]
    J --> K["StackingDriver -> ImageProcessing (Siril): calibrate, register, stack"]
    K --> L["Combine exposure groups"]
    L --> M["Trim noisy edges"]
    M --> N["StackQualitySummary and gates"]
    N --> O["Preview picture and inputs record"]
```

## 2. Input Requirements & FITS Header Specification

<<fits_table>>

## 3. Mathematical & Algorithmic Formulation

### 3.1 Calibration-Frame Selection

The calibration library files each frame under its gain, offset and binning,
and each dark also under a temperature slot <<dark_tol>> °C wide. Lights
use the darks of their own slot. With none, the nearest-temperature dark is
used. The master's temperature is the mean of its darks, and a soft flag is
raised when

$$\left|T_\text{light} - \bar{T}_\text{dark}\right| > <<dark_tol>>\,^\circ\text{C} \tag{1}$$

The dark is still applied; the flag makes `calibration_metadata` fail. When
no dark records a temperature, the match is not judged. A gain or offset
mismatch falls back the same way. A binning mismatch never falls back.

### 3.2 Expected Stack FWHM

For round Gaussian stars of widths $w_1, \dots, w_N$, the stacked star's FWHM
is the root mean square of the inputs:

$$\text{FWHM}_\text{expected} = \sqrt{\frac{1}{N}\sum_{i=1}^{N}\text{FWHM}_i^2} \tag{2}$$

$$R = \frac{\text{FWHM}_\text{stack}}{\text{FWHM}_\text{expected}} \le <<degrade>> \tag{3}$$

### 3.3 Weighting and Rejection

The configured weighting always wins. With none configured, an imaging stack
uses Siril's noise weighting when equal weights would cost at least
<<penalty>> in noise:

$$P = \sqrt{\overline{(1/t)}\;\cdot\;\overline{t}} \ge <<penalty>> \tag{4}$$

where $t$ are the frame exposures. Frames of one exposure length give
$P = 1$, so such a stack is unweighted unless a weighting is configured. Siril stacks with
`-norm=addscale` and sigma rejection (`rej`) at low and high limits that are
either fixed or set from the frame count.

### 3.4 Exposure Groups

Every stack with more than one exposure length is stacked one length at a
time. Each group's stack is turned into counts per second and put on the
reference group's brightness scale using mid-range pixels. A group whose
bright end disagrees is left out. The rest are combined with inverse-variance
weights. All groups are used, not only the largest one.

## 4. Processing Flow

Stacking has no `AnalysisPipeline` adapter. The entry point is
`stage.stack_frames`:

1. **Frame selection and checks.** One camera only, no mix of spectral and
   imaging frames, minority-gain frames set aside, frames set aside by an
   earlier run dropped, the quarantine (imaging only), and the background
   check.
2. **Skip unchanged.** `stack_inputs.py` fingerprints the frames, calibration
   frames, settings and stacking code. A match keeps the stack on disk, unless
   `force=True`.
3. **Previous-stack staging.** The current stack moves to a staging folder.
   On success it becomes the single `_previous` version
   (`previous_stack.py`), which a person can swap back or discard.
4. **Stack.** `stack_runner.run_stack` splits the frames by exposure length
   (and by night for spectra) and calls the `StackingDriver`. The Siril driver
   (`siril_stacking_driver.py`) runs `ImageProcessing` in `siril_interface.py`:
   masters, calibration, registration and stacking. Spectral registration that
   loses too many frames is retried with relaxed detection, then with phase
   correlation.
5. **After the stack.** Noisy edges are trimmed (`stack_crop.py`). The
   quality summary and its gates are built (<<gate_count>>). A preview
   picture is made (GraXpert, Cosmic Clarity, star toning) and the inputs
   record is written last.

### 4.1 Quality Gates

<<gate_table>>

## 5. Tuned Calibration Parameters

<<param_table>>

## 6. Degraded States & Fallback Pathways

1. **Temperature gap above <<dark_tol>> °C.** The nearest dark is applied
   with a soft flag; `calibration_metadata` fails. A missing `CCD-TEMP` means
   the dark is not judged.
2. **Bad frames.** Frames with too few stars or non-round stars move to
   `_excluded`. A batch with more than <<quarantine>> bad frames moves
   nothing and `frame_quarantine` fails.
3. **Registration failure.** Spectral stacks are retried. On an imaging stack,
   blur shows as $R > <<degrade>>$ in `stack_sharpness`.
4. **Over-subtraction.** `zero_pixel_fraction` and `negative_pixels` catch
   darks or biases that remove more than the sky.
5. **A dropped exposure group.** The combined stack is still written, and
   `exposure_groups_combined` fails.
6. **Restack failure.** The staged stack moves back, so the old stack is
   kept.

<<quality_section>>"""
    values["quality_section"] = quality_section("StackQualitySummary", "StackingPipelineQualityMetrics")
    return fill(template, values)


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
    A["1. process_input"] -- "has_work" --> B["2. run"]
    A -- "nothing to do" --> C["3. validate_output"]
    B --> C
    C --> D["4. to_result_dict"]
```

## 2. Pipeline Portfolio Overview

<<overview_table>>

A run gate is recorded on the run's quality summary. A period check is
recorded on each period-search result and can lower its verdict.

## 3. Reviewer Checklist

When auditing the pipelines, check:

1. **Input metadata contracts.** Which header cards each pipeline reads and
   what happens when one is missing.
2. **Mathematical formulations.** The CCD equation, $\text{BJD}_\text{TDB}$
   times, Stetson $J$, the grating equation and the stack FWHM prediction.
3. **Degraded states.** Sparse fields, tracking drift, saturated zero orders
   and catalog outages.
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
        {"version_line": VERSION_LINE, "stage_rows": stage_rows, "overview_table": overview_table},
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
