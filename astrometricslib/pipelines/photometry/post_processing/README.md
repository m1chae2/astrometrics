# Post-processing

This step judges how much to trust a star's variability result and the run as a whole. It runs after processing has already decided whether a star's brightness varies more than the rest of the field.

Terms are defined in the [glossary](../../../../documentation/library_design/Astrometrics_Library_Architecture.md#appendix-a-glossary). This README also defines each term the first time it appears.

In plain words: processing gives each star a yes-or-no "variable" answer. This step adds two things. For each star, it records how far the star's coefficient of variation sits from the field's CV cutoff (the fallback boundary described below). For the run, it builds gates (pass, fail or not-checked tests on the whole run) and checks whether the variability score can tell known variable stars from the rest at all.

## Files

- `assess_output_quality.py` builds the per-star confidence record described below (`assess_output_quality`).
- `known_variability_labels.py` labels each variable candidate with whether SIMBAD, Gaia DR3 or VSX (three star catalogs: a general star database, the Gaia satellite's third data release, and the International Variable Star Index) already list it as variable (`label_known_variability`), and splits stars into listed and unlisted groups for the AUC check (`is_listed_as_variable`, `split_scores_by_catalog_status`). A candidate whose catalogs were never asked is labelled `unknown`, not "not listed".
- `variability_skill.py` measures whether the variability score can see catalogued variables, and the smallest amplitude the candidate rule can flag (see the last section).
- `run_gates.py` builds the run's gate record (`photometry_run_gates`): one `passed`, `failed` or `not_checked` result per run-level check. The photometry README lists every gate.

## Two rules, and which one the margin describes

Processing has two ways to pick variable-star candidates (see `processing/README.md`, "Deciding which stars are variable"):

- **The candidate rule** (used whenever the field has at least 20 usable stars). It fits a noise model, the field's measured curve of how much a constant star of each brightness scatters, computes three variability indices per star, and combines them into a variability score. A star is a candidate when its score is above 1 and its false-discovery q-value is below 0.01.
- **The CV cutoff** (the fallback for smaller fields). The coefficient of variation (CV) is a light curve's standard deviation divided by its mean. A star is a candidate when its CV is above the field's median CV plus 7.4 times the MAD of the field's CVs (and above 0.02 in any case). The MAD (median absolute deviation) is the median distance of the values from their median, a measure of spread that a few extreme stars cannot inflate.

The confidence record built here always measures the star against the CV cutoff. It therefore says how close the call was only when the CV cutoff made the call, that is, in a field too small for a noise model. In a normal field the candidate rule decides, and a candidate can sit below the CV cutoff (a negative margin) or far above it. For those fields, the star's own `variabilityScore` (how far above 1 it is) and `falseDiscoveryQValue` describe how strong the actual decision was, and they are the numbers to rank candidates by.

## The quality metrics

The report built at this step contains the following values, one set per star:

- **Coefficient of variation** (CV). How much this star's own brightness bounces around relative to its average (standard deviation over mean).
- **Adaptive cutoff.** The field's CV cutoff for this session: the median CV of the field's stars plus 7.4 times the MAD of their CVs, and never below 0.02. 7.4 is `DEFAULT_VARIABILITY_SIGMA_THRESHOLD` (measured on the library; see [PHO-10](../../../../documentation/library_design/validation/photometry.md#pho-10)).
- **Margin** (`margin_in_mad_units`, no unit). `(CV - cutoff) / MAD`, where MAD is the field's median absolute deviation of the CV (never taken below 0.0001). A large positive value means the CV is far above the cutoff; a value near zero, on either side, means the CV sits close to it.
- **Low confidence flag.** Set when the margin is within 1 MAD of the cutoff on either side, that is, `|margin| < LOW_CONFIDENCE_MARGIN` (1.0). The cutoff sits at least 7.4 MAD above the field median, so 1 MAD is a small step next to it, and a star that close is one noisy measurement away from the other side. The value 1.0 is a design estimate.
- **Trustworthy flag.** Set when the low confidence flag is not set.

A large margin means the CV-cutoff verdict is unlikely to change if the data were measured again. A small margin, positive or negative, means another night's data could easily flip it. In a field small enough to use the CV cutoff, a reviewer can use these values to separate strong candidates from borderline calls. In a normal field, read them as a second view of the star's scatter, not as the confidence of its candidate status.

## Checking that the score can see variables

`variability_skill.py` asks, once per run, whether the variability score ranks the stars the catalogs list as variable above the stars they do not list. It needs at least 10 catalogued variables and 30 unlisted stars with a score. The measure is the AUC (area under the ROC curve, the receiver operating characteristic curve, which plots the share of true variables caught against the share of other stars wrongly caught as the score's threshold is lowered): the chance that a random catalogued variable has a higher score than a random unlisted star. 0.5 is chance and 1 is a perfect ranking.

Settings: the `variability_discrimination` gate fails when the AUC is below 0.7, a design choice, or below the level that beats chance at the 5% level for the group sizes, whichever is higher. At the smallest allowed group sizes the chance level is 0.68, so the floor of 0.7 decides in every case.

The same file gives the smallest sinusoid (a smooth, regular rise and fall) the candidate rule can flag, for the `detectable_amplitude` gate. The smallest amplitude is `2 sqrt(2) x sqrt(1.5^2 - 1) x` the noise model's scatter for a star of median brightness, in magnitudes peak to peak: a sinusoid of half-height `a` adds a scatter of `a / sqrt(2)`, and the candidate rule needs the total scatter to reach 1.5 times the noise model's. Without a noise model it gives `2 sqrt(2) x 1.0857 x` the CV cutoff, the sinusoid whose own scatter equals the cutoff (1.0857 is 2.5 / ln 10, which turns a fractional scatter into magnitudes). The `detectable_amplitude` gate fails when this estimate is above 0.3 mag, a design limit; it is not a measured detection threshold (the measured one is in [PHO-09](../../../../documentation/library_design/validation/photometry.md#pho-09)).

For exact thresholds and edge cases, read the code.
