# Spectroscopy pipeline

This document describes what happens when the pipeline turns a spectroscopy
image, or a whole observing session of them, into a classified spectrum for
each star. It describes the stages, not the code. Read the modules
themselves for exact methods and thresholds.

Terms are defined in the
[glossary](../../../documentation/library_design/Astrometrics_Library_Architecture.md#appendix-b-glossary).
This README also defines each term the first time it appears.

## In plain words

- **Slitless spectroscopy.** A spectrum is a star's brightness at each
  wavelength (colour) of light. Here a transmission grating (a clear plate
  ruled with fine parallel lines, which bends light by an angle that depends
  on its wavelength) sits in front of the camera with no slit. Every star in
  the field is spread into its own short rainbow at once, with nothing to
  block the sky or neighbouring stars.
- **The spectrum trail.** Each star's rainbow appears on the image as a
  short band of light, the spectrum trail (in this README, "the trail").
  Distance along the trail maps to wavelength; the direction along it is the
  dispersion direction.
- **The zero order.** The grating also lets part of the star's light through
  unbent. That light forms an ordinary point-like image of the star, the zero
  order, at one end of the trail. The pipeline measures every wavelength as a
  distance from the zero order, so the zero order is the anchor of the
  wavelength scale.
- **What the pipeline produces.** For each star: a calibrated spectrum
  (brightness against wavelength, with an error on every sample), a spectral
  type, the absorption and emission features it found, the star's B-V colour,
  and quality records at every stage. For each run: gates, yes-or-no checks
  on the run as a whole. B-V is the star's magnitude through a blue (B)
  filter minus its magnitude through a visual, yellow-green (V) filter.
  Magnitudes are a logarithmic brightness scale on which a larger number is
  fainter, so a larger B-V means a redder star.
- **Spectral types.** Stars are sorted by temperature on the O-B-A-F-G-K-M
  ladder, hottest (O) to coolest (M), with each letter split into ten
  subtypes 0 to 9 (G0 is hotter than G9). "Early" types are the hot end of
  the ladder and "late" types the cool end. A luminosity class follows the
  type and says how large and bright the star is for its temperature: V for
  a dwarf (a main-sequence star: an ordinary star in the long, stable middle
  of its life, like the Sun), III for a giant, I for a supergiant.
- **Why it is hard.** The blur is large: one resolution element (the smallest
  wavelength difference the instrument can separate) is about 40 to 150 A
  (Angstroms; 1 A is 0.1 nm), so neighbouring subtypes look alike. The
  zero-order anchor can be saturated (its pixels at the camera's maximum
  value) or pulled by a neighbour. The air dims and bends blue light more than
  red. The camera and grating add their own colour tilt. Dust between us and
  the star reddens it, which looks like a cooler type. A neighbour's trail can
  leak into the star's own.

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

## How a spectrum is made

This section follows one star from the image to its classified spectrum and
defines the terms that the quality checkpoints use. Each stage README gives
the detail.

1. **Find the star.** The pipeline names the stars in the spectral image from
   a normal (non-spectral) image of the same field (see "How this runs in
   practice"). Each star's zero order marks where its trail begins.
2. **Extract the trail.** At each one-pixel step along the trail, the
   pipeline adds up the light in a short box across the trail (the
   extraction box) and subtracts the sky, measured in strips on both sides of
   the box. The result is a list of brightness samples, each at a known
   distance in pixels from the zero order. Two settings choose the
   extraction path:
   - *Where the box runs.*
     - **Dispersion-line extraction** (the default,
       `use_flare_mask_extraction = false`): the box starts at the distance
       from the star that the instrument model predicts and runs in a
       straight line along the predicted dispersion direction.
     - **Flare-mask extraction** (`use_flare_mask_extraction = true`): the
       pipeline first re-centres the zero order in a 21 by 21 pixel box. It
       then starts the box a set distance past it (`dispersion_start_px`, 120
       pixels by default), so the bright flare around a bright star's zero
       order (its glare and astigmatism, a blur from imperfect optics) stays
       out of the box, and follows the trail's measured tilt column by
       column. `utilities/calibration_tuner.py` turns this on for a camera
       when the star's own flare saturates the pixels where the spectrum
       should start.
   - *How the box follows the trail.*
     - **Traced extraction** (the default, `extraction_method = "traced"`):
       at each step the pipeline fits a Gaussian (a bell curve) across the
       trail, fits a smooth curve (a degree-2 polynomial) through the fitted
       centres, and sizes the box from the fitted widths. It records the
       trail's width at each step.
     - **Fixed-box extraction** (`extraction_method = "fixed"`, also called
       untraced): a straight box of fixed half-width (`extraction_radius`, 10
       pixels by default). It fits no width.

   The two settings combine, so there are four paths, such as the traced
   flare-mask extraction.
3. **Turn distance into wavelength.** The grating equation,
   `wavelength = d sin(arctan(x / L))`, gives the wavelength of a sample at
   distance `x` from the zero order (`d` is the spacing of the grating's
   lines and `L` the distance from the grating to the sensor, fitted once
   per camera; see "Calibration tuning"). The scale is anchored at the zero
   order, so an error in the zero order's measured position shifts every
   wavelength of that spectrum by the same amount. That shift is the
   wavelength zero point (a constant wavelength offset; not the magnitude
   zero point of photometry). Pre-processing first corrects the shift that
   atmospheric refraction causes, then measures the wavelength zero point
   from known lines and removes it when the lines agree.
4. **Remove what is not the star.** The pipeline divides the spectrum by the
   sensor's quantum efficiency (the fraction of arriving light it records at
   each wavelength) and by the instrument response (the smooth colour tilt
   that the grating, telescope and camera add together, measured once on a
   standard star, a star of known spectrum). It then corrects for the
   difference in the air's dimming between this frame and the standard
   star's frame (atmospheric extinction).
5. **Know the blur.** The instrument blurs every spectrum. The line spread is
   that blur at each wavelength, given as a FWHM (full width at half
   maximum: the width of a blurred profile where it drops to half its peak)
   in Angstroms. One FWHM is one resolution element. The pipeline has two
   measures of it:
   - *From the trail width.* The width of the trail across the dispersion,
     in pixels, times the Angstroms per pixel. On Vega's stack this gives
     about 35 to 43 A across the middle of the spectrum, about 40 A at
     5000 A. A spectrum with no trail width (the fixed-box extraction) uses
     a fixed 45 A.
   - *The stored line-spread profile* (`data/line_spread_*.json`). It was
     fitted to the depths of real absorption lines on three stars: 42 A at
     4200 A, about 100 to 110 A from 4861 to 5175 A, and 148 A at H-alpha
     (6563 A). It is wider than the trail width above about 4400 A. A fit to
     line depths may include scattered light that the trail width does not
     see. The classifier, the wavelength zero-point fit, the equivalent
     widths and the Gaia XP comparison use this stored profile.

   Two design limits are set at "half a resolution element at 5000 A",
   20 A: the wavelength zero-point limit and the refraction limit. Both use
   the trail-width figure of about 40 A, not the stored profile's 105 A,
   so they are stricter than half of the stored profile.
6. **Watch for second-order light.** The grating also sends a fainter copy
   of the spectrum, twice as spread out, further along the sensor (the
   second order). Its blue light lands on top of the first-order red light
   at twice its wavelength. The pipeline does not remove it; it flags where
   it could matter.
7. **Read the spectrum (processing).** A few terms:
   - The *continuum* is the smooth brightness the star would have without
     its lines. Line depths are measured against it.
   - The *equivalent width* of a line is the width, in Angstroms, of a fully
     dark band that would remove the same light as the line. Unlike the
     depth, it does not depend on the blur.
   - The *Balmer lines* are the visible lines of hydrogen (H-alpha at
     6563 A, H-beta at 4861 A, H-gamma at 4340 A, and so on).
   - *Control windows*: to judge whether a dip at a named feature is real,
     the feature detector runs the same dip test at many other positions
     along the spectrum. The spread of those results shows how strong a
     "feature" this spectrum's own noise produces, and gives the p-value
     (the chance that noise alone gives a result at least this strong).
     With fewer than 20 usable positions, it assumes Gaussian noise
     instead and the p-value counts as uncalibrated.
   - The classifier compares the whole spectrum with reference spectra
     (templates) of known type. Its score is the *relative RMS*: the
     root-mean-square (typical) difference between the spectrum and a
     template, as a fraction of the spectrum's average brightness. 0 is a
     perfect match.
   - *E(B-V)* (colour excess) is how much redder dust has made the star, in
     magnitudes of B-V. When a catalog gives it, dereddening removes that
     effect before the classifier compares the spectrum.
8. **Judge the result (post-processing).** The pipeline compares the type
   and colour with star catalogs, combines its checks into a trustworthy
   verdict, and compares the calibrated spectrum with an independent
   spectrum of the same star from the Gaia satellite.

## How this runs in practice

- **One image at a time**: the pipeline works out which star is which in a
  single spectral image, then extracts every star's spectrum and carries it
  through all three stages. It names the stars from the plate solution (the
  fitted map from pixel position to sky position) of the target's solved
  normal (non-spectral) stack (an average of many aligned frames) first
  (`identify_spectral_stars_via_solution` in
  `pre_processing/spectral_star_registration.py`). When there is no solved
  stack, or the solution matches no star, it falls back to lining up the
  star positions of the two images by their geometry
  (`identify_spectral_stars_via_registration`). When the target has no
  normal image of its own, the pipeline names
  only the star at the frame centre: it uses the target's name first, then
  the brightest catalog star near the mount's reported position (see
  `astrometry/processing/README.md`). A planet, the Moon or the Sun gets no
  star name, so its spectrum is not saved to the star catalog.
- **A whole observing session at a time**: the pipeline runs the same three
  stages once per frame in the session, in parallel, using star positions
  it works out from one reference frame in that session.

Either way, the pipeline saves the results to the shared star catalog once
per frame, after every star in that frame has finished its three stages.
`record_and_flag_spectroscopy_stars.py` does this save for both paths: it
reconciles the frame's stars with the catalog, saves them, and lists the
classifications that need a reviewer's attention. A crash partway through a
session does not lose the frames already saved.

## Two things that sit outside the three stages

- **Utilities** (`utilities/`) hold tools that are not part of
  processing a target's spectra:
  - `calibration_tuner.py` derives a camera's physical calibration ahead of
    time (see "Calibration tuning" below).
  - `registration_quality.py` checks how well a stack of spectral images
    aligned. It follows each frame's zero-order star (the star's undispersed
    image) and flags frames where that star jumps or fades. The stacking
    stage calls it after it stacks spectral frames
    (`_check_spectral_registration_quality` in `pipelines/stacking/stage.py`),
    and `api/processing.py` exposes its outlier test and thresholds.
- The files at this folder's root drive a star or a session through the three
  stages above. They implement the "how this runs in practice" section, not a
  fourth stage:
  - `pipeline.py` holds `SpectroscopyPipeline`, which extracts and analyzes
    every star in one image.
  - `runner.py` runs one image: it identifies the stars, runs the pipeline and
    builds the run summary.
  - `batch.py` runs a whole session in parallel, one worker per frame.
  - `frame_analysis.py` (`analyze_frame_spectroscopy`) analyzes one frame for
    a target outside a full run and merges its stars into the catalog.
  - `record_and_flag_spectroscopy_stars.py` saves one frame's stars to the
    catalog and flags shaky classifications. Both `runner.py` and `batch.py`
    call it.
- `pre_processing/spectral_frame_check.py` measures raw slitless-spectrum
  frames for `QualityDiagnostics.spectral_frame_check`, outside any run:
  the zero-order position, the trail's tilt and width, and the saturated
  patches along the spectrum. It finds the zero order with the stacking
  stage's point-source search, then its extended-target search for targets
  such as globular clusters, or accepts a position from the caller. It
  takes the saturation level from the camera profile
  (`shared/quality/saturation.py`) and records the level and its source in
  each result.

## Quality checkpoints

A checkpoint is a set of quality numbers taken at one point in the
pipeline. The pipeline measures a spectrum's quality at four checkpoints: one on the
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
such as `zero_order_saturated`. Each stage README lists its checkpoint's flags.

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

| Checkpoint | Stage name | Built by | Built from | Metrics listed in |
|---|---|---|---|---|
| 0 | `raw_frame` | `assess_raw_frame_quality` in `pre_processing/assess_raw_frame_quality.py` | The numbers the extraction already measured for the star | [Pre-processing](pre_processing/README.md), "Quality checkpoint 0" |
| 1 | `pre_processing` | `input_quality_checkpoint` in `pre_processing/assess_input_quality.py` | The `InputQualityAssessment` | [Pre-processing](pre_processing/README.md), "Quality checkpoint 1" |
| 2 | `processing` | `assess_processing_quality` in `processing/assess_processing_quality.py` | The classification, features, colour, emission lines and second-order ratio | [Processing](processing/README.md), "Quality checkpoint 2" |
| 3 | `post_processing` | `output_quality_checkpoint` in `post_processing/assess_output_quality.py` | The `OutputQualityAssessment` and the catalog comparison | [Post-processing](post_processing/README.md), "Quality checkpoint 3" |

`_apply_result_to_stellar_object` in `pipeline.py` calls the four builders and
stores the list. Each builder only gathers numbers that earlier code already
produced. None of them changes a spectrum or a classification.

### Where the limits come from

Limits come from the places that define them: `NO_GOOD_MATCH_RMS`,
`AMBIGUOUS_RMS_GAP` and `DIFFERS_FROM_CATALOG_SUBTYPES` in
`models/stellar_source.py`, `DEFAULT_SATURATION_FLAG_THRESHOLD` in
`pipelines/shared/quality/saturation.py`, and
`MINIMUM_SPECTRUM_SIGNAL_TO_NOISE` in `processing/spectrum_signal.py`. The
checkpoint builders import these shared limits and keep no copy of them. A
few limits belong to one measurement and live with it: the refraction limit
(`DAR_ALONG_DISPERSION_LIMIT_ANGSTROM` in
`pre_processing/assess_raw_frame_quality.py`), the wavelength zero-point
limit (`ZERO_POINT_OFFSET_LIMIT_ANGSTROM` in
`pre_processing/wavelength_zero_point.py`), the defocus limit
(`CHROMATIC_DEFOCUS_RATIO_LIMIT` in `pre_processing/measured_line_spread.py`)
and the Gaia XP limits (in `post_processing/compare_to_gaia_xp.py`). A metric
with no limit is reported for measurement only.

Each stage README lists its checkpoint's metrics in a table, with the unit,
the limit and what a value tells a reader.

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
- `offset_saturation_correlation`: the Pearson correlation (from -1 to 1;
  how closely two quantities rise and fall together) between a
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
model, the distance from the grating to the sensor. That distance sets how
many Angstroms each pixel along the trail covers, so it fixes the whole
wavelength scale. The tuner reads a frame of a star with known absorption
lines (dark dips at fixed wavelengths; Vega, for example) and works in these
steps:

1. Extract the star's spectrum with the normal pipeline and smooth it with a
   5-sample running mean (`SpectrumCalibrator.apply_smoothing`). Only the
   tuner smooths; the pipeline itself never smooths a calibrated spectrum.
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
   configuration, together with two checks: whether the camera needs the
   flare-mask extraction (see "How a spectrum is made") and whether the
   extraction must stop short to stay on the image.

How we know it works: `utilities/test/test_calibration_tuner_sample_distances.py`
builds frames with the Balmer lines at known positions and checks that:

- the tuner recovers those positions to 0.5 pixel and the grating distance to
  1 percent, with and without leading samples dropped;
- a noisy frame (3000 ADU continuum, Poisson noise, the random scatter of
  counted light) gives at most 12 candidates including the three true lines
  and finishes the search in under 10 seconds;
- the refined positions bring the fitted distance within 0.02 percent of the
  truth on both the dispersion-line and the flare-mask extraction, with
  zero-order positions that fall between pixels;
- each flare-mask sample distance lands within 0.01 pixel of the true
  distance from the zero order.

For exact behavior, read the code.

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

`catalog_agreement`, `feature_significance` and `spectra_extracted` raise at the level of the run what would otherwise show only on a star's own record (a disagreement with the catalog, an uncalibrated p-value), and flag a run with no spectra.

The counts behind the gates come from `spectrum_facts`, which the batch workers return per frame so the parallel path builds the same gates as the single-image path.

Second-order contamination and the emission-line detector have no run-level gate yet, because their limits are not validated: the 2% and 10% second-order figures (see [processing](processing/README.md), "Quality checkpoint 2") and the emission detector's 5σ, set on M 57 only.

## What the emission-line detector can see

An emission line is a bright bump at one wavelength, made by glowing gas (a nebula, or a star with hot gas around it). The detector is meant to find these.

- **False alarms.** Measured on 228 stored spectra of stars not known to emit (`scripts/validate_emission_line_detector.py`, 2026-10-09): the detector reports a false "detected" line in 1 of them (0.4%) and calls none an emission-line source.
- **Sensitivity.** It is also insensitive. A box-shaped line added to those spectra is reported as detected only about half the time (44% to 47%) when its height is 1.6 times the spectrum's median brightness, and 2% to 6% of the time at 0.4 to 0.8 times.
- **What it means.** A "not seen" from this detector means only that no strong line is there; it is built for a bright nebula such as M 57 (the Ring Nebula) and will pass a faint emitter.
- **Settings.** The thresholds (5 error bars detected, 3 unclear) were set on M 57 only and have not been tuned on any other nebula.
