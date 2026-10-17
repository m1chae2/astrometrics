# Spectroscopy pre-processing

This stage turns a raw image into a calibrated spectrum for each star and measures how good the raw data was. Nothing here depends on what the star turns out to be. The pipeline runs this stage the same way for every star.

Terms are defined in the [glossary](../../../../documentation/Glossary.md). This document also defines each term the first time it appears.

## What this stage does, in order

- **Slitless spectroscopy.** A transmission grating (a clear plate ruled with fine lines, which bends light by an angle that depends on its wavelength) sits in front of the camera with no slit. It spreads every star in the field into a short rainbow at once.
- **The trail and the zero order.** Each rainbow appears on the image as a short band of light, the spectrum trail (here, "the trail"). Part of the star's light passes the grating unbent and forms a normal point-like image, the zero order, at one end of the trail. Distance from the zero order along the trail maps to wavelength (the dispersion direction is the direction along the trail).
- **What this stage produces.** For each star, a calibrated spectrum: brightness against wavelength, with a 1σ error (one standard deviation of the expected random scatter) on every sample, cleaned of the effects of the camera, the instrument and the air.
- **Why it is hard.** With no slit, the sky and neighboring trails sit right beside each star's trail. The wavelength scale hangs on the zero order, which can be saturated (pixels at the camera's maximum value) or pulled by a neighbor. The air bends and dims blue light more than red, and the camera and grating add their own color tilt. The blur is large, so every correction has to be small and honest about its errors.

Wavelengths are in ångströms (Å; 1 Å is 0.1 nm). ADU (analog-to-digital unit) is one step of the stored pixel value.

The stage runs these steps:

1. **Identify each star.** A spectral image spreads each star's light into a trail, so the pipeline cannot match its stars against the catalog the normal way. It names them from a normal (non-spectral) image of the same field, where astrometry (the measurement of star positions on the sky) has already identified the stars (`spectral_star_registration.py`):
   - First, it uses the plate solution (the fitted map from pixel to sky position) of the target's solved normal stack. The solution places every catalog star in the spectral image by its sky position, saturated stars included (`identify_spectral_stars_via_solution`).
   - When there is no solved stack, or the solution names no star, it falls back to lining up the two images' star positions by their geometry and carrying the names across (`identify_spectral_stars_via_registration`). A session run instead projects the stars found once in the session's reference frame through that frame's sky coordinate system (its WCS, world coordinate system: the map from pixel to sky position).
2. **Model the instrument.** The pipeline builds a digital model of the camera and grating to work out where each star's rainbow should fall on the sensor, using the physics of how a grating spreads light. `optics_physics.py` holds the grating equation, and `spectroscopy_instrument.py` applies it with the camera's configuration.
3. **Extract each spectrum.** The pipeline measures the brightness along each star's trail. At each step along the trail it adds up the light in a box (the extraction box) across the trail, centered on the trail's fitted center line, and subtracts the sky background. See [Extraction box and sky](#extraction-box-and-sky) below. The [pipeline README](../README.md#what-the-pipeline-does-in-order) defines the four extraction paths: dispersion-line or flare-mask (where the box runs), each traced or fixed-box (whether the box follows the trail's fitted center and width). `spectrum_extractor.py` does this. `_extractor_c.c` is an optional C version of the per-step Gaussian fit (a bell curve fitted to the trail's cross-section), about 300 times faster than the Python fallback, which the extractor uses when it is built.
4. **Correct for crowding.** When two stars sit close together, a bright neighbor's trail can leak light into a faint star's reading. The pipeline measures that leakage and subtracts it back out. `neighbor_trail_deblending.py` fits the blurred profile of each trail across the dispersion and works out each neighbor's share of the box. `neighbor_wing_correction.py` applies it to the pipeline's stars, using the camera's stored cross-trail blur (`data/cross_trail_blur_*.json`), and records what share it removed at each wavelength.
5. **Convert pixels to wavelengths.** The pipeline converts each pixel position along the trail into a wavelength, using the grating's known physics (`spectrum_calibrator.py`). It does not smooth the spectrum.
   - *Drop unusable samples.* It then drops the samples that landed off the image or below the camera's shortest wavelength.
   - *Record each sample's distance.* For every sample it keeps, it records `sample_distances_px`: the distance from the zero-order star (the undispersed image of the star), in pixels, measured along the trail. The distance is the one position the grating equation expects, so a later step can place any sample on the physical model without counting samples from the start. Every extraction path uses this same definition.
   - *Tilted trails.* A tilted trail is longer than the number of columns it covers, so the flare-mask extraction divides its column offset by the cosine of the tilt.
   - *The zero-order anchor.* The flare-mask extraction measures that offset from its zero-order anchor, the centroid (brightness-weighted average position) of the zero order. Light from the spectrum trail would pull a plain centroid about 0.08 pixel toward the trail, so the extractor subtracts the trail and the background from the box first.
6. **Correct for atmospheric refraction.** Air lifts blue light toward the zenith (the point straight overhead) more than red light. The pipeline moves each sample's wavelength back by the part of that lift that lies along the trail (see [Atmospheric differential refraction](#atmospheric-differential-refraction) below). It does this before every step that reads a wavelength, and it skips the step when it lacks an observatory site, a sky coordinate system (WCS) or a time.
7. **Check the zero point.** The pipeline then checks the wavelength scale against known lines (see [Wavelength zero point](#wavelength-zero-point) below).
8. **Remove signals that are not the star.** The pipeline corrects for three things that are not properties of the star itself:
   - the camera sensor's uneven sensitivity to different colors (its quantum efficiency, the fraction of arriving light it records; `quantum_efficiency_correction.py`, with the curve from the camera profile),
   - the whole instrument's own tilt (the grating, the telescope's coatings, and every other optic between the star and the sensor; `instrument_response.py`),
   - the difference in how much the air dimmed the blue light compared with the red light, between this frame and the frame of the standard star (a star of known spectrum) that the instrument's tilt was fitted to (see [Airmass extinction](#airmass-extinction) below).

   The wavelengths where Earth's own atmosphere absorbs light (the telluric bands) stay in the stored spectrum. `atmospheric_mask.py` lists those bands, and the classifier, the line indices and the Gaia XP comparison leave them out of their comparisons, so an atmospheric dip does not count as a feature of the star.
9. **Record what the numbers mean.** The pipeline records whether a stored spectrum's brightness values are raw camera counts or an averaged, stacked scale, since the two scales are not directly comparable. `intensity_scale.py` works out the factor that turns them into detector counts per second, and leaves the values themselves unchanged.
10. **Measure the input quality.** Before finding anything in the spectrum, the pipeline measures the raw data itself: how much the instrument blurred it, how saturated the brightest part was, how much of the intended spectrum actually landed on the image, and how strongly it stood out from noise. It attaches this measurement to the result so a reviewer can tell whether a later classification worked from strong data or weak data. `spectral_resolution.py` estimates the blur (the resolution element) from the trail width, or uses a fixed fallback when it cannot. It also loads the stored line-spread profile (the instrument's blur at each wavelength) (`data/line_spread_*.json`) that the classifier blurs its references with.

## Extraction box and sky

**The box.** The pipeline fits a Gaussian (a bell curve) to the trail's cross-section at each step. The fit gives the trail's center and its width (sigma, in pixels). The box reaches 2.5σ to each side of the center, which holds about 99% of a Gaussian profile's light while adding little empty sky.

The fit's width is noisy from step to step. A box that followed each fit directly would change size by a whole pixel from one step to the next, and the extracted brightness would jump by about 2% each time. These jumps come from the fit noise, not from the star. The pipeline avoids this in two ways:

- It takes the box width from the median of the fitted widths over 61 neighboring steps (`APERTURE_SIGMA_SMOOTHING_STEPS`). The median follows the slow change of the real width along the trail and ignores the noise of single fits. The real width changes over hundreds of steps, so 61 steps, about a tenth of a typical trail, removes the noise and still follows the change. The 61 was chosen by judgment and not tuned (partly measured; see [SPE-04](../../../../documentation/library_design/validation/spectroscopy.md#spe-04)). Steps whose fit failed do not count in the median, and read a fixed box of the configured radius instead.
- The box can end part-way through a pixel. Each pixel at the edge counts for the fraction of it that lies inside the box. The box center also keeps its fractional position, so the box does not shift by a whole pixel when the trail's center crosses a pixel boundary.

`trail_width_px` still holds the fitted width of each step, unsmoothed, in pixels. The resolution estimate reads it. The half-width used at each step, in pixels, is in the extractor's `last_diagnostics.aperture_half_width_px`.

**The sky.** The pipeline measures the sky in two strips, one on each side of the box, separated from it by a 6-pixel gap and 10 pixels wide. It does this at every step. The gap keeps the star's own faint wings out of the strips, and a larger gap would come closer to neighboring trails. A strip 10 pixels wide gives 20 sky pixels per step, enough that a few odd pixels cannot move the median. Both values come from a sweep on one sparse frame (partly measured; see [SPE-01](../../../../documentation/library_design/validation/spectroscopy.md#spe-01)).

1. It removes pixels more than 3σ (3 standard deviations) from the strip's median (hot pixels, cosmic rays, the edge of a passing streak) and repeats until nothing more drops out, for at most `SKY_CLIP_MAX_PASSES` (5) passes. It takes the median of what is left.
2. When the two strip medians agree to within noise, the sky is their mean.
3. When they differ by more than 3 times the noise of their difference, the pipeline treats the brighter strip as contaminated (light from a neighboring star can only add to a strip) and uses the other strip alone.
4. When only one strip has enough pixels on the image, it uses that strip.

Taking the lower of the two medians every time would read low by about 0.2 of one pixel's noise, for strips of this size, because the smaller of two noisy numbers sits below their true value. A faint trail would then keep too much sky in its total (partly measured; see [SPE-03](../../../../documentation/library_design/validation/spectroscopy.md#spe-03)).

The extractor records how it found the sky in `last_diagnostics`. `sky_mode_counts` counts the sky readings by mode: `both_bands`, `lower_band_contaminated` or `upper_band_contaminated` (the strip on the lower or higher side of the trail was dropped), `single_band` and `no_sky`. `dominant_sky_mode` is the most common mode, and `contaminated_sky_fraction` is the share of readings that dropped a strip, from 0 to 1. Å fraction well above a few percent means a neighbor's light lies beside the trail, and the star's spectrum deserves a closer look. The pipeline copies a summary onto the star's saved result as `SpectroscopyResult.extraction_diagnostics` (a `SpectralExtractionDiagnostics`). It holds the sky mode counts, the dominant mode, the contaminated fraction, and the median and standard deviation of the box half-width in pixels. The two half-width values are empty (`None`) for an untraced extraction.

## Airmass extinction

**What and why.** The instrument response (the color tilt of the camera, grating and telescope) was measured on a standard star at one height in the sky. A target at another height had its light dimmed by a different amount of air, and that difference tilts its spectrum. This step removes the difference.

Air dims blue light more than red light, and it dims both more at higher airmass (a measure of how much air the light crossed: 1.0 straight overhead, 2.0 about 60° from overhead). The instrument response already removes the dimming at the airmass of the standard star it was fitted to (`reference_airmass`, stored in the response file). A target observed at another airmass keeps a blue-to-red tilt from the difference. Between 4200 Å and 8000 Å the tilt is about 0.09 magnitudes (on the logarithmic brightness scale; 0.09 mag is about 8%) for a change of 0.35 airmass.

After it removes the instrument response, the pipeline multiplies the spectrum by `10 ** (0.4 * k * (target_airmass - reference_airmass))`. Here `k` is the extinction coefficient (how much the air dims the light for each unit of airmass) in magnitudes per airmass, read from `data/atmospheric_extinction_kpno.txt` at each wavelength. The target airmass comes from the frame header's `AIRMASS` card.

**When it is skipped.** The pipeline skips the correction, and records why, when the header has no usable airmass (missing, below 1 or above 10) or the response file records no reference airmass. The result of the correction is an `ExtinctionCorrection` record: `is_applied`, `target_airmass`, `reference_airmass`, `curve_name` and `reason` (set only when skipped). The spectrum analysis carries this record as `SpectrumAnalysis.extinction_correction`. The pipeline copies the record onto the star's saved result as `SpectroscopyResult.extinction_correction` (an `ExtinctionCorrectionRecord`). It is empty (`None`) when no instrument response was applied.

`scripts/recompute_spectral_analysis.py` applies this same correction when it re-analyzes a stored spectrum, using the same function. The script has no frame header, so it takes the target airmass from the stored `extinction_correction` record. A spectrum saved without that record is left uncorrected, and the new record says why.

The stored curve is the mean Kitt Peak curve, a dry site at 2 km altitude. A site at lower altitude has somewhat more extinction in the blue. The correction depends on the difference between two airmasses, so the error from using a different site's curve stays a small fraction of the correction.

## Atmospheric differential refraction

Air bends starlight so that a star looks higher in the sky than it is. It bends blue light more than red light. The zero-order image (the star's undispersed image) is a white-light image, so it sits where the brightness-weighted mix of wavelengths lands. The light of every other wavelength lands a little closer to the zenith (blue) or a little farther from it (red) than the zero order. The pipeline's wavelength scale assumes every wavelength lies on a straight line from the zero order, so this shift has two effects:

- **Along the trail**, it moves a wavelength to a pixel that the scale labels with a different wavelength. This is a wavelength error.
- **Across the trail**, it moves the wavelength sideways. This widens the trail and changes no wavelength.

At sea level, between 4200 Å and 8000 Å, the shift is about 1.6 arcsec (1 arcsec is 1/3600 of a degree) at airmass 1.5 and 2.5 arcsec at airmass 2. The pixel scale is 1.915 arcsec/pixel, so the shift is roughly 0.8 to 1.3 pixels. At about 11.4 Å per pixel, a trail that lies along the vertical gets a wavelength error of 10 to 15 Å between its ends.

The pipeline corrects the part along the trail (`differential_refraction.py`, `atmospheric_refraction.py`). For each spectrum it does the following:

1. **Find the time.** The time is the middle of the exposure: `DATE-OBS` plus half of `EXPTIME`, or halfway to `DATE-END` when the header has it. A header with no time of day gives no result.
2. **Find the altitude and the parallactic angle.** With astropy and the observatory site, it finds the target's altitude (its angle above the horizon) and the position angle (measured on the sky from north toward east) of the direction from the target to the zenith. That angle is the parallactic angle.
3. **Find the direction of the dispersion on the sky.** The pipeline knows the direction in which wavelength grows along the trail as an angle on the image. It sends two points along that direction through the frame's WCS to get the position angle on the sky. The same step gives the sky angle covered by one pixel along the trail.
4. **Split the shift.** The angle `phi` is the dispersion's position angle minus the parallactic angle. The shift of a wavelength `L` toward the zenith is `R(L) - R(L_eff)`, where `R` is the refraction and `L_eff` is the zero order's effective wavelength. Its part along the trail is that shift times `cos(phi)`. Its part across the trail is that shift times `-sin(phi)`. A positive along-trail shift points toward longer wavelengths.
5. **Find the refraction.** `R(L) = (n(L) - 1) * tan(z)`, where `z` is the zenith distance (the angle between the target and the zenith) and `n` is the refractive index of air (how much slower light travels in air than in vacuum, which sets how much it bends) from the Ciddor (1996) equations. The index depends on the air's pressure, temperature and humidity. The pipeline reads `pressure_hpa`, `temperature_c` and `relative_humidity_percent` from the `[Observatory.Location]` section of the config. Any of the three that the config leaves out comes from the standard atmosphere scaled to the site's elevation, which is dry. The record says which source each number came from. The model treats the atmosphere as flat layers (plane-parallel), so the pipeline refuses to use it below an altitude of 20 degrees.
6. **Find the effective wavelength of the zero order.** `L_eff` is the mean wavelength of the extracted spectrum, weighted by the counts. The counts already carry the camera's sensitivity and the star's spectrum, which are the two things that set where the zero-order centroid lies. The estimate leaves out the transmission of the grating's zero order.
7. **Shift the wavelengths.** The pipeline converts the along-trail shift from pixels to ångströms with the local dispersion, the derivative of the grating equation. The dispersion is about 11.3 Å per pixel at 4200 Å and 11.0 Å at 8000 Å for the 200 lines/mm grating at a grating-to-sensor distance of 16.49 mm, the fitted value; see [Calibration tuning](../README.md#calibration-tuning) in the pipeline README). It subtracts the result from each sample's wavelength, and repeats three times because the shift depends on the wavelength it moves.

**Where the step sits.** `_process_single_star` in `pipeline.py` computes the wavelength scale. `_apply_result_to_stellar_object` then shifts the wavelengths first, before the quantum-efficiency correction, the instrument response and the airmass correction. The stored spectrum, the response correction and the classifier all read the shifted wavelengths. `_process_single_star`'s own result keeps the unshifted scale.

The pipeline reads the site from the config when it builds itself without an explicit config (`SpectroscopyPipeline()`), and `analyze_frame_spectroscopy` passes it in. The `SpectroscopyPipeline` constructor also takes `observatory_site` and `atmospheric_conditions`. The stored instrument response was derived without this correction. A response that `derive_instrument_response.py` derives with a site configured fits the shifted wavelengths.

**What it records.** The result of the step is a `DifferentialRefraction` record. The pipeline copies it onto the star's saved result as `SpectroscopyResult.differential_refraction` (a `DifferentialRefractionRecord`). It holds `is_computed`, `is_applied` (`dar_correction_applied`), `reason` (set when the pipeline skipped the step), the mid-exposure time, the altitude, the parallactic angle, the dispersion's position angle, the angle between them, the pixel scale, the effective wavelength, the air's conditions and their source, and the shift at 4200 Å and 8000 Å along the trail (arcseconds and ångströms) and across it (arcseconds). A number is empty (`None`) when the pipeline could not compute it. The record gives the shift across the trail in arcseconds only, because that shift changes no wavelength.

Checkpoint 0 reports the refraction metrics (see "Quality checkpoint 0" below).

## Wavelength zero point

**What and why.** If the zero-order anchor is off, every wavelength in the spectrum is off by the same amount. Lines with known wavelengths reveal that offset, so the pipeline looks for them and, when they agree, shifts the spectrum back.

The wavelength scale is the grating equation with one fitted number, the grating distance, and an anchor at the center of the star's zero-order image. A saturated or crowded zero order, or a centroid pulled toward a neighbor, moves the anchor. Every wavelength of that spectrum then shifts by the same amount, the wavelength zero-point error (a constant wavelength offset, not the magnitude zero point of photometry). Nothing before this step measures it for one spectrum. `wavelength_zero_point.py` measures it and, when the evidence is enough, removes it.

1. **Look for six known lines.** The pipeline tries H-alpha (6562.8 Å), H-beta (4861.3 Å), H-gamma (4340.5 Å) (three hydrogen lines, the Balmer lines), Na D (5892.9 Å, sodium), Mg b (5175 Å, magnesium) and the oxygen A band (7605 Å). All wavelengths are in air. It does not know the star's type yet, so it tries every line and keeps only the ones it finds. The oxygen A band comes from Earth's air, so every spectrum has it whatever the star is.
2. **Fit each line.** The pipeline fits a smooth continuum on both sides of the line (the continuum, the brightness the star would have without the line) and slides a Gaussian dip, as wide as the instrument blur at that wavelength (`load_line_spread_profile`), over plus or minus one blur width around the known wavelength. The dip's center minus the known wavelength is the line's offset.
3. **Keep a line only if it is significant.** A dip must be at least 6 noise widths deep (`MINIMUM_LINE_SIGNIFICANCE`) and its center must lie inside the search range. A noise-only spectrum finds no line.
4. **Combine the lines.** The spectrum's offset is the error-weighted mean of the lines' offsets (a precise line counts more than a noisy one). Each line's error is its fit error and the uncertainty of its own position (blended lines, stellar motion), added in quadrature (the square root of the sum of squares, as independent errors combine).
5. **Apply or only record.** The pipeline subtracts the offset from every wavelength when at least two lines were found and they agree within their errors (a chi-square test, p-value at least 0.01). The chi-square test asks whether the lines' offsets differ by more than their errors allow; the p-value is the chance that noise alone would make them differ this much, so a small p-value means the lines truly disagree. Otherwise it records the offset as measured only. One line cannot be told apart from a misidentified feature. Lines that disagree point to a stretched scale, not a shifted one.

The shift happens in `_apply_result_to_stellar_object` right after the refraction correction, so it measures only the constant offset that is left and does not remove the refraction shift a second time. Both come before the quantum-efficiency, instrument-response and airmass-extinction corrections. All three depend on wavelength, so they must see the corrected values.

Checkpoint 1 carries four metrics from this step: the offset's size (limit 20 Å), its uncertainty, the number of lines found and whether the offset was applied (see "Quality checkpoint 1" below).

The checkpoint raises `wavelength_zero_point_unconstrained` when fewer than two lines were found, `wavelength_zero_point_lines_disagree` when two or more lines were found but failed the chi-square test, and `wavelength_zero_point_large` when the offset is over its limit. The `wavelength_zero_point` field of the result keeps the signed offset, the chi-square, each line and whether the zero order was saturated. The run-level scatter of these offsets is described in the [pipeline README](../README.md).

**Limits of the method.** The correction is a constant shift, so it fixes an anchor error but not a wrong grating distance. A line can be measured only when the true offset is smaller than about one blur width. A different feature near a line can be mistaken for it, for example the CH G band at 4304 Å beside H-gamma in G and K stars (stars about as warm as the Sun or cooler); the chi-square test catches that only when other lines are present to disagree with it.

## Per-sample uncertainty

Every sample of the spectrum carries a 1σ error (the size of the random scatter that sample would show from one exposure to the next, one standard deviation). The pipeline needs it to say how much a later measurement, such as a line strength, can be trusted. The error comes from a model of the camera, not from the finished spectrum.

**Where the number comes from.** The extractor adds up the pixels in a box across the trail and subtracts the sky level. For that sum it computes a variance (the square of the error), in four terms:

1. **Photon noise of the box.** Light arrives in separate particles, so a pixel that collected `N` electrons scatters by `sqrt(N)` electrons (Poisson noise). Each pixel holds star and sky together, so the term uses the whole pixel value. The camera's gain (electrons per ADU) converts ADU to electrons.
2. **Read noise of the box.** Every pixel gets the camera's read noise (the scatter the electronics add; electrons, root mean square) each time the sensor is read.
3. **Edge weights.** A pixel at the box edge counts for the fraction of it inside the box. A pixel counted for a fraction `w` adds `w` times its value and so `w` squared times its variance. A box of whole pixels gives the familiar `n_pix` times the read variance.
4. **The error of the sky level.** The extractor subtracts the sky once for every pixel-area of the box, so the sky level's error counts that many times. Each sky band's median has a variance of about `(pi / 2) s^2 / (n + 1.4)` for `n` pixels with scatter `s` (`n + 0.4` when `n` is odd), and the mean of the two bands has a quarter of the sum. For many pixels, the median of normal values has `pi / 2` times the variance of their mean, `s^2 / n`. For the few pixels of one band the true value is a little lower, and the `+ 1.4` and `+ 0.4` come from a simulation of small bands (partly measured; see [SPE-07](../../../../documentation/library_design/validation/spectroscopy.md#spe-07)). The pixel scatter `s` is measured from the band, but never taken below what the camera model predicts for sky pixels. A band of ten pixels gives a noisy scatter, and the clipping step that drops hot pixels reads it low.

The gain and read noise come from the same resolution the photometry pipeline uses (`pipelines/photometry/pre_processing/detector_noise.py`): the camera profile first, then the `EGAIN` and `RDNOISE` header cards, then an assumption of 1 electron per ADU and no read noise. The result records which of the two was assumed (`SpectroscopyResult.intensity_noise_model`), and checkpoint 1 carries the `gain_assumed` and `read_noise_assumed` flags.

A Siril (the stacking program) stack saved as fractions of full scale is converted to ADU (65,535 per unit) and its variance is divided by the number of stacked frames (`STACKCNT`).

**Pixels where the stacker threw frames away.**

**What it does.** `STACKCNT` counts the frames submitted, but the stacker rejects some values at some pixels (cosmic rays, satellite streaks, bad pixels), so a pixel may be the mean of fewer frames. The stack's rejection map (`<stem>_RejMap.fits`, beside the stack; also used for `<stem>_processed.fits`) holds for each pixel the fraction of its values that were thrown away. A pixel that kept `K` of `N` frames has `N / K` times the variance, which is `1 / (1 - fraction)` (`rejection_variance_scale`), and never more than `N`. The box term of the variance multiplies each pixel's Poisson and read variance by that factor (`PixelNoiseModel.box_variance(..., pixel_scale)`). The sky term is not changed, because it uses the scatter measured in the sky bands, which already shows the local noise.

***Where it lives.*** `find_rejection_map` finds the map and the pipeline loads it (`SpectroscopyPipeline._noise_model_for`); without a map the factor is 1, and the stored record says `rejection_map_used`.

**Evidence.** Measured on real spectral stacks and on simulated stacks that lost frames (measured; see [SPE-08](../../../../documentation/library_design/validation/spectroscopy.md#spe-08)).

**Carrying the error through pre-processing.** Each later step multiplies a sample by a factor, so each multiplies its error by the size of that factor (its variance by the factor squared):

- The usable-sample mask drops the same samples from the errors as from the brightness.
- The quantum-efficiency correction multiplies by one over the sensitivity.
- The instrument-response division multiplies by one over the response, and gives NaN ("not a number", a sample with no value) outside the response's range, like the brightness.
- The extinction correction multiplies by its factor when it was applied.
- The sub-pixel position of a sample blends two image columns (a linear interpolation) with weights `1 - u` and `u`. The two readings use different pixels, so the blend has the variance `(1 - u)^2 V1 + u^2 V2`.

The pipeline stores the errors as `SpectroscopyResult.intensity_errors` (same units and order as `intensities`) and `SpectroscopyResult.response_corrected_intensity_errors` (same as `response_corrected_intensities`). The functions are in `intensity_variance.py`.

**Limits of the model.**

- Neighboring samples share an image column, so their errors are correlated. The stored array holds each sample's own error and not the covariance (how the errors of two samples move together). A sum over several samples that treats them as independent understates the error of the sum.
- The model assumes bias-subtracted and dark-subtracted pixels with one gain (bias is the fixed offset the camera adds; dark is the signal the sensor makes from heat). Flat-fielding (correcting each pixel's sensitivity), stacking and registration change the noise a little, and the model does not follow that.
- The model takes the quantum-efficiency curve and the instrument response to be exact. Their own errors are not in the stored errors.
- The neighbor-wing correction subtracts light from the brightness, and the uncertainty of that subtraction is not added to the errors.
- The traced extraction places its box from fitted centers and widths. The variance covers the pixel noise inside the box it chose. It does not cover the extra scatter from the box moving between exposures.

  The traced flare-mask extraction (the extraction paths are defined in the [pipeline README](../README.md#what-the-pipeline-does-in-order)) can scatter more than its errors say, because its box can switch between the fitted width and the fixed fallback width from one exposure to the next (partly measured; see [SPE-07](../../../../documentation/library_design/validation/spectroscopy.md#spe-07)).

## Measured line spread

A slitless spectrum is blurred, and the blur can differ from one end of the spectrum to the other. The pipeline measures that blur for each spectrum from its own trail width, and reports it. The code is in `measured_line_spread.py`.

What the pipeline does:

1. It takes the width (sigma, the standard deviation of a Gaussian fitted to the cross-section) of the trail across the dispersion at every step along the spectrum. The extractor already measures it. A failed fit counts as no measurement.
2. It groups the steps into bands 400 Å wide from 4200 to 8000 Å. The last band stops at 8000 Å, so it is 200 Å wide. A band needs at least 10 working fits.
3. In each band, it takes the median sigma and multiplies it by 2.355 to get a FWHM in pixels (2.355 is `2 sqrt(2 ln 2)`, the FWHM of a Gaussian in units of its sigma). It then multiplies that by the band's median local dispersion (the ångströms one pixel covers, from the wavelength calibration) to get a FWHM in ångströms.
4. It records the band's scatter: 1.4826 times the median absolute deviation of the per-step FWHM values. A few bad fits cannot inflate it.

The result is stored as `SpectroscopyResult.measured_line_spread` (`measuredLineSpread` in the saved JSON), a `MeasuredLineSpread` record with these lists, one entry per band:

| Field | Unit | Meaning |
| --- | --- | --- |
| `wavelength_angstrom` | Å | The center of the band. |
| `fwhm_angstrom` | Å | The blur in the band. |
| `fwhm_px` | pixel | The same blur in pixels. |
| `scatter_px` | pixel | The band's robust scatter. A large value means a less certain median. |
| `sample_count` | steps | How many working fits the median uses. |

The field is `None` when the trail width is missing (the fixed-box extraction does not fit one; the extraction paths are defined in the [pipeline README](../README.md#what-the-pipeline-does-in-order)) or fewer than two bands have enough fits.

The method assumes that a star's image is round, so the width across the dispersion equals the blur along it. Tracking errors, coma (a comet-shaped blur toward the edge of the field) and a bright neighbor all break this. The stored line-spread profile (`data/line_spread_*.json`) was fitted to line depths on other stars, so it may include scattered light that the trail width does not see. `measured_vs_stored_line_spread_ratio_halpha` shows how far the two disagree on one spectrum.

**Why the pipeline compares the red end with the blue end.** A grating in a converging beam (it sits in the cone of light that the telescope is bringing to a focus, not in parallel light) does not put the first-order spectrum (the main rainbow) in a flat plane. If the camera is focused on the zero order, the red end can be out of focus. That widens the red lines and limits how well the classifier can separate nearby spectral types there.

**What "in focus" looks like.** A spectrum that is in focus changes width with wavelength only through seeing (the blur from the air), which makes the red end slightly sharper (a ratio of about 0.92). The limit sits well above that, and no real focus sweep has set it. The focus-sweep script (below) is the tool to check it.

**The option to blur with the measured profile.** The classifier blurs its reference spectra to the instrument's resolution before it compares them with a spectrum. By default it uses the stored profile. Build the pipeline with `SpectroscopyPipeline(config, use_measured_line_spread=True)` to blur with this spectrum's own measured profile instead.

The option is off by default. It changes only the profile that the classification and feature tests use, and only for a spectrum that has a measured profile. It does not change the instrument response, which was fitted with the stored profile, so switching it on makes the two blurs disagree. Do not switch it on until a real-data check has compared the classifications with and without it on spectra of stars of known type.

Two scripts support this work (see the [scripts README](../../../scripts/README.md)):

- `scripts/spectral_focus_sweep.py` reads spectral frames of one star taken at several focuser positions and reports the best focus for each wavelength band and the position that suits mid-spectrum.
- `scripts/compare_instrument_responses.py` compares two instrument-response files. Use it to check that two standard stars from the same night give the same response.

## What this stage produces

This stage produces a calibrated spectrum, brightness by wavelength, for each identified star, corrected for everything that is not the star's own light, plus an [`InputQualityAssessment`](../../../models/spectroscopy_quality.py) that records how trustworthy the raw data was.

## The input quality assessment

The input quality assessment answers one question: was this spectrum worth analyzing at all? It does not depend on what the classifier or the feature tests later conclude, only on the raw data itself. It records:

- **Resolution.** How much the instrument blurred this spectrum (the resolution element, the smallest wavelength difference it can separate), in ångströms, and whether the pipeline measured that blur from this spectrum's own trail width or fell back to a fixed default. A finer resolution means later stages can trust smaller differences between spectral types. A coarser resolution, or a resolution the pipeline had to estimate, means two nearby types can be hard to tell apart no matter how good the rest of the data is.
- **Saturation.** The fraction of pixels at the star's brightest point that were pinned at the sensor's maximum value. A high fraction means the brightest part of the star's trail may be unreliable.
- **Coverage.** The fraction of the star's spectrum that actually landed on the image, inside the camera's usable range. A value below one means part of the trail ran off the edge of the frame.
- **Signal-to-noise.** How strongly the spectrum stands out from its own noise (the signal divided by its random scatter; higher is cleaner).

A reviewer can use this assessment to judge whether a later classification rests on strong data or weak data, before looking at the classification itself.

## Settings

- **Extraction box.** The box reaches 2.5σ from the trail center (design estimate; see [spectroscopy design estimates](../../../../documentation/library_design/validation/spectroscopy.md#design-estimates)). The box width is the median of 61 steps (partly measured; see [SPE-04](../../../../documentation/library_design/validation/spectroscopy.md#spe-04)).
- **Sky strips.** The gap of 6 pixels and the strip width of 10 pixels come from a sweep on one sparse frame (partly measured; see [SPE-01](../../../../documentation/library_design/validation/spectroscopy.md#spe-01)). Sky clipping uses 3σ and at most 5 passes (design estimate; see [spectroscopy design estimates](../../../../documentation/library_design/validation/spectroscopy.md#design-estimates)). A strip needs at least 4 pixels (design estimate; see [spectroscopy design estimates](../../../../documentation/library_design/validation/spectroscopy.md#design-estimates)).
- **Refraction.** The along-trail limit is 20 Å (design estimate; see [spectroscopy design estimates](../../../../documentation/library_design/validation/spectroscopy.md#design-estimates)). The model is refused below an altitude of 20° (design estimate; see [spectroscopy design estimates](../../../../documentation/library_design/validation/spectroscopy.md#design-estimates)).
- **Wavelength zero point.** A line must be at least 6 noise widths deep (partly measured; see [SPE-11](../../../../documentation/library_design/validation/spectroscopy.md#spe-11)). The correction needs 2 lines and a chi-square p-value of at least 0.01 (design estimate; see [spectroscopy design estimates](../../../../documentation/library_design/validation/spectroscopy.md#design-estimates)). The offset limit is 20 Å (design estimate; see [spectroscopy design estimates](../../../../documentation/library_design/validation/spectroscopy.md#design-estimates)).
- **Focus.** The `chromatic_defocus_ratio` limit is 1.3 (design estimate; see [spectroscopy design estimates](../../../../documentation/library_design/validation/spectroscopy.md#design-estimates)).
- **Signal-to-noise floor.** `MINIMUM_SPECTRUM_SIGNAL_TO_NOISE` is 1.5 (partly measured; see [SPE-10](../../../../documentation/library_design/validation/spectroscopy.md#spe-10)).
- **Saturation.** `DEFAULT_SATURATION_FLAG_THRESHOLD` is 0.001 (design estimate; see [spectroscopy design estimates](../../../../documentation/library_design/validation/spectroscopy.md#design-estimates)).
- **Camera numbers.** The gain and read noise come from the camera profile or the frame header (see [Per-sample uncertainty](#per-sample-uncertainty)).

## Quality metrics

This stage builds two checkpoints in the common `StageQualityCheckpoint` shape (see [Quality checkpoints](../README.md#quality-checkpoints) in the pipeline README). Checkpoints 0 and 1 both read the saturation limit `DEFAULT_SATURATION_FLAG_THRESHOLD` from `pipelines/shared/quality/saturation.py`. The `spectroscopy` result stores them as the first two entries of `stage_quality`.

`assess_raw_frame_quality.py` builds checkpoint 0 right after it extracts a star. The pipeline re-measures nothing from the pixels here. It reuses what the extraction computed for this star, and adds the refraction numbers, which come from the target's position and the time.

`input_quality_checkpoint` in `assess_input_quality.py` builds checkpoint 1. It carries the five numbers of the input quality assessment above (the resolution counts twice: its width, and whether it was measured), the four wavelength zero-point metrics, three metrics built from the per-sample errors and four line-spread numbers (see [Measured line spread](#measured-line-spread) below).

| Field | Unit | Limit (status) | High or low means | Read by |
| --- | --- | --- | --- | --- |
| `zero_order_saturated_fraction` | fraction | `DEFAULT_SATURATION_FLAG_THRESHOLD` (0.001), lower is better; a value at the limit fails (design estimate; see [spectroscopy design estimates](../../../../documentation/library_design/validation/spectroscopy.md#design-estimates)) | The share of the zero-order image (the star's undispersed image) at the camera's saturation level. A high value means the star's brightest part is unreliable. | Checkpoints 0 and 1 |
| `valid_fraction` | fraction | none | The share of the requested spectrum that landed on the image. Below 1, part of the trail ran off the image. | Checkpoints 0 and 1 |
| `median_trail_width` | px | none | The median fitted width of the trail across the dispersion direction. A wide trail means a blurrier spectrum. | Checkpoint 0 |
| `contaminated_sky_fraction` | fraction | none | The share of sky readings that had to drop one of the two sky strips (the sky bands beside the box) because a neighboring star's light fell in it. The note names the dominant sky mode, the most common way the sky was measured (see [Extraction box and sky](#extraction-box-and-sky)). | Checkpoint 0 |
| `dar_along_dispersion_angstrom` | Å | 20 Å, lower is better (design estimate; see [spectroscopy design estimates](../../../../documentation/library_design/validation/spectroscopy.md#design-estimates)) | The spread of the wavelength error that atmospheric differential refraction (DAR: the air bends blue light more than red) causes between 4200 Å and 8000 Å, before the correction. Flag `dar_large` when over the limit. The limit is half the resolution element at 5000 Å as the trail width measures it (about 40 Å; see the [pipeline README](../README.md#what-the-pipeline-does-in-order)). The stored line-spread profile is about 105 Å there, so this limit is stricter than half of that. | Checkpoint 0 |
| `dar_across_dispersion_px` | px | none | How much refraction widens the trail between 4200 Å and 8000 Å. | Checkpoint 0 |
| `target_altitude_degrees` | ° | 20°, higher is better (design estimate; see [spectroscopy design estimates](../../../../documentation/library_design/validation/spectroscopy.md#design-estimates)) | The target's altitude at mid-exposure. Below the limit the pipeline refuses the refraction model and sets the flags `target_altitude_low` and `dar_not_computed`. | Checkpoint 0 |
| `parallactic_to_dispersion_angle_degrees` | ° | none | The angle from the direction of the zenith (the point straight overhead) to the direction of the dispersion on the sky. 0 means the red end of the spectrum points at the zenith. | Checkpoint 0 |
| `resolution_element` | Å | none | How much the instrument blurred the spectrum, from the trail width (or the fixed 45 Å fallback) (partly measured; see [SPE-05](../../../../documentation/library_design/validation/spectroscopy.md#spe-05)). A smaller value separates nearby spectral types better. | Checkpoint 1 |
| `resolution_measured` | flag | none | 1 when the blur came from the spectrum's own trail width, 0 when a fixed fallback was used. | Checkpoint 1 |
| `signal_to_noise` | per resolution element | `MINIMUM_SPECTRUM_SIGNAL_TO_NOISE` (1.5), higher is better (partly measured; see [SPE-10](../../../../documentation/library_design/validation/spectroscopy.md#spe-10)) | The post-hoc signal-to-noise: estimated afterwards from the finished spectrum's own scatter between neighboring resolution elements, not from the per-sample errors. Below the limit the pipeline does not classify the spectrum. | Checkpoint 1 |
| `wavelength_zero_point_offset_angstrom` | Å | 20 Å, lower is better (design estimate; see [spectroscopy design estimates](../../../../documentation/library_design/validation/spectroscopy.md#design-estimates)) | The size of the wavelength zero-point offset measured from known lines, before correction. The limit is half the resolution element at 5000 Å as the trail width measures it (about 40 Å; see the [pipeline README](../README.md#what-the-pipeline-does-in-order)). The stored line-spread profile is about 105 Å there, so this limit is stricter than half of that. | Checkpoint 1 |
| `wavelength_zero_point_uncertainty_angstrom` | Å | none | The error of that offset. | Checkpoint 1 |
| `wavelength_zero_point_line_count` | lines | none | How many of the six lines (see [Wavelength zero point](#wavelength-zero-point)) passed the significance test. | Checkpoint 1 |
| `wavelength_zero_point_applied` | flag | none | 1 when the pipeline shifted the wavelengths by the offset, 0 when it only measured it. | Checkpoint 1 |
| `median_snr_per_resolution_element` | per resolution element | none | The median, over 4200 to 8000 Å, of the brightness summed over one resolution element divided by the error of that sum (from the per-sample errors; the element width comes from the line-spread profile). | Checkpoint 1 |
| `fraction_samples_snr_below_5` | fraction | none | The share of samples in that range whose brightness is below 5 times their own error. | Checkpoint 1 |
| `snr_estimate_ratio` | ratio | none | The variance-based signal-to-noise divided by `signal_to_noise`. A ratio far from 1 means one of the two is wrong. | Checkpoint 1 |
| `trail_fwhm_blue_px` | px | none | The median full width at half maximum (FWHM) of the trail across the dispersion from 4200 to 5000 Å. | Checkpoint 1 |
| `trail_fwhm_red_px` | px | none | The same width from 6200 to 7000 Å. | Checkpoint 1 |
| `chromatic_defocus_ratio` | ratio | 1.3, lower is better (design estimate; see [spectroscopy design estimates](../../../../documentation/library_design/validation/spectroscopy.md#design-estimates)) | `trail_fwhm_red_px` divided by `trail_fwhm_blue_px`. Above the limit the red end is out of focus compared with the blue end. A spectrum in focus end to end gives about 0.92. | Checkpoint 1 |
| `measured_vs_stored_line_spread_ratio_halpha` | ratio | none (reported only) | The spectrum's own measured FWHM at 6563 Å divided by the stored line-spread profile's value there (148 Å). A value far from 1 means the trail width and the stored profile disagree about the blur. | Checkpoint 1 |

Checkpoint 0 raises these flags: `zero_order_saturated` (the saturation metric fails), `trail_partly_off_image` (`valid_fraction` below 1), `sky_band_contaminated` (at least one sky reading dropped a band), and the three refraction flags `dar_large`, `dar_not_computed` and `target_altitude_low`.

The four refraction metrics have no value, and the checkpoint carries the flag `dar_not_computed`, when the pipeline has no observatory site, WCS (world coordinate system, the map from pixel to sky position stored with the image) or mid-exposure time. The refraction correction itself runs in pre-processing (see [Atmospheric differential refraction](#atmospheric-differential-refraction) above).

When the caller supplies the frame-level result of `measure_spectral_frame_file` under `result["frame_check"]`, the checkpoint also carries `streak_tilt` (degree) and `peak_above_sky` (ADU, analog-to-digital units, the steps of the stored pixel value), and the saturation note names the saturation level's source. The spectroscopy pipeline does not run that frame-level check, so a pipeline-built checkpoint 0 normally lacks tilt, peak-to-sky and saturation source.

The checkpoint 1 flags are `resolution_assumed`, `low_signal_to_noise`, `zero_order_saturated`, `gain_assumed` and `read_noise_assumed` (the per-sample errors used an assumed camera gain or read noise), `chromatic_defocus` (`chromatic_defocus_ratio` above its limit), `wavelength_zero_point_unconstrained` (fewer than two lines found), `wavelength_zero_point_lines_disagree` (two or more lines that fail the chi-square test) and `wavelength_zero_point_large` (offset over its limit). The pipeline applies the offset before the quantum-efficiency, response and extinction corrections (see [Wavelength zero point](#wavelength-zero-point) above).

**The two signal-to-noise estimates.** `signal_to_noise` is post-hoc: it is estimated afterwards from the finished spectrum's own scatter. The variance-based estimate behind `median_snr_per_resolution_element` and `snr_estimate_ratio` comes from the per-sample errors. Both estimate the same quantity, and the variance-based one cuts the spectrum into the same non-overlapping resolution elements as the post-hoc one. The element width comes from the line-spread profile at each wavelength, which widens toward the red.

**What a value means.** An `snr_estimate_ratio` far from 1 means one of the two is wrong: the gain or read noise in the model, or scatter in the spectrum that the model does not include.

The post-hoc estimate is itself noisy, so the ratio can stray from 1 for that reason alone (partly measured; see [SPE-09](../../../../documentation/library_design/validation/spectroscopy.md#spe-09)).

No measurement says what value of the three error-based metrics is too low, and the limit of `signal_to_noise` was set for that estimator, so the three are reported only.

## Files

- `spectrum_extractor.py` extracts each spectrum and measures the sky (see [Extraction box and sky](#extraction-box-and-sky)).
- `spectral_star_registration.py` names the stars in a spectral image.
- `optics_physics.py` and `spectroscopy_instrument.py` hold the grating equation and the instrument model.
- `neighbor_trail_deblending.py` and `neighbor_wing_correction.py` correct for crowding.
- `spectrum_calibrator.py` converts pixels to wavelengths.
- `atmospheric_extinction.py` applies the airmass extinction correction.
- `atmospheric_refraction.py` and `differential_refraction.py` correct for atmospheric differential refraction.
- `wavelength_zero_point.py` measures and removes the wavelength zero point.
- `quantum_efficiency_correction.py` and `instrument_response.py` remove the sensor's and the instrument's color tilt. `instrument_response.py` can also read a response from any JSON file (`read_instrument_response_file`, used by `compare_instrument_responses.py`).
- `atmospheric_mask.py` lists the atmospheric absorption bands.
- `intensity_scale.py` and `intensity_variance.py` record the intensity scale and carry the per-sample errors.
- `spectral_resolution.py` and `measured_line_spread.py` handle the line spread (see [Measured line spread](#measured-line-spread)).
- `assess_raw_frame_quality.py` and `assess_input_quality.py` build checkpoints 0 and 1.
- `spectral_frame_check.py` measures raw spectral frames outside any run.
- `standard_star_selection.py` picks the star nearest the frame center among the detected sources (used by `derive_instrument_response.py` and `spectral_focus_sweep.py`).

For exact behavior, read the code.
