# Photometry post-processing

This stage judges how much to trust a star's variability result and the run as a whole. It runs after processing has already decided whether a star's brightness varies more than the rest of the field.

Terms are defined in the [glossary](../../../../documentation/Glossary.md). This document also defines each term the first time it appears.

## What this stage does, in order

Processing gives each star a yes-or-no "variable" answer. This stage adds four things to it:

1. **Record each star's margin.** For each star, the stage records how far the star's coefficient of variation (CV: a light curve's standard deviation divided by its mean) sits from the field's CV cutoff, the fallback boundary described below. The distance is measured in units of the MAD, the median absolute deviation: the median distance of a set of values from their median, a measure of spread that a few extreme stars cannot inflate.
2. **Label known variables.** The stage labels each variable candidate with whether SIMBAD, Gaia DR3 or VSX already list it as variable.
3. **Build the run's gates.** A gate is a pass, fail or not-checked entry on the whole run. The photometry pipeline README lists every gate.
4. **Check that the score can see variables.** The stage checks whether the variability score ranks cataloged variable stars above the rest at all.

## Two rules, and which one the margin describes

Processing has two ways to pick variable-star candidates (see [Deciding which stars are variable](../processing/README.md#deciding-which-stars-are-variable)):

- **The candidate rule.** It applies whenever the field has at least 20 usable stars. It fits a noise model, the field's measured curve of how much a constant star of each brightness scatters, computes three variability indices per star, and combines them into a variability score. A star is a candidate when its score is above 1 and its false-discovery q-value is below 0.01.
- **The CV cutoff.** It is the fallback for smaller fields. A star is a candidate when its CV is above the field's median CV plus 7.4 times the MAD of the field's CVs, and above 0.02 in any case.

The confidence record built here always measures the star against the CV cutoff. It therefore says how close the call was only when the CV cutoff made the call, that is, in a field too small for a noise model. In a normal field the candidate rule decides, and a candidate can sit below the CV cutoff (a negative margin) or far above it. For those fields, the star's own `variabilityScore` (how far above 1 it is) and `falseDiscoveryQValue` describe how strong the actual decision was. Rank candidates by those two numbers.

## Checking that the score can see variables

`variability_skill.py` asks, once per run, whether the variability score ranks the stars the catalogs list as variable above the stars they do not list. It needs at least 10 cataloged variables and 30 unlisted stars with a score.

The measure is the AUC against catalog labels. AUC is the area under the ROC curve (the receiver operating characteristic curve). That curve plots the share of true variables caught against the share of other stars wrongly caught as the score's threshold is lowered. Equivalently, the AUC is the chance that a random cataloged variable has a higher score than a random unlisted star. A value of 0.5 is chance and 1 is a perfect ranking.

The `variability_discrimination` gate fails when the AUC is below 0.7 (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)) or below the level that beats chance at the 5% level for the group sizes, whichever is higher. At the smallest allowed group sizes the chance level is 0.68, so the floor of 0.7 decides in every case.

The same file gives the smallest sinusoid (a smooth, regular rise and fall) the candidate rule can flag, for the `detectable_amplitude` gate. With a noise model, the smallest amplitude is `2 sqrt(2) x sqrt(1.5^2 - 1) x` the noise model's scatter for a star of median brightness, in magnitudes peak to peak. A sinusoid of half-height `a` adds a scatter of `a / sqrt(2)`, and the candidate rule needs the total scatter to reach 1.5 times the noise model's. Without a noise model, the estimate is `2 sqrt(2) x 1.0857 x` the CV cutoff, the sinusoid whose own scatter equals the cutoff. (1.0857 is 2.5 / ln 10, which turns a fractional scatter into magnitudes.)

The `detectable_amplitude` gate fails when this estimate is above 0.3 mag (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)). That limit is not a measured detection threshold. The measured threshold is partly measured (see [PHO-09](../../../../documentation/library_design/validation/photometry.md#pho-09)).

## Settings

- **CV cutoff multiplier.** `DEFAULT_VARIABILITY_SIGMA_THRESHOLD` is 7.4 (measured; see [PHO-10](../../../../documentation/library_design/validation/photometry.md#pho-10)). The cutoff never falls below 0.02.
- **Low-confidence margin.** `LOW_CONFIDENCE_MARGIN` is 1 MAD (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)). The cutoff sits at least 7.4 MAD above the field median, so 1 MAD is a small step next to it. A star that close is one noisy measurement away from the other side.
- **Discrimination floor.** `MINIMUM_DISCRIMINATION_AUC` is 0.7 (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)).
- **Largest detectable amplitude.** `MAXIMUM_DETECTABLE_AMPLITUDE_MAG` is 0.3 mag (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)).

## Quality metrics

The stage records these values for each star. A large margin means the CV-cutoff verdict is unlikely to change if the data were measured again. A small margin, positive or negative, means another session's data could easily flip it. In a field small enough to use the CV cutoff, a reviewer can use these values to separate strong candidates from borderline calls. In a normal field, read them as a second view of the star's scatter, not as the confidence of its candidate status.

| Field | Unit | Limit (status) | High or low means | Read by |
| --- | --- | --- | --- | --- |
| `coefficient_of_variation` | none | CV cutoff of the field: median CV plus 7.4 MAD, at least 0.02 (measured; see [PHO-10](../../../../documentation/library_design/validation/photometry.md#pho-10)) | High: more scatter relative to the mean | Margin calculation |
| `adaptive_cutoff` | none | Same as above | High: the field's stars scatter more, so a star needs more scatter to be flagged | Margin calculation |
| `margin_in_mad_units` | MAD | Magnitude below 1 (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)) | Large positive: the CV is far above the cutoff. Near zero: the CV sits close to it. | Low-confidence flag; reviewers |
| `is_low_confidence` | flag | Set when the margin is within 1 MAD of the cutoff on either side (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)) | Set: a borderline call | `is_trustworthy`; reviewers |
| `is_trustworthy` | flag | Set when `is_low_confidence` is not set | Set: the verdict is stable | Reviewers |

The margin is `(CV - cutoff) / MAD`, where the MAD is the field's median absolute deviation of the CV, never taken below 0.0001.

## Files

- `assess_output_quality.py` builds the per-star confidence record described above (`assess_output_quality`).
- `known_variability_labels.py` labels each variable candidate with whether SIMBAD, Gaia DR3 or VSX already list it as variable (`label_known_variability`). SIMBAD is a general star database, Gaia DR3 is the third data release of the Gaia satellite survey, and VSX is the International Variable Star Index. The file also splits stars into listed and unlisted groups for the AUC check (`is_listed_as_variable`, `split_scores_by_catalog_status`). A candidate whose catalogs were never asked is labeled `unknown`, not "not listed".
- `variability_skill.py` measures whether the variability score can see cataloged variables, and the smallest amplitude the candidate rule can flag.
- `record_quality.py` builds the run's gate record (`photometry_run_gates`): one `passed`, `failed` or `not_checked` result per gate. It also turns the run's counts, measurements and gates into quality measurements (`photometry_run_measurements`) and stores them (`record_photometry_quality`). A gate's stored number always means one thing: the comparison-ensemble gate stores the size of the smallest set and the registration gate stores the largest global drift, and the scatter ratio, the corner displacement and the fallback share have metrics of their own.

For exact behavior, read the code.
