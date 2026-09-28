# Post-processing

This stage evaluates the result of processing: it checks the result against
what the pipeline already knew about the star and produces an overall trust
score for the classification. Nothing here changes the classification
itself. This stage only evaluates it.

## The flow

1. **Compare against the catalog.** Most stars already have a spectral type
   and a B-V colour on record, from SIMBAD or Gaia, measured by a different
   method. The pipeline checks the star's self-determined type and measured
   colour against those catalog values. A disagreement can mean the
   spectrum belongs to a bright neighbour or that the pipeline measured the
   wrong object, so the pipeline records it as a flag rather than
   discarding it. A catalog giant or supergiant is a known special case:
   the classifier only compares against ordinary, dwarf, reference stars,
   so a giant not matching a dwarf reference is an expected outcome, not a
   contradiction.
2. **Evaluate the classification's own confidence.** Independent of the
   catalog comparison, the pipeline checks the classification for two
   warning signs: a weak winning match, or a winning match nearly tied with
   the runner-up type.
3. **Combine the checks into one verdict.** The pipeline combines the
   catalog comparison and the classification's own confidence into a single
   trustworthy or not-trustworthy verdict per star, so a reviewer can read
   the bottom line without re-deriving it from the individual checks.

## What this stage produces

This stage produces a record of how the star's spectrum compares with its
catalog entry and an overall verdict on how much the classification can be
trusted. It attaches both to the star, alongside the classification and
input-quality measurement from the earlier stages, so a reviewer can see
all three at once.

## Quality metrics from this stage

The catalog comparison records, for each check, whether it agrees and, when
it does not, a short note explaining why:

- **Spectral type agreement**: whether the self-determined type is close
  enough to the catalog type to count as agreement.
- **Luminosity class**: whether the catalog calls the star a giant or
  supergiant. When it does, the pipeline also records the giant or
  supergiant reference spectrum that looks closest to this star, since the
  ordinary type comparison above is not a fair test for a giant.
- **Colour agreement**: whether the spectrum's own measured B-V colour, from
  the processing stage, is close enough to the catalog's B-V colour to
  count as agreement.

The output quality assessment then combines this comparison with the
classification's own confidence into one overall verdict:

- **Low confidence**: the winning spectral type had a weak match score.
- **Ambiguous**: the top two candidate types were too close to call apart.
- **Catalog agreement**: repeats the spectral type agreement above, so a
  reviewer can see it without also opening the catalog comparison.
- **Trustworthy**: the overall verdict. False when any of the checks above
  raises a concern, true when none of them do.

A reviewer can read this verdict as the bottom line for a star's
classification, and open the individual checks above only when the bottom
line needs explaining.
