# Post-processing

This step judges how much to trust a star's variability result. It runs after processing has already decided whether a star's brightness varies more than the rest of the field.

## Why a plain yes-or-no answer is not enough

The processing step's variability decision is a single yes-or-no answer: a star's brightness either varies more than the field's typical noise level, or it does not. That answer does not say how close the call was. Processing marks both a star that clears the bar by a wide margin and a star that barely clears it as "variable," even though the second case is much more likely to be a false alarm caused by ordinary measurement noise.

## Building a confidence score

To tell those two cases apart, this step compares a star's own brightness variation directly against the field's typical noise level and expresses the distance between them as a single number: how many steps away from the boundary the star's result sits, using the same units the boundary itself was set with. A large distance means the result is unlikely to change if the data were measured again; a small distance, positive or negative, means the result is close enough to the boundary that another night's data could easily flip it either way.

Based on that distance, this step labels each star's variability result as low confidence when it sits too close to the boundary, and trustworthy otherwise. This label travels with the star's result, so a scientist reviewing a list of variable-star candidates can immediately see which ones are strong candidates and which ones are borderline calls worth a second look before being taken seriously.

## The quality metrics

The report built at this step contains the following values, one set per star:

- **Coefficient of variation.** How much this star's own brightness bounces around relative to its average, the same value processing used to decide whether the star looked variable in the first place.
- **Adaptive cutoff.** The typical noisiness of the whole field on the night this star was measured, which the star's own coefficient of variation was compared against.
- **Margin.** How far the star's coefficient of variation sits from the cutoff, expressed as a distance rather than a plain yes-or-no answer. A large positive value is a confident variable-star call; a value near zero, on either side of the cutoff, is a borderline one.
- **Low confidence flag.** Set when the margin is small enough that the variability call could easily flip with another night's data.
- **Trustworthy flag.** Set when the low confidence flag is not set.

These values stay a statement about the coefficient of variation against the field's CV cutoff. The candidate rule in `processing/` uses the noise model and three indices (see `processing/README.md`), so a candidate can have a negative margin here. The star's own `variabilityScore` (above 1 for a candidate) is the number to rank candidates by.

A scientist reviewing a list of flagged variable stars can use these values to separate strong candidates from borderline calls, rather than treating every flagged star as equally certain.

## Checking that the score can see variables

`variability_skill.py` asks, once per run, whether the variability score ranks the stars the catalogs list as variable above the stars they do not list. It needs at least 10 catalogued variables and 30 unlisted stars with a score. The measure is the AUC: the chance that a random catalogued variable has a higher score than a random unlisted star. 0.5 is chance. The `variability_discrimination` gate fails when the AUC is below 0.7, a design choice, or below the level that beats chance at the 5% level for the group sizes, whichever is higher. At the smallest allowed group sizes the chance level is 0.68, so the floor of 0.7 decides in every case.

The same file gives the smallest sinusoid the candidate rule can flag, for the `detectable_amplitude` gate: `2 sqrt(2) x sqrt(1.5^2 - 1) x` the noise model's scatter for a star of median brightness, in magnitudes peak to peak. Without a noise model it gives `2 sqrt(2) x 1.0857 x` the CV cutoff.

For exact thresholds and edge cases, read the code.
