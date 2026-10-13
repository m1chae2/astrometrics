# Post-processing: what should change?

`recommend_for_sky.py` reads the coverage and the comparisons and says what, if anything, to change about where the telescope observes. The result is a list of `Recommendation` records, most serious first, with the same fields as the guiding recommendations. Confidence is never `high`: the data is observational, and altitude is linked to time of night.

## Rules

Applied in this order:

1. Fewer than five nights with a known position: say so and stop.
2. Each part of the sky that is measurably worse than the rest of the same nights: advise, naming the metric and how much worse it is. For altitude the message adds that a poor band may be a poor hour.
3. Star width poor in the low altitude bands, and the suggested minimum altitude above the configured one: advise raising the minimum altitude.
4. Parts of the sky reached on fewer than three nights, and dimensions along which no comparison was possible: say they were not judged, and say how to get the comparison (the same target early and late in one night, or on both sides of the meridian).
5. Parts were judged and none is poor: say so.

For exact behavior, read the code. The code is the source of truth.
