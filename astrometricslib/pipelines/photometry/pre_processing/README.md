# Pre-processing

This step turns a raw picture into one brightness measurement per star, and checks how good that raw measurement was. It does not decide whether a star is variable; it only produces the numbers that later steps depend on.

## Measuring a picture

For each picture in an observing session, the code:

1. Reads the picture's exposure time, airmass, and capture time from its header. Exposure time is how long the camera's shutter was open. Airmass is a measure of how much atmosphere the light passed through, which is lowest when a star is straight overhead and highest near the horizon. The capture time comes from the `DATE-OBS` card (see "Capture times" below).
2. Finds out how far this picture has drifted compared to the first picture in the session, using up to 50 bright stars as reference points (the alignment anchors, see "Alignment anchors" below). Telescopes drift a little between pictures even while tracking a target, so this step lines every picture up with the first one. Only pixels well above the sky noise count as star light in this step, so noise in the sky does not pull the measured drift toward zero.
3. For every star being tracked, starts at the star's position in the first picture plus the picture's overall drift. It then re-centers the star on its own brightness-weighted centroid (the average of the pixel positions, weighted by how bright each pixel is) in a small box. This corrects for the star's own small offset from the overall drift (see "Aperture placement" below).
4. Measures the amount of light inside a small circle centered on that position, and subtracts the background sky brightness measured in a ring just outside the circle. This gives the star's raw brightness in that one picture. The same step computes the brightness's 1-sigma uncertainty (see "Flux uncertainties" below).
5. Divides that raw brightness and its uncertainty by the exposure time, so a short exposure and a long exposure of the same star can be compared fairly. Both are in ADU per second (ADU is analog-to-digital unit, one step of the stored pixel value).

Every star ends up with a brightness value, its uncertainty, a timestamp, and a flag saying whether the measurement was saturated (too bright to measure accurately) for each picture in the session. The worker also returns the exposure time it used, which the time conversion needs.

## Alignment anchors

The anchors are the stars whose shifts give each picture's overall drift (the median of their shifts). `select_alignment_anchors` in `frame_photometry.py` chooses them once per session, from the stars found in the first picture:

1. Drop every detection with a saturated pixel within 3 pixels of its center.
2. Rank the rest by total brightness, brightest first.
3. Keep the detections whose peak is at least 20 times the sky noise. The sky noise per pixel comes from `estimate_pixel_noise`: the spread of the differences between pixels three columns apart, so a smooth sky gradient does not inflate it.
4. Skip the brightest 5 percent of those, which are the most likely to be nearly saturated, and take the next 50.
5. If fewer than 10 detections qualify, use the detections ranked 50 to 100 by brightness instead. That is the older rule. In a sparse field those ranks are mostly noise peaks, so this fallback is a last resort.

`VariabilityAnalyzer.alignment_anchor_rule` and `alignment_anchor_count` record which rule a session used and how many anchors it had. The `registration_drift` gate names the older rule in its detail when a session used it. The limits (20 times the noise, 5 percent, 50 stars, 10 stars) are design estimates, not measured values.

A synthetic sparse field of 12 real stars on a noisy sky has more than 50 detections, almost all noise peaks. The older rule measured a shift of (-4.7, 2.1) pixels for a true shift of (2.7, -1.3) pixels. The new rule keeps the 12 stars and measures (2.70, -1.30) pixels (`test/pre_processing/test_frame_photometry.py`).

## Aperture placement

The circle (the aperture) and the background ring sit at the star's exact position, including the fractional part of a pixel. The code does not round the position to a whole pixel. A circle that is half a pixel off a star loses 1 to 2 percent of the star's light for a typical star width. That loss changes as the picture drifts, and it is the same size as the brightness changes the pipeline searches for.

The re-centering box is 11 pixels wide, about 2.5 times the width of the typical star the default circle (radius 4 pixels) is sized for. The code subtracts the local sky level (the median of a 31 by 31 pixel cutout) and sets negative pixels to zero before it averages the positions. It repeats this three times, each time centering the box on the previous result.

The code refuses the re-centered position and keeps the shifted position from the first picture when any of these hold:

- The box contains a saturated pixel. A saturated core is flat, so its centroid is not reliable.
- The box has no light above the sky level, or it reaches past the edge of the picture.
- The centroid is more than 1.5 pixels from the shifted position. A move that large means the box found a neighbor or noise instead of the star.

Each measurement keeps the reason for a refusal in `StarPosition.fallback_reason` (`None` means the centroid was accepted). `VariabilityAnalyzer.centroid_fallback_counts` totals the refusals per star over the session. A star near the 1.5 pixel limit in many pictures is measured with a circle that may be off-center by up to that amount.

### Per-star centroid offsets

The overall drift moves every star by the same amount. If the field also rotates or changes scale, each star is displaced by a different amount, and its own centroid ends up away from the shifted position. `centroid_offsets_px` measures that distance, in pixels, for every star whose centroid was accepted. `VariabilityAnalyzer` keeps the offsets per picture (`centroid_offsets_by_frame`) and `centroid_shift_summary()` reduces them to a `CentroidShiftSummary` for the session:

- **Median and 95th-percentile offset.** A pure translation gives offsets near zero (below 0.1 pixel on noisy synthetic pictures). A rotation gives offsets that grow with distance from the rotation center: a rotation of 0.05 degrees per picture moves a star 197 pixels from the center by 1.2 pixels after seven pictures. A 95th percentile well above the median points to rotation or scale change.
- **Fallback fraction.** The share of star measurements, over all pictures, that kept the shifted position because the centroid was refused. A refused star has no offset, so a large fraction also means the offsets underestimate the problem.
- **Worst picture.** The picture with the largest 95th-percentile offset, and that value.

The `registration_drift` gate reads the summary (see the photometry README). The summary is not copied into the quality metrics. It appears only in the gate's detail.

The re-centering box has a fixed size. It assumes a star width near 3 to 4 pixels (FWHM, full width at half maximum). For wider stars the box cuts off more of the star's wings and the centroid shifts toward the box center by a few hundredths of a pixel.

## Flux uncertainties

`frame_photometry.py` computes the 1-sigma uncertainty of each flux with the CCD equation (the noise budget of a camera sensor), in `aperture_flux_error_adu`. The equation works in electrons, because the random scatter of a count follows the number of electrons collected:

`variance = F + n_pix (S + RN^2 + D) + (n_pix^2 / n_sky) (S + RN^2)`

| Symbol | Meaning | Unit |
|---|---|---|
| `F` | The star's net counts (sky-subtracted aperture sum) times the gain | electrons |
| `S` | The sky level per pixel (median of the ring) times the gain | electrons per pixel |
| `RN` | Read noise | electrons per pixel |
| `D` | Dark current collected during the exposure | electrons per pixel |
| `n_pix` | Area of the circle, as the exact area, not a pixel count | pixels |
| `n_sky` | Number of pixels in the sky ring | pixels |

The first term is the star's own shot noise (the random scatter of a count). The second is the noise of the sky, read-out and dark counts under the circle. The third is the noise in the sky level itself, which the ring measures from a limited number of pixels. The code takes the square root, divides by the gain to get ADU, and divides by the exposure time to get ADU per second. The dark current is zero, because no source records it. The equation assumes the picture has had its bias level removed; a picture with the bias still in it has an overestimated sky term. A star too near the frame edge to measure has a flux of 0 and an error of 0.

The error does not change the flux. `measure_aperture_photometry` returns both, and `_measure_aperture_flux` keeps returning only the flux and the saturation flag.

`detector_noise.py` picks the gain (electrons per ADU) and read noise (electrons):

1. The camera's profile: `gain_e_per_adu` and `read_noise_e` in `models/camera_profile.py`. Each carries its source (datasheet, measured or assumed). The values depend on the camera's gain setting, so they are exact only for pictures taken at the setting they were measured at.
2. The picture's `EGAIN` card (electrons per ADU) and `RDNOISE` card (electrons).
3. An assumption: 1 electron per ADU and no read noise.

The `GAIN` card is never read as electrons per ADU. The ZWO cameras write a gain setting there (the sample pictures say `GAIN = 0.0`). `DetectorNoise.gain_is_assumed` and `read_noise_is_assumed` record which numbers were assumed. They reach the light curve as `errorsAssumeUnitGain` and `errorsAssumeZeroReadNoise` and the `flux_uncertainty` gate. With an assumed gain the errors have the right dependence on brightness, but the wrong size by a factor near the square root of the true gain.

## Capture times

The capture time of each picture comes from its `DATE-OBS` card, read with astropy's `Time` class as a UTC date and time. The code stores it without a time zone. This is the moment the shutter opened.

INDI and Ekos write `DATE-OBS` as an ISO 8601 date and time in UTC with no suffix, for example `2026-05-24T04:58:30.570`. The sample pictures have this form. `parse_observation_time` accepts these forms:

- A date and time with no suffix, read as UTC (the INDI and Ekos form).
- A date and time with a trailing `Z` or `+00:00`, read as UTC.
- A date and time with a UTC offset such as `2026-05-24T06:58:30.570+02:00`, `+0200`, `+02` or `-05:30`. The code subtracts the offset and stores the UTC instant (`04:58:30.570` in the example). Offsets beyond 14 hours or with minutes above 59 are refused.

A picture is rejected, and left out of every light curve, when `DATE-OBS` is missing, is not a date and time, or has a date but no time of day. A date alone is refused even with an offset, because every picture of a night would get the same midnight time. The code never substitutes the current time. The rejection reason is kept in `VariabilityAnalyzer.frames_without_usable_date_obs`. The photometry runner reads that list and copies each reason into the `capture_timestamps` gate and into the list of excluded frames in the quality summary. If the first picture of a session is the one rejected, the session produces no light curves, and the session's empty-session reason says so.

`observation_times.py` converts the exposure starts of a session to mid-exposure BJD_TDB values, in days (see "Times" in the photometry README for the reasons). For each picture, it adds half the exposure time to the capture time, converts the result from UTC to TDB (a uniform time scale), and adds the light-travel time to the center of mass of the solar system for the target's direction. The light-travel time is between -499 s and +499 s. The conversion uses the observatory's position when the configuration has one, and Earth's center otherwise. It uses astropy's built-in planet positions, which need no download and are good to about 2 ms. The module's three time-basis sentences name what was done; the `capture_timestamps` gate quotes them.

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
