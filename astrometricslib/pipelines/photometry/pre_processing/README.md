# Pre-processing

This step turns a raw picture into one brightness measurement per star, and checks how good that raw measurement was. It does not decide whether a star is variable; it only produces the numbers that later steps depend on.

## Measuring a picture

For each picture in an observing session, the code:

1. Reads the picture's exposure time and airmass from its header. Exposure time is how long the camera's shutter was open; airmass is a measure of how much atmosphere the light passed through, which is lowest when a star is straight overhead and highest near the horizon.
2. Finds out how far this picture has drifted compared to the first picture in the session, using a handful of bright stars as reference points. Telescopes drift a little between pictures even while tracking a target, so this step lines every picture up with the first one.
3. For every star being tracked, measures the amount of light inside a small circle centered on the star, and subtracts the background sky brightness measured in a ring just outside that circle. This gives the star's raw brightness in that one picture.
4. Divides that raw brightness by the exposure time, so a short exposure and a long exposure of the same star can be compared fairly.

Every star ends up with a brightness value, a timestamp, and a flag saying whether the measurement was saturated (too bright to measure accurately) for each picture in the session.

## Judging the raw data

Once a star's full set of measurements for a session is ready, this step also builds a report on how good those raw measurements were:

- How many of the session's pictures actually gave a usable measurement for this star, out of how many were possible.
- How often this star's measurement was saturated.
- How far the picture-to-picture drift got, at its worst, across every picture this star was measured in.

From these numbers, the report also flags two possible problems: coverage that is too low to trust, and drift large enough that the telescope's tracking, rather than the star's own brightness, is the more likely explanation for anything unusual in the data. This report travels with the star's brightness history into the later processing and post-processing steps.

## The quality metrics

The report built at this step contains the following values, one set per star:

- **Frames measured.** How many pictures in the session actually produced a usable brightness measurement for this star.
- **Frames available.** How many pictures were in the session in total, whether or not this star was measured in each one.
- **Coverage fraction.** Frames measured divided by frames available. A low value means the star's brightness history has a lot of gaps.
- **Saturated fraction.** The share of this star's measurements that were too bright to measure accurately.
- **Maximum registration drift.** The largest picture-to-picture drift, in pixels, seen across every picture this star was measured in. A large value points to a tracking problem rather than a change in the star's own brightness.
- **Low coverage flag.** Set when the coverage fraction is too low to trust.
- **Tracking unstable flag.** Set when the maximum registration drift is large enough that tracking, not the star, is the likely explanation for anything unusual in the data.

A scientist reviewing a star's results can use these values to tell a thin, gap-filled, or poorly tracked brightness history apart from a solid one, before trusting whatever the later steps conclude about that star.
