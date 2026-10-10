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

**Checkpoint 0, raw frame.** The pipeline re-measures nothing here. It reuses
what the extraction computed for this star.

| Metric | Unit | Limit | What it tells a reader |
|---|---|---|---|
| `zero_order_saturated_fraction` | fraction | `DEFAULT_SATURATION_FLAG_THRESHOLD` (0.001), lower is better; a value at the limit fails | The share of the zero-order image (the star's undispersed image) at the camera's saturation level. A high value means the star's brightest part is unreliable. |
| `valid_fraction` | fraction | none | The share of the requested spectrum that landed on the image. Below 1, part of the trail ran off the image. |
| `median_trail_width` | pixel | none | The median fitted width of the trail across the dispersion direction. A wide trail means a blurrier spectrum. |
| `contaminated_sky_fraction` | fraction | none | The share of sky readings that had to drop a sky band because a neighbouring star's light fell in it. The note names the dominant sky mode. |

When the caller supplies the frame-level result of `measure_spectral_frame_file`
under `result["frame_check"]`, the checkpoint also carries `streak_tilt`
(degree) and `peak_above_sky` (ADU), and the saturation note names the
saturation level's source. The spectroscopy pipeline does not run that
frame-level check, so a pipeline-built checkpoint 0 normally lacks tilt,
peak-to-sky and saturation source.

**Checkpoint 1, calibrated spectrum.** It carries the five numbers of
`InputQualityAssessment`.

| Metric | Unit | Limit | What it tells a reader |
|---|---|---|---|
| `resolution_element` | angstrom | none | How much the instrument blurred the spectrum. A smaller value separates nearby spectral types better. |
| `resolution_measured` | flag | none | 1 when the blur came from the spectrum's own trail width, 0 when a fixed fallback was used. |
| `zero_order_saturated_fraction` | fraction | `DEFAULT_SATURATION_FLAG_THRESHOLD`, lower is better | The same saturation share as checkpoint 0, kept so the assessment's numbers are all here. |
| `valid_fraction` | fraction | none | The same coverage as checkpoint 0. |
| `signal_to_noise` | per resolution element | `MINIMUM_SPECTRUM_SIGNAL_TO_NOISE` (1.5), higher is better | How strongly the spectrum stands out from its own scatter. Below the limit the pipeline does not classify the spectrum. |
| `median_snr_per_resolution_element` | per resolution element | none | The median, over 4200 to 8000 A, of the brightness summed over one resolution element divided by the error of that sum (from the per-sample errors; the element width comes from the line-spread profile). |
| `fraction_samples_snr_below_5` | fraction | none | The share of samples in that range whose brightness is below 5 times their own error. |
| `snr_estimate_ratio` | ratio | none | The variance-based signal-to-noise divided by `signal_to_noise`. A ratio far from 1 means one of the two is wrong. |

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
| `median_equivalent_width_relative_error` | fraction | none | The median of the equivalent-width error over its size, for features with a detected or possible verdict. |
| `hbeta_equivalent_width_angstrom`, `halpha_equivalent_width_angstrom` | angstrom | none | The equivalent width of each Balmer line (positive for a dip). The note holds its error. |
| `best_template_reduced_chi_square` | reduced chi-square | none | The chi-square per degree of freedom of the best reference by RMS, using the per-sample errors. Near 1 means a fit within the noise. It decides nothing. |

The processing flags are `unclassified`, `emission_line_source` and
`second_order_risk`. The two second-order metrics have no limit because their
2 percent and 10 percent figures are not validated.

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
   XP residuals and wavelength zero-point scatter at post-processing.
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

`catalog_agreement`, `feature_significance` and `spectra_extracted` are new flags: before, a disagreement with the catalog or an uncalibrated p-value showed only on the star's own record, and a run with no spectra was not flagged at all. The counts behind the gates come from `spectrum_facts`, which the batch workers return per frame so the parallel path builds the same gates as the single-image path. Second-order contamination and the emission-line detector have no run-level gate yet: their limits (2% and 10%, and 5σ on M 57) are not validated, which is Gap 2 of the audit plan.

## What the emission-line detector can see

Measured on 228 stored spectra of stars not known to emit (`scripts/validate_emission_line_detector.py`, 2026-10-09): the detector reports a false "detected" line in 1 of them (0.4%) and calls none an emission-line source. It is also insensitive. A box-shaped line added to those spectra is reported as detected only about half the time (44% to 47%) when its height is 1.6 times the spectrum's median brightness, and 2% to 6% of the time at 0.4 to 0.8 times. So a "not seen" from this detector means only that no strong line is there; it is built for a bright nebula such as M 57 and will pass a faint emitter. The thresholds (5 error bars detected, 3 unclear) were set on M 57 only and have not been tuned on any other nebula.
