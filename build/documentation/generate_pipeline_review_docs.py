"""Generates the scientific review guides for the analysis pipelines.

The guides live in ``documentation/library_design/pipelines/``. Each one
describes one pipeline: what it is for, which FITS header cards it reads, the
math it uses, how a run flows through the code, its tuned limits, how it
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
DOC_VERSION = "1.2"
DOC_DATE = "2026-10-15"
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
            For example ``"post_processing/record_quality.py"``.
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
        "`MAXIMUM_RESIDUAL_FRACTION_OF_FWHM` of the FWHM (Eq. 5). Not checked when "
        "the residual (position error), pixel scale (sky angle per pixel) or FWHM is missing."
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
        "Fails when the variability score does not rank cataloged variables above unlisted "
        "stars (AUC against catalog labels: the chance that a random cataloged variable scores "
        "above a random unlisted star). Not checked with too few of either."
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
        "Fails when, for the worst star, at least `DEFAULT_SATURATION_FLAG_THRESHOLD` (0.1%) of "
        "the pixels around its zero order (its undispersed point image beside the spectrum) are "
        "saturated: at or above the camera's saturation threshold, where pixels clip. Not checked "
        "when no zero order was measured."
    ),
    "spectral_classification": (
        "Fails when a classification is a poor match (best misfit, the relative RMS of Eq. 4, "
        "above `NO_GOOD_MATCH_RMS`) or ambiguous at the class level (misfit gap to the best "
        "reference of another spectral class below `AMBIGUOUS_RMS_GAP`). A tie between "
        "neighboring subtypes alone does not fail it; such ties are counted in the detail."
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
        "fail a limit of the processing checkpoint, the second of four quality checkpoints on "
        "each spectrum. Its limits judge the classification: a poor match, an ambiguous match, "
        "or a type from line strengths far from the template-fit type (Section 4)."
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
        "Fails when a confirmed straight-line mover matched no known solar-system body. The "
        "failure is a prompt, not an error: it flags the run so a person checks what may be a "
        "new object. Not checked when no mover was confirmed."
    ),
    "track_residuals": (
        "Fails when any chain that reached the straight-line test used the assumed position "
        "error (`astrometric_error_default_arcsec`) for a detection, since the verdict then rests "
        "on a number nobody measured. Passes otherwise, and its detail counts the chains tested "
        "and rejected. Not checked when no chain reached the test."
    ),
    # Stacking
    "gain_homogeneity": (
        "Frames taken at a less common gain setting (the camera's amplifier setting) are set "
        "aside, which corrects the stack. Not checked when no frame records its gain setting."
    ),
    "frame_quarantine": (
        "Fails when a batch had more bad frames (too few stars, or stars not round) than "
        "`MAXIMUM_QUARANTINED_FRACTION`, so none were moved."
    ),
    "background_homogeneity": (
        "Fails when the sky-background levels of the frames of one exposure length split into "
        "two groups with a sudden jump between them (a gap at least `DEFAULT_GAP_RATIO_THRESHOLD` "
        "times the spread within a group), as when clouds arrive. Slow changes leave no such gap. "
        "Not checked when turned off or with too few measured frames."
    ),
    "flat_level": (
        "Fails when the level of the master flat (the averaged image of an evenly lit surface) "
        "is outside `MINIMUM_FLAT_LEVEL_FRACTION` to `MAXIMUM_FLAT_LEVEL_FRACTION` of the "
        "sensor's full scale (the largest value a pixel can record)."
    ),
    "flat_noise": (
        "Fails when the relative noise of the master flat (the averaged image of an evenly lit "
        "surface) is above `MAXIMUM_FLAT_NOISE_FRACTION`."
    ),
    "calibration_metadata": (
        "Fails when a calibration frame (dark, bias or flat) was applied with a different gain "
        "setting, offset or temperature, or refused for a binning mismatch."
    ),
    "calibration_frame_count": (
        "Fails when a master bias, dark or flat was built from fewer frames than the configured minimum."
    ),
    "rejected_pixel_fraction": (
        "Fails when the share of pixels rejected as outliers in the stack is above "
        "`DEFAULT_REJECTED_FRACTION_FLAG_THRESHOLD`."
    ),
    "stack_sharpness": (
        "Fails when the stack's FWHM over the expected FWHM is above "
        "`DEFAULT_FWHM_DEGRADATION_RATIO` (Eq. 3), meaning the stack is blurrier than its inputs "
        "predict."
    ),
    "spectral_registration": (
        "Fails when the registration (alignment) test of a spectral stack raised a concern. "
        "Not checked for an imaging stack."
    ),
    "saturated_pixel_fraction": (
        "Fails when at least `DEFAULT_SATURATION_FLAG_THRESHOLD` of the stack's pixels are "
        "saturated (clipped at the sensor's maximum)."
    ),
    "zero_pixel_fraction": "Fails when too many stack pixels are exactly zero (over-subtraction).",
    "negative_pixels": "Fails when the stacking engine (Siril) reports too high a share of negative pixels.",
    "exposure_group_linearity": (
        "Fails when one group scale factor does not describe an exposure group (the frames of "
        "one exposure length): the ratio of its faintest or brightest quarter of stars, or with "
        "the pixel method its brightest pixels, is more than the configured tolerance from the "
        "factor that puts it on the reference group's brightness scale (Section 3.4). That group "
        "is left out of the combination."
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
        "| " + " | ".join(["---"] * len(headers)) + " |",
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
        LaTeX uses. A placeholder that is a register entry ID, such as
        ``<<AST-06>>``, becomes a link to that entry (`register_link`).
    values : `dict` [`str`, `Any`]
        The value for each key. Floats are written with ``:g``.

    Returns
    -------
    text : `str`
        The filled-in text, with one line per paragraph (`unwrap_paragraphs`).

    Raises
    ------
    KeyError
        If a placeholder has no value.
    """
    template = re.sub(r"<<([A-Z]{3}-\d{2,3})>>", lambda match: register_link(match.group(1)), template)
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

    return unwrap_paragraphs(re.sub(r"<<(\w+)>>", _replace, template))


# Curated descriptions for fields that lack Field(description=...) in models
KNOWN_FIELD_DESCRIPTIONS: dict[str, str] = {
    "pipeline_name": "Which pipeline made this summary (for example astrometry or photometry).",
    "pipeline_version": "Version number of the pipeline's processing rules.",
    "target_id": "Identifier of the target (the sky object being studied).",
    "target_session_ids": "Identifiers of the observing sessions (usually one night each) used in the run.",
    "target_session_breakdown": "For each session, how many frames it had and how many the run used.",
    "upstream_quality_summary_reference": "Name of the pipeline that had to run first, if any.",
    "resolved_parameters": "The settings the run actually used, after defaults were filled in.",
    "camera_profile": (
        "The camera settings applied, such as the gain (e-/ADU) and the level at which pixels clip."
    ),
    "quality_processing_applied": "Whether the quality gates were run.",
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
    "plate_scale_arcsec_per_pixel": "Pixel scale: the sky angle one pixel covers, in arcsec/pixel.",
    "star_fwhm_px": "Median FWHM across the image, in pixels.",
    "remote_catalog_queries_attempted": "Number of online catalog requests started.",
    "remote_catalog_queries_failed": "Number of online catalog requests that timed out or failed.",
    "remote_catalog_circuit_breaker_tripped": (
        "True if repeated network failures switched off further online catalog requests."
    ),
    "plate_solve_attempts": "Number of plate-solve tries.",
    "stars_processed": "Number of stars tracked through the light curves (brightness over time).",
    "stars_found": (
        "Number of stars detected on the reference frame (the frame every other frame is aligned to)."
    ),
    "frames_processed": "Number of calibrated frames measured.",
    "rejected_frames": "Frames rejected because their comparison-star level was out of line with the rest.",
    "frame_ensemble_composition": "For each frame, which comparison stars were used.",
    "variable_candidate_count": "Number of stars flagged as short-term variable candidates.",
    "light_curve_scatter_rms_mag": (
        "Median scatter (RMS, in magnitudes) of the light curves of non-variable field stars."
    ),
    "cross_session_match_count": "Number of stars matched across sessions from different nights.",
    "noise_model_curve": (
        "The noise model: typical measured scatter (RMS) against instrumental magnitude "
        "(-2.5 log10 of the mean raw flux in ADU per second), in brightness bins."
    ),
    "sessions_missing_wcs": "Sessions with no plate-solved WCS (pixel-to-sky map).",
    "long_term_variable_candidate_count": "Number of stars flagged as long-term variable candidates.",
    "astrometry_identified_star_count": (
        "Stars matched to a SIMBAD or Gaia DR3 catalog star on each session's reference frame, "
        "summed over sessions (a star seen in two sessions counts twice). 0 when catalog "
        "identification is turned off."
    ),
    "sessions_with_reused_header_wcs": (
        "Sessions whose existing header WCS (pixel-to-sky map) was checked and used."
    ),
    "sessions_with_replaced_header_wcs": "Sessions whose WCS (pixel-to-sky map) was worked out again.",
    "zero_order_saturated_pixel_fraction": (
        "Share of saturated (clipped) pixels in the zero order (the star's undispersed point image beside"
        " its spectrum)."
    ),
    "zero_order_saturation_flagged": (
        "True if, for the worst star, at least 0.1% (`DEFAULT_SATURATION_FLAG_THRESHOLD`) of the "
        "pixels around its zero order are at or above the camera's saturation threshold. It is "
        "the same test as the `zero_order_saturation` gate. False when no zero order was measured."
    ),
    "dispersion_angle_deg": (
        "Angle of the spectra (the direction light is spread by color) relative to the sensor rows."
    ),
    "trail_width_profile_available": "Whether the width profile across the spectrum trail was measured.",
    "median_trail_width_px": (
        "Median Gaussian sigma (not FWHM) of the spectrum trail cross-section, in pixels."
    ),
    "poor_match_classification_count": (
        "Number of stars whose classification misfit (RMS) is above the rejection limit."
    ),
    "ambiguous_classification_count": (
        "Number of stars whose classification is ambiguous between spectral classes."
    ),
    "flagged_spectral_classifications": "Details of each star whose classification is in doubt.",
    "stage_quality_summary": "Summary of the quality metrics at each extraction checkpoint.",
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
    "background_split_detected": (
        "True if the frames' sky-background levels split into two groups with a sudden jump "
        "between them (gap at least 4 times the spread within a group, checked for each exposure "
        "length), for example clouds arriving. The frames outside the larger group are set aside."
    ),
    "background_split_detail": "Description of the sky-background jump.",
    "calibration_mismatch_flags": (
        "Soft flags for calibration frames applied with a different gain setting, offset or temperature."
    ),
    "flat_frame_count": "Number of flat exposures (images of an evenly lit surface) in the master flat.",
    "flat_noise_fraction": "Relative noise level measured in the master flat.",
    "flat_smoothing_sigma_px": (
        "Gaussian smoothing width (sigma, in pixels) applied to the master flat because its "
        "relative noise was above 0.5% (`MAXIMUM_FLAT_NOISE_FRACTION`). None when no smoothing "
        "was applied, including on color sensors."
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
    "stacked_fwhm_px": "Measured FWHM of the stacked image.",
    "median_input_fwhm_px": "Median FWHM of all light frames that were kept.",
    "expected_fwhm_px": "Predicted stack FWHM: the RMS of a sample of input-frame FWHMs.",
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
    "unresolved_star_count": (
        "Stars with no catalog match whose sky position could not be worked out (as when the "
        "image has no pixel-to-sky map); not saved."
    ),
    "gaia_fit_rms_arcsec": "Typical (RMS) distance to Gaia DR3 stars under the kept solution.",
    "gaia_fit_rms_before_arcsec": (
        "Typical (RMS) distance to the same Gaia stars under the solver's solution."
    ),
    "gaia_fit_star_count": "Stars used in the Gaia refit.",
    "wcs_refinement_shift_arcsec": "Largest move of the field made by the Gaia refit.",
    "expected_stack_fwhm_px": "Expected stack FWHM: the RMS of a sample of input-frame FWHMs.",
    "fwhm_degraded": (
        "True if the stack FWHM over the expected FWHM is above the limit (the stack is blurrier than "
        "predicted)."
    ),
    "spectral_registration_flags": (
        "Frames about which the spectral registration (alignment) test raised a concern."
    ),
    "spectral_registration_checked": "Whether the spectral registration (alignment) test ran.",
    "stacking_duration_seconds": "How long the stacking run took, in seconds.",
    "timed_out": "True if the stacking run timed out.",
    "debayer_applied": (
        "Whether color frames were debayered (turned from the sensor's color-filter mosaic into full-"
        "color pixels)."
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

    headers = ["Field name", "Type", "Description"]
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
    return make_table(["Parameter", "Live value", "Status", "Notes"], rows)


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
    return make_table(["FITS keyword", "Requirement", "Data type", "Used for", "When missing"], rows)


def quality_section(summary_name: str, metrics_name: str) -> str:
    """Build section 7, the quality-summary field tables.

    Parameters
    ----------
    summary_name : `str`
        The quality-summary class in
        `astrometricslib.models.quality.quality_summary`.
    metrics_name : `str`
        The metrics class in the same module.

    Returns
    -------
    section : `str`
        The Markdown of section 7.
    """
    module = "astrometricslib.models.quality.quality_summary"
    envelope = get_model_fields_table(require_symbol(module, summary_name))
    metrics = get_model_fields_table(require_symbol(module, metrics_name))
    return (
        f"{SECTION_HEADINGS[7]}\n\n"
        "Every run saves a quality summary: a record of what the run measured and how "
        "each quality gate came out. Section 7.1 lists the fields every pipeline shares. "
        "Section 7.2 lists the measurements that belong to this pipeline. The tables are "
        "read from the data models in the code.\n\n"
        f"### 7.1 Pipeline execution envelope (`{summary_name}`)\n\n{envelope}\n\n"
        f"### 7.2 Domain scientific metrics schema (`{metrics_name}`)\n\n{metrics}\n"
    )


ANALYSIS_STAGES_NOTE = (
    "`run_pipeline` (`pipeline_base.py`) calls the adapter's four methods in order. It skips `run` "
    "only when `process_input` returns a `Result` with `has_work=False`. After `validate_output` it "
    "stores the quality summary on the target and records the run's provenance."
)

# The seven section headings every pipeline guide shares, by section number.
# The generator and the doc-parity test both read them from here.
SECTION_HEADINGS: dict[int, str] = {
    1: "## 1. Overview and scientific objective",
    2: "## 2. Input requirements and FITS header specification",
    3: "## 3. Mathematical and algorithmic formulation",
    4: "## 4. Processing flow",
    5: "## 5. Tuned calibration parameters",
    6: "## 6. Degraded states and fallback pathways",
    7: "## 7. Quality summary data models",
}

# The relative link from a guide to the shared glossary.
GLOSSARY_LINK = "../../Glossary.md"

# The sentence right after the Abstract paragraph of every guide.
GLOSSARY_LINE = (
    f"Terms are defined in the [glossary]({GLOSSARY_LINK}). "
    "This document also defines each term the first time it appears."
)

# How each pipeline is named in prose, keyed by guide name.
PIPELINE_NAMES: dict[str, str] = {
    "astrometry": "astrometry",
    "photometry": "photometry",
    "spectroscopy": "spectroscopy",
    "asteroid_detection": "asteroid detection",
    "stacking": "stacking",
}


# Where the repository's files are browsable. The pipeline READMEs live outside
# the documentation site, so the guides link to them here.
REPOSITORY_BLOB_URL = "https://github.com/m1chae2/astrometrics/blob/main"


def readme_note(pipeline: str) -> str:
    """Write the sentence that links a guide to its pipeline README.

    Parameters
    ----------
    pipeline : `str`
        A key of `PIPELINE_NAMES`, which is also the pipeline's folder name.

    Returns
    -------
    note : `str`
        One sentence with a link from the guide to the README. The README sits
        outside the documentation site, so the link points at the repository
        on GitHub rather than at a relative path Sphinx cannot resolve.
    """
    link = f"{REPOSITORY_BLOB_URL}/astrometricslib/pipelines/{pipeline}/README.md"
    return f"The [{PIPELINE_NAMES[pipeline]} pipeline README]({link}) describes the code stage by stage."


# Words that open section 2 of every guide, before the header table.
FITS_INTRO = (
    "A FITS file (the standard file format for astronomy images) starts with a header: a list of "
    "named cards, each a keyword and a value, that describe the exposure. The table lists the cards "
    "this pipeline reads. The last column says what the pipeline does if a card is absent."
)

# Words that open section 4.1 of every guide, before the gate table.
GATE_INTRO = (
    'A quality gate is an automatic test on a run. It passes, fails, or is "not checked" when the '
    "data it needs are missing. A failed gate marks the run as flagged (the `flagged` field in "
    "Section 7) so a person can look at it. The list below is read from the code when this guide is "
    "generated."
)

# The labels of the "Status" column. Every guide uses only these three, so a
# reader can compare limits across pipelines. The data set and any
# measurement go in the validation register, not in the guide: the label
# links to the register entry (`status_cell`), and the Notes column keeps
# only the reason for the value.
STATUS_MEASURED = "Measured"
STATUS_PARTLY_MEASURED = "Partly measured"
STATUS_DESIGN_ESTIMATE = "Design estimate"

# The folder of the validation register, relative to the guides.
VALIDATION_LINK = "../validation"

# The register file that holds each entry-ID prefix.
REGISTER_FILES: dict[str, str] = {
    "STK": "stacking.md",
    "AST": "astrometry.md",
    "PHO": "photometry.md",
    "SPE": "spectroscopy.md",
    "MOV": "asteroid_detection.md",
}

# How each entry-ID prefix names its pipeline, for the design-estimates link.
REGISTER_PIPELINE_NAMES: dict[str, str] = {
    "STK": "stacking",
    "AST": "astrometry",
    "PHO": "photometry",
    "SPE": "spectroscopy",
    "MOV": "asteroid detection",
}


def register_link(entry_id: str) -> str:
    """Link to one entry of the validation register.

    Parameters
    ----------
    entry_id : `str`
        The entry ID, such as ``"STK-24"``. Its prefix picks the file.

    Returns
    -------
    link : `str`
        A Markdown link such as ``[STK-24](../validation/stacking.md#stk-24)``.
    """
    prefix = entry_id.split("-")[0]
    return f"[{entry_id}]({VALIDATION_LINK}/{REGISTER_FILES[prefix]}#{entry_id.lower()})"


def status_cell(status: str, *entry_ids: str) -> str:
    """Give a status label with links to the register entries behind it.

    Parameters
    ----------
    status : `str`
        `STATUS_MEASURED` or `STATUS_PARTLY_MEASURED`.
    *entry_ids : `str`
        The register entries that hold the evidence, such as ``"STK-24"``.

    Returns
    -------
    cell : `str`
        The label followed by the links in brackets, for example
        ``Partly measured ([STK-24](../validation/stacking.md#stk-24))``.
    """
    links = ", ".join(register_link(entry_id) for entry_id in entry_ids)
    return f"{status} ({links})"


def design_estimate_cell(prefix: str) -> str:
    """Give the design-estimate label with a link to its register table.

    Parameters
    ----------
    prefix : `str`
        The entry-ID prefix of the pipeline, such as ``"STK"``.

    Returns
    -------
    cell : `str`
        The label and a link to the "Design estimates" table of the
        pipeline's register file, which says how the value could be measured.
        For example ``Design estimate ([stacking design
        estimates](../validation/stacking.md#design-estimates))``.
    """
    name = REGISTER_PIPELINE_NAMES[prefix]
    link = f"[{name} design estimates]({VALIDATION_LINK}/{REGISTER_FILES[prefix]}#design-estimates)"
    return f"{STATUS_DESIGN_ESTIMATE} ({link})"


def see_also(*entry_ids: str) -> str:
    """Write the sentence that points a Notes cell at related register entries.

    Parameters
    ----------
    *entry_ids : `str`
        Register entries that bear on a value without measuring it, such as
        a check of the method on synthetic data.

    Returns
    -------
    text : `str`
        A sentence that starts with a space, to append to a note.
    """
    word = "entry" if len(entry_ids) == 1 else "entries"
    return f" Related {word}: " + ", ".join(register_link(entry_id) for entry_id in entry_ids) + "."


# Words that open section 5 of every guide, before the parameter table.
PARAM_INTRO = f"""These are the fixed limits and settings the pipeline uses. The live value is read from
the code when this guide is generated. The status says how far real data back the value, with one
of three labels:

- **{STATUS_MEASURED}.** Chosen or confirmed from a measurement on real data.
- **{STATUS_PARTLY_MEASURED}.** Backed by a measurement that is incomplete, such as synthetic
  (simulated) data only, one target or a small sample.
- **{STATUS_DESIGN_ESTIMATE}.** Chosen by reasoning and not yet measured.

The labels are defined in the [validation register]({VALIDATION_LINK}/index.md). Each label
links to the register entry that holds the evidence: the data, the library calls, the result,
and how to repeat the test on your own data. A design estimate links to the register's table of
values not yet measured, which says how each one could be measured. The Notes column gives the
reason for the value."""

# Placeholder values shared by every guide.
COMMON_VALUES: dict[str, str] = {
    "version_line": VERSION_LINE,
    "glossary_line": GLOSSARY_LINE,
    "fits_intro": FITS_INTRO,
    "gate_intro": GATE_INTRO,
    "param_intro": PARAM_INTRO,
    **{f"heading_{number}": heading for number, heading in SECTION_HEADINGS.items()},
}


# The anchor of the architecture paper's list of symbols.
NOTATION_LINK = "../Astrometrics_Library_Architecture.md#appendix-a-notation"

# Symbols a guide uses that the architecture paper's Appendix A does not
# define, as (symbol, meaning, unit). Every other symbol in a guide has the
# paper's meaning. A symbol listed for two guides has the same meaning in
# both. test_documentation_style.py checks each guide's equations against
# Appendix A and this table.
GUIDE_NOTATION: dict[str, list[tuple[str, str, str]]] = {
    "astrometry": [
        (r"$B_\text{sky}(x, y)$", "Background (sky) level at a pixel", "ADU"),
        (r"$\sigma_\text{sky}$", "Noise of the background", "ADU"),
        (r"$k$", "Detection threshold, in units of the background noise", "none"),
        (r"$s$", "Pixel scale", "arcsec/px"),
        (r"$p_\text{px}$", "Pixel pitch (binned pixel size)", "μm"),
        (r"$f_\text{tel}$", "Focal length of the telescope", "mm"),
        (
            r"$\mu_{\alpha*}$, $\mu_\delta$",
            "Proper motion in right ascension (times cos δ) and in declination",
            "mas/yr",
        ),
        (r"$t_\text{obs}$", "Observation date", "yr"),
        (r"$\Delta t$", "Elapsed time", "yr"),
        (r"$\Delta \theta_i$", "Separation of star $i$ from its catalog match", "arcsec"),
        (r"$N_\text{match}$", "Number of matched stars", "stars"),
        (r"$r$", "Residual of the plate solution", "arcsec"),
    ],
    "photometry": [
        (r"$F_\text{net}$", "Sky-subtracted aperture sum", "ADU"),
        (r"$I_q$", "Value of pixel $q$", "ADU"),
        (r"$\bar{B}$", "Sky level per pixel, the median of the sky ring", "ADU/px"),
        (r"$G$", "Gain", "e⁻/ADU"),
        (
            r"$\sigma_{F,i}(t)$, $\sigma_{F,c}(t)$",
            "Uncertainty of the raw brightness of star $i$ and of comparison star $c$",
            "ADU/s",
        ),
        (r"$\sigma_\text{ens}(t)$", "Uncertainty of the ensemble signal", "ADU/s"),
        (r"$e_i(t)$", "Uncertainty of the normalized brightness of star $i$", "none"),
        (r"$s_\text{frac}$", "Fractional scatter", "none"),
        (r"$m_\text{inst}$", "Instrumental magnitude", "mag"),
        (r"$\bar{F}_\star$", "Mean raw brightness of a star", "ADU/s"),
        (
            r"$\sigma_\text{model}(m_\text{inst})$",
            "Scatter of a constant star in the field's noise model",
            "mag",
        ),
        (r"$\sigma_\text{measured}$", "Measured scatter of a star", "mag"),
        (
            r"$\delta_j$, $P_j$",
            "Normalized residual of point $j$, and the product of neighboring residuals",
            "none",
        ),
        (r"$J$, $J_\text{thr}$", "Stetson J index, and its threshold", "none"),
        (r"$E_\sigma$, $E_{\sigma,\text{thr}}$", "Excess scatter, and its threshold", "none"),
        (r"$\chi^2$, $\chi^2_\text{thr}$", "Chi-square, and the threshold on reduced chi-square", "none"),
        (r"$S_\text{rank}$", "Ranking score of a candidate", "none"),
        (r"$p_\text{lim}$", "Verdict limit on the corrected false-alarm probability", "none"),
    ],
    "spectroscopy": [
        (r"$p_\text{px}$", "Pixel pitch (binned pixel size)", "μm"),
        (r"$\rho(\lambda)$", r"Refraction (bending) of light of wavelength $\lambda$", "arcsec"),
        (r"$\lambda_\text{eff}$", "Reference (effective) wavelength of the refraction shift", "Å"),
        (r"$w(x)$", "Width of the extraction box", "px"),
        (r"$\sigma_\text{trail}(x)$", "Width (standard deviation) of the trail profile", "px"),
        (r"$\Phi_\text{corr}(\lambda)$", "Spectral flux corrected for airmass extinction", "relative"),
        (r"$R_V$", "Ratio of total to selective extinction of the dust", "none"),
        (r"$T_\text{ref}(\lambda)$", "Reference (template) spectrum", "relative"),
        (r"$a$", "Best-fit scale factor of the reference spectrum", "none"),
        (r"$M$", "Number of wavelengths in the match score", "none"),
        (r"$\bar{\Phi}$", "Mean spectral flux", "relative"),
    ],
    "asteroid_detection": [
        (r"$\Delta t$", "Elapsed time", "h"),
        (r"$v_\text{max}$", "Fastest allowed rate of motion", "arcsec/h"),
        (r"$r_\text{search}$, $r_\text{cap}$", "Search radius, and its cap", "arcsec"),
        (
            r"$\mathbf{p}_j$, $\bar{\mathbf{p}}$",
            "Pixel position of detection $j$, and the chain's mean pixel position",
            "px",
        ),
        (
            r"$\mathbf{s}_j$, $\bar{\mathbf{s}}$",
            "Sky position of detection $j$, and the chain's mean sky position",
            "arcsec",
        ),
        (r"$\theta$", "Angle between two sky positions", "arcsec"),
        (r"$\alpha_\text{ref}$, $\delta_\text{ref}$", "Reference point of the tangent plane", "°"),
        (r"$\xi_0$, $\eta_0$", "Offsets at the reference time", "arcsec"),
        (r"$\dot{\xi}$, $\dot{\eta}$", "Rates of motion along the tangent-plane axes", "arcsec/h"),
        (r"$t_0$", "Reference time of the straight-line fit", "h"),
    ],
    "stacking": [
        (
            r"$T_\text{light}$, $\bar{T}_\text{dark}$",
            "Sensor temperature of a light frame, and the mean sensor temperature of the darks",
            "°C",
        ),
    ],
}


def notation_section(number: str, pipeline: str) -> str:
    """Write a guide's notation subsection, the symbols the paper lacks.

    Parameters
    ----------
    number : `str`
        The subsection number, such as ``"3.7"``.
    pipeline : `str`
        The key of the guide in `GUIDE_NOTATION`.

    Returns
    -------
    section : `str`
        A heading, one sentence, and a table of symbol, meaning and unit.
    """
    rows = "\n".join(
        f"| {symbol} | {meaning} | {unit} |" for symbol, meaning, unit in GUIDE_NOTATION[pipeline]
    )
    return (
        f"### {number} Notation\n\n"
        f"Symbols follow the architecture paper's [list of symbols]({NOTATION_LINK}). The table "
        "lists only the symbols this guide adds.\n\n"
        "| Symbol | Meaning | Unit |\n| --- | --- | --- |\n"
        f"{rows}"
    )


def unwrap_paragraphs(text: str) -> str:
    """Join hard-wrapped prose lines so each paragraph is one line.

    Code fences, display math, headings, tables and the first line of each
    list item are left as they are. A line that continues a paragraph or a
    list item is joined to the line before it.

    Parameters
    ----------
    text : `str`
        Markdown text with wrapped paragraphs.

    Returns
    -------
    text : `str`
        The same text with one line per paragraph and per list item.
    """
    starts_block = re.compile(r"^\s*(#{1,6} |\||<!--|:::|[-*] |\d+\. )")
    lines: list[str] = []
    in_code = False
    in_math = False
    joinable = False
    for line in text.split("\n"):
        stripped = line.strip()
        if in_code or in_math:
            lines.append(line)
            if in_code and stripped.startswith("```"):
                in_code = False
            if in_math and "$$" in line:
                in_math = False
            continue
        if stripped.startswith("```"):
            in_code, joinable = True, False
            lines.append(line)
        elif stripped.startswith("$$"):
            in_math, joinable = stripped.count("$$") < 2, False
            lines.append(line)
        elif not stripped:
            joinable = False
            lines.append("")
        elif starts_block.match(line):
            joinable = bool(re.match(r"^\s*([-*] |\d+\. )", line))
            lines.append(line)
        elif joinable:
            lines[-1] += " " + stripped
        else:
            joinable = True
            lines.append(line)
    return "\n".join(lines)


def build_astrometry_doc() -> str:
    """Build the Astrometry Pipeline review documentation.

    Returns
    -------
    content : `str`
        Markdown content for astrometry.md.
    """
    gates = discover_gate_names("astrometry")
    record_quality_module = "astrometricslib.pipelines.astrometry.post_processing.record_quality"
    identifier_module = "astrometricslib.pipelines.astrometry.processing.star_identifier"
    refine_module = "astrometricslib.pipelines.astrometry.processing.wcs_refinement"
    driver_module = "astrometricslib.drivers.astrometry_net_driver"
    fwhm_module = "astrometricslib.pipelines.astrometry.pre_processing.fwhm"
    detector = require_symbol(
        "astrometricslib.pipelines.astrometry.pre_processing.source_detection", "SourceDetector"
    )
    values = {
        "version_line": VERSION_LINE,
        "max_res": require_symbol(record_quality_module, "MAXIMUM_RESIDUAL_FRACTION_OF_FWHM"),
        "min_match": require_symbol(record_quality_module, "MINIMUM_MATCHED_STARS"),
        "max_fail": require_symbol(record_quality_module, "MAXIMUM_FAILED_LOOKUP_FRACTION"),
        "match_radius": require_symbol(
            "astrometricslib.pipelines.shared.catalog_star_identity", "CATALOG_MATCH_RADIUS_ARCSEC"
        ),
        "solver_sources": require_symbol(identifier_module, "MAXIMUM_PLATE_SOLVE_SOURCES"),
        "gaia_epoch": require_symbol(identifier_module, "GAIA_DR3_REFERENCE_EPOCH"),
        "depth_stars": require_symbol(identifier_module, "MINIMUM_STARS_FOR_DEPTH_ESTIMATE"),
        "local_minimum": require_symbol(driver_module, "MINIMUM_SOURCES_FOR_LOCAL_SOLVE"),
        "blind_cutoff": f"{require_symbol(driver_module, 'HINTED_SOLVE_NEAR_TIMEOUT_FRACTION'):.0%}",
        "blob_stars": require_symbol(fwhm_module, "FWHM_MEASUREMENT_STAR_COUNT"),
        "blob_box": 2 * require_symbol(fwhm_module, "BLOB_WIDTH_BOX_RADIUS_PX"),
        "threshold": float(default_of(detector.__init__, "threshold_sigma")),
        "merge_px": float(default_of(detector.deduplicate, "separation_px")),
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
            "Pixel size after binning (binning joins neighboring sensor pixels into one), "
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
            status_cell(STATUS_PARTLY_MEASURED, "AST-04"),
            "Largest allowed residual (position error of the sky map), as a share of the FWHM. "
            "It is there to catch a failed solve, not to rank good ones.",
        ],
        [
            "`MINIMUM_MATCHED_STARS` (`record_quality`)",
            f"`{values['min_match']}`",
            status_cell(STATUS_PARTLY_MEASURED, "AST-05"),
            "Fewest stars matched to a catalog for the residual to mean anything.",
        ],
        [
            "`MAXIMUM_FAILED_LOOKUP_FRACTION`",
            f"`{values['max_fail']}`",
            design_estimate_cell("AST"),
            "Share of failed online catalog lookups at which `catalog_lookup` fails.",
        ],
        [
            "`CATALOG_MATCH_RADIUS_ARCSEC`",
            f"`{values['match_radius']}` arcsec",
            design_estimate_cell("AST"),
            "Largest distance at which a detected star is paired with a SIMBAD or Gaia DR3 star.",
        ],
        [
            "`MAXIMUM_PLATE_SOLVE_SOURCES`",
            f"`{values['solver_sources']}`",
            design_estimate_cell("AST"),
            "How many of the brightest detected stars are sent to the online solver.",
        ],
        [
            "Solve minimum",
            "4 sources",
            design_estimate_cell("AST"),
            "Fewest detections for a solve to be tried (`process_image`).",
        ],
        [
            "`MINIMUM_SOURCES_FOR_LOCAL_SOLVE`",
            f"`{values['local_minimum']}`",
            status_cell(STATUS_PARTLY_MEASURED, "AST-13"),
            "Fewest detections for the local solver to be tried; with fewer, the run goes "
            "straight to the online solver, because a local attempt would spend its whole time "
            "limit on a near-certain failure (Section 3.3).",
        ],
        [
            "`SourceDetector` threshold",
            f"`{values['threshold']:g}σ`",
            design_estimate_cell("AST"),
            "How far a peak must stand above the sky, in standard deviations of the sky noise (Eq. 1).",
        ],
        [
            "`MINIMUM_MATCHED_STARS` (`wcs_refinement`)",
            f"`{values['refit_min_stars']}`",
            design_estimate_cell("AST"),
            "Fewest Gaia star matches needed for the refit with distortion terms (SIP). The fit "
            "has about a dozen terms per axis, and 40 stars leaves a wide margin.",
        ],
        [
            "`MAXIMUM_SHIFT_ARCSEC`",
            f"`{values['refit_shift']}` arcsec",
            status_cell(STATUS_PARTLY_MEASURED, "AST-02"),
            "Largest move of the whole field a refit may make and still be kept. Set at about "
            "ten times the largest solver error seen; a larger move means the fit followed "
            "wrong matches.",
        ],
    ])
    template = r"""# Astrometry and plate solving pipeline review guide

<<version_line>>

## Abstract

This document reviews the plate solving and astrometric calibration pipeline
(`astrometricslib.pipelines.astrometry`). It covers the header cards the pipeline reads, its
detection and solving methods, the Gaia DR3 refit of the solution, how stars are named, the run
flow, the tuned limits, the fallback paths and the quality-summary fields.

<<glossary_line>>

<<heading_1>>

<<readme_note>>

**In plain words.** A telescope image shows stars, but it does not say where on the sky it
points. This pipeline works that out. For every pixel it finds the matching sky direction, given
as right ascension and declination (the sky's longitude and latitude). Finding this pixel-to-sky
map is called a plate solve.

The image carries no labels, so the map must come from the stars themselves. The mount's
reported pointing can be off, the optics bend the field slightly, and stars drift slowly across
the sky over the years. The pipeline finds the stars in the image and matches their pattern
against a star catalog. It then fine-tunes the map against Gaia DR3 (the third data release of
the Gaia space mission, the most accurate star positions available).

The result is the map, written into the image file, a name for each star, and a quality summary
that says how accurate the map is.

**Statement of need.** Astrometry (the measurement of star positions) finds the World Coordinate
System (WCS) of an image. The WCS is the map from detector pixels $(x, y)$ to sky coordinates
$(\alpha, \delta)$ in the International Celestial Reference System (ICRS, the standard sky
coordinate frame). Photometry needs it to match stars across sessions. Spectroscopy needs it to
name the stars whose spectra it extracts. Asteroid detection needs it to place every frame on
the sky.

The diagram shows one run, top to bottom.

```mermaid
flowchart TD
    A["Image file (usually the target's stacked image)"] --> B["Find stars against the sky background"]
    B --> C["Gather scale and position hints (image file, then settings)"]
    C --> D{"At least 4 stars found?"}
    D -- no --> F["Use the sky map already in the file, if any"]
    D -- yes --> L{"At least <<local_minimum>> stars?"}
    L -- yes --> E["Local solver reads the image file (with hints, then blind)"]
    L -- no --> O["Online solver: the brightest <<solver_sources>> stars, then the whole image"]
    E -- "no solution" --> O
    E -- solved --> G["Fine-tune the map against Gaia star positions"]
    O -- solved --> G
    G --> H["Name stars: SIMBAD, then Gaia, then sky position"]
    F --> H
    H --> I["Quality gates"]
    I --> J["Quality summary"]
```

Astrometry.net is an open-source plate solver, used both as a program installed on this computer
(the local solver) and as an online service. SIMBAD is an online database of named stars and
other objects. The fine-tuning step fits SIP distortion terms of order 2 (Section 3.3), and the
quality summary is an `AstrometryQualitySummary` (Section 7).

<<heading_2>>

<<fits_intro>>

No card is strictly required: a missing card weakens a hint or a correction, and the run says so
in its flags.

<<fits_table>>

The pixel-scale hint is the scale from Eq. 2 with a window of ±5%. The solver driver widens that
window to 0.8 times its lower edge and 1.2 times its upper edge before calling `solve-field`.
With no focal length or no pixel size, no scale hint is sent.

<<heading_3>>

This section covers, in order: finding stars, estimating how much sky a pixel covers, solving
and refining the map, moving catalog stars to the image date, naming stars, and judging the
map's accuracy.

### 3.1 Point source detection

A point source is anything in the image that looks like a single star. To find them, the
pipeline first estimates the sky background: the faint glow behind the stars. The background is
a 2-D map (photutils `Background2D`, median estimator, 3σ clipping). Sigma clipping repeatedly
drops pixels more than 3 standard deviations from the median, so stars do not inflate the sky
estimate. A small image falls back to one sigma-clipped median.

The test below asks how many noise units a pixel stands above the local sky. A large value means
a real source; a value near zero means only sky. A source is kept when its peak stands above the
background $B_\text{sky}$ by the threshold $k$, where $\sigma_\text{sky}$ is the noise of the background:

$$\frac{I(x, y) - B_\text{sky}(x, y)}{\sigma_\text{sky}} \ge k = <<threshold>> \tag{1}$$

`DAOStarFinder` (a standard star finder from photutils) also checks shape. Sharpness compares a
peak's height with its spread; roundness compares its width along the two axes, with 0 meaning
round. It keeps sharpness in $[0.2, 1.0]$ and roundness in $[-0.6, +0.6]$, which rejects hot
pixels (single pixels that always read high) and filament structure.

**The kernel width.** The finder looks for blobs of a set width, the kernel width. On a
single-color (monochrome) frame that width is measured from the image. It is the spread (second
moment) of all the light in a <<blob_box>>-pixel box around each of the <<blob_stars>> brightest
sources. It is written as the FWHM (full width at half maximum, the width of a star's light
profile at half its peak height) of a Gaussian with the same spread, which is 2.355 times its
standard deviation. Because the box also holds faint wings and neighbors, this blob width reads
about twice the star's fitted FWHM. It is used because it detects better than a kernel sized
from the fitted FWHM: more catalog matches and a smaller residual (partly measured; see
<<AST-06>>). It is never reported as a FWHM.

**Clean-up.** Detections closer than <<merge_px>> pixels are merged into one, because the finder
can split one large star into two. Color frames are averaged in 2×2 blocks before detection:
color interpolation leaves clumps of noise that would otherwise be found as thousands of false
stars.

### 3.2 Pixel-scale hint

The pixel scale is the sky angle one pixel covers. A longer focal length or a smaller pixel
gives a smaller (finer) scale. The solver runs much faster when it knows the scale in advance:

$$s = 206.265 \, \frac{p_\text{px}}{f_\text{tel}} \tag{2}$$

where $s$ is the scale in arcsec/pixel (1 arcsec is 1/3600 of a degree), $p_\text{px}$ the binned pixel
size in μm and $f_\text{tel}$ the focal length in mm. The constant is the number of arcseconds in a radian
(206,265) times $10^{-3}$, which turns μm over mm into the same unit.

### 3.3 Plate solve and Gaia DR3 refit

**Local, then online.** Astrometry.net is tried in up to three ways, in order:

1. The local solver reads the image file and finds its own stars. It is tried only when the
   pipeline found at least <<local_minimum>> stars; with fewer, a local attempt would spend its
   whole time limit on a near-certain failure. It runs first with the scale and position hints.
   If that fails quickly, it retries blind (no hints), because a wrong hint, such as a forgotten
   focal reducer, can block the right answer. If the hinted run used at least <<blind_cutoff>>
   of its time limit, the blind retry is skipped.
2. The online service is sent the brightest <<solver_sources>> of the pipeline's own detections.
3. As a last resort, the whole image is uploaded to the online service.

Only the local solver reports its fit statistics; an online solve has none (Section 3.6).

**Why refit.** The solver fits only the bright stars of its index files, so its solution can be
a pixel or more off. The pipeline then refits the solution to the field's Gaia DR3 stars
(`wcs_refinement.py`).

**How.** Each detection is matched to the nearest Gaia star within <<refit_r1>> pixels, then
<<refit_r2>> pixels on later passes. The map is fitted with
`astropy.wcs.utils.fit_wcs_from_points` as a tangent-plane solution (the sky projected onto a
flat plane touching it at the field center) plus SIP distortion of order <<refit_sip>>. SIP
(Simple Imaging Polynomial) adds small polynomial terms that model the bending of the field by
the optics. Stars more than <<refit_clip>> robust standard deviations off are dropped, and the
fit repeats until the matched set stops changing. A robust standard deviation is 1.4826 times
the median absolute deviation, a measure of spread that a few wild points cannot inflate.

**When it is kept.** The refit needs at least <<refit_min_stars>> matches. It is kept only when
it fits Gaia better than the solver's solution and moves the field by at most <<refit_shift>>
arcsec.

### 3.4 Proper motion epoch propagation

Stars move slowly across the sky; this drift is their proper motion. Gaia DR3 gives each
position at one date, the epoch <<gaia_epoch>>. Before matching, each position is moved to the
image time with astropy's `SkyCoord.apply_space_motion`. The move grows with the star's speed
and with the years since 2016. To first order it is

$$\alpha(t) = \alpha_0 + \frac{\mu_{\alpha*} \, \Delta t}{\cos \delta_0}, \qquad
\delta(t) = \delta_0 + \mu_\delta \, \Delta t, \qquad \Delta t = t_\text{obs} - 2016.0 \tag{3}$$

where $\mu_{\alpha*}$ is Gaia's `pmra` (which already includes $\cos \delta$) and $\mu_\delta$
is `pmdec`. Both are in milliarcseconds per year and $\Delta t$ is in years, so each product is
an angle in milliarcseconds. It is turned into degrees (1 degree is $3.6 \times 10^6$
milliarcseconds) before it is added to $\alpha_0$ or $\delta_0$.

### 3.5 Star naming

With a solution, each star is named from SIMBAD first, within <<match_radius>> arcsec. Stars
SIMBAD does not know are matched against a Gaia DR3 cone (all catalog stars within a circle
around the field).

The cone's faint limit is set from the frame's magnitude zero point: the catalog magnitude a
star of flux 1 would have. Each SIMBAD-named star with a catalog V magnitude gives $V + 2.5
\log_{10}(\text{flux})$, and the median over at least <<depth_stars>> stars is the frame's zero
point. With it, the flux of the faintest detection gives the faintest magnitude the Gaia search
needs. Stars neither catalog knows get a position-based name such as `FIELD_J123.4+45.6`.

Separations are great-circle distances (the shortest angle between two points on the sky) from
`SkyCoord.separation`. The catalog match separation is the typical distance between a detected
star and its catalog match:

$$\text{RMS}_\text{sep} = \sqrt{\frac{1}{N_\text{match}} \sum_{i=1}^{N_\text{match}} \Delta
\theta_i^2} \tag{4}$$

over the $N_\text{match}$ stars matched within the radius. RMS (root mean square) is the square root of the
mean squared distance. It is a rough test, not a measure of the fit: wrong matches and SIMBAD's
uneven position precision raise it, and the match radius caps it.

### 3.6 Residual gate

The residual $r$ (arcsec) is the map's typical position error. A position error matters in
proportion to the size of a star in the image, so the gate divides it by the FWHM in arcsec. A
ratio of 0.1 means the error is a tenth of the FWHM; above <<max_res>> the map is too loose:

$$\frac{r}{s \cdot \text{FWHM}} \le <<max_res>> \tag{5}$$

The residual is the first of these that was measured:

1. the RMS distance to Gaia stars under the kept solution, when the Gaia refit ran (it does not
   depend on the solver);
2. the solver's own fit residual, which only a local solve reports;
3. the catalog match separation of Eq. 4.

The gate detail names the one used.

<<notation_section>>

<<heading_4>>

`run_astrometry_analysis` (`runner.py`) runs `AstrometryPipelineAdapter` through `run_pipeline`.
<<analysis_note>>

- **Stage 1 (`process_input`).** A no-op that returns an empty `Result`. `analyze_target` has
  already checked that an image path exists.
- **Stage 2 (`run`).** Runs `AstrometryPipeline.process` (detection, solve, refit, header-WCS
  fallback, naming). It writes the solved WCS into the FITS header, fills in the target's RA/Dec
  when they were unset, and saves the stars to the shared catalog.
- **Stage 3 (`validate_output`).** Builds the `AstrometryQualitySummary`. The runner records
  `plate_solve`, then `astrometry_run_gates` adds the rest (<<gate_count>> in all).
  The runner then stores the summary's numbers and the gates as quality measurements
  (`record_astrometry_quality`).
- **Stage 4 (`to_result_dict`).** Returns `context`, `stellar_objects`, `wcs` and `image_stats`.

### 4.1 Quality gates

<<gate_intro>>

<<gate_table>>

<<heading_5>>

<<param_intro>>

<<param_table>>

<<heading_6>>

1. **Missing optical metadata.** `FOCALLEN` falls back to the configured focal length. Pixel
   size falls back from `XPIXSZ` to `PIXSIZE1` times `XBINNING`, then to `PIXSCAL`. With none of
   these, the solve runs with no scale hint.
2. **Too few detections (fewer than 4).** No solve is tried. A celestial WCS already in the
   image header is used instead. Without one, `plate_solve` fails.
3. **Solver failure.** A tried solve that finds no solution, locally or online, raises
   `PlateSolveFailedError`. The adapter does not catch it, so the error reaches the caller:
   `validate_output` never runs, and no quality summary or gate is saved for that run.
4. **No Gaia stars.** The refit is skipped with a flag and the solver's solution is kept.
   Without `DATE-OBS`, or with a cache that has no proper motions, Gaia positions stay at epoch
   2016.0 with a flag.
5. **Catalog outages.** Repeated failures trip a process-wide circuit breaker: a shared switch
   that stops further online catalog queries. Unnamed stars keep position-based names, and
   `catalog_lookup` reports the outage.

<<quality_section>>"""
    values["analysis_note"] = ANALYSIS_STAGES_NOTE
    values["quality_section"] = quality_section(
        "AstrometryQualitySummary", "AstrometryPipelineQualityMetrics"
    )
    values["readme_note"] = readme_note("astrometry")
    values["notation_section"] = notation_section("3.7", "astrometry")
    return fill(template, {**COMMON_VALUES, **values})


def build_photometry_doc() -> str:
    """Build the Photometry Pipeline review documentation.

    Returns
    -------
    content : `str`
        Markdown content for photometry.md.
    """
    gates = discover_gate_names("photometry")
    record_quality_module = "astrometricslib.pipelines.photometry.post_processing.record_quality"
    ensemble_module = "astrometricslib.pipelines.photometry.processing.comparison_ensemble"
    indices_module = "astrometricslib.pipelines.photometry.processing.variability_indices"
    analyzer_module = "astrometricslib.pipelines.photometry.processing.variability_analyzer"
    periodicity_module = "astrometricslib.pipelines.photometry.processing.periodicity_search"
    aperture = require_symbol(
        "astrometricslib.pipelines.photometry.pre_processing.frame_photometry", "measure_aperture_photometry"
    )
    values = {
        "version_line": VERSION_LINE,
        "rej_frac": require_symbol(record_quality_module, "MINIMUM_ENSEMBLE_REJECTION_FRACTION_TO_FLAG"),
        "rej_cnt": require_symbol(record_quality_module, "MINIMUM_ENSEMBLE_REJECTION_COUNT_TO_FLAG"),
        "max_amp": require_symbol(record_quality_module, "MAXIMUM_DETECTABLE_AMPLITUDE_MAG"),
        "corner": require_symbol(record_quality_module, "MAXIMUM_CORNER_DISPLACEMENT_PX"),
        "ens_scatter": require_symbol(record_quality_module, "MAXIMUM_ENSEMBLE_SCATTER_OVER_PREDICTED"),
        "scatter_pop": require_symbol(record_quality_module, "MINIMUM_STARS_FOR_SCATTER_POPULATION"),
        "fallback": require_symbol(record_quality_module, "MAXIMUM_CENTROID_FALLBACK_FRACTION"),
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
        "fap_detect": require_symbol(periodicity_module, "FALSE_ALARM_FOR_DETECTION"),
        "fap_possible": require_symbol(periodicity_module, "_FALSE_ALARM_FOR_POSSIBLE"),
        "max_shuffles": require_symbol(
            "astrometricslib.pipelines.photometry.processing.family_wise_correction",
            "MAXIMUM_REPEAT_SHUFFLES",
        ),
        "radius": float(default_of(aperture, "radius")),
        "r_in": float(default_of(aperture, "annulus_inner")),
        "r_out": float(default_of(aperture, "annulus_outer")),
        "gate_count": gate_count_text(gates),
        "gate_table": gate_table(gates),
    }
    values["cv_robust_sigmas"] = f"{values['cv_sigma'] / 1.4826:.1f}"
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
            "Gain (e-/ADU): electrons per ADU (analog-to-digital unit, one camera count). Used "
            "for the flux errors when the camera profile has none. `GAIN`, the camera's gain "
            "setting, is a different number and is never read.",
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
            "Airmass (how much atmosphere the light passed through; 1 straight overhead). Used "
            "only by the optional ensemble airmass correction, which is off by default: the "
            "median trend of the comparison stars' normalized flux against airmass is removed "
            "from every star. No star's own flux is fitted against airmass.",
            "1.0.",
        ],
    ])
    values["param_table"] = parameter_table([
        [
            "`MINIMUM_ENSEMBLE_REJECTION_FRACTION_TO_FLAG`",
            f"`{values['rej_frac']}`",
            design_estimate_cell("PHO"),
            "Share of frames rejected as ensemble outliers (frames whose comparison-star level "
            "is out of line with the rest) that fails the gate.",
        ],
        [
            "`MINIMUM_ENSEMBLE_REJECTION_COUNT_TO_FLAG`",
            f"`{values['rej_cnt']}`",
            design_estimate_cell("PHO"),
            "Fewest rejected frames that can fail the gate, so one frame of a short run cannot.",
        ],
        [
            "Aperture radius",
            f"`{values['radius']:g}` px",
            design_estimate_cell("PHO"),
            "Radius of the circle in which a star's light is summed. Default of "
            "`measure_aperture_photometry`. It is fixed, not scaled to each star's width.",
        ],
        [
            "Sky annulus radii",
            f"`{values['r_in']:g}` to `{values['r_out']:g}` px",
            design_estimate_cell("PHO"),
            "Inner and outer radius of the sky ring (annulus) around each star, where the sky "
            "level is measured.",
        ],
        [
            "`MINIMUM_COMPARISON_STARS`",
            f"`{values['min_comp']}`",
            design_estimate_cell("PHO"),
            "Comparison-star vetting stops at this size; fewer fails `comparison_ensemble`."
            + see_also("PHO-01"),
        ],
        [
            "`DEFAULT_MAXIMUM_COMPARISON_STARS`",
            f"`{values['max_comp']}`",
            design_estimate_cell("PHO"),
            "Most comparison stars kept in the set. More stars lower the ensemble noise, but the "
            "extra ones are fainter, so the benefit shrinks quickly." + see_also("PHO-01"),
        ],
        [
            "`MINIMUM_STARS_FOR_NOISE_MODEL`",
            f"`{values['noise_model_stars']}`",
            design_estimate_cell("PHO"),
            "Fewest stars for a noise model (expected scatter against brightness): two "
            "brightness bins of 10 stars. With fewer, the CV cutoff (Eq. 9) picks candidates."
            + see_also("PHO-06"),
        ],
        [
            "`FALSE_DISCOVERY_LEVEL`",
            f"`{values['fdr']}`",
            design_estimate_cell("PHO"),
            "Largest Benjamini–Hochberg q-value a candidate may have (Section 3.4). The level "
            "journals usually ask for." + see_also("PHO-07"),
        ],
        [
            "`EXCESS_SCATTER_THRESHOLD`",
            f"`{values['excess']}`",
            design_estimate_cell("PHO"),
            "Fixed threshold on the excess scatter (Eq. 7). A 50% excess, which the ordinary "
            "spread of a constant star's scatter (10 to 20% for 25 to 50 points) rarely reaches."
            + see_also("PHO-06"),
        ],
        [
            "`CALIBRATION_SIGMAS`",
            f"`{values['cal_sigmas']}`",
            design_estimate_cell("PHO"),
            "Robust standard deviations above the field median for the chi-square and J "
            "thresholds: the one-sided 99% point of a normal distribution." + see_also("PHO-06"),
        ],
        [
            "`DEFAULT_VARIABILITY_SIGMA_THRESHOLD`",
            f"`{values['cv_sigma']}`",
            status_cell(STATUS_MEASURED, "PHO-10"),
            "Multiplier of the unscaled MAD in the CV cutoff (Eq. 9), about "
            f"{values['cv_robust_sigmas']} robust standard deviations. Chosen to flag about the "
            "top 3% of stars.",
        ],
        [
            "`MAXIMUM_DETECTABLE_AMPLITUDE_MAG`",
            f"`{values['max_amp']}` mag",
            design_estimate_cell("PHO"),
            "If the smallest detectable peak-to-peak amplitude (in magnitudes, a logarithmic "
            "brightness scale) is larger, the run is called blind to most variables. Most "
            "pulsating and rotating variables change by less than this." + see_also("PHO-09"),
        ],
        [
            "`UNSTABLE_TRACKING_DRIFT_PX`",
            f"`{values['drift']:g}` px",
            design_estimate_cell("PHO"),
            "Largest shift of the field between frames before `registration_drift` fails. Half "
            "of the ±40 px box in which a star's center is searched, so stars are caught "
            "before they leave the box.",
        ],
        [
            "`MAXIMUM_CORNER_DISPLACEMENT_PX`",
            f"`{values['corner']}` px",
            design_estimate_cell("PHO"),
            "Largest shift of an image corner caused by rotation and scale change between frames "
            "(95th percentile)." + see_also("PHO-04"),
        ],
        [
            "`MAXIMUM_CENTROID_FALLBACK_FRACTION`",
            f"`{values['fallback']:g}`",
            design_estimate_cell("PHO"),
            "Largest share of star measurements that may keep the aligned position because the "
            "star's own centroid was refused." + see_also("PHO-04"),
        ],
        [
            "`MAXIMUM_ENSEMBLE_SCATTER_OVER_PREDICTED`",
            f"`{values['ens_scatter']}`",
            status_cell(STATUS_PARTLY_MEASURED, "PHO-02"),
            "Largest ratio of the comparison stars' measured scatter to the scatter their error "
            "bars predict. Set between clear nights and a cloudy one.",
        ],
    ])
    template = r"""# Differential photometry and light curve pipeline review guide

<<version_line>>

## Abstract

This document reviews the differential aperture photometry, ensemble normalization, variability
and period-search pipeline (`astrometricslib.pipelines.photometry`).

<<glossary_line>>

<<heading_1>>

<<readme_note>>

**In plain words.** This pipeline measures how the brightness of each star in a field changes
over a night or over many nights, and picks out the stars whose changes are real. A plot of a
star's brightness against time is its light curve.

The difficulty is that the air changes too. Thin cloud, haze and a star sinking toward the
horizon dim every star at once, often by more than the real changes. The main idea is to compare
each star with a set of steady stars in the same image, the comparison stars. The shared dimming
cancels in the ratio. Statistical tests then separate real variation from noise, and a period
search looks for patterns that repeat.

The result is a light curve for every star, a list of candidate variable stars, any periods
found, and a quality summary.

**Statement of need.** Time-domain photometry (brightness measurement repeated over time)
measures how a star's brightness changes. It finds pulsating, eclipsing and rotating variables
and planet transits (the small dip when a planet crosses its star). From the ground, cloud, haze
and airmass (how much atmosphere the light passed through; 1 straight overhead) change every
star's flux together. Differential photometry removes that shared change by dividing each star
by an ensemble (a combined set) of constant comparison stars in the same frames.

A session here is the frames of one night (noon to noon) taken with the same gain setting and
offset; the run uses only the frames of the requested filter. Each session has a reference
frame: the frame every other frame is aligned to, and on which the stars to measure are found.

The diagram shows one run, top to bottom.

```mermaid
flowchart TD
    A["Frames in the requested filter (spectral frames left out)"] --> B["Group into observing sessions"]
    B --> C["Per session: find the stars to measure on the reference frame, optionally named from a catalog"]
    C --> D["Per frame: read capture time, align to the reference frame, find star centers"]
    D --> E["Sum each star's light in a circle, subtract sky, estimate the error"]
    E --> F["Convert times to a common solar-system clock"]
    F --> G["Divide by a fixed set of steady comparison stars"]
    G --> H["Model the noise and score each star's variability"]
    H --> I["Pick variable candidates, limiting false positives"]
    I --> J["Merge sessions (two or more)"]
    J --> K["Label known variables from catalogs"]
    K --> L["Search for periods and test for false alarms"]
    L --> M["Quality gates"]
    M --> N["Quality summary"]
```

Section 3 gives the details: the solar-system clock is $\text{BJD}_\text{TDB}$ (Section 3.1),
the error estimate is the CCD equation (Section 3.2), and the candidate tests are the
chi-square, Stetson J, excess-scatter and q-value tests (Section 3.4). The quality summary is a
`PhotometryQualitySummary` (Section 7).

<<heading_2>>

<<fits_intro>>

The filter comes from each frame's record, and the observatory site comes from the
configuration. The target's sky position comes from the target record.

<<fits_table>>

<<heading_3>>

This section follows a measurement from the time stamp, through the brightness of each star and
its division by the comparison stars, to the tests that call a star variable and the search for
a period.

### 3.1 Capture Time ($\text{BJD}_\text{TDB}$)

Earth moves around the Sun, so light from a star reaches Earth up to about 8 min earlier or
later than it reaches the Sun. Times are therefore moved to the solar system's center of mass
(the barycenter), where this effect vanishes.

`DATE-OBS` is the shutter-open time in UTC. The pipeline adds half the exposure, converts UTC to
TDB (Barycentric Dynamical Time, a uniform time scale for the solar system), and adds the
light-travel time to the solar-system barycenter for the target's direction (up to ±499 s). The
result is the mid-exposure Barycentric Julian Date, $\text{BJD}_\text{TDB}$ (a day count
measured at the barycenter).

With no site configured, the observer is placed at Earth's center. With no target position, no
barycentric time can be computed, and the times stay as the UTC exposure-start times (not
mid-exposure). The `capture_timestamps` gate quotes which time basis was used.

### 3.2 Centroid and aperture photometry

**Placing each star.** Each star is placed at its position on the reference frame, moved by the
frame's fitted shift, rotation and scale change. It is then re-centered on its centroid (the
brightness-weighted center of its light, photutils `centroid_com`). When the centroid is not
trustworthy, the aligned position is kept and the reason is recorded. Apertures are never
rounded to whole pixels.

**Measuring its light.** Aperture photometry sums the light inside a circle (the aperture) and
subtracts the sky that falls in the same circle. The radius is a fixed <<radius>> pixels for
every star, not scaled to the star's width. The net flux is

$$F_\text{net} = \sum_{q \in \text{ap}} I_q - n_\text{pix} \bar{B} \tag{1}$$

where $I_q$ is the value of pixel $q$ in ADU (analog-to-digital units, raw camera counts),
$n_\text{pix}$ the number of pixels in the circle, and $\bar{B}$ the median of a sky ring
(annulus) from <<r_in>> to <<r_out>> pixels. Divided by the exposure, it becomes a flux in ADU
per second.

**Its error.** The CCD equation adds up every noise source in electrons: the star's own photon
noise, the sky, the read noise and the dark current. A large error compared with $F_\text{net}$
means a noisy measurement. In the symbols of the architecture paper's Equation (7), the 1σ error
(one standard deviation) in ADU is $\sigma_{\text{ap}} / G$, where

$$\sigma_{\text{ap}}^2 = S_\star + n_\text{pix}\left(s_\text{sky} + \sigma_\text{rd}^2 + D\right)
  + \frac{n_\text{pix}^2}{n_\text{sky}}\left(s_\text{sky} + \sigma_\text{rd}^2 + D\right) \tag{2}$$

Here $G$ is the gain in electrons per ADU, $S_\star = G F_\text{net}$ the star's net counts, and
$s_\text{sky} = G \bar{B}$ the sky per pixel, all in electrons. $\sigma_\text{rd}$ is the read
noise (the electronic noise of reading the sensor). $D$ is the dark current per pixel (a signal
the sensor builds up from heat); no source records it, so it is taken as zero. $n_\text{sky}$ is
the number of sky pixels. The last term is the error of the sky level itself. This predicted
noise is compared later with the scatter the light curves actually show (Section 3.4).

### 3.3 Differential ensemble normalization

**Choosing the comparison stars.** A fixed comparison set is chosen per session. Candidates are
measured, unsaturated (not clipped at the sensor's maximum) and positive in every usable frame
and not listed as variable. The brightest 2% are skipped, and the band reaches the brightest
30%. Candidates that scatter more than their errors allow are then dropped one at a time, worst
first. Dropping stops when no candidate is out of line, or when only <<min_comp>> remain. At
most <<max_comp>> are kept.

**The ensemble signal.** The symbols follow the architecture paper: $i$ is a star, $c$ a
comparison star and $t$ the time of a frame. Each comparison star's flux is divided by its own
mean over the session, so a ratio of 1 means "normal". The ensemble signal $L_\text{ens}(t)$ is
the weighted average of those ratios in the frame at time $t$, scaled back to flux units by the
mean of the comparison stars' session means. The average is inverse-variance weighted: each star
counts in proportion to one over its squared fractional error, so precise stars count more than
noisy ones. This is Equation (8) of the paper:

$$L_\text{ens}(t) = \bar{\mu}\,\frac{\sum_c w_c(t) \, F_c(t) / \mu_c}{\sum_c w_c(t)}, \qquad
w_c(t) = \frac{1}{\epsilon_c(t)^2}, \qquad
\sigma_\text{ens}(t) = \frac{\bar{\mu}}{\sqrt{\sum_c w_c(t)}} \tag{3}$$

Here $F_c(t)$ is the comparison star's flux (ADU per second), $\mu_c$ its session mean,
$\bar{\mu}$ the mean of the $\mu_c$, and $\epsilon_c(t) = \sigma_{F,c}(t) / \mu_c$ its
fractional error in that frame. $\sigma_\text{ens}(t)$ is the error of the signal, in ADU per
second. $L_\text{ens}(t)$ is the flux of an "average comparison star" at time $t$: if the sky
dims every star by 10% in one frame, it drops by 10% too.

**Normalizing a star.** A star's normalized flux is $\hat{F}_i(t) = F_i(t) / L_\text{ens}(t)$. A
star as bright as the average comparison star has a normalized flux near 1. A comparison star is
divided by the signal built from the other members only (leave one out), so its own changes
never enter its divisor. The error combines the star's own error $\sigma_{F,i}(t)$ and the
signal's error; either one being large makes the result noisy:

$$e_i(t) = \sqrt{\left(\frac{\sigma_{F,i}(t)}{L_\text{ens}(t)}\right)^2
  + \left(\frac{F_i(t) \, \sigma_\text{ens}(t)}{L_\text{ens}(t)^2}\right)^2} \tag{4}$$

Normalization needs at least 2 comparison stars. With fewer, the raw light curves are kept.

### 3.4 Variability indices and candidate rule

**Why several tests.** Every light curve scatters a little from noise alone. A variable star
scatters more than its error bars allow, and its changes are smooth from one point to the next.
Three indices each catch one of these signs.

**Units.** The indices are computed on each star's normalized fluxes $\hat{F}_i(t)$ (Section
3.3). Scatter is quoted in magnitudes (a logarithmic scale where a larger number means fainter;
1 mag is a factor of about 2.5). A fractional scatter $s_\text{frac}$ is
$1.0857\,s_\text{frac}$ magnitudes, where
$1.0857 = 2.5 / \ln 10$. A star's brightness is its instrumental magnitude $m_\text{inst} = -2.5
\log_{10} \bar{F}_\star$, with $\bar{F}_\star$ its mean raw flux in ADU per second: a magnitude on the
camera's own scale, not calibrated to a catalog.

**The noise model.** A noise model gives the scatter a constant star of instrumental magnitude
$m_\text{inst}$ shows in this field, $\sigma_\text{model}(m_\text{inst})$. It is the median
measured scatter in
brightness bins, so it includes noise the CCD equation leaves out (centroid jitter, flat-field
errors). It needs at least <<noise_model_stars>> stars.

**The three indices.** In each, $\hat{F}_j$ is point $j$ of one star's light curve, $e_j$ its
error, $\bar{F}_w$ the error-weighted mean of the points and $n_\text{pts}$ the number of
points, as in the paper's Equation (9). When a star's errors are smaller than the noise model's
scatter, they are scaled up to it, so a constant star gives the values below.

The reduced chi-square $\chi^2_\nu$ asks whether the points scatter more than their error bars
allow. A value near 1 means noise only; well above 1 means extra variation:

$$\chi^2_\nu = \frac{1}{n_\text{pts}-1}\sum_{j=1}^{n_\text{pts}}
  \left(\frac{\hat{F}_j - \bar{F}_w}{e_j}\right)^2 \tag{5}$$

The Stetson J index asks whether neighboring points in time stray to the same side of the mean
together. Noise gives $J$ near 0; a smooth real change gives a clearly positive $J$:

$$\delta_j = \sqrt{\frac{n_\text{pts}}{n_\text{pts}-1}}\,\frac{\hat{F}_j - \bar{F}_w}{e_j},
\qquad J = \frac{1}{n_\text{pts}-1}\sum_{j=1}^{n_\text{pts}-1}\operatorname{sgn}(P_j)
\sqrt{|P_j|}, \qquad P_j = \delta_j \, \delta_{j+1} \tag{6}$$

The excess scatter $E_\sigma$ compares the star's measured scatter (in magnitudes) with what the noise
model expects for a star of its brightness. 1 means typical; 2 means twice the usual scatter:

$$E_\sigma = \frac{\sigma_\text{measured}}{\sigma_\text{model}(m_\text{inst})} \tag{7}$$

Eq. 6 is the single-band Stetson J (all points in one filter; Stetson also defines a version
that pairs points taken in two filters), with consecutive pairs and unit weights (Stetson 1996,
PASP 108, 851).

**Thresholds and score.** The thresholds on $\chi^2_\nu$ and $J$ are the field median plus
<<cal_sigmas>> robust standard deviations. A robust standard deviation is 1.4826 times the
median absolute deviation (MAD), a spread measure that a few variable stars barely move. For
normal data it equals the standard deviation. <<cal_sigmas>> is the one-sided 99% point of a
normal distribution, so a constant star clears a threshold by chance about 1% of the time. For
$\chi^2_\nu$ the threshold is set on its logarithm, because its spread grows with its level. The
threshold on $E_\sigma$ is fixed at <<excess>>.

The ranking score divides each index by its threshold, so a score above 1 means the star clears
its tests. Without a high $J$, the score is capped at 1, so it cannot pass:

$$S_\text{rank} = \begin{cases}\min\left(\chi^2_\nu / \chi^2_\text{thr},\; E_\sigma /
E_{\sigma,\text{thr}}\right) & J > J_\text{thr}\\
\min\left(\chi^2_\nu / \chi^2_\text{thr},\; E_\sigma / E_{\sigma,\text{thr}},\; 1\right) & \text{otherwise}
\end{cases} \tag{8}$$

**Limiting false positives.** Testing thousands of stars makes some pass by chance. A star is a
candidate when $S_\text{rank} > 1$ and its q-value is below <<fdr>>. The chi-square false-alarm probability
is the chance that noise alone gives a $\chi^2$ this large (the upper tail of the chi-square
distribution with $n_\text{pts} - 1$ degrees of freedom). The Benjamini–Hochberg method turns these
probabilities into q-values that control the false discovery rate (FDR): the expected share of
false positives among the stars flagged. A q-value of 0.05 means about 1 in 20 such picks is
expected to be noise.

**Small fields.** A field too small for a noise model falls back to a cutoff on the coefficient
of variation (CV, standard deviation over mean). The cutoff is the typical CV plus a multiple of
its spread, and never below 2%:

$$\text{CV}_\text{cut} = \max\left(0.02,\;
  \text{median}(\text{CV}) + <<cv_sigma>> \times \text{MAD}(\text{CV})\right) \tag{9}$$

where MAD is the unscaled median absolute deviation (the median distance of the values from
their median). <<cv_sigma>> unscaled MADs are about <<cv_robust_sigmas>> robust standard
deviations (<<cv_sigma>> / 1.4826).

### 3.5 Period search

**Methods.** The target's star and the brightest stars are searched with a Lomb-Scargle
periodogram and box least squares. Lomb-Scargle fits sine waves of many trial periods to
unevenly spaced data and finds smooth cycles. Box least squares fits a flat-bottomed dip that
repeats, and finds eclipses and transits.

**False alarms.** The false-alarm probability (FAP) $p$ is the share of block-shuffled copies of
the light curve (the data cut into blocks and put back in random order, which destroys any real
period) that do as well.

**The verdict ladder.** Each search result gets one verdict. From strongest to weakest:

1. "detected": FAP at most <<fap_detect>>, with enough repeats (cycles, or dips) seen;
2. "possible": FAP at most <<fap_possible>> (a dip search also needs at least two dips);
3. "not detected".

A light curve too short to search gets "insufficient data" instead.

**Many searches.** Searching many stars raises the chance that one passes by luck. For $N_s$
independent searches, the chance that at least one reaches probability $p$ by luck is the
family-wise false-alarm probability (Equation (11) of the architecture paper)

$$p_\text{corr} = 1 - (1 - p)^{N_s} \tag{10}$$

The Šidák correction holds that family-wise rate at the verdict limit $p_\text{lim}$ (<<fap_detect>>
for "detected"). Each search's $p$ is turned into $p_\text{corr}$ and judged against $p_\text{lim}$.
That is the same as asking each search for $p \le 1 - (1 - p_\text{lim})^{1/N_s}$
(`family_wise_correction.py`). The correction can only lower a verdict.

**Enough shuffles.** A shuffle test with $N_\text{sh}$ copies cannot report a probability below about
$1/(N_\text{sh}+1)$. With many searches, that floor can sit above the corrected limit, so no verdict could
pass. Searches that need it are run again with more copies, up to <<max_shuffles>>.

**Period checks.** Two period checks (Section 4.1) then test each "detected" or "possible"
result. Each failed check lowers the verdict one step on the ladder. A check that could not run
lowers nothing and marks the result "unconfirmed".

### 3.6 Cross-session merge and labels

With two or more sessions, each star's light curves are matched by sky position and merged
(`batch.py`). Each session keeps its own normalized level. Stars whose level changes between
sessions become long-term candidates. Candidates are then labeled from SIMBAD (a database of
named objects), Gaia DR3 (the Gaia space mission's third catalog) and VSX (the International
Variable Star Index) as listed, not listed, or unknown (`known_variability_labels.py`).

<<notation_section>>

<<heading_4>>

`run_photometry_analysis` (`runner.py`) runs `PhotometryPipelineAdapter` through `run_pipeline`.
<<analysis_note>>

- **Stage 1 (`process_input`).** Keeps frames in the requested filter, leaves out spectral
  frames, and groups frames with a capture time into sessions. With no frame or no session it
  returns `has_work=False`, and `photometry_work` fails.
- **Stage 2 (`run`).** Runs one `VariabilityAnalyzer` pass per session, merges sessions, labels
  candidates, saves the stars, and runs the period search in a step whose failure cannot cost
  the light curves.
- **Stage 3 (`validate_output`).** Fits the run's noise model and records the run gates from
  `photometry_run_gates`. The period checks were already recorded on each period result in
  stage 2. The package defines <<gate_count>>.
  It then stores the run's counts, four run measurements and the gates as quality measurements
  next to the summary (`record_photometry_quality`).
- **Stage 4 (`to_result_dict`).** Returns run counts, the rejected frames, the short-term and
  long-term variable candidates and the cross-session match count.

### 4.1 Quality gates

<<gate_intro>>

Run gates are recorded on the quality summary. Period checks are recorded on each period result.

<<gate_table>>

<<heading_5>>

<<param_intro>>

<<param_table>>

<<heading_6>>

1. **Sparse fields.** Below <<noise_model_stars>> usable stars there is no noise model.
   Candidates come from the CV cutoff of Eq. 9, and `scatter_population` is not checked. Below
   <<scatter_pop>> stars with a scatter, the cutoff itself is called unreliable.
2. **Few comparison stars.** With fewer than 2, raw light curves are kept. With fewer than
   <<min_comp>>, `comparison_ensemble` fails.
3. **Missing or bad `DATE-OBS`.** Only that frame is rejected, with a reason. It is never given
   the computer's clock time instead.
4. **No site configured.** BJD_TDB is computed with the observer at Earth's center, and the time
   basis says so.
5. **Unknown gain.** Errors assume 1 e-/ADU. The fluxes do not change, but `flux_uncertainty` is
   not checked.
6. **Noisy runs.** `detectable_amplitude` reports how large a variable must be to clear the
   noise. A run that needs more than <<max_amp>> mag is blind to most variables.

<<quality_section>>"""
    values["analysis_note"] = ANALYSIS_STAGES_NOTE
    values["quality_section"] = quality_section(
        "PhotometryQualitySummary", "PhotometryPipelineQualityMetrics"
    )
    values["readme_note"] = readme_note("photometry")
    values["notation_section"] = notation_section("3.7", "photometry")
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
        "processing_fraction": require_symbol(
            "astrometricslib.pipelines.spectroscopy.post_processing.record_quality",
            "PROCESSING_QUALITY_MAXIMUM_FAILED_FRACTION",
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
            "shift caused by the air bending each color by a different amount).",
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
            "`float` (°)",
            "Field center (right ascension and declination, the sky's longitude and latitude). "
            "When there is no solved stack (a stack with a sky map), the catalog stars offered "
            "for naming the spectra are limited to this field.",
            "The catalog stars are not limited to the field, so a star from elsewhere on the "
            "sky can be matched by its pixel pattern alone.",
        ],
    ])
    values["param_table"] = parameter_table([
        [
            "`NO_GOOD_MATCH_RMS`",
            f"`{values['no_good']}`",
            status_cell(STATUS_PARTLY_MEASURED, "SPE-16"),
            "Misfit to the best reference spectrum (relative RMS, Eq. 4) above which a "
            "classification is a poor match. It sits between the misfits of most right and "
            "wrong matches on stars of known type.",
        ],
        [
            "`UNRELIABLE_MATCH_RMS`",
            f"`{values['unreliable']}`",
            status_cell(STATUS_PARTLY_MEASURED, "SPE-17"),
            "Misfit (relative RMS, Eq. 4) above which no spectral type is given at all: the "
            "closest reference would only invent a type. Misfits this large mostly mean the "
            "star was identified wrongly.",
        ],
        [
            "`AMBIGUOUS_RMS_GAP`",
            f"`{values['gap']}`",
            status_cell(STATUS_MEASURED, "SPE-18"),
            "Smallest misfit gap to the best reference of another spectral class; a smaller gap "
            "makes the match ambiguous. It is about the smallest spacing between neighboring "
            "reference spectra.",
        ],
        [
            "`DIFFERS_FROM_CATALOG_SUBTYPES`",
            f"`{values['subtypes']}`",
            status_cell(STATUS_PARTLY_MEASURED, "SPE-19"),
            "Subtype steps (ten to a class, so 20 is two whole classes, such as B to F) beyond "
            "which the measured type disagrees with the catalog type. It is set above the "
            "ordinary spread of good classifications.",
        ],
        [
            "`APERTURE_SIGMA_MULTIPLIER`",
            f"`{values['box']}`",
            design_estimate_cell("SPE"),
            "Half-width of the extraction box, in units of the spectrum trail's Gaussian sigma (its "
            "width measure, Eq. 2). ±2.5σ holds about 99% of a Gaussian's light." + see_also("SPE-04"),
        ],
        [
            "`SKY_BAND_GAP_PX` / `SKY_BAND_WIDTH_PX`",
            f"`{values['sky_gap']}` px / `{values['sky_width']}` px",
            status_cell(STATUS_PARTLY_MEASURED, "SPE-01", "SPE-02"),
            "Gap between the box and each sky band, and the band's width, on each side of the "
            "box. The gap keeps the star's own light out of the sky estimate.",
        ],
        [
            "`MINIMUM_LINE_SIGNIFICANCE`",
            f"`{values['zp_sig']:g}`",
            status_cell(STATUS_PARTLY_MEASURED, "SPE-11"),
            "Depth, in units of the local noise, a line needs to count for the zero-point "
            "shift. Set above what noise alone reaches and well below the depth of a real line "
            "in a clean spectrum.",
        ],
        [
            "`WAVELENGTH_SCALE_LIMIT_ANGSTROM`",
            f"`{values['wl_limit']}` Å",
            design_estimate_cell("SPE"),
            "Largest scatter of the run's wavelength zero points (offsets of the wavelength "
            "scale) after correction. It is one pixel of dispersion (11.0 to 11.4 Å per pixel): "
            "a larger error puts a line in the wrong sample.",
        ],
        [
            "`GAIA_XP_SLOPE_LIMIT_PERCENT_PER_1000_ANGSTROM`",
            f"`{values['xp_limit']}`",
            design_estimate_cell("SPE"),
            "Largest median tilt of the ratio observed / Gaia XP (the Gaia mission's own "
            "low-resolution spectra), in % per 1000 Å. About the error the extinction "
            "correction makes at 0.3 airmass." + see_also("SPE-28"),
        ],
        [
            "`PROCESSING_QUALITY_MAXIMUM_FAILED_FRACTION`",
            f"`{values['processing_fraction']:g}`",
            design_estimate_cell("SPE"),
            "Largest share of classified spectra that may fail a limit of the processing "
            "checkpoint before `processing_quality` fails.",
        ],
    ])
    template = r"""# Slitless spectroscopy extraction and classification pipeline review guide

<<version_line>>

## Abstract

This document reviews the slitless spectroscopy pipeline
(`astrometricslib.pipelines.spectroscopy`): extraction, wavelength calibration, refraction,
zero-point, response and extinction corrections, dereddening, classification, the Gaia XP
comparison, and the quality checkpoints.

<<glossary_line>>

<<heading_1>>

<<readme_note>>

**In plain words.** This pipeline spreads each star's light into its colors, a spectrum, and
uses the spectrum to classify the star. The class, or spectral type, mainly tells the star's
surface temperature. The types run O, B, A, F, G, K, M from hottest to coolest, each split into
ten finer subtypes (A0 to A9, for example).

There is no slit, so every star in the field makes its own short rainbow, its spectrum trail, at
once. Spectrum trails can overlap, the sky adds light, and the camera, the optics and the air
each dim some colors more than others. The pipeline traces each spectrum trail, sums its light,
and turns position along the spectrum trail into wavelength. It then removes each known color
effect in turn and finds the reference spectrum that matches best.

The result is a corrected spectrum and a spectral type for each star, compared with star
catalogs and with spectra from the Gaia space mission.

**Statement of need.** A transmission grating (a clear plate ruled with many fine parallel
lines) in front of the camera, for example a Star Analyser 100 or 200, splits each star's light
into a few beams called orders. The zero order passes straight through: it is the star's
undispersed point image. The first order is the first spread-out copy beside it, the first-order
spectrum, and it is the one measured. The pipeline turns those spectrum trails into calibrated
spectra and classifies them against the Pickles (1998) stellar library (a set of reference
spectra of known types).

The diagram shows one run, top to bottom.

```mermaid
flowchart TD
    A["Stacked spectral image"] --> B["Find stars (detection only, no sky map)"]
    B --> C["Name stars from the matching normal image's sky map, else by their pixel pattern"]
    C --> D["Find the spectrum direction and trace each spectrum trail"]
    D --> E["Sum light across each spectrum trail; subtract sky from two side bands"]
    E --> F["Remove light spilling over from bright neighbors"]
    F --> G["Turn position along the spectrum trail into wavelength"]
    G --> H["Correct the shift from the air bending light"]
    H --> I["Correct the offset of the wavelength scale"]
    I --> J["Correct for the sensor's sensitivity at each color"]
    J --> K["Correct for the instrument and for dimming by the air"]
    K --> L["Remove reddening by interstellar dust"]
    L --> M["Classify against reference spectra"]
    M --> N["Compare with Gaia spectra"]
    N --> O["Four checkpoints and quality gates"]
    O --> P["Quality summary"]
```

Section 3 names the steps in the usual terms: the grating equation, differential refraction and
the zero-point shift (Section 3.1); QE, instrument response, airmass extinction and dereddening
(Section 3.3); and the Pickles and Gaia XP comparisons (Sections 3.4 and 3.5). The normal image
is the target's solved standard stack, whose WCS (pixel-to-sky map) names the stars. Without it,
the stars are named by matching the pattern of their pixel positions to catalog stars (Section
6). The quality summary is a `SpectroscopyQualitySummary` (Section 7).

<<heading_2>>

<<fits_intro>>

The dispersion direction is measured from the image. `DISPAXIS` and `GRATROT` are not read. The
observatory site comes from the configuration.

<<fits_table>>

<<heading_3>>

This section follows one spectrum: turning position along the spectrum trail into wavelength,
summing the light, correcting the brightness at each color, and matching the result to reference
spectra.

### 3.1 Wavelength model

**The grating equation.** The grating bends each color by a different angle, so distance along
the spectrum trail measures wavelength: the farther from the zero order, the redder the light.
The dispersion is this spreading of light by wavelength, and its direction on the sensor is
measured from the image. A sample's distance $x$ from the zero-order center, along the
dispersion, becomes a wavelength through the first-order grating equation (`optics_physics.py`):

$$\lambda(x) = d \, \sin\left(\arctan\frac{x \, p_\text{px}}{L_g}\right) \tag{1}$$

where $d$ is the groove spacing (1 / lines per mm), $p_\text{px}$ the pixel pitch and $L_g$ the
grating-to-sensor distance. A larger $L_g$ spreads the spectrum over more pixels. $L_g$ is the one
fitted number. It is fitted once, from the hydrogen (Balmer) absorption lines of a bright
reference star such as Vega (`calibration_tuner.py`), and saved for later runs.

**Refraction shift.** The air bends light slightly, and bends blue light more than red.
Differential atmospheric refraction is this color-dependent bending. It moves each wavelength
along the spectrum trail by $\rho(\lambda) - \rho(\lambda_\text{eff})$, projected onto the
dispersion direction, where $\rho(\lambda)$ is the bending at wavelength $\lambda$ and
$\lambda_\text{eff}$ is a reference (effective) wavelength. It needs the site, a WCS (a
pixel-to-sky map) and the exposure time.

**Zero-point shift.** If the zero-order center is misplaced, the whole wavelength scale slides
by a fixed amount, its wavelength zero point. A zero-point shift corrects that offset. It is
measured from known absorption lines (dark lines where the star's atmosphere absorbs light).
Each line must be at least <<zp_sig>> times the local noise deep, and the shift is applied when
at least <<zp_lines>> lines agree, since one line could be a misidentified feature.

### 3.2 Extraction and background subtraction

**The box.** The spectrum trail is traced step by step. At each step a Gaussian (a bell-shaped
curve) is fitted across the trail. Its sigma is a width measure: a wider trail gets a wider box,
so the box holds the same share of the light everywhere. The box half-width is

$$w(x) = <<box>> \, \sigma_\text{trail}(x) \tag{2}$$

where $\sigma_\text{trail}$ is the fitted Gaussian sigma, smoothed over 61 steps.
`median_trail_width_px` in the summary is this sigma too, not a FWHM (full width at half
maximum, a different width measure about 2.35 times sigma). Edge pixels count for the fraction
inside the box.

**The sky.** Two sky bands, each <<sky_width>> pixels wide and <<sky_gap>> pixels from the box,
are sigma-clipped (pixels far from the median, such as stars, are dropped). The sky level is the
mean of their medians. When the two disagree by more than the noise allows, the fainter band
(the one with the lower median) is used alone: light can only add to a band, so the brighter one
holds a neighbor's light.

**Neighbors.** A bright neighbor's wings (the faint outer edges of its spectrum trail) are
removed with a blur profile measured from an isolated star (`neighbor_trail_deblending.py`,
`neighbor_wing_correction.py`).

### 3.3 Flux calibration

**What is corrected.** The raw counts at each wavelength are not the star's true brightness at
that color. The sensor, the optics and the air each pass some colors better than others. Three
corrections undo this in turn.

- **QE.** The counts are divided by the sensor's quantum efficiency (QE: the share of arriving
  light the sensor records, which depends on color).
- **Instrument response.** The counts are then divided by the instrument response: everything
  else about the optics and grating that favours some colors. The response is Vega's observed
  spectrum over the Pickles A0V template. A0V is Vega's own type: spectral type A0 with
  luminosity class V, a main-sequence (ordinary, hydrogen-burning) star.
- **Airmass extinction.** The air dims light more at blue wavelengths, and more when the star is
  low. The extinction curve $k_\text{ext}(\lambda)$ gives the dimming in magnitudes per airmass (a
  magnitude is a logarithmic brightness unit; 1 mag is a factor of about 2.5). Only the airmass
  difference $X_\text{target} - X_\text{ref}$ from the response's spectroscopic standard star (the star of
  known spectrum that set the response) is removed, because the response already holds that
  star's own dimming:

$$\Phi_\text{corr}(\lambda) = \Phi(\lambda)\; 10^{0.4\, k_\text{ext}(\lambda)\,
(X_\text{target} - X_\text{ref})} \tag{3}$$

The result is a relative $\Phi(\lambda)$ (energy flux per unit wavelength) up to one constant: the
shape of the spectrum is right, its overall level is not known.

**Dereddening.** Dust between the stars dims blue light more than red, so a star seen through
dust looks redder, and cooler, than it is. $E(B-V)$ is the size of this reddening, given as the
extra difference between blue and visual magnitudes. When a catalog gives the star's $E(B-V)$,
interstellar reddening is removed with the Cardelli, Clayton and Mathis (1989) law with $R_V =
3.1$ ($R_V$ sets how the dimming changes with wavelength).

### 3.4 Classification

Each reference spectrum is blurred to the instrument's resolution (the smallest wavelength
detail it can separate) and scaled to the observation by a best-fit factor $a$. The match score
is the relative RMS (root mean square) difference between the observed spectrum $\Phi$ and the
scaled reference $T_\text{ref}$. It is taken over $M$ wavelengths and divided by the mean flux $\bar{\Phi}$. A
score of 0 is a perfect match; 0.1 means a typical misfit of 10% of the flux:

$$\text{RMS} = \frac{1}{\bar{\Phi}}
  \sqrt{\frac{1}{M}\sum_{j=1}^{M}\left[\Phi(\lambda_j) - a\,T_\text{ref}(\lambda_j)\right]^2} \tag{4}$$

The references are ranked by this score, and the lowest wins. Above <<no_good>> the match is
poor. Above <<unreliable>> no type is given.

Two gaps measure how clearly the best reference wins. The gap to the second-best reference
(often a neighboring subtype) and the gap to the best reference of another spectral class are
each compared with <<gap>>. A gap below it makes the match ambiguous at that level. Only a
class-level ambiguity fails `spectral_classification`; subtype-level ties are counted in the
gate detail.

### 3.5 Gaia XP comparison

The Gaia space mission measured low-resolution spectra, called XP spectra, for many stars,
independently of this instrument. Where Gaia DR3 (its third data release) has an XP spectrum for
the star, both spectra are brought to the same resolution and divided by their median over 5400
to 5600 Å (1 Å, an ångström, is 0.1 nm). If the calibration is right, their ratio is flat. The
tilt of their ratio tests the response, the extinction correction and the wavelength scale
together.

<<notation_section>>

<<heading_4>>

`run_spectroscopy_analysis` (`runner.py`) runs `SpectroscopyPipelineAdapter` through
`run_pipeline`. <<analysis_note>>

- **Stage 1 (`process_input`).** A no-op that returns an empty `Result`.
- **Stage 2 (`run`).** Finds the stars with `AstrometryPipeline` (no solve), names them from the
  target's solved stack, and runs `SpectroscopyPipeline.process`. Each spectrum carries four
  quality checkpoints, in order: `raw_frame`, `pre_processing`, `processing` and
  `post_processing`. Each checkpoint is a set of measured values (metrics), some with a limit.
  The `processing` checkpoint judges the classification: a poor match, an ambiguous match, and a
  type from line strengths that is more than <<subtypes>> subtype steps from the template-fit
  type.
- **Stage 3 (`validate_output`).** Builds the summary, records the eight
  `spectroscopy_run_gates`, then `gaia_xp_agreement` (<<gate_count>> in all).
  It then stores the run totals, the per-stage medians and the gates as quality measurements
  (`record_spectroscopy_quality`).
- **Stage 4 (`to_result_dict`).** Returns only `context` and `stellar_objects`.

A session of raw frames runs through `batch.py` instead. It names the stars in one reference
frame, follows them through the session's other frames in parallel workers, and builds the same
gates from the same plain counts.

### 4.1 Quality gates

<<gate_intro>>

The count covers only these run gates. Some quality tests are not gates. Two travel with each
spectrum: the extinction record (whether the airmass correction ran, and why not) and the share
of sky readings disturbed by a neighbor. The third, the rejection of a wavelength fit that
leaves too large an error, happens when the instrument is calibrated (`calibration_tuner.py`).

<<gate_table>>

<<heading_5>>

<<param_intro>>

<<param_table>>

<<heading_6>>

1. **Zero-order saturation.** Saturated pixels are clipped at the sensor's maximum value, so
   their true brightness is lost. When a star's zero order holds a significant share of them,
   `zero_order_saturation` fails. A saturated center can offset the wavelength scale; the
   zero-point shift corrects it when enough lines agree.
2. **Missing header data.** No `DATE-OBS`, site or WCS skips the refraction correction. No
   `AIRMASS` skips the extinction correction. Each skip is recorded on the spectrum.
3. **Ambiguous or poor matches.** They are kept with a flag and counted in the summary.
   `spectral_classification` fails only on a class-level ambiguity or a poor match.
4. **Reddened hot stars.** With no catalog $E(B-V)$, an O, B or A star tends to read about one
   step of the reference list cool. The list skips some subtypes, so one step can be more than
   one subtype: the A stars of <<SPE-16>> read two subtypes cool.
5. **No solved stack.** Stars are named by matching the pattern of their pixel positions to
   catalog stars ("by geometry"), which can fail on sparse fields.

<<quality_section>>"""
    values["analysis_note"] = ANALYSIS_STAGES_NOTE
    values["quality_section"] = quality_section(
        "SpectroscopyQualitySummary", "SpectroscopyPipelineQualityMetrics"
    )
    values["readme_note"] = readme_note("spectroscopy")
    values["notation_section"] = notation_section("3.6", "spectroscopy")
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
    pipeline_module = "astrometricslib.pipelines.asteroid_detection.pipeline"
    values = {
        "version_line": VERSION_LINE,
        "pointing_radius": require_symbol(pipeline_module, "_POINTING_CORRECTION_SEARCH_RADIUS_ARCSEC"),
        "vote_bin": require_symbol(pipeline_module, "_POINTING_CORRECTION_VOTE_BIN_ARCSEC"),
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
            "`float` (°)",
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
            design_estimate_cell("MOV"),
            "Fewest detections in a chain (detections linked across frames as one object)."
            + see_also("MOV-01"),
        ],
        [
            "`pixel_match_tolerance_px`",
            cfg("pixel_match_tolerance_px"),
            design_estimate_cell("MOV"),
            "Pixel spread below which a chain is a hot pixel (a sensor pixel that always reads high)."
            + see_also("MOV-02"),
        ],
        [
            "`sky_match_tolerance_arcsec`",
            cfg("sky_match_tolerance_arcsec"),
            design_estimate_cell("MOV"),
            "Sky spread below which a chain is a fixed star (Eq. 2)." + see_also("MOV-02"),
        ],
        [
            "`rate_min_arcsec_per_hour` / `rate_max_arcsec_per_hour`",
            f"{cfg('rate_min_arcsec_per_hour')} / {cfg('rate_max_arcsec_per_hour')} arcsec/h",
            design_estimate_cell("MOV"),
            "Allowed range of the total speed across the sky." + see_also("MOV-01"),
        ],
        [
            "`chain_match_radius_max_arcsec`",
            f"{cfg('chain_match_radius_max_arcsec')} arcsec",
            design_estimate_cell("MOV"),
            "Cap on how far a chain may reach for its next detection (Eq. 1)." + see_also("MOV-08"),
        ],
        [
            "`residual_rms_max_multiple`",
            cfg("residual_rms_max_multiple"),
            status_cell(STATUS_PARTLY_MEASURED, "MOV-03"),
            "Largest RMS (root mean square) distance from the fitted straight line on each axis, "
            "in units of that axis's position error.",
        ],
        [
            "`min_displacement_error_multiple`",
            cfg("min_displacement_error_multiple"),
            status_cell(STATUS_PARTLY_MEASURED, "MOV-03"),
            "Smallest total distance moved, in units of the position error.",
        ],
        [
            "`astrometric_error_default_arcsec`",
            f"{cfg('astrometric_error_default_arcsec')} arcsec",
            design_estimate_cell("MOV"),
            "Position error assumed for a detection with none measured." + see_also("MOV-06"),
        ],
        [
            "`ephemeris_cross_match_radius_arcsec`",
            f"{cfg('ephemeris_cross_match_radius_arcsec')} arcsec",
            design_estimate_cell("MOV"),
            "Match radius for a known body from SkyBoT (an online service that lists known "
            "solar-system bodies), at both the first and last detection." + see_also("MOV-06"),
        ],
        [
            "`_POINTING_CORRECTION_SEARCH_RADIUS_ARCSEC`",
            f"`{values['pointing_radius']:g}` arcsec",
            status_cell(STATUS_PARTLY_MEASURED, "MOV-05"),
            "Pairing radius between stack stars and frame detections in the per-frame "
            "pointing-offset vote (Section 3.1). Wide enough to contain a mount error of several "
            "arcminutes; the vote copes with the wrong pairs a wide radius admits.",
        ],
        [
            "`_POINTING_CORRECTION_VOTE_BIN_ARCSEC`",
            f"`{values['vote_bin']:g}` arcsec",
            status_cell(STATUS_PARTLY_MEASURED, "MOV-05"),
            "Bin width of the offset vote. Small enough to separate the true offset from the "
            "typical spacing of the field's stars, and well above the star finder's sub-arcsec "
            "centering noise.",
        ],
    ])
    template = r"""# Asteroid detection pipeline review guide

<<version_line>>

## Abstract

This document reviews the asteroid detection pipeline
(`astrometricslib.pipelines.asteroid_detection`): per-frame pointing, detection, chaining within
each session (one observing night, from local noon to local noon), the stationary and
straight-line tests, and the SkyBoT cross-match.

<<glossary_line>>

<<heading_1>>

<<readme_note>>

**In plain words.** This pipeline looks for asteroids and comets in a series of frames of the
same patch of sky. Stars stay put from frame to frame. A nearby body in the solar system moves a
little between frames.

Many things appear in one frame and not the next without being asteroids: noise, hot pixels
(sensor pixels that always read high) and faint stars near the detection limit. Each frame's
pointing also differs slightly. The pipeline lines up every frame on the stars and links
detections from frame to frame into chains. It drops chains that do not move, and keeps chains
that move in a straight line at a plausible, steady speed. It then asks an online service which
known bodies were at those places at those times.

The result is a list of moving objects, each matched to a known body or marked as unknown and
worth a closer look.

**Statement of need.** Asteroids and comets move against the stars, so they show up as point
sources (star-like dots) that shift from frame to frame. The pipeline finds those sources, links
them into straight-line tracks and asks the IMCCE SkyBoT service (an online list of where every
known solar-system body is at a given time) which known bodies were there. A confirmed mover
with no match is a lead for a person to check.

The diagram shows one run. The left branch prepares the reference stars; the right branch
prepares each frame.

```mermaid
flowchart TD
    A["Stacked image with a sky map (run astrometry first)"] --> B["Reference stars from the stack"]
    D["Sky map per frame: rotation and scale from the stack, center from the mount"]
    C["Single frames with capture times"] --> D
    B --> E["Correct each frame's pointing (offset vote)"]
    D --> E
    E --> F["Find star-like sources"]
    F --> G["Link detections into chains within each session"]
    G --> H["Drop chains that do not move (hot pixels, stars)"]
    H --> I["Keep straight-line movers at a plausible speed"]
    I --> J["Look up known bodies (SkyBoT)"]
    J --> K["Quality gates"]
    K --> L["Quality summary"]
```

A sky map is a WCS (World Coordinate System): the map from pixels to sky positions. A
plate-solved stack is a stacked image whose WCS has been found by the astrometry pipeline. The
quality summary is an `AsteroidDetectionQualitySummary` (Section 7).

<<heading_2>>

<<fits_intro>>

The target must have a plate-solved stack: run astrometry first. Without a stacked image the run
raises `ConflictError`.

<<fits_table>>

<<heading_3>>

This section follows the frames from pointing, through linking detections into chains, to the
tests a chain must pass and the look-up of known bodies. Angles on the sky are in arcseconds
(arcsec, 1/3600 of a degree).

### 3.1 Per-frame pointing

**Why.** Siril (the stacking program) does not save each frame's alignment, so each frame's WCS
takes its rotation and scale from the stack and its center from the mount's `RA`/`DEC`
(`frame_wcs_composer.py`). A mount is often tens of arcseconds off.

**How.** The stack's own stars are therefore matched to each frame's detections within
<<pointing_radius>> arcsec. Every pair votes for an offset in <<vote_bin>>-arcsec bins, and the
winning offset is removed from the frame's positions. True pairs agree on one offset; chance
pairs scatter their votes. A plain nearest-star match is not used: where the stars are about as
far apart as the mount's error, many detections pair with the wrong star (partly measured; see
<<MOV-05>>).

**Position error.** The same vote measures each frame's position error: the scatter of the
matched stack stars about the winning offset, on each axis, combined with the error of finding a
faint dot's center. This is the "position error" the tests below use. A chain's error is the
root mean square (RMS) of its detections' errors.

### 3.2 Chaining

Detections are linked only within one session. Frames are taken in time order, and each open
chain may claim the nearest detection in the next frame. The search radius $r_\text{search}$ is
the distance the
fastest allowed mover could travel in the time between frames $\Delta t$, capped so it never
grows too large:

$$r_\text{search} = \min\left(v_\text{max}\, \Delta t,\; r_\text{cap}\right) \tag{1}$$

with $v_\text{max}$ = <<cfg_rate_max_arcsec_per_hour>> arcsec/h and $r_\text{cap}$ =
<<cfg_chain_match_radius_max_arcsec>> arcsec. Claims go nearest-first over all chains. A chain
may skip frames; it is not limited to consecutive ones. A chain needs at least
<<cfg_min_frames_for_persistence>> detections.

### 3.3 Stationary tests

A chain that does not move is not an asteroid. If it stays put on the sensor, it is a hot pixel;
if it stays put on the sky, it is a star. Both spreads are the largest distance of any detection
from the chain's mean position. A small spread means the chain did not move:

$$\max_j \left\lVert \mathbf{p}_j - \bar{\mathbf{p}} \right\rVert
  < <<cfg_pixel_match_tolerance_px>>\ \text{px} \;\Rightarrow\; \text{hot pixel}, \qquad
\max_j \theta\left(\mathbf{s}_j, \bar{\mathbf{s}}\right)
  < <<cfg_sky_match_tolerance_arcsec>>\ \text{arcsec} \;\Rightarrow\; \text{fixed star} \tag{2}$$

where $\mathbf{p}_j$ is the pixel position of detection $j$ and $\mathbf{s}_j$ its sky position
on the tangent plane
(the sky projected onto a flat plane that touches it at the field center), and $\theta$ is the
angle between two sky positions.

### 3.4 Straight-line and rate tests

Over one session, an asteroid moves in a nearly straight line at a nearly steady speed. Noise
linked by chance does not. Each detection's RA and Dec (right ascension and declination, the
sky's longitude and latitude) are turned into offsets $\xi, \eta$ in arcsec on the tangent
plane, measured from a reference point $(\alpha_\text{ref}, \delta_\text{ref})$:

$$\xi = (\alpha - \alpha_\text{ref}) \cos \delta_\text{ref}, \qquad
  \eta = \delta - \delta_\text{ref} \tag{3}$$

The $\cos \delta$ factor is needed because lines of equal RA meet at the poles: away from the
equator, a step in RA covers a smaller angle on the sky. Each offset is fitted against time with
a straight line. The slopes $\dot{\xi}$ and $\dot{\eta}$ are the true speeds across the sky
along the two axes:

$$\xi(t) = \xi_0 + \dot{\xi}\,(t - t_0), \qquad
  \eta(t) = \eta_0 + \dot{\eta}\,(t - t_0) \tag{4}$$

A chain passes when all of these hold:

- The total rate $\sqrt{\dot\xi^2 + \dot\eta^2}$ is between <<cfg_rate_min_arcsec_per_hour>> and
  <<cfg_rate_max_arcsec_per_hour>> arcsec/h.
- On each axis, the RMS distance from the line is at most <<cfg_residual_rms_max_multiple>>
  times that axis's position error.
- The total displacement is at least <<cfg_min_displacement_error_multiple>> times the position
  error, so a star that only jitters by its error does not pass.
- The positions never move back along the fitted direction by more than the combined position
  error of the two detections compared (the square root of the sum of their squared errors).
  Otherwise the chain is `REJECTED_NON_MONOTONIC`, meaning the motion is not one-way.

### 3.5 Known-body match

SkyBoT's prediction of where a known body is at a given time is its ephemeris. SkyBoT is asked
twice per mover: at its first and at its last detection. Checking both ends makes sure the known
body also moves the same way. A known body matches only if it is within
<<cfg_ephemeris_cross_match_radius_arcsec>> arcsec at both moments.

<<notation_section>>

<<heading_4>>

`run_asteroid_detection_analysis` (`runner.py`) runs `AsteroidDetectionPipelineAdapter` through
`run_pipeline`. <<analysis_note>>

- **Stage 1 (`process_input`).** A no-op that returns an empty `Result`.
- **Stage 2 (`run`).** Runs `AsteroidDetectionPipeline.process` on the light frames that have a
  timestamp. Only straight-line movers and matched bodies are kept on the target.
- **Stage 3 (`validate_output`).** Builds the summary, records `asteroid_run_gates`
  (<<gate_count>>) and stores the run's counts and gates as quality measurements
  (`record_asteroid_quality`).
- **Stage 4 (`to_result_dict`).** Returns the status, stage counts and surviving candidates.

`mpc_report.py` can write Minor Planet Center (MPC, the clearing house for asteroid
observations) reports from detections, in its 80-column and ADES text formats, but no pipeline
step calls it. A run does not produce MPC records. `rotation_period.py` builds a light curve
from a track for the period search; the pipeline does not call it either.

### 4.1 Quality gates

<<gate_intro>>

A failed gate here does not always mean the run went wrong. `unmatched_movers` fails on purpose
when a confirmed mover matches no known body: the failure flags the run so that a person looks
at what may be a new object, or at a known one the match missed.

<<gate_table>>

<<heading_5>>

<<param_intro>>

The values below are the defaults of `MovingObjectConfig`, plus the two pointing-vote constants
of `pipeline.py`. The configuration can change the defaults.

<<param_table>>

<<heading_6>>

1. **Too few frames.** With fewer searchable frames than a chain needs, `search_frames` is not
   checked, rather than reporting a clean null result.
2. **Missing pointing cards.** Frames without `RA`, `DEC`, `NAXIS1` or `NAXIS2` are excluded and
   `pointing_metadata` fails.
3. **Weak pointing vote.** When too few pairs agree on an offset, the frame keeps its raw mount
   pointing and has no measured position error.
4. **SkyBoT outages.** Confirmed movers stay as candidates and `ephemeris_cross_match` fails.
5. **No measured position error.** The default error is assumed, and `track_residuals` fails and
   says how many detections used it.

<<quality_section>>"""
    values["analysis_note"] = ANALYSIS_STAGES_NOTE
    values["quality_section"] = quality_section(
        "AsteroidDetectionQualitySummary", "AsteroidDetectionPipelineQualityMetrics"
    )
    values["readme_note"] = readme_note("asteroid_detection")
    values["notation_section"] = notation_section("3.6", "asteroid_detection")
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
    flat_module = "astrometricslib.pipelines.stacking.pre_processing.flat_calibration"
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
        "group_minimum": require_symbol(
            "astrometricslib.pipelines.stacking.processing.exposure_groups",
            "MINIMUM_FRAMES_PER_EXPOSURE_GROUP",
        ),
        "gap_ratio": require_symbol(
            "astrometricslib.pipelines.stacking.pre_processing.background_homogeneity",
            "DEFAULT_GAP_RATIO_THRESHOLD",
        ),
        "flat_noise": require_symbol(flat_module, "MAXIMUM_FLAT_NOISE_FRACTION"),
        "flat_low": require_symbol(flat_module, "MINIMUM_FLAT_LEVEL_FRACTION"),
        "flat_high": require_symbol(flat_module, "MAXIMUM_FLAT_LEVEL_FRACTION"),
        "saturation": require_symbol(
            "astrometricslib.pipelines.shared.quality.saturation", "DEFAULT_SATURATION_FLAG_THRESHOLD"
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
            "Gain setting: the camera's amplifier setting, a unitless number (not the gain in "
            "electrons per ADU). `ISOSPEED` is a DSLR camera's ISO setting, its counterpart of a "
            "gain setting. Used for the minority-gain test and to file calibration frames.",
            "Frames fall into one unknown-gain group; `gain_homogeneity` is not checked.",
        ],
        [
            "`OFFSET` (or `BLKLEVEL`)",
            "Recommended",
            "`int`",
            "Camera offset: a constant the camera adds to every pixel so that noise below zero "
            "is still recorded. Used to file calibration frames.",
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
            status_cell(STATUS_PARTLY_MEASURED, "STK-06"),
            "Width of a dark temperature slot, and the largest temperature gap before a soft "
            "flag (a warning that does not stop the dark being used). Two real incidents show the "
            "harm of mixing temperatures; the width itself is not fitted to the camera's "
            "dark-current curve.",
        ],
        [
            "`DEFAULT_MINIMUM_SURVIVING_FRAMES`",
            f"`{values['min_surviving']}` frames",
            status_cell(STATUS_PARTLY_MEASURED, "STK-10"),
            "Fewest frames that must be left after Siril's percentage-based frame filters.",
        ],
        [
            "`DEFAULT_FWHM_DEGRADATION_RATIO`",
            f"`{values['degrade']}`",
            status_cell(STATUS_PARTLY_MEASURED, "STK-25"),
            "Largest ratio of the stack's FWHM to the expected FWHM (Eq. 3). Set "
            "just above the ratio of a normal stack.",
        ],
        [
            "`DEFAULT_REJECTED_FRACTION_FLAG_THRESHOLD`",
            f"`{values['rej']}`",
            status_cell(STATUS_PARTLY_MEASURED, "STK-24"),
            "Share of pixels rejected as outliers during stacking above which "
            "`rejected_pixel_fraction` fails. Set above the share a normal session rejects.",
        ],
        [
            "`MAXIMUM_QUARANTINED_FRACTION`",
            f"`{values['quarantine']}`",
            status_cell(STATUS_PARTLY_MEASURED, "STK-03"),
            "Largest share of a batch the quarantine (setting bad frames aside) may move. About "
            "twice the share it picks from a normal batch: room for a bad stretch, without "
            "gutting a session whose limits do not suit the equipment.",
        ],
        [
            "`MINIMUM_PENALTY_TO_WEIGHT`",
            f"`{values['penalty']}`",
            design_estimate_cell("STK"),
            "Noise penalty $P$ of equal weights (Eq. 4) at or above which noise weighting is "
            "used. The benefit of noise weighting is measured; the switch-on value is not."
            + see_also("STK-07"),
        ],
        [
            "`MINIMUM_REGISTERED_FRACTION`",
            f"`{values['registered']}`",
            design_estimate_cell("STK"),
            "Share of spectral frames that must register (align on each other); fewer triggers a "
            "retry. Chosen to tolerate one lost frame in ten." + see_also("STK-12"),
        ],
        [
            "`MINIMUM_FRAMES_PER_EXPOSURE_GROUP`",
            f"`{values['group_minimum']}`",
            design_estimate_cell("STK"),
            "Fewest frames an exposure length needs to be stacked on its own; a smaller set joins "
            "the group with the nearest exposure length." + see_also("STK-11"),
        ],
        [
            "`DEFAULT_GAP_RATIO_THRESHOLD`",
            f"`{values['gap_ratio']:g}`",
            status_cell(STATUS_PARTLY_MEASURED, "STK-04"),
            "Smallest gap between two groups of sky-background levels, as a multiple of the "
            "spread within a group, that counts as a split. Set between the gaps of a normal "
            "night and of sudden cloud.",
        ],
        [
            "`MINIMUM_FLAT_LEVEL_FRACTION` / `MAXIMUM_FLAT_LEVEL_FRACTION`",
            f"`{values['flat_low']:g}` / `{values['flat_high']:g}`",
            design_estimate_cell("STK"),
            "Allowed level of the master flat, as a share of the sensor's full scale (the "
            "largest value a pixel can record). Common practice.",
        ],
        [
            "`MAXIMUM_FLAT_NOISE_FRACTION`",
            f"`{values['flat_noise']:g}`",
            status_cell(STATUS_PARTLY_MEASURED, "STK-05"),
            "Largest relative noise (standard deviation over mean) of the master flat before it "
            "is smoothed and `flat_noise` fails. A flat at the limit adds only a few percent to "
            "the variance of a typical stack's sky noise; a noisier flat leaves a fixed pattern, "
            "which smoothing removes.",
        ],
        [
            "`DEFAULT_SATURATION_FLAG_THRESHOLD`",
            f"`{values['saturation']:g}`",
            design_estimate_cell("STK"),
            "Share of saturated pixels at or above which the stack is flagged (`saturated_pixel_fraction`).",
        ],
    ])
    template = r"""# Astronomical stacking and image calibration pipeline review guide

<<version_line>>

## Abstract

This document reviews the stacking pipeline (`astrometricslib.pipelines.stacking`): frame tests,
calibration-frame selection, the Siril run, exposure groups, stack quality gates and the steps
after the stack.

<<glossary_line>>

<<heading_1>>

<<readme_note>>

**In plain words.** This pipeline combines many frames of the same target into one deeper,
cleaner image, called a stack.

Each raw frame carries flaws from the camera and the optics: a fixed electronic level, a glow
that builds up with exposure time, and darker corners and dust shadows. Each frame is also
noisy, and some catch satellite or meteor streaks or cosmic-ray hits. The pipeline first removes
the camera and optics flaws using reference frames taken for the purpose (calibration frames).
It then lines the frames up on their stars and averages them, rejecting pixels that disagree
with the rest.

The result is the stacked image, a preview image, and a quality summary that checks both the
inputs and the result.

**Statement of need.** Raw frames carry three kinds of flaw:

- dark current: a signal the sensor builds up from heat, which grows with exposure time;
- a bias level: the fixed electronic level of an empty exposure;
- vignetting (darker corners from the optics) and dust shadows.

Three kinds of master calibration frame remove them. Each master is the average of many frames.
A master dark is taken with the shutter closed, a master bias with the shortest exposure the
camera allows, and a master flat of an evenly lit surface. Stacking many aligned frames raises
the signal-to-noise ratio by about $\sqrt{N}$ for $N$ frames and rejects streaks and cosmic
rays.

The diagram shows one run, top to bottom.

```mermaid
flowchart TD
    A["Start a stack for a target"] --> B["Select frames: one camera, no mix of spectra and normal images"]
    B --> C["Set aside frames taken at a less common gain setting"]
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
    M --> N["Quality summary and quality gates"]
    N --> O["Preview image and inputs record"]
```

Siril is an open-source astronomy image processor that does the pixel work. In code terms the
run starts at `stage.stack_frames(target)`, splits frames in `stack_runner.run_stack`, and
stacks through a `StackingDriver` (the interface for a stacking program), whose Siril version
runs `ImageProcessing` (Section 4). Setting bad frames aside is the frame quarantine, and the
quality summary is a `StackQualitySummary` (Section 7).

<<heading_2>>

<<fits_intro>>

<<fits_table>>

<<heading_3>>

This section covers choosing calibration frames, predicting how sharp the stack should be,
weighting frames, and combining frames of different exposure lengths.

### 3.1 Calibration-frame selection

A dark only cancels the heat signal if it was taken at about the same sensor temperature as the
light frames (the frames of the sky). The calibration library files each frame under its gain
setting, offset and binning, and each dark also under a temperature slot <<dark_tol>> °C wide.
Lights use the darks of their own slot. With none, the nearest-temperature dark is used.

The master's temperature is the mean of its darks. A soft flag (a warning that does not stop the
dark being used) is raised when the gap between the light's temperature and the master's is too
large:

$$\left|T_\text{light} - \bar{T}_\text{dark}\right| > <<dark_tol>>\,^\circ\text{C} \tag{1}$$

The dark is still applied; the flag makes `calibration_metadata` fail. When no dark records a
temperature, the match is not judged. A gain-setting or offset mismatch falls back the same way.
A binning mismatch never falls back.

### 3.2 Expected stack FWHM

FWHM (full width at half maximum) is the width of a star's light profile at half its peak
height; a smaller FWHM means a sharper image. Averaging aligned frames adds no blur of its own,
so the stack's FWHM follows from the input FWHMs. The pipeline predicts that FWHM and checks the
stack against it. For round Gaussian (bell-shaped) stars whose widths in the $N$ frames are
$\text{FWHM}_1, \dots, \text{FWHM}_N$, the stacked star's FWHM is the root mean square of the
inputs:

$$\text{FWHM}_\text{expected} = \sqrt{\frac{1}{N}\sum_{j=1}^{N}\text{FWHM}_j^2} \tag{2}$$

The ratio $\rho_\text{FWHM}$ of measured to expected FWHM is 1 for a perfect stack. A ratio well above 1 means
the alignment or the combination blurred the stars. The stack passes when

$$\rho_\text{FWHM} = \frac{\text{FWHM}_\text{stack}}{\text{FWHM}_\text{expected}} \le <<degrade>> \tag{3}$$

### 3.3 Weighting and rejection

**Why weight.** For frames limited by the noise of the sky, a frame's signal-to-noise squared
grows in step with its exposure. A 30 s frame then carries less information than a 120 s frame,
and giving both the same weight wastes the better one. Weighting each frame by its own noise
gives the best combination.

**The penalty of equal weights.** $P$ is how much noisier an equal-weight stack is than the
best-weighted one, for sky-limited frames. It follows from the exposure times alone:

$$P = \sqrt{\langle 1/\tau_j \rangle \, \langle \tau_j \rangle} \tag{4}$$

where $\tau_j$ is the exposure of frame $j$ and $\langle \cdot \rangle$ is the mean over
frames. $P$ is 1 when all exposures
are equal and grows as they differ; 1.17 means 17% more noise.

**The rule.** A configured weighting always wins. With none configured, an imaging stack uses
Siril's noise weighting when $P \ge <<penalty>>$. Spectral stacks are left to the exposure-group
handling of Section 3.4.

**When P is not 1.** $P$ is computed over all of a stack's frames before they are split by
exposure length (Section 3.4). So a stack whose exposure lengths differ enough to reach the
limit gets noise weighting, and that setting is passed to the Siril stack of every group. Inside
one group the exposures are equal, so the weighting then evens out other differences in noise
between frames. Frames of different lengths share one Siril stack in two cases. The first is
when a length has fewer than <<group_minimum>> frames and joins the group with the nearest
length. The second is when no length has that many frames, or a frame has no readable exposure,
so all frames are stacked together. Between groups, the inverse-variance combination of Section
3.4 does the weighting.

**Rejection.** Siril stacks with `-norm=addscale` (each frame's level and spread are matched to
the group's reference frame before combining) and sigma rejection (`rej`). Sigma rejection
drops, at each pixel, values more than a set number of standard deviations from the rest, which
removes streaks and cosmic rays.

The limits are set in one of three ways. A limit passed by the caller always wins. In the
default "adaptive" mode the high limit comes from Chauvenet's criterion, which rejects a value
when fewer than half a frame of a stack of $N$ frames would stray that far by chance. This limit
grows slowly with $N$. The high limit is kept at or above a configured floor, and the low limit
is set a little looser, because the bad values in sky images (streaks, cosmic rays, hot pixels)
are brighter than the truth. A "fixed" mode uses one configured pair of limits for every stack.

### 3.4 Exposure groups

**Why groups.** Siril's usual recipe matches every frame to one noise level and then clips
outliers. With several exposure lengths, a 5 s frame and a 0.5 s frame hold very different
amounts of light in the same pixel, and the clipping throws away the best frames' values. Each
length also needs darks of its own length. So every stack with more than one exposure length is
stacked one length at a time. A spectral stack is also split by night, because the zero order
(the star's undispersed image) sits on a different pixel each night and aligning two nights
together can lose most frames.

**Putting groups on one scale.** Each group's stack is turned into counts per second. That alone
does not line the groups up, because Siril's normalization leaves each group's stack on the
brightness scale of its own reference frame. Each group therefore gets a group scale factor that
puts it on the scale of the reference group (the group with the most weight). On an imaging
stack the factor is the median ratio of the aperture brightness of isolated, unsaturated stars
found in both groups. A group with too few such stars, and every spectral stack, uses pixels
instead: the median ratio over mid-range pixels, with saturated pixels left out. A group that
one factor does not describe is left out, because its brightness does not grow in step with
exposure. With stars, that is when the ratio of its faintest quarter or its brightest quarter of
stars sits more than a set tolerance from the factor; with pixels, when the ratio over its
brightest pixels does.

**Combining.** The rest are combined with inverse-variance weights: each group counts in
proportion to $1/\sigma^2$, where $\sigma$ is the background noise of its stack, so less noisy
groups count more. All groups are used, not only the largest one.

<<notation_section>>

<<heading_4>>

Stacking has no `AnalysisPipeline` adapter. The entry point is `stage.stack_frames`:

1. **Frame selection and tests.** One camera only, no mix of spectral and imaging frames, frames
   at a less common gain setting set aside, frames set aside by an earlier run dropped, the
   quarantine (imaging only), and the background test.
2. **Skip unchanged.** `stack_inputs.py` takes a fingerprint of the frames, calibration frames,
   settings and stacking code: a short code that changes whenever any of them changes. A match
   with the last run keeps the stack on disk, unless `force=True`.
3. **Previous-stack staging.** The current stack moves to a staging folder. On success it
   becomes the single `_previous` version (`previous_stack.py`), which a person can swap back or
   discard.
4. **Stack.** `stack_runner.run_stack` splits the frames by exposure length (and by night for
   spectra) and calls the `StackingDriver`. The Siril driver (`siril_stacking_driver.py`) runs
   `ImageProcessing` in `siril_interface.py`: masters, calibration, registration (alignment) and
   stacking. Spectral registration that loses too many frames is retried with relaxed detection,
   then with phase correlation (aligning whole images by their overall pattern rather than by
   single stars).
5. **After the stack.** Noisy edges are trimmed (`stack_crop.py`). Frames never line up exactly,
   so the edges of a stack are covered by fewer frames than the middle and are noisier. The trim
   measures the noise and cuts each side back to where it matches the interior; a stack with
   clean edges is left alone. The quality summary and its gates are built (<<gate_count>>).
6. **Preview image.** A JPEG for people to look at is made from a copy of the stack; the stack
   itself is never changed. GraXpert, an AI tool, removes the sky's brightness gradient. Siril
   stretches the brightness so faint parts show. Cosmic Clarity, another AI tool, removes noise.
   Star toning dims the stars in proportion to their brightness, because the stretch makes
   nearly every star the same white dot. GraXpert and Cosmic Clarity run only when configured,
   and a failed step never fails the stack. The inputs record is written last.

### 4.1 Quality gates

<<gate_intro>>

<<gate_table>>

<<heading_5>>

<<param_intro>>

<<param_table>>

<<heading_6>>

1. **Temperature gap above <<dark_tol>> °C.** The nearest dark is applied with a soft flag;
   `calibration_metadata` fails. A missing `CCD-TEMP` means the dark is not judged.
2. **Bad frames.** Frames with too few stars or non-round stars move to `_excluded`. A batch
   whose share of bad frames is above <<quarantine>> moves nothing and `frame_quarantine` fails.
3. **Registration failure.** Spectral stacks are retried. On an imaging stack, blur shows as $R
   > <<degrade>>$ in `stack_sharpness`.
4. **Over-subtraction.** `zero_pixel_fraction` and `negative_pixels` catch darks or biases that
   remove more than the sky, which pushes pixels to zero or below.
5. **A dropped exposure group.** The combined stack is still written, and
   `exposure_groups_combined` fails.
6. **Restack failure.** The staged stack moves back, so the old stack is kept.

<<quality_section>>"""
    values["quality_section"] = quality_section("StackQualitySummary", "StackingPipelineQualityMetrics")
    values["readme_note"] = readme_note("stacking")
    values["notation_section"] = notation_section("3.5", "stacking")
    return fill(template, {**COMMON_VALUES, **values})


# The adapter class each pipeline's run goes through, for the index table.
PIPELINE_ADAPTERS: dict[str, tuple[str, str, str]] = {
    "astrometry": ("Astrometry", "`AstrometryPipelineAdapter`", "`AstrometryQualitySummary`"),
    "photometry": ("Photometry", "`PhotometryPipelineAdapter`", "`PhotometryQualitySummary`"),
    "spectroscopy": ("Spectroscopy", "`SpectroscopyPipelineAdapter`", "`SpectroscopyQualitySummary`"),
    "asteroid_detection": (
        "Asteroid detection",
        "`AsteroidDetectionPipelineAdapter`",
        "`AsteroidDetectionQualitySummary`",
    ),
    "stacking": ("Image stacking", "None (runs from `stage.stack_frames`)", "`StackQualitySummary`"),
}


def build_index_doc() -> str:
    """Build the parent index documentation.

    Returns
    -------
    content : `str`
        Markdown content for index.md.
    """
    rows = []
    for key, (title, adapter, summary) in PIPELINE_ADAPTERS.items():
        rows.append([
            title,
            f"[{title}]({key}.md)",
            adapter,
            summary,
            gate_count_text(discover_gate_names(key)),
        ])
    overview_table = make_table(
        ["Pipeline", "Review guide", "Adapter class", "Quality summary", "Gates"], rows
    )
    stage_rows = make_table(
        ["Pipeline", "Stage 1 (`process_input`)"],
        [
            ["Astrometry", "No-op: returns an empty `Result`."],
            ["Photometry", "Filters frames, builds sessions; may return `has_work=False`."],
            ["Spectroscopy", "No-op: returns an empty `Result`."],
            ["Asteroid detection", "No-op: returns an empty `Result`."],
        ],
    )
    template = r"""# Pipeline review guides

<<version_line>>

## Abstract

This index is the entry point for scientific review of the Astrometrics processing pipelines. A
pipeline here is a fixed series of processing steps that turns telescope images into a
scientific result. A quality gate is an automatic test on a run that passes, fails, or is not
checked when its data are missing. Each guide covers the header cards a pipeline reads, its
math, its processing flow, its tuned limits, its fallback paths and its quality gates. Header
cards are the named keyword and value entries at the top of a FITS image file, such as `EXPTIME`
for the exposure length. The gate lists and counts are read from the code when the guides are
generated.

<<glossary_line>>

## 1. Pipeline architecture

There are five pipelines. Four of them (astrometry, photometry, spectroscopy and asteroid
detection) are analysis pipelines that share one Python interface, `AnalysisPipeline`
(`pipeline_base.py`). Each has an adapter: a class that wraps the pipeline's own code in that
interface. The function `run_pipeline` drives an adapter by calling four of its methods in
order. Each guide calls these the four stages of a run:

1. **`process_input`.** Decides whether there is work to do.
2. **`run`.** Does the science.
3. **`validate_output`.** Builds the quality summary and records the gates.
4. **`to_result_dict`.** Returns the result the caller expects.

Stage 1 returns a `Result` object. `run` is skipped only when that object says `has_work=False`.
Stage 1 does real work only in photometry:

<<stage_rows>>

The fifth pipeline, stacking, does not use this interface and has no adapter. It runs from
`stage.stack_frames` through `stack_runner.run_stack` to a `StackingDriver`: the interface for a
program that calibrates, aligns and combines frames. The one implementation uses Siril, an
open-source astronomy image processor, through the `ImageProcessing` class in
`siril_interface.py`.

```mermaid
flowchart LR
    A["1. process_input: is there work?"] -- "work to do" --> B["2. run: do the science"]
    A -- "nothing to do" --> C["3. validate_output: build the quality summary"]
    B --> C
    C --> D["4. to_result_dict: return the result"]
```

## 2. Pipeline portfolio overview

In plain words, the five pipelines do this:

- Astrometry finds where an image points on the sky, pixel by pixel, and names its stars.
- Photometry measures how each star's brightness changes over time and picks out the variable
  stars.
- Spectroscopy spreads each star's light into its colors and classifies the star from that
  spectrum.
- Asteroid detection finds objects that move between images and checks them against known
  asteroids and comets.
- Image stacking removes camera flaws from many frames and combines them into one deeper
  image.

Code outside the library calls the pipelines through the package root (see
`Astrometrics_Library_Implementation.md`). The "Adapter class" column names the class each
analysis run goes through inside the library.

<<overview_table>>

A run gate is recorded on the run's quality summary. A period check is recorded on each
period-search result and can lower its verdict. The counts cover only these recorded gates. The
Architecture paper also describes quality tests that are not gates: the spectroscopy extinction
record and sky-contamination share, kept with each spectrum, and the rejection of a poor
wavelength fit when the instrument is calibrated.

## 3. Reviewer checklist

When auditing the pipelines, check:

1. **Input metadata contracts.** Which header cards each pipeline reads and what happens when
   one is missing.
2. **Mathematical formulations.** The CCD equation (the error of a measured brightness) and
   $\text{BJD}_\text{TDB}$ times (times measured at the solar system's center of mass). Stetson
   J (a test for smooth, real brightness changes) and the grating equation (position along a
   spectrum to wavelength). The prediction of the stack's FWHM (full width at half maximum, the
   width of a star's light profile at half its peak), which says how sharp the stars of a stack
   should be.
3. **Degraded states.** Sparse fields, tracking drift (the field sliding across the sensor
   during a run), saturated zero orders (clipped point images beside the spectra) and catalog
   outages.
4. **Status labels.** Which limits were measured on real data and which are design estimates.
   The Status column in Section 5 of every guide labels each limit "<<status_measured>>",
   "<<status_partly>>" or "<<status_design>>", and links the label to the [validation
   register](<<validation_link>>/index.md). The register gives the evidence for each value and
   the library calls to repeat the test on other data.

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
            "stage_rows": stage_rows,
            "overview_table": overview_table,
            "status_measured": STATUS_MEASURED,
            "status_partly": STATUS_PARTLY_MEASURED,
            "status_design": STATUS_DESIGN_ESTIMATE,
            "validation_link": VALIDATION_LINK,
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
