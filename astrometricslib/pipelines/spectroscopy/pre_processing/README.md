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
   each star's streak, following the streak's exact center and width, and
   subtracts the sky background.
4. **Correct for crowding.** When two stars sit close together, a bright
   neighbour's streak can leak light into a faint star's reading. The
   pipeline measures that leakage and subtracts it back out.
5. **Convert pixels to wavelengths.** The pipeline converts each pixel
   position along the streak into a wavelength, using the grating's known
   physics, and smooths the result into a clean curve.
6. **Remove signals that are not the star.** The pipeline corrects for
   three things that are not properties of the star itself:
   - the camera sensor's uneven sensitivity to different colours,
   - the whole instrument's own tilt (the grating, the telescope's
     coatings, and every other optic between the star and the sensor),
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
