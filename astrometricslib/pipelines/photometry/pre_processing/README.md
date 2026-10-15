# Pre-processing

This step turns a raw picture into one brightness measurement per star, and checks how good that raw measurement was. It does not decide whether a star is variable; it only produces the numbers that later steps depend on.

Terms are defined in the [glossary](../../../../documentation/library_design/Astrometrics_Library_Architecture.md#appendix-b-glossary). This README also defines each term the first time it appears.

In plain words: the step lines up every picture of a session with the first one, puts a small circle on each star, adds up the light inside it, and subtracts the sky. The result for each star is one point of its light curve (its brightness against time) per picture, with an error bar and a time stamp. The hard parts are keeping the circle exactly on the star as the telescope drifts, and getting honest error bars and times.

## Measuring a picture

For each picture in an observing session, the code:

1. Reads the picture's exposure time, airmass, and capture time from its header. Exposure time is how long the camera's shutter was open. Airmass is a measure of how much atmosphere the light passed through, which is lowest when a star is straight overhead and highest near the horizon. The capture time comes from the `DATE-OBS` card (see "Capture times" below).
2. Finds out how this picture sits against the first picture in the session: how far it shifted, and how much it rotated and changed scale. It uses up to 50 bright stars as reference points (the alignment anchors, see "Alignment anchors" and "Frame alignment" below). Telescopes drift a little between pictures even while tracking a target, and a field can turn slightly during a night, so this step lines every picture up with the first one. Only pixels well above the sky noise count as star light in this step, so noise in the sky does not pull the measured drift toward zero.
3. For every star being tracked, starts at the star's position in the first picture moved by the picture's alignment. It then re-centers the star on its own brightness-weighted centroid (the average of the pixel positions, weighted by how bright each pixel is) in a small box. This corrects for the star's own small offset from the aligned position (see "Aperture placement" below).
4. Measures the amount of light inside a small circle centered on that position (the aperture), and subtracts the background sky brightness measured in a ring just outside the circle. This gives the star's raw brightness (its flux) in that one picture. The same step computes the brightness's 1-sigma uncertainty, one standard deviation of the random scatter expected from one measurement to the next (see "Flux uncertainties" below).
5. Divides that raw brightness and its uncertainty by the exposure time, so a short exposure and a long exposure of the same star can be compared fairly. Both are in ADU per second (ADU is analog-to-digital unit, one step of the stored pixel value).

Every star ends up with a brightness value, its uncertainty, a timestamp, and a flag saying whether the measurement was saturated (too bright to measure accurately) for each picture in the session. The worker also returns the exposure time it used, which the time conversion needs.

## Alignment anchors

The anchors are the stars whose positions give each picture's alignment (see "Frame alignment"). `select_alignment_anchors` in `frame_photometry.py` chooses them once per session, from the stars found in the first picture:

1. Drop every detection with a saturated pixel within 3 pixels of its center.
2. Rank the rest by total brightness, brightest first.
3. Keep the detections whose peak is at least 20 times the sky noise (the pixel-to-pixel scatter of the empty sky). The sky noise per pixel comes from `estimate_pixel_noise`: the spread of the differences between pixels three columns apart, divided by the square root of 2 (a difference of two noisy pixels is that much noisier than one pixel). A smooth sky gradient cancels in the difference, so it does not inflate the estimate. The spacing of three columns is a design choice; the code gives no further reason.
4. Skip the brightest 5 percent of those, which are the most likely to be nearly saturated, and take the next 50.
5. If fewer than 10 detections qualify, use the detections ranked 50 to 100 by brightness instead. That is the older rule. In a sparse field those ranks are mostly noise peaks, so this fallback is a last resort.

`VariabilityAnalyzer.alignment_anchor_rule` and `alignment_anchor_count` record which rule a session used and how many anchors it had. The `registration_drift` gate (the run-level check on picture alignment, see the photometry README) names the older rule in its detail when a session used it.

Settings: the limits (20 times the noise, 5 percent, 50 stars, 10 stars) are design estimates, not measured values.

How we know the new rule helps: a synthetic sparse field of 12 real stars on a noisy sky has more than 50 detections, almost all noise peaks. The older rule measured a shift of (-4.7, 2.1) pixels for a true shift of (2.7, -1.3) pixels. The new rule keeps the 12 stars and measures (2.70, -1.30) pixels (`test/pre_processing/test_frame_photometry.py`).

## Aperture placement

The circle (the aperture) and the background ring sit at the star's exact position, including the fractional part of a pixel. The code does not round the position to a whole pixel. A circle that is half a pixel off a star loses 1 to 2 percent of the star's light for a typical star width. That loss changes as the picture drifts, and it is the same size as the brightness changes the pipeline searches for.

The re-centering box (the small square in which the code computes the centroid) is 11 pixels wide, about 2.5 times the width of the typical star the default circle (radius 4 pixels) is sized for. The code subtracts the local sky level (the median of a 31 by 31 pixel cutout) and sets negative pixels to zero before it averages the positions. It repeats this three times, each time centering the box on the previous result.

The code refuses the re-centered position and keeps the aligned position (the star's position in the first picture, moved by the picture's alignment) when any of these hold:

- The box contains a saturated pixel. A saturated core is flat, so its centroid is not reliable.
- The box has no light above the sky level, or it reaches past the edge of the picture.
- The centroid is more than 1.5 pixels from the aligned position. A move that large means the box found a neighbor or noise instead of the star.

Each measurement keeps the reason for a refusal in `StarPosition.fallback_reason` (`None` means the centroid was accepted). `VariabilityAnalyzer.centroid_fallback_counts` totals the refusals per star over the session. A star near the 1.5 pixel limit in many pictures is measured with a circle that may be off-center by up to that amount.

### Frame alignment

`calculate_frame_alignment` fits three things to each picture: a shift, a rotation and a change of scale about the middle of the picture (`FrameAlignment`). It works in two steps:

1. Each anchor is found in a wide box (`locate_star_centroid`, which searches up to 40 pixels from the anchor's expected position) and the median shift is taken. The wide box is easily pulled off by a neighbor or a galaxy, with errors of several pixels on some stars in M 81, but the median ignores them.
2. Each anchor is re-centered in the small box (`refine_star_centroid`), starting where the alignment so far says it is.
   - *The fit.* A shift, a rotation and a scale change are then fitted to those accurate positions by least squares, leaving out anchors more than three scaled median absolute deviations from the fit. (The median absolute deviation, MAD, is the median distance of the values from their median; scaled by 1.4826 it estimates a standard deviation without being pulled by outliers.)
   - *Wrong stars.* The small box refuses a wrong star by itself, because it will not move more than 1.5 pixels from where the alignment so far puts the star.
   - *Second try.* When that leaves fewer than 10 anchors (a rotation not yet fitted can put the far ones out of reach), the step is tried again with a reach of 4 pixels in place of 1.5 (`ALIGNMENT_ANCHOR_REACH_PX`): an anchor's centroid may then sit up to 4 pixels from where the alignment so far puts it. A wide reach is only the second try, because it lets blends (two stars close enough to merge into one blob) in. On M 81 the corner displacement (defined under "Per-star centroid offsets" below) was 0.9 pixels with the usual 1.5 pixel reach and 1.3 to 1.6 pixels with a reach of 3 to 4 pixels, where an independent fit on 800 bright stars gave about 0.6.
   - *Repeats.* The step is repeated three times, because a better alignment puts more anchors inside the box.

When fewer than 5 anchors are found (`MINIMUM_ANCHORS_FOR_SHIFT`), the code fits nothing and treats the picture as not shifted at all, rather than guess a shift from too few stars. Only the shift is used when fewer than 10 anchors are found, or when they sit within 7 percent of the corner distance of one another (a cluster core cannot tell a rotation from a shift). The 7 percent, the 4 pixels and the three repeats are design estimates.

How we know the rotation matters: the reason for fitting a rotation is NGC 2403. On 2026-10-11 its field turned by 0.05 degrees over the night, which moved the corners 1.9 pixels in the worst picture (1.8 pixels at the 95th percentile over the pictures). A shift alone left the far stars outside the 1.5 pixel reach of the centroid box, and 12 percent of the measurements fell back to a position that was off the star. With the rotation fitted, 4 percent fell back. M 13 gave 0.2 pixels at the corner and M 81 gave 0.9, mostly the same small scale change in every picture. That scale change is not a motion of the field. It comes from comparing two ways of measuring a position: the first picture's star positions come from the star finder's centroids, and every later picture's come from the small re-centering box, and the two methods differ slightly.

### Per-star centroid offsets

`centroid_offsets_px` measures how far each star's own centroid sits from where the alignment says it should be, in pixels, for every star whose centroid was accepted. `VariabilityAnalyzer` keeps the offsets per picture (`centroid_offsets_by_frame`) and the alignment of each picture (`alignment_by_frame`). `centroid_shift_summary()` reduces them to a `CentroidShiftSummary` for the session:

The 95th percentile used below is the value that 95 percent of the measurements fall below.

- **Corner displacement.** For each picture, how far the fitted rotation and scale change move a corner of the picture (`FrameAlignment.corner_displacement_px`). The summary keeps the 95th percentile over the pictures, the worst picture and how many pictures had a rotation fitted. This is the number the `registration_drift` gate judges.
- **Median and 95th-percentile offset.** What is left after the alignment: centroid noise, which is larger for faint and crowded stars, and any distortion the fit does not describe. On 2026-10-11 the 95th percentile was 1.0 to 1.3 pixels on M 13, M 81 and NGC 2403, with a median of 0.3 to 0.5, and it did not change with the rotation. The gate reports these but does not judge them.
- **Fallback fraction.** The share of star measurements, over all pictures, that kept the aligned position because the centroid was refused. A refused star has no offset, so a large fraction also means the offsets underestimate the problem.
- **Worst picture.** The picture with the largest 95th-percentile offset, and that value.

The `registration_drift` gate reads the summary (see the photometry README). The summary is not copied into the quality metrics. It appears only in the gate's detail.

The re-centering box has a fixed size. It assumes a star width near 3 to 4 pixels (FWHM, full width at half maximum). For wider stars the box cuts off more of the star's wings and the centroid shifts toward the box center by a few hundredths of a pixel.

## Flux uncertainties

`frame_photometry.py` computes the 1-sigma uncertainty of each flux with the CCD equation (the noise budget of a camera sensor), in `aperture_flux_error_adu`. The equation adds up the variances (errors squared) of the independent noise sources: the star's own light, the sky, and the camera electronics. It works in electrons, because the random scatter of a count follows the number of electrons collected:

`variance = F + n_pix (S + RN^2 + D) + (n_pix^2 / n_sky) (S + RN^2)`

| Symbol | Meaning | Unit |
|---|---|---|
| `F` | The star's net counts (sky-subtracted aperture sum) times the gain (electrons per ADU) | electrons |
| `S` | The sky level per pixel (median of the ring) times the gain | electrons per pixel |
| `RN` | Read noise (the scatter the electronics add each time a pixel is read) | electrons per pixel |
| `D` | Dark current collected during the exposure (signal the sensor makes by itself from heat) | electrons per pixel |
| `n_pix` | Area of the circle, as the exact area, not a pixel count | pixels |
| `n_sky` | Number of pixels in the sky ring | pixels |

The first term is the star's own shot noise (the random scatter of a count). The second is the noise of the sky, read-out and dark counts under the circle. The third is the noise in the sky level itself, which the ring measures from a limited number of pixels. The code takes the square root, divides by the gain to get ADU, and divides by the exposure time to get ADU per second.

Assumptions: the dark current is zero, because no source records it. The equation assumes the picture has had its bias level (the fixed offset the camera adds to every pixel) removed; a picture with the bias still in it has an overestimated sky term. A star too near the frame edge to measure has a flux of 0 and an error of 0.

The error does not change the flux. `measure_aperture_photometry` returns both, and `_measure_aperture_flux` keeps returning only the flux and the saturation flag.

`detector_noise.py` picks the gain (electrons per ADU) and read noise (electrons):

1. The camera's profile: `gain_e_per_adu` and `read_noise_e` in `models/camera_profile.py`. Each carries its source (datasheet, measured or assumed). The values depend on the camera's gain setting, so they are exact only for pictures taken at the setting they were measured at.
2. The picture's `EGAIN` card (electrons per ADU) and `RDNOISE` card (electrons).
3. An assumption: 1 electron per ADU and no read noise.

The `GAIN` card is never read as electrons per ADU. The ZWO cameras write a gain setting there (the sample pictures say `GAIN = 0.0`). `DetectorNoise.gain_is_assumed` and `read_noise_is_assumed` record which numbers were assumed. They reach the light curve as `errorsAssumeUnitGain` and `errorsAssumeZeroReadNoise` and the `flux_uncertainty` gate. With an assumed gain the errors have the right dependence on brightness, but the wrong size by a factor near the square root of the true gain.

## Capture times

The capture time of each picture comes from its `DATE-OBS` card, read with astropy's `Time` class as a UTC date and time. The code stores it without a time zone. This is the moment the shutter opened.

INDI and Ekos (the observatory-control software that takes the pictures) write `DATE-OBS` as an ISO 8601 (the international standard) date and time in UTC with no suffix, for example `2026-05-24T04:58:30.570`. The sample pictures have this form. `parse_observation_time` accepts these forms:

- A date and time with no suffix, read as UTC (the INDI and Ekos form).
- A date and time with a trailing `Z` or `+00:00`, read as UTC.
- A date and time with a UTC offset such as `2026-05-24T06:58:30.570+02:00`, `+0200`, `+02` or `-05:30`. The code subtracts the offset and stores the UTC instant (`04:58:30.570` in the example). Offsets beyond 14 hours or with minutes above 59 are refused.

A picture is rejected, and left out of every light curve, when `DATE-OBS` is missing, is not a date and time, or has a date but no time of day. A date alone is refused even with an offset, because every picture of a night would get the same midnight time. The code never substitutes the current time.

The rejection reason is kept in `VariabilityAnalyzer.frames_without_usable_date_obs`. The photometry runner reads that list and copies each reason into the `capture_timestamps` gate and into the list of excluded frames in the quality summary. If the first picture of a session is the one rejected, the session produces no light curves, and the session's empty-session reason says so.

`observation_times.py` converts the exposure starts of a session to mid-exposure BJD_TDB values, in days (see "Times" in the photometry README for the reasons). BJD_TDB is the time the light would have reached the center of mass of the solar system, so the time of an event does not depend on where Earth is in its orbit. For each picture, it adds half the exposure time to the capture time, converts the result from UTC to TDB (a uniform time scale), and adds the light-travel time to the center of mass of the solar system for the target's direction. The light-travel time is between -499 s and +499 s. The conversion uses the observatory's position when the configuration has one, and Earth's center otherwise. It uses astropy's built-in planet positions, which need no download and are good to about 2 ms. The module's three time-basis sentences name what was done; the `capture_timestamps` gate quotes them.

## Judging the raw data

Once a star's full set of measurements for a session is ready, `assess_input_quality.py` builds a report on how good those raw measurements were:

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
- **Low coverage flag.** Set when the coverage fraction is below 0.5 (`LOW_COVERAGE_THRESHOLD`), so the star was measured in fewer than half of the session's pictures.
- **Tracking unstable flag.** Set when the maximum registration drift is above 20 pixels (`UNSTABLE_TRACKING_DRIFT_PX`), so tracking, not the star, is the likely explanation for anything unusual in the data. The wide centroid search box reaches 40 pixels from a star's expected position, so 20 pixels flags the problem well before stars start to fall outside it. The `registration_drift` gate uses the same limit.

A scientist reviewing a star's results can use these values to tell a thin, gap-filled, or poorly tracked brightness history apart from a solid one, before trusting whatever the later steps conclude about that star. Both limits are design estimates.

For exact behavior, read the code.
