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
2. **Check the classification's own match numbers.** Independent of the
   catalog comparison, the pipeline checks the classification for two
   warning signs: a best match that is still far from the spectrum, and a
   best match that is nearly tied with a rival type. Both checks read
   the relative RMS (root-mean-square difference as a fraction of the
   spectrum's average brightness) that the classifier records. Neither check
   uses a probability.
3. **Combine the checks into one verdict.** The pipeline combines the
   catalog comparison and the classification's own match checks into a single
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

- **Spectral type agreement**: whether the self-determined type is within
  `DIFFERS_FROM_CATALOG_SUBTYPES` (20) subtype steps of the catalog type. A
  step is a tenth of a letter class on the O-to-M ladder (B0 is 10, A5 is
  25), so 20 steps is two whole classes. The star's `differsFromCatalog`
  field uses the same rule, so the two cannot disagree.
- **Luminosity class**: whether the catalog calls the star a giant or
  supergiant. When it does, the pipeline also records the giant or
  supergiant reference spectrum that looks closest to this star, since the
  ordinary type comparison above is not a fair test for a giant.
- **Colour agreement**: whether the spectrum's own measured B-V colour, from
  the processing stage, is close enough to the catalog's B-V colour to
  count as agreement.

The output quality assessment then combines this comparison with the
classification's own match checks into one overall verdict:

- **Poor match** (`is_poor_match`): the best reference's relative RMS
  (`classification_rms`) is above `NO_GOOD_MATCH_RMS` (0.15). Unit: relative
  RMS. It is `False` for a star with no classification.
- **Ambiguous at the subtype level** (`is_ambiguous`): the gap between the
  best and the second-best reference (`rms_gap_to_second_best`) is below
  `AMBIGUOUS_RMS_GAP` (0.02). Unit: relative RMS. The gap is a difference in
  RMS, not a probability. It is `False` when fewer than two references were
  compared. Neighbouring subtypes of one class are often this close, so this
  flag is common and does not fail the run gate.
- **Ambiguous at the class level** (`is_class_ambiguous`): the gap between
  the best reference and the best reference of a different spectral class
  letter (`rms_gap_to_next_class`) is below `AMBIGUOUS_RMS_GAP`. Unit:
  relative RMS. It is `False` when no other class was compared. The
  `spectral_classification` gate fails on this flag, not on the subtype one.
- **Catalog agreement**: repeats the spectral type agreement above, so a
  reviewer can see it without also opening the catalog comparison.
- **Trustworthy**: the overall verdict. False when any of the checks above
  raises a concern, true when none of them do.

A reviewer can read this verdict as the bottom line for a star's
classification, and open the individual checks above only when the bottom
line needs explaining.

## Where the limits live

`astrometricslib/models/stellar_source.py` defines every limit in this
stage once, each with a docstring that states its unit:

| Limit | Value | Unit | Read by |
|---|---|---|---|
| `NO_GOOD_MATCH_RMS` | 0.15 | Relative RMS | `is_classification_poor_match`, the star's `isPoorMatch` field, the `spectral_classification` gate |
| `UNRELIABLE_MATCH_RMS` | 0.45 | Relative RMS | The classifier (above it, no type is named) |
| `AMBIGUOUS_RMS_GAP` | 0.02 | Relative RMS (a difference of two RMS values) | `is_classification_ambiguous` (subtype level) and `is_classification_class_ambiguous` (class level), the star's `isAmbiguous` and `isClassAmbiguous` fields, the `spectral_classification` gate (class level only) |
| `DIFFERS_FROM_CATALOG_SUBTYPES` | 20 | Subtype steps | `compare_to_catalog`, the star's `differsFromCatalog` field, the `catalog_agreement` gate |

The modules in this stage import these values and define none of their
own. A test (`test/test_threshold_single_definition.py`) reads their source
and fails if one defines a second copy.

The colour limits in `compare_to_catalog.py`
(`COLOUR_DISAGREEMENT_MAGNITUDES` and `MAXIMUM_CALIBRATED_B_MINUS_V`) are
used only there and stay in that file.

For exact behavior, read the code.
