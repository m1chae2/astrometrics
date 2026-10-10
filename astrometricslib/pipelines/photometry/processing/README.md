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

When a target has more than one observing session, the code combines the brightness histories of each star from the different sessions into one longer record. It then measures how much the star's brightness differs from one session to another. This can reveal a variable star that changes too slowly to notice within a single session.

The measurement works on each session's median normalized flux (the star's flux divided by the comparison group's median flux in the same picture, so it has no units). The combined record keeps each session's level as measured and does not rescale one session to match another. Rescaling would remove the very change this search looks for.

For each star with at least two sessions of at least three usable points each, the code:

1. Takes the highest and the lowest session median.
2. Converts their ratio to an amplitude in magnitudes: `2.5 log10(highest / lowest)`.
3. Divides the difference of the two medians by their combined expected error. The error of one session's median is `1.2533 x scatter / sqrt(points)`, where `scatter` is the session's within-session scatter (1.4826 times the median absolute deviation of its normalized flux). The result is the significance, in multiples of that error.
4. Flags the star when the significance is above 3 and the amplitude is at least 0.02 mag.

The code stores the amplitude and the significance on the star's light curve (`betweenSessionAmplitudeMag` and `betweenSessionSignificance`). It leaves the star's coefficient of variation unchanged, so that number stays a within-session scatter.

Each session picks its own comparison group, so two sessions can disagree about a star's level because the groups differ and not because the star changed. The code keeps a summary of each session on the light curve (`sessionSummaries`): the session id, the number of usable points, the star's median normalized flux and its scatter, the typical number of comparison stars per picture, and the median of the comparison group's flux. A reader can compare these across stars. A shift that every star in the field shares points to the comparison group, not to the star.
