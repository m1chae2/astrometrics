# Pre-processing

This step turns a raw picture into one brightness measurement per star, and checks how good that raw measurement was. It does not decide whether a star is variable; it only produces the numbers that later steps depend on.

## Measuring a picture

For each picture in an observing session, the code:

1. Reads the picture's exposure time, airmass, and capture time from its header. Exposure time is how long the camera's shutter was open. Airmass is a measure of how much atmosphere the light passed through, which is lowest when a star is straight overhead and highest near the horizon. The capture time comes from the `DATE-OBS` card (see "Capture times" below).
2. Finds out how far this picture has drifted compared to the first picture in the session, using a handful of bright stars as reference points. Telescopes drift a little between pictures even while tracking a target, so this step lines every picture up with the first one. Only pixels well above the sky noise count as star light in this step, so noise in the sky does not pull the measured drift toward zero.
3. For every star being tracked, starts at the star's position in the first picture plus the picture's overall drift. It then re-centers the star on its own brightness-weighted centroid (the average of the pixel positions, weighted by how bright each pixel is) in a small box. This corrects for the star's own small offset from the overall drift (see "Aperture placement" below).
4. Measures the amount of light inside a small circle centered on that position, and subtracts the background sky brightness measured in a ring just outside the circle. This gives the star's raw brightness in that one picture.
5. Divides that raw brightness by the exposure time, so a short exposure and a long exposure of the same star can be compared fairly.

Every star ends up with a brightness value, a timestamp, and a flag saying whether the measurement was saturated (too bright to measure accurately) for each picture in the session.

## Aperture placement

The circle (the aperture) and the background ring sit at the star's exact position, including the fractional part of a pixel. The code does not round the position to a whole pixel. A circle that is half a pixel off a star loses 1 to 2 percent of the star's light for a typical star width. That loss changes as the picture drifts, and it is the same size as the brightness changes the pipeline searches for.

The re-centering box is 11 pixels wide, about 2.5 times the width of the typical star the default circle (radius 4 pixels) is sized for. The code subtracts the local sky level (the median of a 31 by 31 pixel cutout) and sets negative pixels to zero before it averages the positions. It repeats this three times, each time centering the box on the previous result.

The code refuses the re-centered position and keeps the shifted position from the first picture when any of these hold:

- The box contains a saturated pixel. A saturated core is flat, so its centroid is not reliable.
- The box has no light above the sky level, or it reaches past the edge of the picture.
- The centroid is more than 1.5 pixels from the shifted position. A move that large means the box found a neighbor or noise instead of the star.

Each measurement keeps the reason for a refusal in `StarPosition.fallback_reason` (`None` means the centroid was accepted). `VariabilityAnalyzer.centroid_fallback_counts` totals the refusals per star over the session. Nothing downstream reads these counts yet: the quality report does not use them, so a star with many refusals appears only in the analyzer's data. A star near the 1.5 pixel limit in many pictures is measured with a circle that may be off-center by up to that amount.

The re-centering box has a fixed size. It assumes a star width near 3 to 4 pixels (FWHM, full width at half maximum). For wider stars the box cuts off more of the star's wings and the centroid shifts toward the box center by a few hundredths of a pixel.

## Capture times

The capture time of each picture comes from its `DATE-OBS` card, read with astropy's `Time` class as a UTC date and time (for example `2026-05-24T04:58:30.570`). The code stores it without a time zone.

A picture is rejected, and left out of every light curve, when `DATE-OBS` is missing, is not a date and time, or has a date but no time of day. The code never substitutes the current time. The rejection reason is kept in `VariabilityAnalyzer.frames_without_usable_date_obs`. The photometry runner reads that list and copies each reason into the `capture_timestamps` gate and into the list of excluded frames in the quality summary. If the first picture of a session is the one rejected, the session produces no light curves, and the session's empty-session reason says so.

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
