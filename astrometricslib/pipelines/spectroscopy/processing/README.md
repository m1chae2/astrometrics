# Processing

This stage finds things in a star's calibrated spectrum. Everything here
takes a spectrum that pre-processing already cleaned up and works out what
that spectrum shows about the star. "Cleaned up" means the camera's
sensitivity, the instrument's tilt and the difference in the air's dimming
between this frame and the standard star's frame are already removed (see
the [pre-processing README](../pre_processing/README.md)).

## The flow

1. **Check for a real signal.** A star too faint for the frames produces a
   "spectrum" that is just noise sitting on zero. The pipeline checks for
   this case before running anything else, because a classification or
   feature test still produces an answer for pure noise. That answer does
   not mean anything.
2. **Flag possible second-order contamination.** A grating sends a small
   amount of a star's blue light to a second position further along the
   sensor, where it lands on top of the star's own red light. For a hot
   star this can account for a meaningful share of the reported red
   brightness, so the pipeline flags the risk instead of ignoring it.
3. **Look for named features.** The pipeline checks the spectrum at the
   known wavelengths of standard absorption features, such as the hydrogen
   series and the Ca II H&K blend, and asks at each one whether the dip is
   deep enough that noise alone is unlikely to have produced it.
   - For a star, these are absorption dips.
   - For a glowing gas cloud (a nebula), the pipeline runs the same check
     in reverse and looks for bright emission humps instead of dips.
4. **Remove interstellar reddening, when the catalog gives it.** Dust
   between us and a star makes the star look redder than it is. When the
   star has a Gaia DR3 id and Gaia gives a colour excess E(B-V) for it, the
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
   blue-versus-visual brightness ratio, its B-V colour, directly from the
   spectrum, independent of every other step in this stage. It measures the
   observed spectrum, because the catalog colour it is later checked
   against is also reddened. Post-processing checks this measurement
   against the star's catalog colour.

## What this stage records about its input

`analyze_spectrum` stores the airmass extinction record that
pre-processing produced in `SpectrumAnalysis.extinction_correction`. The
record holds `is_applied` (true or false), `target_airmass` and
`reference_airmass` (both unitless), `curve_name`, and `reason` (why the
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
Pearson correlation. The correlation is kept for comparison only, because
it barely separates neighbouring types.

A best match above `NO_GOOD_MATCH_RMS` (0.15) gets `match_quality` of
`poor`. A best match above `UNRELIABLE_MATCH_RMS` (0.45) gets no type at
all, because the closest reference would only invent one.

The limits `NO_GOOD_MATCH_RMS`, `UNRELIABLE_MATCH_RMS` and
`AMBIGUOUS_RMS_GAP` are defined once, in
`astrometricslib/models/stellar_source.py`, each with a docstring that
states its unit and the measurement behind it. The classifier imports them
and defines no limit of its own. A test
(`test/test_threshold_single_definition.py`) fails if it does.

Neighbouring rungs of the reference ladder sit 0.02 to 0.19 RMS apart
(median 0.043). Real spectra often fit two neighbouring rungs almost
equally well, and interstellar reddening shifts the score by about as much
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
   `ebpminrp_gspphot`, the colour excess E(BP-RP) in magnitudes, and divides
   it by 1.339 to get E(B-V). If that value is missing, it reads
   `ag_gspphot`, the dimming in Gaia's G band, and divides it by 2.740. Both
   ratios come from Casagrande and VandenBerg (2018, MNRAS 479, L102) for a
   typical star. A failed lookup is logged and the spectrum is classified as
   observed. A caller replaces the driver with `Astrometrics(...,
   reddening_driver=...)`.
3. **Remove the reddening.** `interstellar_extinction.deredden_spectrum`
   multiplies the spectrum by 10 to the power of 0.4 A(wavelength), where
   A(wavelength) is the dimming from the Cardelli, Clayton and Mathis (1989)
   law with R_V = 3.1 (the formula is in the module's docstring).
4. **Classify twice.** `analyze_spectrum` classifies the observed and the
   dereddened spectrum. The reported type, its RMS scores and the candidates
   come from the dereddened spectrum. If the dereddened spectrum cannot be
   classified, the observed result stays.

The result keeps `SpectroscopyResult.reddening`: `ebv`, `ebv_source`,
`gaia_source_id`, `observed_best_type`, `dereddened_best_type` and
`type_shift_steps` (dereddened minus observed position on the O-to-M ladder,
negative when dereddening gives a hotter type). The stored spectrum stays
as observed.

Measured on the bundled templates blurred to the stored line spread: a G0V
template reddened with E(B-V) = 0.3 classifies as K2V (12 subtypes too late)
without dereddening, and as G0V with the known E(B-V). With E(B-V) 0.25 for a
star that has 0.30, the type still moves closer to G0V. The catalog value is
itself an estimate, so a real star improves by less than a template does.

## Line-index cross-check

`spectral_line_indices.py` estimates the type from six line depths. Each
depth is how far the spectrum sits below a local continuum at the line, as a
fraction of that continuum. The local continuum is the quadratic curve the
feature detector uses (`spectral_feature_detector.measure_local_dip_depth`),
so a smooth tilt cancels. Measured on a G0V template, adding E(B-V) = 0.3
moves every index by under 0.01.

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
that space. The result is `SpectroscopyResult.line_index_classification`:
`best_type`, `distance` (unitless, root-mean-square in spread units),
`indices`, `template_fit_type` and `steps_from_template_fit`.

How accurate the estimate is, measured on the bundled templates with the
ZWO ASI 533MM Pro line spread (34 templates):

- Each template given as input, with itself among the references: 34 of 34
  exact. This only shows that the six indices tell the templates apart.
- Each template with itself removed from the references: 50% within two
  subtypes, 71% within five, and the worst miss is 19 subtypes (B3 read as
  F2). The ladder has gaps of three to five subtypes in places, so even a
  perfect method could not reach two subtypes everywhere.
- With 0.5% random noise on every 10 A sample: 99% within two subtypes. With
  1% noise: 85%. With 2% noise: 72%.

At this resolution a hot B star and a mid F star have nearly the same line
indices. The estimate is therefore a second opinion on the type, good to a
few subtypes. It is not a replacement for the template fit, and it cannot
separate the hot side of A from the cool side without the continuum.

## What this stage produces

This stage produces a likely spectral type, the reddening correction behind
it (when there was one), a line-strength estimate of the type, the features
found or not found at each expected wavelength, a second-order-contamination
risk flag, and the spectrum's own measured colour. It attaches all of these to the star
for post-processing to evaluate.

## Quality checkpoint 2

`assess_processing_quality.py` judges this stage's results on their own and
returns a `StageQualityCheckpoint` for the `processing` stage. It reads the
classification, the feature tests, the synthetic colour, the emission lines and
the second-order ratio, and changes none of them. Its metrics are:

- the classification RMS and the RMS gaps to the second-best and the next
  class, judged against `NO_GOOD_MATCH_RMS` and `AMBIGUOUS_RMS_GAP`;
- whether the pipeline measured a synthetic colour, and the colour itself;
- the number of significant features, their best p-value and the number of
  p-values that were not calibrated;
- the share of samples with second-order risk and the largest blue-to-red
  ratio;
- the number of detected emission lines;
- `ebv_used` (magnitudes) and `dereddening_type_shift_steps` (subtype steps),
  which report the dereddening and have no limit, because a type shift is
  expected when dust is present;
- `index_vs_template_type_steps` (subtype steps), the distance between the
  line-index type and the reported type, judged against
  `DIFFERS_FROM_CATALOG_SUBTYPES` (20). The limit is the one the repository
  already uses for two types that disagree. It is a designed value. The
  checkpoint raises the `slope_and_lines_disagree` flag when the distance
  passes it. On the templates, the leave-one-out distance never passes it
  (the worst miss is 19 subtypes).

The `processing_quality` run gate counts the spectra in which any of these
metrics fails. The [pipeline README](../README.md) lists every metric with its
unit and limit.

For exact behavior, read the code.
