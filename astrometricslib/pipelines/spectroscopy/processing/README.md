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
4. **Classify the star.** The pipeline compares the spectrum's overall
   shape, such as how steeply its brightness changes across colours and how
   strong its hydrogen lines are, against a library of reference spectra
   and reports the closest match as the star's likely spectral type. The
   score for each reference is its relative RMS (see "Quality metrics from
   the classifier" below). Lower is closer. The score is not a probability.
5. **Measure the spectrum's own colour.** The pipeline measures the star's
   blue-versus-visual brightness ratio, its B-V colour, directly from the
   spectrum, independent of every other step in this stage. Post-processing
   later checks this measurement against the star's catalog colour.

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

## What this stage produces

This stage produces a likely spectral type, the features found or not found
at each expected wavelength, a second-order-contamination risk flag, and
the spectrum's own measured colour. It attaches all of these to the star
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
- the number of detected emission lines.

The `processing_quality` run gate counts the spectra in which any of these
metrics fails. The [pipeline README](../README.md) lists every metric with its
unit and limit.

For exact behavior, read the code.
