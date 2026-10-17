# Photometry pre-processing

This stage turns a raw frame into one brightness measurement per star, and checks how good that raw measurement was. It does not decide whether a star is variable; it only produces the numbers that later steps depend on.

Terms are defined in the [glossary](../../../../documentation/Glossary.md). This document also defines each term the first time it appears.

## What this stage does, in order

In plain words, the stage lines up every frame of a session with the first one, puts a small circle on each star, adds up the light inside it, and subtracts the sky. The result for each star is one point of its light curve (its brightness against time) per frame, with an error bar and a time stamp. The hard parts are keeping the circle exactly on the star as the telescope drifts, and getting honest error bars and times.

For each frame in a session, the code:

1. Reads the frame's exposure time, airmass, and capture time from its header. Exposure time is how long the camera's shutter was open. Airmass is a measure of how much atmosphere the light passed through, which is lowest when a star is straight overhead and highest near the horizon. The capture time comes from the `DATE-OBS` card (see [Capture times](#capture-times)).
2. Finds out how this frame sits against the first frame in the session: how far it shifted, and how much it rotated and changed scale. It uses up to 50 bright stars as reference points (the alignment anchors; see [Alignment anchors](#alignment-anchors) and [Frame alignment](#frame-alignment)). Telescopes drift a little between frames even while tracking a target, and a field can turn slightly during a session, so this step lines every frame up with the first one. Only pixels well above the sky noise count as star light in this step, so noise in the sky does not pull the measured drift toward zero.
3. For every star being tracked, starts at the star's position in the first frame moved by the frame's alignment. It then re-centers the star on its own brightness-weighted centroid (the average of the pixel positions, weighted by how bright each pixel is) in a small box. This corrects for the star's own small offset from the aligned position (see [Aperture placement](#aperture-placement)).
4. Measures the amount of light inside a small circle centered on that position (the aperture), and subtracts the background sky brightness measured in a ring just outside the circle. This gives the star's raw brightness (its flux) in that one frame. The same step computes the brightness's 1σ uncertainty, one standard deviation of the random scatter expected from one measurement to the next (see [Flux uncertainties](#flux-uncertainties)).
5. Divides that raw brightness and its uncertainty by the exposure time, so a short exposure and a long exposure of the same star can be compared fairly. Both are in ADU per second (ADU is analog-to-digital unit, one step of the stored pixel value).

Every star ends up with a brightness value, its uncertainty, a timestamp, and a flag saying whether the measurement was saturated (too bright to measure accurately) for each frame in the session. The worker also returns the exposure time it used, which the time conversion needs.

## Alignment anchors

The anchors are the stars whose positions give each frame's alignment (see [Frame alignment](#frame-alignment)). `select_alignment_anchors` in `frame_photometry.py` chooses them once per session, from the stars found in the first frame:

1. Drop every detection with a saturated pixel within 3 pixels of its center.
2. Rank the rest by total brightness, brightest first.
3. Keep the detections whose peak is at least 20 times the sky noise (the pixel-to-pixel scatter of the empty sky). The sky noise per pixel comes from `estimate_pixel_noise`: the spread of the differences between pixels three columns apart, divided by the square root of 2 (a difference of two noisy pixels is that much noisier than one pixel). A smooth sky gradient cancels in the difference, so it does not inflate the estimate. The spacing of three columns is a tuned value (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)).
4. Skip the brightest 5% of those, which are the most likely to be nearly saturated, and take the next 50.
5. If fewer than 10 detections qualify, use the detections ranked 50 to 100 by brightness instead. That is the fallback rule. In a sparse field those ranks are mostly noise peaks, so this fallback is a last resort.

`VariabilityAnalyzer.alignment_anchor_rule` and `alignment_anchor_count` record which rule a session used and how many anchors it had. The `registration_drift` gate (the run-level check on frame alignment, see the [photometry README](../README.md#the-registration-drift-gate)) names the fallback rule in its detail when a session used it.

The main rule exists because in a sparse field the detections ranked 50 to 100 are mostly noise peaks, which give a wrong shift (partly measured; see [PHO-05](../../../../documentation/library_design/validation/photometry.md#pho-05)).

## Aperture placement

The circle (the aperture) and the background ring sit at the star's exact position, including the fractional part of a pixel. The code does not round the position to a whole pixel. A circle that is half a pixel off a star loses 1 to 2% of the star's light for a typical star width. That loss changes as the frame drifts, and it is the same size as the brightness changes the pipeline searches for.

The re-centering box (the small square in which the code computes the centroid) is 11 pixels wide, about 2.5 times the width of the typical star the default circle (radius 4 pixels) is sized for. The code subtracts the local sky level (the median of a 31 by 31 pixel cutout) and sets negative pixels to zero before it averages the positions. It repeats this three times, each time centering the box on the previous result.

The code refuses the re-centered position and keeps the aligned position (the star's position in the first frame, moved by the frame's alignment) when any of these hold:

- The box contains a saturated pixel. A saturated core is flat, so its centroid is not reliable.
- The box has no light above the sky level, or it reaches past the edge of the frame.
- The centroid is more than 1.5 pixels from the aligned position. A move that large means the box found a neighbor or noise instead of the star.

Each measurement keeps the reason for a refusal in `StarPosition.fallback_reason` (`None` means the centroid was accepted). `VariabilityAnalyzer.centroid_fallback_counts` totals the refusals per star over the session. A star near the 1.5 pixel limit in many frames is measured with a circle that may be off-center by up to that amount.

### Frame alignment

`calculate_frame_alignment` fits three things to each frame: a shift, a rotation and a change of scale about the middle of the frame (`FrameAlignment`). It works in two steps:

1. Each anchor is found in a wide box (`locate_star_centroid`, which searches up to 40 pixels from the anchor's expected position) and the median shift is taken. The wide box is easily pulled off by a neighbor or a galaxy, but the median ignores those stars.
2. Each anchor is re-centered in the small box (`refine_star_centroid`), starting where the alignment so far says it is.
   - *The fit.* A shift, a rotation and a scale change are then fitted to those accurate positions by least squares, leaving out anchors more than three scaled median absolute deviations from the fit. (The median absolute deviation, MAD, is the median distance of the values from their median; scaled by 1.4826 it estimates a standard deviation without being pulled by outliers.)
   - *Wrong stars.* The small box refuses a wrong star by itself, because it will not move more than 1.5 pixels from where the alignment so far puts the star.
   - *Second try.* When that leaves fewer than 10 anchors (a rotation not yet fitted can put the far ones out of reach), the step is tried again with a reach of 4 pixels in place of 1.5 (`ALIGNMENT_ANCHOR_REACH_PX`): an anchor's centroid may then sit up to 4 pixels from where the alignment so far puts it. A wide reach is only the second try, because it lets blends (two stars close enough to merge into one blob) in, which makes the fitted rotation worse (partly measured; see [PHO-04](../../../../documentation/library_design/validation/photometry.md#pho-04)).
   - *Repeats.* The step is repeated three times, because a better alignment puts more anchors inside the box.

When fewer than 5 anchors are found (`MINIMUM_ANCHORS_FOR_SHIFT`), the code fits nothing and treats the frame as not shifted at all, rather than guess a shift from too few stars. Only the shift is used when fewer than 10 anchors are found, or when they sit within 7% of the corner distance of one another (a cluster core cannot tell a rotation from a shift). The 7%, the 4 pixels and the three repeats are tuned values (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)).

**Why the rotation is fitted.** A field can turn during a session far enough that a shift alone leaves the far stars outside the 1.5 pixel reach of the centroid box, and their measurements then fall back to a position off the star (partly measured; see [PHO-04](../../../../documentation/library_design/validation/photometry.md#pho-04)). A small scale change can also appear in every frame, even on a field that does not turn. It is not a motion of the field. It comes from comparing two ways of measuring a position: the first frame's star positions come from the star finder's centroids, and every later frame's come from the small re-centering box, and the two methods differ slightly.

### Per-star centroid offsets

`centroid_offsets_px` measures how far each star's own centroid sits from where the alignment says it should be, in pixels, for every star whose centroid was accepted. `VariabilityAnalyzer` keeps the offsets per frame (`centroid_offsets_by_frame`) and the alignment of each frame (`alignment_by_frame`). `centroid_shift_summary()` reduces them to a `CentroidShiftSummary` for the session. The 95th percentile used below is the value that 95% of the measurements fall below.

- **Corner displacement.** For each frame, how far the fitted rotation and scale change move a corner of the frame (`FrameAlignment.corner_displacement_px`). The summary keeps the 95th percentile over the frames, the worst frame and how many frames had a rotation fitted. This is the number the `registration_drift` gate judges.
- **Median and 95th-percentile offset.** What is left after the alignment: centroid noise, which is larger for faint and crowded stars, and any distortion the fit does not describe. It does not change with the rotation fit (values on real fields are partly measured; see [PHO-04](../../../../documentation/library_design/validation/photometry.md#pho-04)). The gate reports these but does not judge them.
- **Fallback fraction.** The share of star measurements, over all frames, that kept the aligned position because the centroid was refused. A refused star has no offset, so a large fraction also means the offsets underestimate the problem.
- **Worst frame.** The frame with the largest 95th-percentile offset, and that value.

The `registration_drift` gate reads the summary (see the photometry README). The summary is not copied into the quality metrics. It appears only in the gate's detail.

The re-centering box has a fixed size. It assumes a star width near 3 to 4 pixels (FWHM, full width at half maximum). For wider stars the box cuts off more of the star's wings and the centroid shifts toward the box center by a few hundredths of a pixel.

## Flux uncertainties

`frame_photometry.py` computes the 1σ uncertainty of each flux with the CCD equation (the noise budget of a camera sensor), in `aperture_flux_error_adu`. The equation adds up the variances (errors squared) of the independent noise sources: the star's own light, the sky, and the camera electronics. It works in electrons, because the random scatter of a count follows the number of electrons collected:

`variance = F + n_pix (S + RN^2 + D) + (n_pix^2 / n_sky) (S + RN^2 + D)`

| Symbol | Meaning | Unit |
| --- | --- | --- |
| `F` | The star's net counts (sky-subtracted aperture sum) times the gain (electrons per ADU) | electrons |
| `S` | The sky level per pixel (median of the ring) times the gain | electrons per pixel |
| `RN` | Read noise (the scatter the electronics add each time a pixel is read) | electrons per pixel |
| `D` | Dark current collected during the exposure (signal the sensor makes by itself from heat) | electrons per pixel |
| `n_pix` | Area of the circle, as the exact area, not a pixel count | pixels |
| `n_sky` | Number of pixels in the sky ring | pixels |

The first term is the star's own shot noise (the random scatter of a count). The second is the noise of the sky, read-out and dark counts under the circle. The third is the noise in the sky level itself, which the ring measures from a limited number of pixels. The code takes the square root, divides by the gain to get ADU, and divides by the exposure time to get ADU per second.

**Assumptions.** The dark current is zero, because no source records it. The equation assumes the frame has had its bias level (the fixed offset the camera adds to every pixel) removed; a frame with the bias still in it has an overestimated sky term. A star too near the frame edge to measure has a flux of 0 and an error of 0.

The error does not change the flux. `measure_aperture_photometry` returns both, and `_measure_aperture_flux` keeps returning only the flux and the saturation flag.

`detector_noise.py` picks the gain (electrons per ADU) and read noise (electrons):

1. The camera's profile: `gain_e_per_adu` and `read_noise_e` in `models/camera_profile.py`. Each carries its source (datasheet, measured or assumed). The values depend on the camera's gain setting, so they are exact only for frames taken at the setting they were measured at.
2. The frame's `EGAIN` card (electrons per ADU) and `RDNOISE` card (electrons).
3. An assumption: 1 electron per ADU and no read noise.

The `GAIN` card is never read as electrons per ADU. The ZWO cameras write a gain setting there (the sample frames say `GAIN = 0.0`). `DetectorNoise.gain_is_assumed` and `read_noise_is_assumed` record which numbers were assumed. They reach the light curve as `errorsAssumeUnitGain` and `errorsAssumeZeroReadNoise` and the `flux_uncertainty` gate. With an assumed gain the errors have the right dependence on brightness, but the wrong size by a factor near the square root of the true gain.

## Capture times

The capture time of each frame comes from its `DATE-OBS` card, read with astropy's `Time` class as a UTC date and time. The code stores it without a time zone. This is the moment the shutter opened.

INDI and Ekos (the observatory-control software that takes the frames) write `DATE-OBS` as an ISO 8601 (the international standard) date and time in UTC with no suffix, for example `2026-05-24T04:58:30.570`. The sample frames have this form. `parse_observation_time` accepts these forms:

- A date and time with no suffix, read as UTC (the INDI and Ekos form).
- A date and time with a trailing `Z` or `+00:00`, read as UTC.
- A date and time with a UTC offset such as `2026-05-24T06:58:30.570+02:00`, `+0200`, `+02` or `-05:30`. The code subtracts the offset and stores the UTC instant (`04:58:30.570` in the example). Offsets beyond 14 hours or with minutes above 59 are refused.

A frame is rejected, and left out of every light curve, when `DATE-OBS` is missing, is not a date and time, or has a date but no time of day. A date alone is refused even with an offset, because every frame taken on one date would get the same midnight time. The code never substitutes the current time.

The rejection reason is kept in `VariabilityAnalyzer.frames_without_usable_date_obs`. The photometry runner reads that list and copies each reason into the `capture_timestamps` gate and into the list of excluded frames in the quality summary. If the first frame of a session is the one rejected, the session produces no light curves, and the session's empty-session reason says so.

`observation_times.py` converts the exposure starts of a session to mid-exposure BJD_TDB values, in days (see [Times](../README.md#times) in the photometry README for the reasons). BJD_TDB is the time the light would have reached the center of mass of the solar system, so the time of an event does not depend on where Earth is in its orbit. For each frame, it adds half the exposure time to the capture time, converts the result from UTC to TDB (a uniform time scale), and adds the light-travel time to the center of mass of the solar system for the target's direction. The light-travel time is between -499 s and +499 s. The conversion uses the observatory's position when the configuration has one, and Earth's center otherwise. It uses astropy's built-in planet positions, which need no download and are good to about 2 ms. The module's three time-basis sentences name what was done; the `capture_timestamps` gate quotes them.

## Judging the raw data

Once a star's full set of measurements for a session is ready, `assess_input_quality.py` builds a report on how good those raw measurements were:

- How many of the session's frames actually gave a usable measurement for this star, out of how many were possible.
- How often this star's measurement was saturated.
- How far the frame-to-frame drift got, at its worst, across every frame this star was measured in.

From these numbers, the report also flags two possible problems: coverage that is too low to trust, and drift large enough that the telescope's tracking, rather than the star's own brightness, is the more likely explanation for anything unusual in the data. This report travels with the star's brightness history into the later processing and post-processing steps.

## Settings

- **Alignment anchors.** The limits are 20 times the sky noise, skipping the brightest 5%, 50 anchors and a minimum of 10 (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)).
- **Shift and rotation fit.** The minimum of 5 anchors for a shift, the 7% spread for a rotation fit, the 4-pixel second-try reach and the three repeats are tuned values (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)).
- **Aperture and re-centering.** The aperture radius of 4 pixels, the 11-pixel re-centering box, the three re-centering passes and the largest move of 1.5 pixels (`MAX_CENTROID_SHIFT_PX`) are tuned values (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)).
- **Input-quality limits.** `LOW_COVERAGE_THRESHOLD` is 0.5 and `UNSTABLE_TRACKING_DRIFT_PX` is 20 pixels (design estimates; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)).
- **Camera numbers and times.** The gain and read noise come from the camera profile or the frame header (see [Flux uncertainties](#flux-uncertainties)). `latitude`, `longitude` and `elevation` in `[Observatory.Location]` set the observer for BJD_TDB.

## Quality metrics

The assessment builds one record per star.

| Field | Unit | Limit (status) | High or low means | Read by |
| --- | --- | --- | --- | --- |
| `frames_measured` | frames | None | High: more frames gave a usable measurement for this star | `coverage_fraction` |
| `frames_available` | frames | None | The number of frames in the session | `coverage_fraction` |
| `coverage_fraction` | none (0 to 1) | At least 0.5 (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)) | Low: the brightness history has many gaps | `is_low_coverage` |
| `saturated_fraction` | none (0 to 1) | None | High: more measurements were too bright to measure accurately | Reviewers |
| `max_registration_drift_px` | pixels | At most 20 (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)) | High: tracking, not the star, is the likely cause of anything unusual | `is_tracking_unstable`; `registration_drift` gate |
| `is_low_coverage` | flag | Set when `coverage_fraction` is below 0.5 | Set: fewer than half of the session's frames measured this star | Reviewers |
| `is_tracking_unstable` | flag | Set when the drift is above 20 pixels | Set: tracking is the likely explanation for anything unusual | Reviewers |

A reviewer can use these values to tell a thin, gap-filled or poorly tracked brightness history apart from a solid one, before trusting what the later stages conclude about that star. The wide centroid search box reaches 40 pixels from a star's expected position, so 20 pixels flags the problem well before stars start to fall outside it.

## Files

- `frame_photometry.py` selects alignment anchors, aligns frames, re-centers stars and measures aperture flux and its error.
- `detector_noise.py` picks the gain and read noise.
- `observation_times.py` reads capture times and converts them to BJD_TDB.
- `assess_input_quality.py` builds the per-star input-quality record.

For exact behavior, read the code.
