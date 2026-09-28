# Processing

This stage finds things in a star's calibrated spectrum. Everything here
takes a spectrum that pre-processing already cleaned up and works out what
that spectrum shows about the star.

## The flow

1. **Check for a real signal.** A star too faint for the frames produces a
   "spectrum" that is just noise sitting on zero. The pipeline checks for
   this case before running anything else, because a classification or
   feature test still produces an answer for pure noise. That answer does
   not mean anything.
2. **Flag possible second-order contamination.** A grating sends a small
   amount of a star's blue light to a second position further along the
   sensor, where it lands on top of the star's own red light. For a hot
   star this can account for a meaningful share of the reported red
   brightness, so the pipeline flags the risk instead of ignoring it.
3. **Look for named features.** The pipeline checks the spectrum at the
   known wavelengths of standard absorption features, such as the hydrogen
   series and the Ca II H&K blend, and asks at each one whether the dip is
   deep enough that noise alone is unlikely to have produced it.
   - For a star, these are absorption dips.
   - For a glowing gas cloud (a nebula), the pipeline runs the same check
     in reverse and looks for bright emission humps instead of dips.
4. **Classify the star.** The pipeline compares the spectrum's overall
   shape, such as how steeply its brightness changes across colours and how
   strong its hydrogen lines are, against a library of reference spectra
   and reports the closest match as the star's likely spectral type.
5. **Measure the spectrum's own colour.** The pipeline measures the star's
   blue-versus-visual brightness ratio, its B-V colour, directly from the
   spectrum, independent of every other step in this stage. Post-processing
   later checks this measurement against the star's catalog colour.

## What this stage produces

This stage produces a likely spectral type, the features found or not found
at each expected wavelength, a second-order-contamination risk flag, and
the spectrum's own measured colour. It attaches all of these to the star
for post-processing to evaluate.
