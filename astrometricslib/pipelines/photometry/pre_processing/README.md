# Photometry pre-processing

This stage turns a raw frame into one brightness measurement per star, and checks how good that raw measurement was. It does not decide whether a star is variable; it only produces the numbers that later steps depend on.

Terms are defined in the [glossary](../../../../documentation/Glossary.md). This document also defines each term the first time it appears.

## What this stage does, in order

In plain words, the stage finds where each star is in every frame of a session, puts a small circle on it, adds up the light inside it, and subtracts the sky. The result for each star is one point of its light curve (its brightness against time) per frame, with an error bar and a time stamp. The hard parts are keeping the circle exactly on the star as the telescope drifts, and getting honest error bars and times.

For each frame in a session, the code:

1. Reads the frame's exposure time, airmass, and capture time from its header. Exposure time is how long the camera's shutter was open. Airmass is a measure of how much atmosphere the light passed through, which is lowest when a star is straight overhead and highest near the horizon. The capture time comes from the `DATE-OBS` card (see [Capture times](#capture-times)).
2. Finds each star's place in this frame from the frame's own plate solution. A plate solution is the map from pixel positions to sky positions that Siril works out from the frame's stars (see [Frame solutions](#frame-solutions)). The star's sky position comes from the first frame's solution, and this frame's solution turns it back into a pixel position. Telescopes drift a little between frames even while tracking a target, and a field can turn slightly during a session, so each frame has its own map. A frame with no solution is left out and listed with the reason.
3. For every star being tracked, starts at the position the frame's solution gives. It then re-centers the star on its own brightness-weighted centroid (the average of the pixel positions, weighted by how bright each pixel is) in a small box. This corrects for the star's own small offset from the solution's position (see [Aperture placement](#aperture-placement)).
4. Measures the amount of light inside a small circle centered on that position (the aperture), and subtracts the background sky brightness measured in a ring just outside the circle. This gives the star's raw brightness (its flux) in that one frame. The same step computes the brightness's 1σ uncertainty, one standard deviation of the random scatter expected from one measurement to the next (see [Flux uncertainties](#flux-uncertainties)).
5. Divides that raw brightness and its uncertainty by the exposure time, so a short exposure and a long exposure of the same star can be compared fairly. Both are in ADU per second (ADU is analog-to-digital unit, one step of the stored pixel value).

Every star ends up with a brightness value, its uncertainty, a timestamp, and a flag saying whether the measurement was saturated (too bright to measure accurately) for each frame in the session. The worker also returns the exposure time it used, which the time conversion needs.

## Frame solutions

Siril solves every frame of a session (`PlateSolveDriver.solve_frames`; the Siril driver's `sequence_solve.py`). It solves the first frame, the session's reference frame, on its own, with the retries for a wrong focal length. It then solves the other frames in groups of 50 with one Siril run per group, with the focal length that solved the first frame. Siril starts offline, with the app's private settings file, and does not wait for the stack's Siril slot. The frames are only linked into Siril's scratch folder, and the library's files are never changed. On 39 frames of 3,008 by 3,008 pixels the solve took 3.6 s (partly measured; see [PHO-23](../../../../documentation/library_design/validation/photometry.md#pho-23)).

`frame_mapping.py` reads the solutions. `FrameMapping` turns a position in the first frame into a position in another frame: the first frame's solution gives the sky position, and the other frame's solution gives the pixel. It also keeps two numbers for the `registration_corner_displacement` gate: how far the middle of the frame moved (`shift_x`, `shift_y`), and how far a corner moves beyond that shift (`corner_displacement_px`), which a turned or rescaled field produces.

A frame is left out when Siril cannot solve it, when its solution holds no sky map, or when its map does not place the middle or a corner of the frame at a finite pixel. The reason is logged as a warning, which the job lists, and kept in `VariabilityAnalyzer.frames_without_solution`; the runner lists the frame in the run's excluded frames. If the first frame has no solution, no frame can be placed, and the session gives no light curves. No other way of placing the circles exists: the stage never guesses a position from the stars it can find in the frame. The earlier frame-to-frame tracking fitted a shift, rotation and scale change to up to 50 anchor stars; its register entry was partly measured and is retired (partly measured; retired; see [PHO-05](../../../../documentation/library_design/validation/photometry.md#pho-05)). A star that a frame's solution cannot place gets a flux of 0 and an error of 0, the same as a star near the frame edge.

On one M 13 session whose field drifted by up to 16 pixels, the positions from these solutions agreed with the earlier frame-to-frame tracking to a median of 0.06 pixels (partly measured; see [PHO-24](../../../../documentation/library_design/validation/photometry.md#pho-24)). The light curves of M 13 with the solutions equal those with the earlier tracking (partly measured; see [PHO-25](../../../../documentation/library_design/validation/photometry.md#pho-25)).

## Aperture placement

The circle (the aperture) and the background ring sit at the star's exact position, including the fractional part of a pixel. The code does not round the position to a whole pixel. A circle that is half a pixel off a star loses 1 to 2% of the star's light for a typical star width. That loss changes as the frame drifts, and it is the same size as the brightness changes the pipeline searches for.

The re-centering box (the small square in which the code computes the centroid) is 11 pixels wide, about 2.5 times the width of the typical star the default circle (radius 4 pixels) is sized for. The code subtracts the local sky level (the median of a 31 by 31 pixel cutout) and sets negative pixels to zero before it averages the positions. It repeats this three times, each time centering the box on the previous result.

The code refuses the re-centered position and keeps the solution's position when any of these hold:

- The box contains a saturated pixel. A saturated core is flat, so its centroid is not reliable.
- The box has no light above the sky level, or it reaches past the edge of the frame.
- The centroid is more than 1.5 pixels from the solution's position. A move that large means the box found a neighbor or noise instead of the star.

Each measurement keeps the reason for a refusal in `StarPosition.fallback_reason` (`None` means the centroid was accepted). `VariabilityAnalyzer.centroid_fallback_counts` totals the refusals per star over the session. A star near the 1.5 pixel limit in many frames is measured with a circle that may be off-center by up to that amount.

### Per-star centroid offsets

`centroid_offsets_px` measures how far each star's own centroid sits from where the frame's solution says it should be, in pixels, for every star whose centroid was accepted. `VariabilityAnalyzer` keeps the offsets per frame (`centroid_offsets_by_frame`) and the mapping of each frame (`mapping_by_frame`). `centroid_shift_summary()` reduces them to a `CentroidShiftSummary` for the session. The 95th percentile used below is the value that 95% of the measurements fall below.

- **Corner displacement.** For each frame, how far a corner of the frame moves beyond the shift of its middle (`FrameMapping.corner_displacement_px`). The summary keeps the 95th percentile over the frames, the worst frame and how many frames were mapped. This is the number the `registration_corner_displacement` gate judges.
- **Median and 95th-percentile offset.** What is left after the solution has placed the star: centroid noise, which is larger for faint and crowded stars, and any error of the solution itself (values on real fields are partly measured; see [PHO-04](../../../../documentation/library_design/validation/photometry.md#pho-04)). The gate reports these but does not judge them.
- **Fallback fraction.** The share of star measurements, over all frames, that kept the solution's position because the centroid was refused. A refused star has no offset, so a large fraction also means the offsets underestimate the problem.
- **Worst frame.** The frame with the largest 95th-percentile offset, and that value.

The `registration_corner_displacement` gate reads the summary (see the photometry README). The summary is not copied into the quality metrics. It appears only in the gate's detail.

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

**Assumptions.** The dark current is zero, because no source records it. The equation assumes the frame has had its bias level (the fixed offset the camera adds to every pixel) removed; a frame with the bias still in it has an overestimated sky term. A star too near the frame edge to measure has a flux of 0 and an error of 0. The equation has no term for scintillation (the twinkling of starlight in the air). The processing step adds it later, once the session's comparison stars are known (see [Scintillation](../README.md#scintillation)), so the error stored with a point is larger than this one for a bright star.

The error does not change the flux. `measure_aperture_photometry` returns both, and `_measure_aperture_flux` keeps returning only the flux and the saturation flag.

**Faint stars.** The net flux is the measured value, and it can be zero or below. A star near the noise measures a little below zero in about half its frames, because the sky level is itself an estimate. The code does not clamp it to zero: a clamp would raise the star's mean and hide the frames where it was lost in the noise. The error stays above zero (the shot noise term uses the flux or zero, whichever is larger). A star too near the frame edge is the one case with no measurement: its flux and error are both 0, and the comparison step reads the error of 0 as "not measured".

`detector_noise.py` picks the gain (electrons per ADU) and read noise (electrons):

1. The camera's profile: the `sensor_noise` entry in `models/camera_profile.py` for the frame's own gain setting, offset and binning. The setting comes from the header (`ISOSPEED` or `GAIN`, `OFFSET` or `BLKLEVEL`, `XBINNING` and `YBINNING`) or is passed in from a frame record. Each value carries its source (datasheet, measured or assumed). An exact match counts. When the caller passes `clipped_at_zero=False`, an entry of the same gain and binning at another offset may stand in (see `models/README.md`), and `DetectorNoise.offset_fallback_note` says so. The photometry run and the spectroscopy noise model measure the share of exact zeros in the reference frame (`frame_is_clipped_at_zero` in `shared/quality/clipped_frames.py`, limit `CLIPPED_FRAME_ZERO_FRACTION`) and pass that result. A caller that does not know leaves it at the default, so a frame at any other setting gets nothing from the profile, and `DetectorNoise.profile_gap_reason` says why. A frame that is clipped at zero keeps the gap, the warning and the assumed flags. A profile still holding the old single gain and read noise is treated the same way until it is migrated (`scripts/migrate_camera_profiles.py`).
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
- How often this star's measurement was saturated. The saturated points themselves are removed from the light curve (see [Saturated points](../README.md#saturated-points)), so this share counts them against all the points the star was measured at.
- How far the field sat from its place in the first frame, at its worst, across every frame this star was measured in.

The report holds numbers only. It sets no yes/no flag for low coverage or unstable tracking. Any reading of the numbers as good or bad is done later, in wayfindinglib. This report travels with the star's brightness history into the later processing and post-processing steps.

## Settings

- **Frame solutions.** Siril solves the frames after the first in groups of 50. The group size only bounds the scratch space (about 1 GB for 9-megapixel frames); it does not change a result. No tuned value remains from the earlier frame tracking.
- **Aperture and re-centering.** The aperture radius of 4 pixels, the 11-pixel re-centering box, the three re-centering passes and the largest move of 1.5 pixels (`MAX_CENTROID_SHIFT_PX`) are tuned values (design estimate; see [photometry design estimates](../../../../documentation/library_design/validation/photometry.md#design-estimates)).
- **Camera numbers and times.** The gain and read noise come from the camera profile or the frame header (see [Flux uncertainties](#flux-uncertainties)). `latitude`, `longitude` and `elevation` in `[Observatory.Location]` set the observer for BJD_TDB.

## Quality metrics

The assessment builds one record per star.

| Field | Unit | Limit (status) | High or low means | Read by |
| --- | --- | --- | --- | --- |
| `frames_measured` | frames | None | High: more frames gave a usable measurement for this star | `coverage_fraction` |
| `frames_available` | frames | None | The number of frames in the session | `coverage_fraction` |
| `coverage_fraction` | none (0 to 1) | None | Low: the brightness history has many gaps | Reviewers |
| `saturated_fraction` | none (0 to 1) | None | High: more measurements were too bright to measure accurately | Reviewers |
| `max_registration_drift_px` | pixels | None | High: the field sat far from its place in the first frame. The solutions follow it, so this is a mount fact, not a measuring problem | Reviewers; the `registration_corner_displacement` gate quotes it |

A reviewer can use these values to tell a thin, gap-filled or poorly tracked brightness history apart from a solid one, before trusting what the later stages conclude about that star. No limit judges this offset: the solutions place every star wherever the field went, so a large drift does not spoil the measuring itself.

## Files

- `frame_mapping.py` reads each frame's plate solution and maps the first frame's positions onto it.
- `frame_photometry.py` re-centers stars and measures aperture flux and its error.
- `detector_noise.py` picks the gain and read noise.
- `observation_times.py` reads capture times and converts them to BJD_TDB.
- `assess_input_quality.py` builds the per-star input-quality record.

For exact behavior, read the code.
