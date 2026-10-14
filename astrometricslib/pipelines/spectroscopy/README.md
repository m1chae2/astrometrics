# Spectroscopy pipeline

This document describes what happens when the pipeline turns a spectroscopy
image, or a whole observing session of them, into a classified spectrum for
each star. It describes the stages, not the code. Read the modules
themselves for exact methods and thresholds.

## The three stages

1. **[Pre-processing](pre_processing/README.md)** turns the raw image into a
   calibrated spectrum for each star and measures how good the raw data was.
2. **[Processing](processing/README.md)** finds things in that calibrated
   spectrum: what features it contains, what type of star it resembles, and
   what its own colour is.
3. **[Post-processing](post_processing/README.md)** evaluates the result: it
   checks the result against what the pipeline already knew about the star
   and produces an overall trust score for the classification.

Each star's spectrum moves through all three stages in order. The pipeline
stores the result of each stage on the star, so a reviewer can see the
final classification along with the input quality and output trust score
behind it. A quality checkpoint sits before the first stage and after each
stage, so a reviewer can also see where quality was lost (see "Quality
checkpoints" below).

## How this runs in practice

- **One image at a time**: the pipeline matches a single spectral image
  against a normal image of the same field to work out which star is which,
  then extracts every star's spectrum and carries it through all three
  stages. When the target has no normal image of its own, the pipeline names
  only the star at the frame centre: it uses the target's name first, then
  the brightest catalog star near the mount's reported position (see
  `astrometry/processing/README.md`). A planet, the Moon or the Sun gets no
  star name, so its spectrum is not saved to the star catalog.
- **A whole observing session at a time**: the pipeline runs the same three
  stages once per frame in the session, in parallel, using star positions
  it works out from one reference frame in that session.

Either way, the pipeline saves each star's result to the shared star
catalog as soon as its three stages finish, so a crash partway through a
session does not lose the frames already processed.

## Two things that sit outside the three stages

- **Utilities** (`utilities/`) hold standalone tools that are not part of
  processing a target's spectra: deriving a camera's physical calibration
  ahead of time, and checking how well a stack of spectral images aligned.
- The files at this folder's root (`pipeline.py`, `runner.py`, `batch.py`,
  `frame_analysis.py`) drive a star or a session through the three stages
  above. They implement the "how this runs in practice" section, not a
  fourth stage.

## Quality checkpoints

The pipeline measures a spectrum's quality at four checkpoints: one on the
raw frame and one after each of the three stages. Every checkpoint reports
its numbers in the same shape, so a reader can compare them and see at which
point quality was lost.

```text
checkpoint 0 (raw frame) -> pre-processing -> checkpoint 1 (calibrated spectrum)
  -> processing -> checkpoint 2 (processing result)
  -> post-processing -> checkpoint 3 (final result)
```

### The record

A checkpoint is a `StageQualityCheckpoint` (in `models/spectroscopy_quality.py`).
It holds the stage name (`raw_frame`, `pre_processing`, `processing` or
`post_processing`), a list of `StageQualityMetric` records, and a list of
`flags`. A flag is a short label for a condition worth a reader's attention,
such as `zero_order_saturated`.

Each `StageQualityMetric` has these fields:

| Field | Meaning |
|---|---|
| `name` | A short snake_case name, unique within its checkpoint. |
| `value` | The measured number, or `None` when the pipeline could not measure it. A yes/no check is stored as 1.0 (yes) or 0.0 (no). |
| `unit` | What `value` is measured in, such as `fraction`, `angstrom` or `relative RMS`. |
| `limit` | The number the value is judged against, or `None` when the metric has no limit. |
| `passed` | `True` when the value is on the good side of the limit, `False` when it is on the bad side, `None` when there is no limit or no value. A value exactly at the limit passes, except where a metric sets `limit_is_a_pass=False`. |
| `note` | One sentence of context, such as where the limit comes from. |

The pipeline stores the four checkpoints on each star as
`SpectroscopyResult.stage_quality` (`stageQuality` in the saved JSON), in stage
order. The older `input_quality` and `output_quality` records stay as they
are. The user interface reads them, and checkpoints 1 and 3 repeat their
numbers.

### The four checkpoints

| Checkpoint | Stage name | Built by | Built from |
|---|---|---|---|
| 0 | `raw_frame` | `assess_raw_frame_quality` in `pre_processing/assess_raw_frame_quality.py` | The numbers the extraction already measured for the star |
| 1 | `pre_processing` | `input_quality_checkpoint` in `pre_processing/assess_input_quality.py` | The `InputQualityAssessment` |
| 2 | `processing` | `assess_processing_quality` in `processing/assess_processing_quality.py` | The classification, features, colour, emission lines and second-order ratio |
| 3 | `post_processing` | `output_quality_checkpoint` in `post_processing/assess_output_quality.py` | The `OutputQualityAssessment` and the catalog comparison |

`_apply_result_to_stellar_object` in `pipeline.py` calls the four builders and
stores the list. Each builder only gathers numbers that earlier code already
produced. None of them changes a spectrum or a classification.

### Metrics each checkpoint carries today

Limits come from the places that define them: `NO_GOOD_MATCH_RMS`,
`AMBIGUOUS_RMS_GAP` and `DIFFERS_FROM_CATALOG_SUBTYPES` in
`models/stellar_source.py`, `DEFAULT_SATURATION_FLAG_THRESHOLD` in
`pipelines/shared/quality/saturation.py`, and
`MINIMUM_SPECTRUM_SIGNAL_TO_NOISE` in `processing/spectrum_signal.py`. The new
modules import these limits and define none of their own. A metric with no
limit is reported for measurement only.

**Checkpoint 0, raw frame.** The pipeline re-measures nothing from the pixels
here. It reuses what the extraction computed for this star, and adds the
refraction numbers, which come from the target's position and the time.

| Metric | Unit | Limit | What it tells a reader |
|---|---|---|---|
| `zero_order_saturated_fraction` | fraction | `DEFAULT_SATURATION_FLAG_THRESHOLD` (0.001), lower is better; a value at the limit fails | The share of the zero-order image (the star's undispersed image) at the camera's saturation level. A high value means the star's brightest part is unreliable. |
| `valid_fraction` | fraction | none | The share of the requested spectrum that landed on the image. Below 1, part of the trail ran off the image. |
| `median_trail_width` | pixel | none | The median fitted width of the trail across the dispersion direction. A wide trail means a blurrier spectrum. |
| `contaminated_sky_fraction` | fraction | none | The share of sky readings that had to drop a sky band because a neighbouring star's light fell in it. The note names the dominant sky mode. |
| `dar_along_dispersion_angstrom` | angstrom | 20, lower is better. A design choice (half the resolution element at 5000 A), not a measurement | The spread of the wavelength error that atmospheric refraction causes between 4200 A and 8000 A, before the correction. Flag `dar_large` when over the limit. |
| `dar_across_dispersion_px` | pixel | none | How much refraction widens the trail between 4200 A and 8000 A. |
| `target_altitude_degrees` | degree | 20, higher is better. A design choice, not a measurement | The target's altitude at mid-exposure. Below the limit the pipeline refuses the refraction model and sets the flags `target_altitude_low` and `dar_not_computed`. |
| `parallactic_to_dispersion_angle_degrees` | degree | none | The angle from the direction of the zenith to the direction of the dispersion on the sky. 0 means the red end of the spectrum points at the zenith. |

The four refraction metrics have no value, and the checkpoint carries the flag
`dar_not_computed`, when the pipeline has no observatory site, WCS or
mid-exposure time. The refraction correction itself runs in pre-processing
(see "Atmospheric differential refraction" in
[pre-processing](pre_processing/README.md)).

When the caller supplies the frame-level result of `measure_spectral_frame_file`
under `result["frame_check"]`, the checkpoint also carries `streak_tilt`
(degree) and `peak_above_sky` (ADU), and the saturation note names the
saturation level's source. The spectroscopy pipeline does not run that
frame-level check, so a pipeline-built checkpoint 0 normally lacks tilt,
peak-to-sky and saturation source.

**Checkpoint 1, calibrated spectrum.** It carries the five numbers of
`InputQualityAssessment`, the four wavelength zero-point metrics, three
metrics built from the per-sample errors and four line-spread numbers (see
"Measured line spread" below).

| Metric | Unit | Limit | What it tells a reader |
|---|---|---|---|
| `resolution_element` | angstrom | none | How much the instrument blurred the spectrum. A smaller value separates nearby spectral types better. |
| `resolution_measured` | flag | none | 1 when the blur came from the spectrum's own trail width, 0 when a fixed fallback was used. |
| `zero_order_saturated_fraction` | fraction | `DEFAULT_SATURATION_FLAG_THRESHOLD`, lower is better | The same saturation share as checkpoint 0, kept so the assessment's numbers are all here. |
| `valid_fraction` | fraction | none | The same coverage as checkpoint 0. |
| `signal_to_noise` | per resolution element | `MINIMUM_SPECTRUM_SIGNAL_TO_NOISE` (1.5), higher is better | How strongly the spectrum stands out from its own scatter. Below the limit the pipeline does not classify the spectrum. |
| `wavelength_zero_point_offset_angstrom` | angstrom | 20, lower is better (designed) | The size of the wavelength zero-point offset measured from known lines, before correction. The limit is half a resolution element at 5000 A. It is a designed value, not a measured one. |
| `wavelength_zero_point_uncertainty_angstrom` | angstrom | none | The error of that offset. |
| `wavelength_zero_point_line_count` | lines | none | How many of the six lines passed the significance test. |
| `wavelength_zero_point_applied` | flag | none | 1 when the pipeline shifted the wavelengths by the offset, 0 when it only measured it. |
| `median_snr_per_resolution_element` | per resolution element | none | The median, over 4200 to 8000 A, of the brightness summed over one resolution element divided by the error of that sum (from the per-sample errors; the element width comes from the line-spread profile). |
| `fraction_samples_snr_below_5` | fraction | none | The share of samples in that range whose brightness is below 5 times their own error. |
| `snr_estimate_ratio` | ratio | none | The variance-based signal-to-noise divided by `signal_to_noise`. A ratio far from 1 means one of the two is wrong. |
| `trail_fwhm_blue_px` | pixel | none | The median full width at half maximum (FWHM) of the trail across the dispersion from 4200 to 5000 A. |
| `trail_fwhm_red_px` | pixel | none | The same width from 6200 to 7000 A. |
| `chromatic_defocus_ratio` | ratio | 1.3, lower is better; designed, not measured | `trail_fwhm_red_px` divided by `trail_fwhm_blue_px`. Above the limit the red end is out of focus compared with the blue end. A spectrum in focus end to end gives about 0.92. |
| `measured_vs_stored_line_spread_ratio_halpha` | ratio | none (reported only) | The spectrum's own measured FWHM at 6563 A divided by the stored line-spread profile's value there (148 A). A value far from 1 means the trail width and the stored profile disagree about the blur. |

The checkpoint 1 flags are `resolution_assumed`, `low_signal_to_noise`,
`zero_order_saturated`, `gain_assumed` and `read_noise_assumed` (the
per-sample errors used an assumed camera gain or read noise),
`chromatic_defocus` (`chromatic_defocus_ratio` above its limit),
`wavelength_zero_point_unconstrained` (fewer than two
lines found), `wavelength_zero_point_lines_disagree` (two or more lines that
fail the chi-square test) and `wavelength_zero_point_large` (offset over its
limit). The pipeline applies the offset before the quantum-efficiency,
response and extinction corrections; see "Wavelength zero point" in the
[pre-processing README](pre_processing/README.md).

**Checkpoint 2, processing result.** It judges the processing results on their
own, before the catalog comparison. For an unclassified spectrum the
classification and colour metrics have no value, and the checkpoint carries the
`unclassified` flag.

| Metric | Unit | Limit | What it tells a reader |
|---|---|---|---|
| `classification_rms` | relative RMS | `NO_GOOD_MATCH_RMS` (0.15), lower is better | How far the best reference spectrum is from this spectrum. |
| `rms_gap_to_second_best` | relative RMS | none (reported only) | How much closer the best reference is than the runner-up. Below `AMBIGUOUS_RMS_GAP` (0.02) the subtype is uncertain, which is common at this resolution, so it fails nothing. |
| `rms_gap_to_next_class` | relative RMS | `AMBIGUOUS_RMS_GAP`, higher is better | The same gap to the best reference of another class letter. A small gap means the class is uncertain. |
| `synthetic_colour_measured` | flag | 1, higher is better | 1 when the pipeline measured a B-V colour from the spectrum. |
| `synthetic_b_minus_v` | magnitude | none | The B-V colour measured from the spectrum. |
| `significant_feature_count` | features | none | How many named features have a detected or possible verdict. |
| `best_feature_p_value` | probability | none | The smallest feature p-value (the chance that noise like this spectrum's gives a feature this strong). Lower is stronger. |
| `uncalibrated_feature_count` | features | 0, lower is better | How many p-values assumed Gaussian noise because too few control windows were available. |
| `second_order_risky_fraction` | fraction | none | The share of samples where second-order light could add a tenth of the signal. |
| `second_order_max_blue_to_red_ratio` | ratio | none | The largest ratio of the brightness at half a wavelength to the brightness at that wavelength. |
| `emission_lines_detected` | lines | none | How many named emission lines or blends have a detected verdict. The `emission_line_source` flag marks two or more. |
| `ebv_used` | magnitude | none (reported only) | The colour excess E(B-V) the pipeline removed from the spectrum before classifying. The note names the catalog and the conversion. No value when the star has no Gaia DR3 id or Gaia gives none. |
| `dereddening_type_shift_steps` | subtype steps | none (reported only) | How many steps on the O-to-M ladder the best type moved because of dereddening (ten steps to a letter class). A large shift means the observed colour was mostly dust. No value without an E(B-V). |
| `index_vs_template_type_steps` | subtype steps | `DIFFERS_FROM_CATALOG_SUBTYPES` (20), lower is better | The distance between the type that six line strengths point to and the template-fit type. A designed limit, the one used for two types that disagree. |
| `median_equivalent_width_relative_error` | fraction | none | The median of the equivalent-width error over its size, for features with a detected or possible verdict. |
| `hbeta_equivalent_width_angstrom`, `halpha_equivalent_width_angstrom` | angstrom | none | The equivalent width of each Balmer line (positive for a dip). The note holds its error. |
| `best_template_reduced_chi_square` | reduced chi-square | none | The chi-square per degree of freedom of the best reference by RMS, using the per-sample errors. Near 1 means a fit within the noise. It decides nothing. |

The processing flags are `unclassified`, `emission_line_source`,
`second_order_risk` and `slope_and_lines_disagree`. The last one marks a
spectrum whose `index_vs_template_type_steps` is above its limit: the
continuum slope and the line strengths point to different types. The two
second-order metrics have no limit because their 2 percent and 10 percent
figures are not validated.

**Checkpoint 3, final result.** It carries the verdicts of
`OutputQualityAssessment` and the catalog distance. Each verdict is 1 (yes) or
0 (no). For an unclassified spectrum every value is `None` and the checkpoint
carries the `unclassified` flag.

| Metric | Unit | Limit | What it tells a reader |
|---|---|---|---|
| `catalog_type_steps_apart` | subtype steps | `DIFFERS_FROM_CATALOG_SUBTYPES` (20), lower is better | The distance on the O-to-M ladder between the measured and the catalog type (B0 is 10, A5 is 25). |
| `poor_match` | flag | 0, lower is better | 1 when `classification_rms` is above `NO_GOOD_MATCH_RMS`. |
| `subtype_ambiguous` | flag | 0, lower is better | 1 when the gap to the second best is below `AMBIGUOUS_RMS_GAP`. Common, because neighbouring subtypes often fit about equally well. |
| `class_ambiguous` | flag | 0, lower is better | 1 when the gap to the next class is below `AMBIGUOUS_RMS_GAP`. |
| `catalog_agrees` | flag | 1, higher is better | 1 when the measured type agrees with the catalog type. |
| `colour_agrees` | flag | 1, higher is better | 1 when the spectrum's B-V colour agrees with the catalog colour. |
| `trustworthy` | flag | 1, higher is better | The overall verdict of `OutputQualityAssessment`. |
| `gaia_xp_residual_rms_fraction` | fraction | 0.05, lower is better (designed) | The RMS of (observed / Gaia XP - 1) over 4200-8000 Å. See "Check against Gaia XP". Empty when the spectrum was not compared. |
| `gaia_xp_slope_percent_per_1000_angstrom` | percent per 1000 Å | 3, lower is better (designed) | The absolute tilt of observed / Gaia XP. A tilt points at the instrument response or the airmass correction. |
| `gaia_xp_wavelength_shift_angstrom` | Å | 11, lower is better (designed) | The absolute shift of the spectrum's features against Gaia XP's. 11 Å is one pixel of dispersion. A shift points at the wavelength scale. |
| `gaia_xp_available` | flag | none (reported only) | 1 when the spectrum was compared with Gaia XP, 0 when not. The note gives the reason. |

The Gaia XP metrics are filled for classified and unclassified spectra alike,
because they test the calibration, not the classification. When a Gaia XP metric
fails, the checkpoint carries the `gaia_xp_disagrees` flag.

### Check against Gaia XP

The Gaia satellite measured a low-resolution spectrum, the XP spectrum, for about
220 million stars. Its resolving power (wavelength divided by the smallest
resolvable wavelength difference) is about 30 to 100, close to the Star
Analyser's. It comes from a different instrument above the atmosphere. The
pipeline compares each calibrated spectrum with the XP spectrum of the same star.
The comparison tests the instrument response, the airmass correction and the
wavelength scale together. The code is in
`post_processing/compare_to_gaia_xp.py`.

1. **Find the Gaia DR3 source id.** The pipeline reads `StellarObject.gaia_dr3_source_id`
   through `catalog_star_identity.gaia_dr3_source_id_of`, the same function the
   reddening lookup uses.
   The star identifier fills it from the `Gaia DR3 <number>` name in SIMBAD's list
   of identifiers, or from a star that was named from Gaia. If the star has none
   (the brightest stars are not in Gaia DR3), the comparison is `not_checked`
   with that reason.
2. **Fetch the XP spectrum** through the `GaiaXpDriver` (`drivers/gaia_xp_driver.py`).
   The driver caches each spectrum on disk. A source with no XP spectrum, or a
   failed download, gives `not_checked` and never stops the pipeline.
3. **Put both on the same quantity.** The response-corrected spectrum is the
   counts divided by the response, which is Vega observed over the Pickles A0V
   template. Pickles fluxes are energy per unit wavelength, so the corrected
   spectrum is a relative F-lambda above the atmosphere. Gaia's sampled flux is
   also F-lambda, in W m⁻² nm⁻¹. The two differ by one constant, which the
   normalization removes. A difference between Vega and the A0V template stays
   inside every corrected spectrum, so it appears as an offset common to all stars.
4. **Match the resolutions.** XP's blur is a Gaussian of 95 Å full width at half
   maximum (FWHM) at 4340 Å, 130 Å at 4861 Å, 70 Å at 6563 Å and 185 Å at 8800 Å,
   with straight lines between. A fit of the Pickles A0V template to the XP
   spectra of 12 A0V stars (G 6.0 to 6.6) gave these medians on 2026-10-10. The
   spread between stars was 20 to 40 percent. The instrument's stored line
   spread is sharper than XP's below about 5000 Å and broader above it. Where
   the instrument is broader, the pipeline blurs XP to it. Where XP is broader,
   the pipeline blurs the observed spectrum to XP. The blur width is the
   quadrature difference, `sqrt(broad² - narrow²)`.
5. **Compare.** The pipeline keeps 4200 to 8000 Å outside the atmospheric bands,
   divides each spectrum by its median over 5400 to 5600 Å, and computes
   - the RMS of observed / XP - 1;
   - the slope of a straight line fitted to observed / XP, in percent of the flux
     at 5500 Å per 1000 Å. The fit weights samples by XP's errors, the spectrum's
     own errors when `response_corrected_intensity_errors` exists, and a 1 percent
     floor;
   - the median ratio and the RMS in four bands: 4200-5000, 5000-6000, 6000-7000
     and 7000-8000 Å;
   - the wavelength shift that best correlates the two spectra's logarithmic
     derivatives (d ln F / dλ). The sign is positive when the observed features sit
     at longer wavelengths than XP's. The search covers ±60 Å. A correlation below
     0.3 gives no shift.

The result is stored on `SpectroscopyResult.gaia_xp_comparison`
(`GaiaXpComparison`, in `models/gaia_xp_comparison.py`), together with the
normalization levels and whether either spectrum was blurred.

All three limits are designed, not measured: 0.05 is above XP's own 1 to 2
percent calibration error, 3 percent per 1000 Å is an 11 percent change across the
compared range, and 11 Å is one pixel. Synthetic tests recover a 5 percent per
1000 Å tilt to within 0.2 and a 20 Å shift to within 1 Å. Against real data, a
shift measured between a real XP spectrum and a template of a different star ranged
from -23 to +36 Å over 12 A0V stars (median +11 Å). That number measures
differences between stars, not the instrument. The shift's noise floor for a real
spectrum of the same star is not known yet.
`scripts/compare_spectra_with_gaia_xp.py` measures it on a processed target.

At the run level, the summary's `gaiaXpSummary` (`GaiaXpRunSummary`) gives, for
each of the four bands, the median over the compared stars of observed / XP and
its scatter (1.4826 times the median absolute deviation). A star with several
spectra counts once. That median ratio is the measured residual instrument
response: dividing the corrected spectra by it makes them agree with Gaia.

### Measured line spread

A slitless spectrum is blurred, and the blur can differ from one end of the
spectrum to the other. The pipeline measures that blur for each spectrum from
its own trail width, and reports it. The code is in
`pre_processing/measured_line_spread.py`.

What the pipeline does:

1. It takes the width (sigma) of the trail across the dispersion at every step
   along the spectrum. The extractor already measures it. A failed fit counts
   as no measurement.
2. It groups the steps into bands 400 A wide from 4200 to 8000 A. The last band
   stops at 8000 A, so it is 200 A wide. A band needs at least 10 working fits.
3. In each band, it takes the median sigma, multiplies it by 2.355 to get a FWHM
   in pixels, and multiplies that by the band's median local dispersion (the
   Angstroms one pixel covers, from the wavelength calibration) to get a FWHM
   in Angstroms.
4. It records the band's scatter: 1.4826 times the median absolute deviation of
   the per-step FWHM values. A few bad fits cannot inflate it.

The result is stored as `SpectroscopyResult.measured_line_spread`
(`measuredLineSpread` in the saved JSON), a `MeasuredLineSpread` record with
these lists, one entry per band:

| Field | Unit | Meaning |
|---|---|---|
| `wavelength_angstrom` | A | The centre of the band. |
| `fwhm_angstrom` | A | The blur in the band. |
| `fwhm_px` | pixel | The same blur in pixels. |
| `scatter_px` | pixel | The band's robust scatter. A large value means a less certain median. |
| `sample_count` | steps | How many working fits the median uses. |

The field is `None` when the trail width is missing (the fixed-box extraction
does not fit one) or fewer than two bands have enough fits.

The method assumes that a star's image is round, so the width across the
dispersion equals the blur along it. Tracking errors, coma (a comet-shaped
blur toward the edge of the field) and a bright neighbour all break this. The
stored line-spread profile (`data/line_spread_*.json`) was fitted to line
depths on other stars, so it may include scattered light that the trail width
does not see. `measured_vs_stored_line_spread_ratio_halpha` shows how far the
two disagree on one spectrum.

Why the pipeline compares the red end with the blue end: a grating in a
converging beam does not put the first-order spectrum in a flat plane. If the
camera is focused on the zero order, the red end can be out of focus. That
widens the red lines and limits how well the classifier can separate nearby
spectral types there. The limit of 1.3 on `chromatic_defocus_ratio` is a
designed value. A spectrum that is in focus changes width with wavelength only
through seeing (the blur from the air), roughly as wavelength to the power
-0.2. From 4600 A to 6600 A that gives a red-to-blue ratio of about 0.92. The
limit sits well above that, and no real focus sweep has set it. The focus-sweep
script (below) is the tool to check it.

**The option to blur with the measured profile.** The classifier blurs its
reference spectra to the instrument's resolution before it compares them with
a spectrum. By default it uses the stored profile. Build the pipeline with
`SpectroscopyPipeline(config, use_measured_line_spread=True)` to blur with this
spectrum's own measured profile instead. The option is off by default. It
changes only the profile that the classification and feature tests use, and
only for a spectrum that has a measured profile. It does not change the
instrument response, which was fitted with the stored profile, so switching it
on makes the two blurs disagree. Do not switch it on until a real-data check
has compared the classifications with and without it on spectra of stars of
known type.

Two scripts support this work (see the [scripts README](../../scripts/README.md)):

- `scripts/spectral_focus_sweep.py` reads spectral frames of one star taken at
  several focuser positions and reports the best focus for each wavelength band
  and the position that suits mid-spectrum.
- `scripts/compare_instrument_responses.py` compares two instrument-response
  files. Use it to check that two standard stars from the same night give the
  same response.

### Adding a metric

Adding a metric to a checkpoint takes one `metric(...)` call in that
checkpoint's builder. `metric` is in `models/spectroscopy_quality.py`:

```python
metric(name, value, unit, limit=None, higher_is_better=True, note="", *, limit_is_a_pass=True)
```

`metric` casts the value and limit to plain `float`, turns NaN and infinity
into `None`, and sets `passed` from the limit and the direction. For example,
a per-pixel signal-to-noise floor at the pre-processing checkpoint is one line
in the list that `input_quality_checkpoint` builds:

```python
metric("minimum_pixel_signal_to_noise", value, "per pixel", limit=3.0, note="source of the limit")
```

Use these rules:

1. Add the metric in the builder of the checkpoint that owns the stage. Metrics
   suited to each checkpoint are: atmospheric dispersion at the raw frame;
   per-pixel signal-to-noise and line width against wavelength at
   pre-processing; equivalent-width errors and reddening at processing; Gaia
   XP residuals at post-processing. The scatter of the wavelength zero
   points across a run is in the run-level summary below.
2. Take a limit from the module that defines it. If the limit does not exist
   yet, define it once, in `models/stellar_source.py` when it is a
   classification limit, and leave `limit` out until a validated value exists.
3. Keep the name unique within the checkpoint. The run summary keys its medians
   by name.
4. Pass `higher_is_better=False` for a quantity where smaller is better.
   Pass `limit_is_a_pass=False` when reaching the limit already counts as a
   failure.

A new metric needs no change to the models, the run summary or the generated
TypeScript types. The roll-up and the generated types read the list as it is.

### The run-level summary

Both `runner.py` (one image) and `batch.py` (a session) roll the per-spectrum
checkpoints up into `stage_quality_summary` on the summary's
`spectroscopy_metrics` (`SpectroscopyPipelineQualityMetrics`).
`summarize_stage_quality` in `post_processing/run_gates.py` builds it. It holds
one `StageQualityRollup` per stage, in stage order:

- `spectrum_count`: how many spectra have a checkpoint for the stage.
- `failed_spectrum_count`: how many of them have at least one metric with
  `passed` equal to `False`. A spectrum counts once per stage.
- `metric_medians`: the median of each metric across the spectra that measured
  it. A metric that no spectrum measured is left out.

The batch workers return each spectrum's checkpoints as plain dictionaries
(`stage_quality_rows`), because a worker process cannot return whole star
objects. The summary is `None` for a run with no spectra.

`wavelength_scale_summary` on the same metrics (`WavelengthScaleSummary`,
built by `summarize_wavelength_scale` in `post_processing/wavelength_scale.py`
from the pre-processing checkpoints) holds the scatter of the wavelength zero
points across the run. It counts only spectra with a measured offset:

- `spectrum_count` and `applied_count`: how many spectra had an offset, and how
  many of those had it removed.
- `rms_before_correction_angstrom`: the root mean square (RMS) of the offsets
  before any correction.
- `rms_after_correction_angstrom`: the RMS of what is left. A spectrum with the
  offset removed counts as the offset's uncertainty, because a correction
  cannot be more exact than its measurement. A spectrum with the offset only
  measured counts as the whole offset.
- `offset_saturation_correlation`: the Pearson correlation between a
  spectrum's absolute offset and its zero-order saturated fraction, with
  `correlation_spectrum_count` spectra behind it. A value near 1 means the
  saturated zero orders cause the offsets. It is `None` with fewer than three
  spectra, or when either quantity does not vary. The correlation uses the
  absolute offset because saturation can pull the centre either way.

The `wavelength_scale` gate fails when `rms_after_correction_angstrom` is over
`WAVELENGTH_SCALE_LIMIT_ANGSTROM` (11 A, one pixel of dispersion). The limit is
a designed value, not a measured one. The gate is `not_checked` when fewer than
three spectra have an offset.

The `processing_quality` gate reads the processing checkpoint. It fails when
more than `PROCESSING_QUALITY_MAXIMUM_FAILED_FRACTION` of the classified spectra
fail a processing-checkpoint metric. That limit is 0.5 (50 percent). The share
is a designed value, not a measured one, and no real run has validated it. The
gate is `not_checked` when no classified spectrum has a processing checkpoint,
which includes a run with no spectra. A tie between neighbouring subtypes does not
count: like the `spectral_classification` gate, this gate fails on ambiguity
between spectral classes only.

## Calibration tuning

`utilities/calibration_tuner.py` fits the one unknown in the instrument
model, the distance from the grating to the sensor. It reads a frame of a
star with known absorption lines (Vega, for example) and works in these
steps:

1. Extract the star's spectrum with the normal pipeline and smooth it.
2. Find the dark valleys (absorption lines) in the smoothed spectrum:
   1. Divide the spectrum by a running median 101 samples wide (its
      continuum, the slowly changing brightness under the lines). A valley's
      depth is then a fraction of the continuum, so the same thresholds work
      for a spectrum of 3000 ADU (the camera's counts) or 3 ADU.
   2. Keep a valley only if its depth is at least 1 percent of the continuum
      (the search lowers this to 0.1 percent when fewer than three valleys
      pass) and at least 5 times the local noise. The noise is the median
      absolute deviation of what is left after the continuum is removed,
      scaled to a standard deviation.
   3. Keep the 12 deepest valleys. This caps the search in step 4, which
      tries every group of three: 12 valleys give 220 groups, and a noisy
      spectrum with 60 valleys gave about 34,000.
   4. Refine each valley's position to a fraction of a sample. A parabola
      through the lowest sample and its two neighbours gives the position of
      its lowest point, which is within 0.5 sample of the sample itself and
      within 0.1 sample of the true centre for a smooth valley.
3. Look up where each valley sits. The tuner reads the position from
   `sample_distances_px`, the distance from the zero-order star, in pixels,
   that the pipeline recorded for each kept sample. It does not count
   samples from a fixed start, because the pipeline drops leading samples
   that are off the image or shorter than the camera's shortest wavelength.
   Counting from a fixed start would shift every valley by the number of
   dropped samples and bias the fitted distance. A refined position between
   two samples gets a distance between their two recorded distances.
4. Try every group of three valleys against the Balmer lines H-delta,
   H-gamma and H-beta (hydrogen lines at 410.17, 434.05 and 486.13 nm). For
   each group, solve for the grating distance that makes the grating
   equation, `wavelength = d * sin(arctan(x / L))`, match the three
   wavelengths. `d` is the spacing between grating lines, `x` is the
   position on the sensor, and `L` is the grating distance.
5. Keep the group with the smallest RMS error (the root of the mean squared
   wavelength error, in nanometers). The tuner raises an error when the best
   RMS error is above 10 nm. The report lists each line's pixel offset,
   calibrated wavelength and deviation in nanometers.
6. Save the fitted distance and the start of the spectrum to the camera's
   configuration, together with the flare-mask and extraction-length checks.

`utilities/test/test_calibration_tuner_sample_distances.py` builds frames
with the Balmer lines at known positions and checks that the tuner recovers
those positions to 0.5 pixel and the grating distance to 1 percent, with and
without leading samples dropped. The same file checks that a noisy frame
(3000 ADU continuum, Poisson noise) gives at most 12 candidates including the
three true lines and finishes the search in under 10 seconds, and that the
refined positions bring the fitted distance within 0.02 percent of the truth
on both extraction paths, with zero-order positions that fall between pixels.
It also checks that each flare-mask sample distance lands within 0.01 pixel of
the true distance from the zero order. For exact behavior, read the code.

## The gate record

Besides each star's own quality records, a run keeps one record per run-level check in the summary's `gates` (built in `post_processing/run_gates.py`, by both `runner.py` and `batch.py`). Each gate is `passed`, `failed` or `not_checked`; a check that could not look is never recorded as passed. The tests in `test/post_processing/test_run_gates.py` give every gate input that must fail it.

| Gate | Fails when | Not checked when |
|---|---|---|
| `spectra_extracted` | No star produced a spectrum | Never |
| `zero_order_saturation` | 0.1% or more of a zero-order image is saturated | No zero-order saturation was measured |
| `spectral_classification` | A star's best match is above `NO_GOOD_MATCH_RMS` (0.15 relative RMS) or its gap to the best reference of another spectral class is below `AMBIGUOUS_RMS_GAP` (0.02 relative RMS). Ties between neighbouring subtypes do not fail it; the detail reports "N of M classifications ambiguous at subtype level" | No star was given a type |
| `catalog_agreement` | A measured type is more than `DIFFERS_FROM_CATALOG_SUBTYPES` (20 subtype steps) from the catalog type | No star had a catalog type to compare with |
| `feature_significance` | A star's feature p-values fell back to assuming Gaussian noise | No star had its features tested |
| `resolution_measured` | Never | The resolution was assumed from the instrument design for every spectrum |
| `processing_quality` | More than 50% of classified spectra fail a metric of the processing checkpoint (see "Quality checkpoints") | No classified spectrum has a processing checkpoint, including a run with no spectra |
| `gaia_xp_agreement` | The median absolute slope of observed / Gaia XP over the compared stars is above 3 percent per 1000 Å (designed limit) | Fewer than three stars were compared with Gaia XP |
| `wavelength_scale` | The RMS of the per-spectrum wavelength offsets left after correction is over 11 A (one pixel of dispersion; designed, not measured) | Fewer than three spectra have a measured offset |

`catalog_agreement`, `feature_significance` and `spectra_extracted` are new flags: before, a disagreement with the catalog or an uncalibrated p-value showed only on the star's own record, and a run with no spectra was not flagged at all. The counts behind the gates come from `spectrum_facts`, which the batch workers return per frame so the parallel path builds the same gates as the single-image path. Second-order contamination and the emission-line detector have no run-level gate yet: their limits (2% and 10%, and 5σ on M 57) are not validated, which is Gap 2 of the audit plan.

## What the emission-line detector can see

Measured on 228 stored spectra of stars not known to emit (`scripts/validate_emission_line_detector.py`, 2026-10-09): the detector reports a false "detected" line in 1 of them (0.4%) and calls none an emission-line source. It is also insensitive. A box-shaped line added to those spectra is reported as detected only about half the time (44% to 47%) when its height is 1.6 times the spectrum's median brightness, and 2% to 6% of the time at 0.4 to 0.8 times. So a "not seen" from this detector means only that no strong line is there; it is built for a bright nebula such as M 57 and will pass a faint emitter. The thresholds (5 error bars detected, 3 unclear) were set on M 57 only and have not been tuned on any other nebula.
