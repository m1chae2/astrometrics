# Post-processing

This stage evaluates the result of processing: it checks the result against
what the pipeline already knew about the star and produces an overall trust
score for the classification. Nothing here changes the classification
itself. This stage only evaluates it.

Terms are defined in the
[glossary](../../../../documentation/library_design/Astrometrics_Library_Architecture.md#appendix-a-glossary).
This README also defines each term the first time it appears.

In plain words: the classifier always names some closest reference spectrum,
even when none fits well. This stage asks whether the answer deserves trust:
does it agree with what star catalogs already say, is the best match close,
and is it clearly better than the runner-up? It also checks the calibration
against an independent spectrum of the same star from the Gaia satellite.

## The flow

1. **Compare against the catalog.** Most stars already have a spectral type
   and a B-V colour on record (the star's magnitude through a blue filter
   minus its magnitude through a visual, yellow-green filter; magnitudes are
   a logarithmic brightness scale on which larger is fainter, so a larger B-V
   is redder), from SIMBAD (a general star database) or
   Gaia (a satellite survey of over a billion stars), measured by a different
   method. The pipeline checks the star's self-determined type and measured
   colour against those catalog values. A disagreement can mean the
   spectrum belongs to a bright neighbour or that the pipeline measured the
   wrong object, so the pipeline records it as a flag rather than
   discarding it.

   A catalog giant or supergiant (a large, evolved star; luminosity class
   III or I) is a known special case:
   the classifier only compares against ordinary, dwarf (main-sequence,
   class V), reference stars,
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

4. **Compare with Gaia XP.** Independent of the catalog, the pipeline compares
   the calibrated spectrum with the star's Gaia DR3 XP spectrum, a low-resolution
   spectrum from a different instrument. The comparison measures the tilt, the
   residual and the wavelength shift of the calibration. The code is in
   `compare_to_gaia_xp.py`. "Check against Gaia XP" below gives the method
   and the limits. A star with no known Gaia id is recorded as not checked,
   with the reason.

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
  step is a tenth of a letter class on the O-to-M ladder (the spectral types
  O, B, A, F, G, K, M, from hottest to coolest) (B0 is 10, A5 is
  25), so 20 steps is two whole classes. The star's `differsFromCatalog`
  field uses the same rule, so the two cannot disagree.
- **Luminosity class**: whether the catalog calls the star a giant or
  supergiant. When it does, the pipeline also records the giant or
  supergiant reference spectrum that looks closest to this star, since the
  ordinary type comparison above is not a fair test for a giant.
- **Colour agreement**: whether the spectrum's own measured B-V colour, from
  the processing stage, is within 0.4 mag of the catalog's B-V colour
  (`COLOUR_DISAGREEMENT_MAGNITUDES`). 0.4 is a judgement from one data set
  (partly measured; see [SPE-27](../../../../documentation/library_design/validation/spectroscopy.md#spe-27)). A catalog colour redder than 1.7
  (`MAXIMUM_CALIBRATED_B_MINUS_V`) is not compared, because the references
  stop at M4V (B-V 1.64) and the colour measurement is not calibrated beyond
  it.

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
  flag is common. It fails no run gate (the `spectral_classification` and
  `processing_quality` gates look only at class-level ambiguity), but it does
  make the star's `is_trustworthy` verdict false (see below).
- **Ambiguous at the class level** (`is_class_ambiguous`): the gap between
  the best reference and the best reference of a different spectral class
  letter (`rms_gap_to_next_class`) is below `AMBIGUOUS_RMS_GAP`. Unit:
  relative RMS. It is `False` when no other class was compared. The
  `spectral_classification` gate fails on this flag, not on the subtype one.
- **Catalog agreement**: repeats the spectral type agreement above, so a
  reviewer can see it without also opening the catalog comparison.
- **Trustworthy** (`is_trustworthy`): the overall verdict,
  `not (is_poor_match or is_ambiguous or catalog disagrees)`. It is false
  when the match is poor, the subtype is ambiguous or the measured type
  disagrees with the catalog type. Because subtype ambiguity is common,
  a star whose class is clear can still be marked not trustworthy for that
  reason alone; the `subtype_ambiguous` flag at checkpoint 3 shows when that is
  the cause. Class-level ambiguity and a colour
  disagreement do not change it. They raise their own flags at checkpoint 3
  (`class_ambiguous` and `colour_disagrees`), and class-level ambiguity
  fails the `spectral_classification` gate.

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

## Quality checkpoint 3

`output_quality_checkpoint` in `assess_output_quality.py` turns the output
quality assessment into the final `StageQualityCheckpoint` (stage
`post_processing`). It carries the verdicts of
`OutputQualityAssessment` and the catalog distance. Each verdict is 1 (yes) or
0 (no). For an unclassified spectrum every classification value is `None`
and the checkpoint carries the `unclassified` flag.

| Metric | Unit | Limit | What it tells a reader |
|---|---|---|---|
| `catalog_type_steps_apart` | subtype steps | `DIFFERS_FROM_CATALOG_SUBTYPES` (20), lower is better | The distance on the O-to-M ladder between the measured and the catalog type (B0 is 10, A5 is 25). |
| `poor_match` | flag | 0, lower is better | 1 when `classification_rms` is above `NO_GOOD_MATCH_RMS`. |
| `subtype_ambiguous` | flag | 0, lower is better | 1 when the gap to the second best is below `AMBIGUOUS_RMS_GAP`. Common, because neighbouring subtypes often fit about equally well. A 1 fails this metric and makes `trustworthy` 0. |
| `class_ambiguous` | flag | 0, lower is better | 1 when the gap to the next class is below `AMBIGUOUS_RMS_GAP`. |
| `catalog_agrees` | flag | 1, higher is better | 1 when the measured type agrees with the catalog type. |
| `colour_agrees` | flag | 1, higher is better | 1 when the spectrum's B-V colour agrees with the catalog colour. |
| `trustworthy` | flag | 1, higher is better | The overall verdict of `OutputQualityAssessment`: 0 when the match is poor, the subtype is ambiguous or the type disagrees with the catalog. |
| `gaia_xp_residual_rms_fraction` | fraction | 0.05, lower is better (designed) | The RMS of (observed / Gaia XP - 1) over 4200-8000 Å. See "Check against Gaia XP" below. Empty when the spectrum was not compared. |
| `gaia_xp_slope_percent_per_1000_angstrom` | percent per 1000 Å | 3, lower is better (designed) | The absolute tilt of observed / Gaia XP. A tilt points at the instrument response or the airmass correction. |
| `gaia_xp_wavelength_shift_angstrom` | Å | 11, lower is better (designed) | The absolute shift of the spectrum's features against Gaia XP's. 11 Å is one pixel of dispersion. A shift points at the wavelength scale. |
| `gaia_xp_available` | flag | none (reported only) | 1 when the spectrum was compared with Gaia XP, 0 when not. The note gives the reason. |

The Gaia XP metrics are filled for classified and unclassified spectra alike,
because they test the calibration, not the classification. When a Gaia XP metric
fails, the checkpoint carries the `gaia_xp_disagrees` flag.

For a classified spectrum, checkpoint 3 also raises one flag for each failed
verdict: `poor_match`, `subtype_ambiguous`, `class_ambiguous`,
`catalog_disagrees` (the measured type is too far from the catalog type),
`colour_disagrees` (the B-V colour disagrees with the catalog) and
`not_trustworthy`. The `trustworthy` verdict is
`not (poor_match or subtype_ambiguous or catalog_disagrees)`. Class
ambiguity and colour disagreement raise their own flags but do not change it.

`run_gates.py` also rolls the four
checkpoints of all spectra up into the run summary's `stage_quality_summary`
and builds the `processing_quality` gate. `wavelength_scale.py` reads the
wavelength zero-point metrics from the pre-processing checkpoints and builds
the run-level `wavelength_scale_summary` and the `wavelength_scale` gate. The
[pipeline README](../README.md) describes all three.

## Check against Gaia XP

What and why: an independent spectrum of the same star, taken from space,
shows whether this pipeline's calibration is right. A tilt in the ratio of
the two spectra points at the colour correction; a sideways shift points at
the wavelength scale.

The Gaia satellite measured a low-resolution spectrum, the XP spectrum, for about
220 million stars. Its resolving power (wavelength divided by the smallest
resolvable wavelength difference) is about 30 to 100, close to the Star
Analyser's (the grating this instrument uses). It comes from a different
instrument above the atmosphere. The
pipeline compares each calibrated spectrum with the XP spectrum of the same star.
The comparison tests the instrument response (the colour tilt the camera and
grating add), the airmass correction (for the air's dimming) and the
wavelength scale together. The code is in
`compare_to_gaia_xp.py`.

1. **Find the Gaia DR3 source id.** The pipeline reads `StellarObject.gaia_dr3_source_id`
   through `catalog_star_identity.gaia_dr3_source_id_of`, the same function the
   reddening lookup uses.
   The star identifier fills it from the `Gaia DR3 <number>` name in SIMBAD's list
   of identifiers, or from a star that was named from Gaia. If the star has none
   (the brightest stars are not in Gaia DR3), the comparison is `not_checked`
   with that reason.
2. **Fetch the XP spectrum** through the `GaiaXpDriver` (`drivers/gaia_xp_driver.py`).
   The driver caches each spectrum on disk. A source with no XP spectrum, or a
   failed download, gives `not_checked` and never stops the pipeline.
3. **Put both on the same quantity.** The response-corrected spectrum is the
   counts divided by the response, which is Vega observed over the Pickles A0V
   template (a published reference spectrum of a star of Vega's type, from the
   Pickles 1998 library). Pickles fluxes are energy per unit wavelength, so the
   corrected spectrum is a relative F-lambda (energy per unit time, area and
   wavelength) above the atmosphere. Gaia's sampled flux is
   also F-lambda, in W m⁻² nm⁻¹. The two differ by one constant, which the
   normalization removes. A difference between Vega and the A0V template stays
   inside every corrected spectrum, so it appears as an offset common to all stars.
4. **Match the resolutions.** XP's blur is a Gaussian of 95 Å full width at half
   maximum (FWHM) at 4340 Å, 130 Å at 4861 Å, 70 Å at 6563 Å and 185 Å at 8800 Å,
   with straight lines between. These are medians of a fit of the Pickles
   A0V template to the XP spectra of A0V stars (partly measured; see
   [SPE-28](../../../../documentation/library_design/validation/spectroscopy.md#spe-28)). The instrument's stored line
   spread is sharper than XP's below about 5000 Å and broader above it. Where
   the instrument is broader, the pipeline blurs XP to it. Where XP is broader,
   the pipeline blurs the observed spectrum to XP. The blur width is the
   quadrature difference, `sqrt(broad² - narrow²)` (Gaussian blurs add as the
   square root of the sum of their squares, so this is the extra blur that
   turns the narrow one into the broad one).
5. **Compare.** The pipeline keeps 4200 to 8000 Å outside the atmospheric bands,
   divides each spectrum by its median over 5400 to 5600 Å, and computes
   - the RMS of observed / XP - 1;
   - the slope of a straight line fitted to observed / XP, in percent of the flux
     at 5500 Å per 1000 Å. The fit weights samples by XP's errors, the spectrum's
     own errors when `response_corrected_intensity_errors` exists, and a 1 percent
     floor;
   - the median ratio and the RMS in four bands: 4200-5000, 5000-6000, 6000-7000
     and 7000-8000 Å;
   - the wavelength shift that best correlates the two spectra's logarithmic
     derivatives (d ln F / dλ, which emphasizes the lines and edges and ignores
     the overall slope). The sign is positive when the observed features sit
     at longer wavelengths than XP's. The search covers ±60 Å. A correlation below
     0.3 gives no shift. 0.3 is a design value, far above chance correlation
     and far below the correlation of two spectra of the same star (see
     [SPE-28](../../../../documentation/library_design/validation/spectroscopy.md#spe-28)).

The result is stored on `SpectroscopyResult.gaia_xp_comparison`
(`GaiaXpComparison`, in `models/gaia_xp_comparison.py`), together with the
normalization levels and whether either spectrum was blurred.

Settings: all three limits are designed, not measured: 0.05 is above XP's own 1 to 2
percent calibration error, 3 percent per 1000 Å is an 11 percent change across the
compared range, and 11 Å is one pixel.

How we know: synthetic tests recover a known tilt and shift; the shift's
noise floor for a real spectrum of the same star is not known yet (partly
measured; see [SPE-28](../../../../documentation/library_design/validation/spectroscopy.md#spe-28)).
`astrometricslib/scripts/compare_spectra_with_gaia_xp.py` measures it on a processed target.

At the run level, the summary's `gaiaXpSummary` (`GaiaXpRunSummary`) gives, for
each of the four bands, the median over the compared stars of observed / XP and
its scatter (1.4826 times the median absolute deviation, MAD: the median
distance from the median, scaled to match a standard deviation for normal
data while ignoring a few wild values). A star with several
spectra counts once. That median ratio is the measured residual instrument
response: dividing the corrected spectra by it makes them agree with Gaia.

## Files

- `compare_to_catalog.py`: compares the measured type and colour with the
  catalog values (step 1).
- `assess_output_quality.py`: builds the output quality assessment, the
  checkpoint 3 record, and the list of classifications a reviewer should
  look at.
- `compare_to_gaia_xp.py`: compares the calibrated spectrum with Gaia XP
  (step 4).
- `run_gates.py`: builds the run-level gates and the
  `stage_quality_summary`.
- `wavelength_scale.py`: builds the run-level wavelength zero-point summary
  and the `wavelength_scale` gate.

For exact behavior, read the code.
