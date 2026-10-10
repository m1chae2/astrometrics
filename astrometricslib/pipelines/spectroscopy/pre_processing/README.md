# Pre-processing

This stage turns a raw image into a calibrated spectrum for each star and
measures how good the raw data was. Nothing here depends on what the star
turns out to be. The pipeline runs this stage the same way for every star.

## The flow

1. **Identify each star.** A spectral image spreads each star's light into
   a streak, so the pipeline cannot match its stars against the catalog the
   normal way. Instead, it lines up a normal (non-spectral) image of the
   same field, where astrometry has already identified the stars, against
   the spectral image, then carries the names across.
2. **Model the instrument.** The pipeline builds a digital model of the
   camera and grating to work out where each star's rainbow should fall on
   the sensor, using the physics of how a grating spreads light.
3. **Extract each spectrum.** The pipeline measures the brightness along
   each star's streak. At each step along the streak it adds up the light in
   a box across the streak, centred on the streak's fitted centre line, and
   subtracts the sky background. See "Extraction box and sky" below.
4. **Correct for crowding.** When two stars sit close together, a bright
   neighbour's streak can leak light into a faint star's reading. The
   pipeline measures that leakage and subtracts it back out.
5. **Convert pixels to wavelengths.** The pipeline converts each pixel
   position along the streak into a wavelength, using the grating's known
   physics, and smooths the result into a clean curve. It then drops the
   samples that landed off the image or below the camera's shortest
   wavelength. For every sample it keeps, it records `sample_distances_px`:
   the distance from the zero-order star (the undispersed image of the
   star), in pixels, measured along the streak. A tilted streak is longer
   than the number of columns it covers, so the flare-mask extraction
   divides its column offset by the cosine of the tilt. The flare-mask
   extraction measures that offset from its zero-order anchor, the centroid
   (brightness-weighted average position) of the zero order. Light from the
   spectrum trail would pull a plain centroid about 0.08 pixel toward the
   trail, so the extractor subtracts the trail and the background from the
   box first. Both extraction paths use this same definition. The distance is the one position the
   grating equation expects, so a later step can place any sample on the
   physical model without counting samples from the start.
6. **Correct for atmospheric refraction.** Air lifts blue light toward
   the zenith (the point straight overhead) more than red light. The
   pipeline moves each sample's wavelength back by the part of that lift
   that lies along the streak (see "Atmospheric differential refraction"
   below). It does this before every step that reads a wavelength, and it
   skips the step when it lacks an observatory site, a sky coordinate
   system (WCS) or a time.
7. **Check the zero point.** The pipeline then checks the wavelength
   scale against known lines (see "Wavelength zero point" below).
8. **Remove signals that are not the star.** The pipeline corrects for
   four things that are not properties of the star itself:
   - the camera sensor's uneven sensitivity to different colours,
   - the whole instrument's own tilt (the grating, the telescope's
     coatings, and every other optic between the star and the sensor),
   - the difference in how much the air dimmed the blue light compared with
     the red light, between this frame and the frame of the standard star
     that the instrument's tilt was fitted to (see "Airmass extinction"
     below),
   - the wavelengths where Earth's own atmosphere absorbs light, which
     would otherwise look like a feature of the star.
9. **Record what the numbers mean.** The pipeline records whether a stored
   spectrum's brightness values are raw camera counts or an averaged,
   stacked scale, since the two scales are not directly comparable.
10. **Measure the input quality.** Before finding anything in the spectrum,
   the pipeline measures the raw data itself: how much the instrument
   blurred it, how saturated the brightest part was, how much of the
   intended spectrum actually landed on the image, and how strongly it
   stood out from noise. It attaches this measurement to the result so a
   reviewer can tell whether a later classification worked from strong data
   or weak data.

## Extraction box and sky

**The box.** The pipeline fits a Gaussian to the streak's cross-section at
each step. The fit gives the streak's centre and its width (sigma, in
pixels). The box reaches 2.5 sigma to each side of the centre.

The fit's width is noisy from step to step. A box that followed each fit
directly would change size by a whole pixel from one step to the next, and
the extracted brightness would jump by about 2 percent each time. These
jumps come from the fit noise, not from the star. The pipeline avoids this
in two ways:

- It takes the box width from the median of the fitted widths over 61
  neighbouring steps (`APERTURE_SIGMA_SMOOTHING_STEPS`). The median follows
  the slow change of the real width along the streak and ignores the noise
  of single fits. Steps whose fit failed do not count in the median, and
  read a fixed box of the configured radius instead.
- The box can end part-way through a pixel. Each pixel at the edge counts
  for the fraction of it that lies inside the box. The box centre also
  keeps its fractional position, so the box does not shift by a whole pixel
  when the streak's centre crosses a pixel boundary.

`trail_width_px` still holds the fitted width of each step, unsmoothed, in
pixels. The resolution estimate reads it. The half-width used at each step,
in pixels, is in the extractor's `last_diagnostics.aperture_half_width_px`.

**The sky.** The pipeline measures the sky in two strips, one on each side
of the box, separated from it by a 6-pixel gap and 10 pixels wide. It does
this at every step:

1. It removes pixels more than 3 sigma from the strip's median (hot pixels,
   cosmic rays, the edge of a passing streak), repeats until nothing more
   drops out, and takes the median of what is left.
2. When the two strip medians agree to within noise, the sky is their mean.
3. When they differ by more than 3 times the noise of their difference, the
   pipeline treats the brighter strip as contaminated (light from a
   neighbouring star can only add to a strip) and uses the other strip
   alone.
4. When only one strip has enough pixels on the image, it uses that strip.

Taking the lower of the two medians every time would read low by about 0.2
of one pixel's noise, for strips of this size, because the smaller of two
noisy numbers sits below their true value. A faint streak would then keep
too much sky in its total.

The extractor records how it found the sky in `last_diagnostics`.
`sky_mode_counts` counts the sky readings by mode: `both_bands`,
`lower_band_contaminated` or `upper_band_contaminated` (the strip on the
lower or higher side of the streak was dropped), `single_band` and `no_sky`.
`dominant_sky_mode` is the most common mode, and `contaminated_sky_fraction`
is the share of readings that dropped a strip, from 0 to 1. A fraction well
above a few percent means a neighbour's light lies beside the streak, and
the star's spectrum deserves a closer look. The pipeline copies a summary
onto the star's saved result as `SpectroscopyResult.extraction_diagnostics`
(a `SpectralExtractionDiagnostics`). It holds the sky mode counts, the
dominant mode, the contaminated fraction, and the median and standard
deviation of the box half-width in pixels. The two half-width values are
empty (`None`) for an untraced extraction.

## Airmass extinction

Air dims blue light more than red light, and it dims both more at higher
airmass (a measure of how much air the light crossed: 1.0 straight overhead,
2.0 about 60 degrees from overhead). The instrument response already removes
the dimming at the airmass of the standard star it was fitted to
(`reference_airmass`, stored in the response file). A target observed at
another airmass keeps a blue-to-red tilt from the difference. Between 4200 A
and 8000 A the tilt is about 0.09 magnitudes for a change of 0.35 airmass.

After it removes the instrument response, the pipeline multiplies the
spectrum by `10 ** (0.4 * k * (target_airmass - reference_airmass))`. Here
`k` is the extinction coefficient in magnitudes per airmass, read from
`data/atmospheric_extinction_kpno.txt` at each wavelength. The target
airmass comes from the frame header's `AIRMASS` card.

The pipeline skips the correction, and records why, when the header has no
usable airmass (missing, below 1 or above 10) or the response file records
no reference airmass. The result of the correction is an
`ExtinctionCorrection` record: `is_applied`, `target_airmass`,
`reference_airmass`, `curve_name` and `reason` (set only when skipped). The
spectrum analysis carries this record as `SpectrumAnalysis.extinction_correction`.
The pipeline copies the record onto the star's saved result as
`SpectroscopyResult.extinction_correction` (an `ExtinctionCorrectionRecord`).
It is empty (`None`) when no instrument response was applied.

`scripts/recompute_spectral_analysis.py` applies this same correction when
it re-analyzes a stored spectrum, using the same function. The script has no
frame header, so it takes the target airmass from the stored
`extinction_correction` record. A spectrum saved without that record is left
uncorrected, and the new record says why.

The stored curve is the mean Kitt Peak curve, a dry site at 2 km altitude.
A site at lower altitude has somewhat more extinction in the blue. The
correction depends on the difference between two airmasses, so the error from
using a different site's curve stays a small fraction of the correction.

## Atmospheric differential refraction

Air bends starlight so that a star looks higher in the sky than it is. It
bends blue light more than red light. The zero-order image (the star's
undispersed image) is a white-light image, so it sits where the
brightness-weighted mix of wavelengths lands. The light of every other
wavelength lands a little closer to the zenith (blue) or a little farther
from it (red) than the zero order. The pipeline's wavelength scale assumes
every wavelength lies on a straight line from the zero order, so this shift
has two effects:

- **Along the streak**, it moves a wavelength to a pixel that the scale
  labels with a different wavelength. This is a wavelength error.
- **Across the streak**, it moves the wavelength sideways. This widens the
  streak and changes no wavelength.

At sea level, between 4200 A and 8000 A, the shift is about 1.6 arcseconds
at airmass 1.5 and 2.5 arcseconds at airmass 2. One pixel covers 1.915
arcseconds, so the shift is roughly 0.8 to 1.3 pixels. At about 11.4 A per
pixel, a streak that lies along the vertical gets a wavelength error of 10
to 15 A between its ends.

The pipeline corrects the part along the streak (`differential_refraction.py`,
`atmospheric_refraction.py`). For each spectrum it does the following:

1. **Find the time.** The time is the middle of the exposure: `DATE-OBS`
   plus half of `EXPTIME`, or halfway to `DATE-END` when the header has it.
   A header with no time of day gives no result.
2. **Find the altitude and the parallactic angle.** With astropy and the
   observatory site, it finds the target's altitude and the position angle
   (measured on the sky from north toward east) of the direction from the
   target to the zenith. That angle is the parallactic angle.
3. **Find the direction of the dispersion on the sky.** The pipeline knows
   the direction in which wavelength grows along the streak as an angle on
   the image. It sends two points along that direction through the frame's
   WCS to get the position angle on the sky. The same step gives the sky
   angle covered by one pixel along the streak.
4. **Split the shift.** The angle `phi` is the dispersion's position angle
   minus the parallactic angle. The shift of a wavelength `L` toward the
   zenith is `R(L) - R(L_eff)`, where `R` is the refraction and `L_eff` is
   the zero order's effective wavelength. Its part along the streak is that
   shift times `cos(phi)`. Its part across the streak is that shift times
   `-sin(phi)`. A positive along-streak shift points toward longer
   wavelengths.
5. **Find the refraction.** `R(L) = (n(L) - 1) * tan(z)`, where `z` is the
   zenith distance (the angle between the target and the zenith) and `n` is
   the refractive index of air from the Ciddor (1996) equations. The index
   depends on the air's pressure, temperature and humidity. The pipeline
   reads `pressure_hpa`, `temperature_c` and `relative_humidity_percent`
   from the `[Observatory.Location]` section of the config. Any of the three
   that the config leaves out comes from the standard atmosphere scaled to
   the site's elevation, which is dry. The record says which source each
   number came from. The model treats the atmosphere as flat layers
   (plane-parallel), so the pipeline refuses to use it below an altitude
   of 20 degrees.
6. **Find the effective wavelength of the zero order.** `L_eff` is the mean
   wavelength of the extracted spectrum, weighted by the counts. The counts
   already carry the camera's sensitivity and the star's spectrum, which are
   the two things that set where the zero-order centroid lies. The estimate
   leaves out the transmission of the grating's zero order.
7. **Shift the wavelengths.** The pipeline converts the along-streak shift
   from pixels to Angstroms with the local dispersion, the derivative of
   the grating equation (about 11.3 A per pixel at 4200 A and 11.0 A at
   8000 A for the 200 lines/mm grating at 16.49 mm). It subtracts the
   result from each sample's wavelength, and repeats three times because the
   shift depends on the wavelength it moves.

Where the step sits: `_process_single_star` in `pipeline.py` computes the
wavelength scale. `_apply_result_to_stellar_object` then shifts the
wavelengths first, before the quantum-efficiency correction, the instrument
response and the airmass correction. The stored spectrum, the response
correction and the classifier all read the shifted wavelengths.
`_process_single_star`'s own result keeps the unshifted scale.

The pipeline reads the site from the config when it builds itself without
an explicit config (`SpectroscopyPipeline()`), and `analyze_frame_spectroscopy`
passes it in. The `SpectroscopyPipeline` constructor also takes
`observatory_site` and `atmospheric_conditions`. The stored instrument
response was derived without this correction. A response that
`derive_instrument_response.py` derives with a site configured fits the
shifted wavelengths.

The result of the step is a `DifferentialRefraction` record. The pipeline
copies it onto the star's saved result as
`SpectroscopyResult.differential_refraction` (a
`DifferentialRefractionRecord`). It holds `is_computed`, `is_applied`
(`dar_correction_applied`), `reason` (set when the pipeline skipped the
step), the mid-exposure time, the altitude, the parallactic angle, the
dispersion's position angle, the angle between them, the pixel scale, the
effective wavelength, the air's conditions and their source, and the shift
at 4200 A and 8000 A along the streak (arcseconds and Angstroms) and across
it (arcseconds). A number is empty (`None`) when the pipeline could not
compute it. The record gives the shift across the streak in arcseconds only,
because that shift changes no wavelength.

Checkpoint 0 reports the refraction metrics (see "Quality checkpoints 0 and
1" below).

## Wavelength zero point

The wavelength scale is the grating equation with one fitted number, the
grating distance, and an anchor at the centre of the star's zero-order image.
A saturated or crowded zero order, or a centroid pulled toward a neighbour,
moves the anchor. Every wavelength of that spectrum then shifts by the same
amount, the zero-point error. Nothing before this step measures it for one
spectrum. `wavelength_zero_point.py` measures it and, when the evidence is
enough, removes it.

1. **Look for six known lines.** The pipeline tries H-alpha (6562.8 A),
   H-beta (4861.3 A), H-gamma (4340.5 A), Na D (5892.9 A), Mg b (5175 A) and
   the oxygen A band (7605 A). All wavelengths are in air. It does not know
   the star's type yet, so it tries every line and keeps only the ones it
   finds. The oxygen A band comes from Earth's air, so every spectrum has it
   whatever the star is.
2. **Fit each line.** The pipeline fits a smooth continuum on both sides of
   the line and slides a Gaussian dip, as wide as the instrument blur at that
   wavelength (`load_line_spread_profile`), over plus or minus one blur width
   around the known wavelength. The dip's centre minus the known wavelength is
   the line's offset.
3. **Keep a line only if it is significant.** A dip must be at least 6 noise
   widths deep (`MINIMUM_LINE_SIGNIFICANCE`) and its centre must lie inside
   the search range. A noise-only spectrum finds no line.
4. **Combine the lines.** The spectrum's offset is the error-weighted mean of
   the lines' offsets. Each line's error is its fit error and the uncertainty
   of its own position (blended lines, stellar motion), added in quadrature.
5. **Apply or only record.** The pipeline subtracts the offset from every
   wavelength when at least two lines were found and they agree within their
   errors (a chi-square test, p-value at least 0.01). Otherwise it records the
   offset as measured only. One line cannot be told apart from a misidentified
   feature. Lines that disagree point to a stretched scale, not a shifted one.

The shift happens in `_apply_result_to_stellar_object` right after the
refraction correction, so it measures only the constant offset that is left
and does not remove the refraction shift a second time. Both come before the
quantum-efficiency, instrument-response and airmass-extinction corrections.
All three depend on wavelength, so they must see the corrected values.

Checkpoint 1 carries these metrics:

| Metric | Unit | Limit | What it tells a reader |
|---|---|---|---|
| `wavelength_zero_point_offset_angstrom` | angstrom | 20, lower is better (designed) | The size of the offset before correction. The limit is half a resolution element at 5000 A (about 40 A). It is a designed value, not a measured one. |
| `wavelength_zero_point_uncertainty_angstrom` | angstrom | none | The error of the offset. |
| `wavelength_zero_point_line_count` | lines | none | How many lines passed the significance test. |
| `wavelength_zero_point_applied` | flag | none | 1 when the pipeline removed the offset, 0 when it only measured it. |

The checkpoint raises `wavelength_zero_point_unconstrained` when fewer than two
lines were found, `wavelength_zero_point_lines_disagree` when two or more lines
were found but failed the chi-square test, and `wavelength_zero_point_large`
when the offset is over its limit. The `wavelength_zero_point` field of the
result keeps the signed offset, the chi-square, each line and whether the zero
order was saturated. The run-level scatter of these offsets is described in the
[pipeline README](../README.md).

Limits of the method: the correction is a constant shift, so it fixes an
anchor error but not a wrong grating distance. A line can be measured only
when the true offset is smaller than about one blur width. A different feature
near a line can be mistaken for it, for example the CH G band at 4304 A beside
H-gamma in G and K stars; the chi-square test catches that only when other
lines are present to disagree with it.

## What this stage produces

This stage produces a calibrated spectrum, brightness by wavelength, for
each identified star, corrected for everything that is not the star's own
light, plus an
[`InputQualityAssessment`](../../../models/spectroscopy_quality.py) that
records how trustworthy the raw data was.

## Quality metrics from this stage

The input quality assessment answers one question: was this spectrum worth
analyzing at all? It does not depend on what the classifier or the feature
tests later conclude, only on the raw data itself. It records:

- **Resolution**: how much the instrument blurred this spectrum, in
  Angstroms, and whether the pipeline measured that blur from this
  spectrum's own trail width or fell back to a fixed default. A finer
  resolution means later stages can trust smaller differences between
  spectral types. A coarser resolution, or a resolution the pipeline had to
  estimate, means two nearby types can be hard to tell apart no matter how
  good the rest of the data is.
- **Saturation**: the fraction of pixels at the star's brightest point that
  were pinned at the sensor's maximum value. A high fraction means the
  brightest part of the star's streak may be unreliable.
- **Coverage**: the fraction of the star's spectrum that actually landed on
  the image, inside the camera's usable range. A value below one means part
  of the streak ran off the edge of the picture.
- **Signal-to-noise**: how strongly the spectrum stands out from its own
  noise.

A reviewer can use this assessment to judge whether a later classification
rests on strong data or weak data, before looking at the classification
itself.

## Quality checkpoints 0 and 1

This stage builds two checkpoints in the common `StageQualityCheckpoint`
shape (see "Quality checkpoints" in the [pipeline README](../README.md)).

- **Checkpoint 0, raw frame** (`assess_raw_frame_quality.py`): the pipeline
  builds it right after it extracts a star. It reuses the zero-order
  saturated fraction, the valid fraction, the median trail width and the sky
  contamination from the extraction, and re-measures nothing. It adds the
  frame's streak tilt and peak above the sky only when the caller supplies the
  frame-level check result. It also carries the four refraction metrics and
  three flags described below.
- **Checkpoint 1, calibrated spectrum** (`input_quality_checkpoint` in
  `assess_input_quality.py`): it carries the five numbers of the input quality
  assessment above and the four wavelength zero-point metrics. Its
  signal-to-noise metric uses the limit
  `MINIMUM_SPECTRUM_SIGNAL_TO_NOISE`, below which the pipeline does not
  classify the spectrum.

The four refraction metrics of checkpoint 0:

| Metric | Unit | Limit | What it tells a reader |
|---|---|---|---|
| `dar_along_dispersion_angstrom` | angstrom | 20, lower is better. A design choice: half the resolution element at 5000 A (about 40 A). Not measured. | The spread of the wavelength error between 4200 A and 8000 A before the correction. The metric reports it whether or not the pipeline applied the correction. |
| `dar_across_dispersion_px` | pixel | none | How much refraction widens the streak between 4200 A and 8000 A. |
| `target_altitude_degrees` | degree | 20, higher is better. A design choice for the plane-parallel model, not a measurement. | The target's altitude at mid-exposure. Below the limit the pipeline skips the correction. |
| `parallactic_to_dispersion_angle_degrees` | degree | none | The angle from the zenith direction to the dispersion direction. 0 means the red end points at the zenith. |

The flags: `dar_large` when `dar_along_dispersion_angstrom` is over its
limit; `dar_not_computed` when the pipeline had no site, WCS, time or usable
altitude (the metrics then have no value); `target_altitude_low` when the
altitude is under 20 degrees.

Both read the saturation limit `DEFAULT_SATURATION_FLAG_THRESHOLD` from
`pipelines/shared/quality/saturation.py`. The `spectroscopy` result stores
them as the first two entries of `stage_quality`.

For exact behavior, thresholds and edge cases, read the code
(`spectrum_extractor.py`, `atmospheric_extinction.py`,
`atmospheric_refraction.py`, `differential_refraction.py` and
`instrument_response.py`).
