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
   divides its column offset by the cosine of the tilt. Both extraction
   paths use this same definition. The distance is the one position the
   grating equation expects, so a later step can place any sample on the
   physical model without counting samples from the start.
6. **Remove signals that are not the star.** The pipeline corrects for
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
7. **Record what the numbers mean.** The pipeline records whether a stored
   spectrum's brightness values are raw camera counts or an averaged,
   stacked scale, since the two scales are not directly comparable.
8. **Measure the input quality.** Before finding anything in the spectrum,
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

For exact behavior, thresholds and edge cases, read the code
(`spectrum_extractor.py`, `atmospheric_extinction.py` and
`instrument_response.py`).
