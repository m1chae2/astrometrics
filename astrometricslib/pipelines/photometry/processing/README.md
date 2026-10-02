# Processing

This step takes the raw, per-picture brightness measurements from pre-processing and turns them into a decision: is this star variable, and does its brightness show a repeating pattern?

## Correcting for conditions that are not about the star

A star's raw brightness changes a little from picture to picture even when the star itself is not changing, because of clouds, changing sky darkness, or small tracking errors. To correct for this, the code:

1. Picks a group of comparison stars in the same field that are bright, rarely saturated, and measured in almost every picture of the session.
2. For each picture, finds the typical brightness of that comparison group and uses it to correct every star's measurement in that picture. It drops entirely any picture whose comparison group looks like a clear outlier compared to every other picture, since that likely reflects a passing cloud or a similar problem rather than real star behavior.
3. Removes the effect of airmass on each star's corrected brightness, since stars appear dimmer near the horizon for reasons that have nothing to do with their own variability.

## Deciding which stars are variable

Once the code corrects every star's brightness, it measures how much each star's brightness bounces around relative to its average, a standard way of comparing "noisiness" between stars of different brightness. It then compares each star's own noisiness against the typical noisiness of the whole field. The code flags a star that is noisier than the field by a wide enough margin as a possible variable star. This comparison is relative to the field's own conditions on the night, rather than to one fixed number, so noisier nights need a correspondingly bigger difference before the code flags a star.

## Looking for repeating patterns

For a star with enough brightness measurements, the code also searches for a period: a repeating pattern in brightness over time. It searches for two different kinds of pattern:

- A smooth, repeating rise and fall in brightness, which can indicate a pulsating star or one star orbiting another.
- A brief, box-shaped dip in brightness that repeats on a regular schedule, which can indicate a transiting planet or an eclipsing binary star.

Both searches report a verdict on whether the pattern found actually stands out from noise, since chance alone can always produce some repeating pattern in any data set.

## Long-term variability

When a target has more than one observing session, the code can combine brightness histories from different sessions into one longer record for the same star. It can then run the same variability check described above on that combined record, which can reveal a variable star that changes too slowly to notice within a single session.
