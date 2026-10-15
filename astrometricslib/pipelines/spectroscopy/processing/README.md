# Processing

This stage finds things in a star's calibrated spectrum. Everything here
takes a spectrum that pre-processing already cleaned up and works out what
that spectrum shows about the star. "Cleaned up" means the camera's
sensitivity, the instrument's tilt and the difference in the air's dimming
between this frame and the standard star's frame are already removed (see
the [pre-processing README](../pre_processing/README.md)).

Terms are defined in the
[glossary](../../../../documentation/library_design/Astrometrics_Library_Architecture.md#appendix-a-glossary).
This README also defines each term the first time it appears.

In plain words: a spectrum is a star's brightness at each wavelength. Its
overall slope says how hot the star is (hot stars are bluer), and dark dips
at fixed wavelengths (absorption lines, light absorbed by atoms in the
star's outer layers) say which atoms are present and at what temperature.
Bright bumps (emission lines) come from glowing gas. This stage reads those
clues and names the closest spectral type on the O-B-A-F-G-K-M ladder
(hottest to coolest, each letter split into subtypes 0 to 9). "Early" types
sit at the hot end of the ladder and "late" types at the cool end, so
"12 subtypes too late" means 12 steps too cool. Wavelengths
are in Angstroms (A; 1 A is 0.1 nm).

## The flow

`analyze_spectrum` in `spectrum_analysis.py` runs steps 1 to 7 for every
spectrum. The pipeline and `scripts/recompute_spectral_analysis.py` both
call it, so the two cannot drift apart. Step 8 runs in `pipeline.py` after
it.

1. **Check for a real signal.** A star too faint for the frames produces a
   "spectrum" that is just noise sitting on zero. The pipeline checks for
   this case before running anything else, because a classification or
   feature test still produces an answer for pure noise. That answer does
   not mean anything.
2. **Look for emission lines.** The pipeline looks for bright emission
   humps at the known wavelengths of named emission lines
   (`emission_line_detector.py`). It does this first because the
   classification must leave detected emission out of its comparison.
   When the catalog says the target is extended (a nebula or a cluster),
   the pipeline stops here: it reports the emission lines and gives no
   stellar type and no absorption-feature results, because the light is a
   whole object's, not one star's. An extended target made of emission
   lines is labelled as glowing gas.
3. **Look for named features.** The pipeline checks the spectrum at the
   known wavelengths of standard absorption features, such as the hydrogen
   series (the Balmer lines) and the Ca II H&K blend (two lines of ionized
   calcium), and asks at each one whether the dip is
   deep enough that noise alone is unlikely to have produced it. The answer
   is a p-value (the chance that noise alone gives a dip this strong; small
   means unlikely to be noise) and a verdict such as detected or possible.
4. **Remove interstellar reddening, when the catalog gives it.** Dust
   between us and a star makes the star look redder than it is. When the
   star has a Gaia DR3 id (its number in the third data release of the Gaia
   satellite survey) and Gaia gives a colour excess E(B-V) for it (how much
   redder dust has made the star, in magnitudes), the
   pipeline removes the reddening from the spectrum (see "Dereddening"
   below). Without an E(B-V), the pipeline classifies the spectrum as
   observed.
5. **Classify the star.** The pipeline compares the spectrum's overall
   shape, such as how steeply its brightness changes across colours and how
   strong its hydrogen lines are, against a library of reference spectra
   and reports the closest match as the star's likely spectral type. When
   step 4 ran, the pipeline classifies both the observed and the dereddened
   spectrum and reports the dereddened result. The score for each reference
   is its relative RMS (see "Quality metrics from the classifier" below).
   Lower is closer. The score is not a probability.
6. **Estimate the type from line strengths.** The pipeline measures a few
   spectral lines against the continuum next to each one and names the
   reference with the closest line strengths (see "Line-index cross-check"
   below). This estimate ignores the continuum slope. It does not change
   the type from step 5. It reports how far the two types are apart.
7. **Measure the spectrum's own colour.** The pipeline measures the star's
   blue-versus-visual brightness ratio, its B-V colour (in magnitudes, the
   logarithmic brightness scale; a larger B-V is redder), directly from the
   spectrum, independent of every other step in this stage. It measures the
   observed spectrum, because the catalog colour it is later checked
   against is also reddened. Post-processing checks this measurement
   against the star's catalog colour.
8. **Flag possible second-order contamination.** A grating sends a small
   amount of a star's blue light to a second position further along the
   sensor (the second order), where it lands on top of the star's own red light. For a hot
   star this can account for a meaningful share of the reported red
   brightness, so the pipeline flags the risk instead of ignoring it
   (`second_order_risk.py`). It computes this ratio after the analysis
   above, and nothing in steps 1 to 7 reads it.

`star_reddening.py` connects a star to the reddening driver for step 4: it
reads the star's Gaia DR3 id and asks the driver for E(B-V).

## What this stage records about its input

`analyze_spectrum` stores the airmass extinction record that
pre-processing produced in `SpectrumAnalysis.extinction_correction`. The
record holds `is_applied` (true or false), `target_airmass` and
`reference_airmass` (both unitless; airmass measures how much air the light
crossed, 1 straight overhead), `curve_name`, and `reason` (why the
correction was skipped, if it was). The analysis does not apply the
correction itself. A reviewer reads the record to see whether the
classifier compared a spectrum that was scaled to the standard star's
airmass or one that was not. When the correction was skipped, a tilt of
about 0.09 magnitudes between 4200 A and 8000 A per 0.35 airmass of
difference can remain. That is as large as the gap between neighbouring
spectral types, so a skipped correction makes a classification less
certain.

## Quality metrics from the classifier

The classifier scores every reference spectrum with one number, the
relative RMS. RMS stands for root-mean-square difference. The classifier
first scales the reference to the same overall brightness as the observed
spectrum. It then takes the typical size of the remaining difference and
divides it by the spectrum's average brightness. A relative RMS of 0.05
means a typical sample is off by 5% of the average brightness. Zero is a
perfect match, and lower is closer. The score has no units.

The classifier reports five values built from these scores. None of them
is a probability.

| Value | What it measures | Units | Who reads it |
|---|---|---|---|
| `classification_rms` | The best reference's relative RMS. | Relative RMS | `match_quality`, the poor-match flag in post-processing, the star's `isPoorMatch` field |
| `rms_gap_to_second_best` | The second-best reference's RMS minus the best one's. A large gap means the best reference clearly beats its neighbour. A gap of zero means a tie. It is a difference in RMS, not a probability. `None` when fewer than two references were compared. | Relative RMS | `is_ambiguous`, the ambiguity flag in post-processing, the star's `rmsGapToSecondBest` field |
| `is_ambiguous` | Whether that gap is below `AMBIGUOUS_RMS_GAP` (0.02). This is the subtype-level statement: the runner-up is usually a neighbouring subtype of the same class, so a small gap says the subtype is uncertain. | True, false, or `None` when there is no gap | The star's `isAmbiguous` field, the subtype count in the gate's detail |
| `rms_gap_to_next_class` | The RMS of the best reference whose spectral class letter differs from the best one's, minus the best RMS. For a G0V best match, the other class is the closest F, K or other reference. `None` when no other class was compared. It is a difference in RMS, not a probability. | Relative RMS | `is_class_ambiguous`, the star's `rmsGapToNextClass` field |
| `is_class_ambiguous` | Whether the class gap is below `AMBIGUOUS_RMS_GAP` (0.02). This is the class-level statement: the class letter itself is uncertain. | True, false, or `None` when no other class was compared | The `spectral_classification` gate and the star's `isClassAmbiguous` field |

The classifier also returns `ranked_types`, the list of every reference it
compared, closest first. Each entry holds the type, its `rms` and its
Pearson correlation (from -1 to 1; how closely the two spectra rise and
fall together). The correlation is kept for comparison only, because
it barely separates neighbouring types.

A best match above `NO_GOOD_MATCH_RMS` (0.15) gets `match_quality` of
`poor`. 0.15 sits between the scores of right and wrong matches on stars of
known type (partly measured; see [SPE-16](../../../../documentation/library_design/validation/spectroscopy.md#spe-16)). A best match above
`UNRELIABLE_MATCH_RMS` (0.45) gets no type at all, because the closest
reference would only invent one (partly measured; see [SPE-17](../../../../documentation/library_design/validation/spectroscopy.md#spe-17)).

The limits `NO_GOOD_MATCH_RMS`, `UNRELIABLE_MATCH_RMS` and
`AMBIGUOUS_RMS_GAP` are defined once, in
`astrometricslib/models/stellar_source.py`, each with a docstring that
states its unit and the measurement behind it. The classifier imports them
and defines no limit of its own. A test
(`test/test_threshold_single_definition.py`) fails if it does.

Neighbouring rungs of the reference ladder sit only a few hundredths of RMS
apart (measured; see [SPE-18](../../../../documentation/library_design/validation/spectroscopy.md#spe-18)). Real spectra often fit two
neighbouring rungs almost equally well, and interstellar reddening shifts the score by about as much
as one rung. A small gap therefore means the data cannot choose between
the two rungs, and the flag reports that.

## Dereddening

Reddening and the difference between neighbouring spectral types look alike
at this instrument's resolution. Both tilt the spectrum, so the classifier
cannot tell them apart from the spectrum alone (see the header of
`spectral_classifier.py`). A catalog value of E(B-V) removes the ambiguity
from outside the spectrum.

1. **Find the Gaia id.** `catalog_star_identity.gaia_dr3_source_id_of`
   (in `pipelines/shared/`) reads the star's stored `gaia_dr3_source_id`,
   which the star identifier fills from SIMBAD's list of names or from a
   Gaia match. Otherwise it looks for a `Gaia DR3 <digits>` name in the
   star's `id`, `name` or `target_ids`. The Gaia XP comparison uses the same
   function. A star with no Gaia id (the brightest stars are not in Gaia
   DR3) gets no reddening lookup.
2. **Ask the reddening driver.** `ReddeningDriver.get_reddening` returns an
   E(B-V) and a note on where it came from. The built-in driver
   (`drivers/astroquery_gaia_reddening_driver.py`) reads Gaia DR3
   `ebpminrp_gspphot`, the colour excess E(BP-RP) in magnitudes (the same
   idea measured in Gaia's own blue and red bands), and divides
   it by 1.339 to get E(B-V). If that value is missing, it reads
   `ag_gspphot`, the dimming in Gaia's G band, and divides it by 2.740. Both
   ratios come from Casagrande and VandenBerg (2018, MNRAS 479, L102) for a
   typical star.

   A failed lookup is logged and the spectrum is classified as
   observed. A caller replaces the driver with `Astrometrics(...,
   reddening_driver=...)`.
3. **Remove the reddening.** `interstellar_extinction.deredden_spectrum`
   multiplies the spectrum by 10 to the power of 0.4 A(wavelength), where
   A(wavelength) is the dimming from the Cardelli, Clayton and Mathis (1989)
   law with R_V = 3.1 (the formula is in the module's docstring). The law
   gives how dust dimming changes with wavelength; R_V sets its shape, and 3.1
   is the usual value for dust in our galaxy.
4. **Classify twice.** `analyze_spectrum` classifies the observed and the
   dereddened spectrum. The reported type, its RMS scores and the candidates
   come from the dereddened spectrum. If the dereddened spectrum cannot be
   classified, the observed result stays.

The result keeps `SpectroscopyResult.reddening`: `ebv`, `ebv_source`,
`gaia_source_id`, `observed_best_type`, `dereddened_best_type` and
`type_shift_steps` (dereddened minus observed position on the O-to-M ladder,
negative when dereddening gives a hotter type). The stored spectrum stays
as observed.

How we know it helps: on the bundled templates (reference spectra), removing
the known E(B-V) brings a reddened template back to its own type (partly
measured, on templates only; see [SPE-21](../../../../documentation/library_design/validation/spectroscopy.md#spe-21)). The catalog value is
itself an estimate, so a real star improves by less than a template does.

## Line-index cross-check

What and why: the template fit leans on the spectrum's overall slope, which
dust and calibration errors can tilt. Line depths measured against the
brightness right next to each line do not depend on that slope, so they give
an independent second opinion on the type.

`spectral_line_indices.py` estimates the type from six line depths. Each
depth is how far the spectrum sits below a local continuum at the line, as a
fraction of that continuum (the brightness the star would have without the
line). The local continuum is the quadratic curve the
feature detector uses (`spectral_feature_detector.measure_local_dip_depth`),
so a smooth tilt cancels (see [SPE-22](../../../../documentation/library_design/validation/spectroscopy.md#spe-22)).

| Index | Core window | What it tracks |
|---|---|---|
| `h_beta` | 4861 A, 25 A either side or half a resolution element | Strongest at A0, weaker toward hotter and cooler stars. |
| `h_alpha` | 6563 A, 25 A either side or half a resolution element | The same shape as H-beta. It turns negative in late K and M stars. |
| `mg_b` | 5175 A, 20 A either side or half a resolution element | Rises through G and K stars. |
| `na_d` | 5893 A, 20 A either side or half a resolution element | Rises through K and M stars. |
| `tio_6200` | 6150-6250 A | Titanium oxide band in M stars. |
| `tio_7100` | 7050-7150 A | Titanium oxide band in M stars. |

The module measures the same indices on every main-sequence reference,
blurred to the instrument's line spread. It divides each index by its spread
(standard deviation) across the references and picks the reference nearest in
that space (the smallest root-mean-square difference over the six scaled
indices). The result is `SpectroscopyResult.line_index_classification`:
`best_type`, `distance` (unitless, root-mean-square in spread units),
`indices`, `template_fit_type` and `steps_from_template_fit`.

How accurate the estimate is: on the bundled templates, with each template
left out of the references in turn (a leave-one-out test, which mimics a star
whose exact type is not in the library), it lands within a few subtypes, and
its worst miss stays inside the catalog-disagreement limit (partly measured,
on templates only; see [SPE-22](../../../../documentation/library_design/validation/spectroscopy.md#spe-22)).

At this resolution a hot B star and a mid F star have nearly the same line
indices. The estimate is therefore a second opinion on the type, good to a
few subtypes. It is not a replacement for the template fit, and it cannot
separate the hot side of A from the cool side without the continuum.

## Equivalent widths and their errors

What and why: the pipeline needs a measure of line strength that does not
depend on the instrument's blur, and an honest error on it.

The depth of a feature depends on how much the instrument blurred it. The
equivalent width (EW) does not. It is the width, in Angstroms, of a rectangle
from zero up to the continuum that holds the same area as the line's deficit:
the integral of `1 - F / C` over wavelength, with `F` the spectrum and `C` the
continuum (the brightness without the line). A dip has a positive EW, and an
emission bump a negative one. A Gaussian dip of depth `d` and standard
deviation `s` has an EW of `d * s * sqrt(2 * pi)`.

For each named feature that the detector measures, the pipeline adds these
keys to the feature's record, next to the depth fields (which stay as they
were):

- `equivalent_width_angstrom`
- `equivalent_width_error_angstrom`
- `equivalent_width_window_half_width_angstrom`, how far the integral reached
  on each side of the feature

They are `None` when the spectrum carries no errors or the window is not
covered.

How it is measured: the EW is measured at the center of the dip the detector chose. The
integral runs over 1.5 times the line spread (the width of the instrument's
blur at that wavelength) on each side. The continuum is the same quadratic the
detector fits to the bands beside the feature. When the integration window is
wider than the detector's inner band edge, which happens in the red where the
blur is wider, the bands start at the window edge instead, so they never hold
the line's own wings.

The error has two independent parts, added in quadrature (as a variance sum,
the square root of the sum of squares):

- the sample errors inside the window, each scaled by the width of the sample
  and divided by the continuum;
- the error of the continuum fit, from the covariance of the fitted
  coefficients (how their errors move together), which the pipeline computes from the sample errors in the
  bands and propagates to the EW through the slope of the EW with respect to
  each coefficient.

When the band samples scatter about the fitted curve by clearly more than their
errors say, the pipeline inflates the error. The reduced chi-square is the
mean squared residual in units of the error bars: about 1 when the scatter
matches the errors. For pure noise with `n` degrees of freedom it scatters
about 1 with a standard deviation of `sqrt(2 / n)`. "Clearly more" means
above `1 + 3 sqrt(2 / n)`, 3 of those standard deviations above 1. Then
the continuum part is multiplied by the square root of that reduced
chi-square. `equivalent_width.py` holds the details and its limits. The
verdicts and p-values do not use the errors.

How we know: on synthetic lines, the mean EW and the reported error match
the truth and the scatter of many noise realizations; with the center chosen
by the detector's best-dip search, a faint line's EW reads a few percent high
(partly measured; see [SPE-23](../../../../documentation/library_design/validation/spectroscopy.md#spe-23)).

## Reduced chi-square next to the RMS

The RMS says how far a reference is from the spectrum, but not whether that
distance is larger than the noise. The reduced chi-square answers that.

When the spectrum carries errors, the classifier gives every reference a
second score: the reduced chi-square. It is the sum of the squared residuals,
each divided by its sample's error squared, over the number of samples used
minus one (for the fitted scale). It uses the same scale and the same samples
as the RMS (it leaves out the atmospheric bands and the excluded windows), so
it scores the residuals the RMS scores. A value near 1 means the reference fits
within the noise. A value far above 1 means the mismatch is larger than the
noise, which is the usual case for a bright star, because the references and
the instrument response are not exact.

The score appears as `reduced_chi_square` in every `ranked_types` entry, and
the classification result carries it for the best and the second-best
reference by RMS as `reduced_chi_square` and `second_best_reduced_chi_square`.
The ranking, the decision and every threshold still use the RMS alone.

## What this stage produces

This stage produces a likely spectral type, the reddening correction behind
it (when there was one), a line-strength estimate of the type, the features
found or not found at each expected wavelength, a second-order-contamination
risk flag, and the spectrum's own measured colour. It attaches all of these to the star
for post-processing to evaluate.

## Quality checkpoint 2

`assess_processing_quality.py` judges this stage's results on their own,
before the catalog comparison, and returns a `StageQualityCheckpoint` for the
`processing` stage. It reads the classification, the feature tests, the
synthetic colour, the emission lines and the second-order ratio, and changes
none of them. For an unclassified spectrum the
classification and colour metrics have no value, and the checkpoint carries the
`unclassified` flag.

| Metric | Unit | Limit | What it tells a reader |
|---|---|---|---|
| `classification_rms` | relative RMS | `NO_GOOD_MATCH_RMS` (0.15), lower is better | How far the best reference spectrum is from this spectrum. |
| `rms_gap_to_second_best` | relative RMS | none (reported only) | How much closer the best reference is than the runner-up. Below `AMBIGUOUS_RMS_GAP` (0.02) the subtype is uncertain. That is common at this resolution, so this metric has no limit and fails nothing at this checkpoint or in any run gate. The same condition does set checkpoint 3's `subtype_ambiguous` flag and makes the star's `trustworthy` verdict 0 (see the [post-processing README](../post_processing/README.md)). |
| `rms_gap_to_next_class` | relative RMS | `AMBIGUOUS_RMS_GAP`, higher is better | The same gap to the best reference of another class letter. A small gap means the class is uncertain. |
| `synthetic_colour_measured` | flag | 1, higher is better | 1 when the pipeline measured a B-V colour from the spectrum. |
| `synthetic_b_minus_v` | magnitude | none | The B-V colour measured from the spectrum. |
| `significant_feature_count` | features | none | How many named features have a detected or possible verdict. |
| `best_feature_p_value` | probability | none | The smallest feature p-value (the chance that noise like this spectrum's gives a feature this strong). Lower is stronger. |
| `uncalibrated_feature_count` | features | 0, lower is better | How many p-values assumed Gaussian noise because fewer than 20 control windows were available. Control windows are other positions along the spectrum where the feature detector runs the same dip test, to learn how strong a "feature" this spectrum's noise alone produces. |
| `second_order_risky_fraction` | fraction | none | The share of samples where second-order light could add a tenth (10 percent) of the signal. |
| `second_order_max_blue_to_red_ratio` | ratio | none | The largest ratio of the brightness at half a wavelength to the brightness at that wavelength. |
| `emission_lines_detected` | lines | none | How many named emission lines or blends have a detected verdict. The `emission_line_source` flag marks two or more. |
| `ebv_used` | magnitude | none (reported only) | The colour excess E(B-V) the pipeline removed from the spectrum before classifying. The note names the catalog and the conversion. No value when the star has no Gaia DR3 id or Gaia gives none. |
| `dereddening_type_shift_steps` | subtype steps | none (reported only) | How many steps on the O-to-M ladder the best type moved because of dereddening (ten steps to a letter class). A large shift means the observed colour was mostly dust. No value without an E(B-V). |
| `index_vs_template_type_steps` | subtype steps | `DIFFERS_FROM_CATALOG_SUBTYPES` (20), lower is better | The distance between the type that the six line indices point to (see "Line-index cross-check") and the template-fit type. A designed limit, the one used for two types that disagree. |
| `median_equivalent_width_relative_error` | fraction | none | The median of the equivalent-width error over its size, for features with a detected or possible verdict. |
| `hbeta_equivalent_width_angstrom`, `halpha_equivalent_width_angstrom` | angstrom | none | The equivalent width of each Balmer line (positive for a dip). The note holds its error. |
| `best_template_reduced_chi_square` | reduced chi-square | none | The chi-square per degree of freedom of the best reference by RMS, using the per-sample errors. Near 1 means a fit within the noise. It decides nothing. |

The processing flags are `unclassified`, `emission_line_source`,
`second_order_risk` and `slope_and_lines_disagree`. The last one marks a
spectrum whose `index_vs_template_type_steps` is above its limit: the
continuum slope and the line strengths point to different types.

**Where the limits come from.**

- 0.15 (`NO_GOOD_MATCH_RMS`): the gap between correct and wrong matches on
  stars of known type (see "Quality metrics from the classifier"; partly
  measured, [SPE-16](../../../../documentation/library_design/validation/spectroscopy.md#spe-16)).
- 0.02 (`AMBIGUOUS_RMS_GAP`): the smallest RMS distance between two
  neighbouring templates on the reference ladder (measured; see
  [SPE-18](../../../../documentation/library_design/validation/spectroscopy.md#spe-18)). A smaller gap means the spectrum cannot prefer one rung
  over its neighbour even by the margin a perfect match to the closest-spaced
  pair would show.
- 20 subtype steps (`DIFFERS_FROM_CATALOG_SUBTYPES`): two whole letter
  classes, such as B to F. It sits above the ordinary misses of real stars
  and of the line-index estimate on the templates (partly measured; see
  [SPE-19](../../../../documentation/library_design/validation/spectroscopy.md#spe-19) and [SPE-22](../../../../documentation/library_design/validation/spectroscopy.md#spe-22)).

**Second-order figures.** The risk metric assumes that second-order light is
at most 2 percent of the first-order brightness at half the wavelength. 2
percent is the largest value seen when fitting stored spectra, not a
measured value (partly measured; see [SPE-26](../../../../documentation/library_design/validation/spectroscopy.md#spe-26)). A wavelength is called risky when that 2
percent could add 10 percent of its own brightness, that is, when the star is
at least 5 times brighter at half the wavelength. 10 percent is a judgement:
about where an added signal starts to change a spectrum's shape visibly. The
two second-order metrics have no limit because neither figure is validated.

The `processing_quality` run gate counts the spectra in which any of these
metrics fails. The [pipeline README](../README.md) describes the checkpoint
record and the run-level summary.

For exact behavior, read the code.
