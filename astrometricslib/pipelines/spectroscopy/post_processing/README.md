# Spectroscopy post-processing

This stage evaluates the result of processing: it checks the result against what the pipeline already knew about the star and records the numbers a reviewer needs to judge the classification. Nothing here changes the classification itself, and it states no verdict about it: each check compares a number with its limit and reports the outcome.

Terms are defined in the [glossary](../../../../documentation/Glossary.md). This document also defines each term the first time it appears.

## What this stage does, in order

In plain words, the classifier always names some closest reference spectrum, even when none fits well. This stage measures how far the answer is from what star catalogs already say, how close the best match is, and how much better it is than the runner-up. It also checks the calibration against an independent spectrum of the same star from the Gaia satellite.

1. **Compare against the catalog.** Most stars already have a spectral type and a B-V color on record, from SIMBAD (a general star database) or Gaia (a satellite survey of over a billion stars), measured by a different method. B-V is the star's magnitude through a blue filter minus its magnitude through a visual, yellow-green filter. Magnitudes are a logarithmic brightness scale on which larger is fainter, so a larger B-V is redder. The pipeline checks the star's self-determined type and measured color against those catalog values. A disagreement can mean the spectrum belongs to a bright neighbor or that the pipeline measured the wrong object, so the pipeline records the size of the difference and a failed check rather than discarding the spectrum.

   A catalog giant or supergiant (a large, evolved star; luminosity class III or I) is a known special case: the classifier only compares against ordinary, dwarf (main-sequence, class V), reference stars, so a giant not matching a dwarf reference is an expected outcome, not a contradiction.
2. **Check the classification's own match numbers.** Independent of the catalog comparison, the pipeline checks the classification for two warning signs: a best match that is still far from the spectrum, and a best match that is nearly tied with a rival type. Both checks read the relative RMS (root-mean-square difference as a fraction of the spectrum's average brightness) that the classifier records. Neither check uses a probability.
3. **Report each check as a number against its limit.** The final quality checkpoint (checkpoint 3) lists the catalog distance, the color difference and the gap to the second best reference, each with its limit and whether it passed. The other two match numbers are checked at checkpoint 2. The pipeline makes no single trust verdict. Whether a result is good enough to use is a suggestion for wayfindinglib (the library that plans the observing) to make from these numbers.
4. **Compare with Gaia XP.** Independent of the catalog, the pipeline compares the calibrated spectrum with the star's Gaia DR3 XP spectrum, a low-resolution spectrum from a different instrument. The comparison measures the tilt, the residual and the wavelength shift of the calibration. The code is in `compare_to_gaia_xp.py`. [Check against Gaia XP](#check-against-gaia-xp) below gives the method and the limits. A star with no known Gaia id is recorded as not checked, with the reason.

## What this stage produces

This stage produces a record of how the star's spectrum compares with its catalog entry and the final quality checkpoint (checkpoint 3). It attaches both to the star, alongside the classification and input-quality measurement from the earlier stages, so a reviewer can see all three at once.

## Catalog comparison and the match checks

The catalog comparison records the numbers and, when a check fails, a short note explaining why:

- **Spectral type distance.** How many subtype steps apart the self-determined type and the catalog type are. A step is a tenth of a letter class on the O-to-M ladder (the spectral types O, B, A, F, G, K, M, from hottest to coolest) (B0 is 10, A5 is 25), so 20 steps is two whole classes. The check fails, and the comparison stores a note, when the distance is more than `DIFFERS_FROM_CATALOG_SUBTYPES` (20). The distance is checkpoint 3's `catalog_type_steps_apart`.
- **Luminosity class.** Whether the catalog calls the star a giant or supergiant. When it does, the pipeline also records the giant or supergiant reference spectrum that looks closest to this star, since the ordinary type comparison above is not a fair test for a giant.
- **Color difference.** The spectrum's own measured B-V color, from the processing stage, minus the catalog's B-V color, stored as `b_minus_v_difference_mag`. The check fails, and the comparison stores a note, when the size of the difference is more than 0.4 mag (partly measured; see [SPE-27](../../../../documentation/library_design/validation/spectroscopy.md#spe-27)) (`COLOUR_DISAGREEMENT_MAGNITUDES`). A catalog color redder than 1.7 (design estimate; see [spectroscopy design estimates](../../../../documentation/library_design/validation/spectroscopy.md#design-estimates)) is not compared (`MAXIMUM_CALIBRATED_B_MINUS_V`), because the references stop at M4V (B-V 1.64) and the color measurement is not calibrated beyond it.

The classification's own match numbers are three more checks, all in relative RMS:

- **Best match distance.** The best reference's relative RMS (`classification_rms`). The check fails when it is above `NO_GOOD_MATCH_RMS` (0.15). It is empty for a star with no classification.
- **Gap to the second best.** The gap between the best and the second-best reference (`rms_gap_to_second_best`). The check fails when it is below `AMBIGUOUS_RMS_GAP` (0.02). The gap is a difference in RMS, not a probability. It is empty when fewer than two references were compared. Neighboring subtypes of one class are often this close, so this check often fails. It fails no run gate (the `spectral_classification` and `processing_quality` gates look only at the class-level gap).
- **Gap to the next class.** The gap between the best reference and the best reference of a different spectral class letter (`rms_gap_to_next_class`). The check fails when it is below `AMBIGUOUS_RMS_GAP`. It is empty when no other class was compared. The `spectral_classification` gate fails on this check, not on the subtype one.

The older "trustworthy" yes/no verdict and the yes/no copies of these checks are gone. Results saved with them still load; the models ignore the old fields.

## Check against Gaia XP

**What and why.** An independent spectrum of the same star, taken from space, shows whether this pipeline's calibration is right. A tilt in the ratio of the two spectra points at the color correction; a sideways shift points at the wavelength scale.

The Gaia satellite measured a low-resolution spectrum, the XP spectrum, for about 220 million stars. Its resolving power (wavelength divided by the smallest resolvable wavelength difference) is about 30 to 100, close to the Star Analyser's (the grating this instrument uses). It comes from a different instrument above the atmosphere. The pipeline compares each calibrated spectrum with the XP spectrum of the same star. The comparison tests the instrument response (the color tilt the camera and grating add), the airmass correction (for the air's dimming) and the wavelength scale together. The code is in `compare_to_gaia_xp.py`.

1. **Find the Gaia DR3 source id.** The pipeline reads `StellarObject.gaia_dr3_source_id` through `catalog_star_identity.gaia_dr3_source_id_of`, the same function the reddening lookup uses. The star identifier fills it from the `Gaia DR3 <number>` name in SIMBAD's list of identifiers, or from a star that was named from Gaia. If the star has none (the brightest stars are not in Gaia DR3), the comparison is `not_checked` with that reason.
2. **Fetch the XP spectrum** through the `GaiaXpDriver` (`drivers/gaia_xp_driver.py`). The driver caches each spectrum on disk. A source with no XP spectrum gives `not_checked` with the reason `REASON_NO_XP`. A failed download (the driver raises `GaiaXpDownloadError`) gives `not_checked` with the different reason `REASON_XP_DOWNLOAD_FAILED`, so a run where the network was down does not look like a run where no star has an XP spectrum. Neither stops the pipeline. The run stores a notice with the number of stars whose download failed (`spectroscopy.notice_gaia_xp_download_failed`), which appears in the job's warnings.
3. **Put both on the same quantity.** The response-corrected spectrum is the counts divided by the response, which is Vega observed over the Pickles A0V template (a published reference spectrum of a star of Vega's type, from the Pickles 1998 library). Pickles fluxes are energy per unit wavelength, so the corrected spectrum is a relative F-lambda (energy per unit time, area and wavelength) above the atmosphere. Gaia's sampled flux is also F-lambda, in W m⁻² nm⁻¹. The two differ by one constant, which the normalization removes. A difference between Vega and the A0V template stays inside every corrected spectrum, so it appears as an offset common to all stars.
4. **Match the resolutions.** XP's blur is a Gaussian of 95 Å full width at half maximum (FWHM) at 4340 Å, 130 Å at 4861 Å, 70 Å at 6563 Å and 185 Å at 8800 Å, with straight lines between. These are medians of a fit of the Pickles A0V template to the XP spectra of A0V stars (partly measured; see [SPE-28](../../../../documentation/library_design/validation/spectroscopy.md#spe-28)). The instrument's stored line spread is sharper than XP's below about 5000 Å and broader above it. Where the instrument is broader, the pipeline blurs XP to it. Where XP is broader, the pipeline blurs the observed spectrum to XP. The blur width is the quadrature difference, `sqrt(broad² - narrow²)` (Gaussian blurs add as the square root of the sum of their squares, so this is the extra blur that turns the narrow one into the broad one).
5. **Compare.** The pipeline keeps 4200 to 8000 Å outside the atmospheric bands, divides each spectrum by its median over 5400 to 5600 Å, and computes
   - the RMS of observed / XP - 1;
   - the slope of a straight line fitted to observed / XP, in percent of the flux at 5500 Å per 1000 Å. The fit weights samples by XP's errors, the spectrum's own errors when `response_corrected_intensity_errors` exists, and a 1% floor;
   - the median ratio and the RMS in four bands: 4200 to 5000 Å, 5000 to 6000 Å, 6000 to 7000 Å and 7000 to 8000 Å;
   - the wavelength shift that best correlates the two spectra's logarithmic derivatives (d ln F / dλ, which emphasizes the lines and edges and ignores the overall slope). The sign is positive when the observed features sit at longer wavelengths than XP's. The search covers ±60 Å. A correlation below 0.3 gives no shift. The 0.3 design estimate; see [spectroscopy design estimates](../../../../documentation/library_design/validation/spectroscopy.md#design-estimates) sits far above chance correlation and far below the correlation of two spectra of the same star (see [SPE-28](../../../../documentation/library_design/validation/spectroscopy.md#spe-28)).

The result is stored on `SpectroscopyResult.gaia_xp_comparison` (`GaiaXpComparison`, in `models/quality/gaia_xp_comparison.py`), together with the normalization levels and whether either spectrum was blurred.

**Limits.** The 0.05 residual limit is above XP's own 1 to 2% calibration error, 3% per 1000 Å is an 11% change across the compared range, and 11 Å is one pixel (design estimate; see [spectroscopy design estimates](../../../../documentation/library_design/validation/spectroscopy.md#design-estimates)).

**Evidence.** Synthetic tests recover a known tilt and shift; the shift's noise floor for a real spectrum of the same star is not known (partly measured; see [SPE-28](../../../../documentation/library_design/validation/spectroscopy.md#spe-28)). `astrometricslib/scripts/compare_spectra_with_gaia_xp.py` measures it on a processed target.

At the run level, the summary's `gaiaXpSummary` (`GaiaXpRunSummary`) gives, for each of the four bands, the median over the compared stars of observed / XP and its scatter. The scatter is 1.4826 times the MAD, the median absolute deviation: the median distance from the median, scaled to match a standard deviation for normal data while ignoring a few wild values. A star with several spectra counts once. That median ratio is the measured residual instrument response: dividing the corrected spectra by it makes them agree with Gaia.

## Settings

`astrometricslib/models/stellar_source.py` defines every limit in this stage once, each with a docstring that states its unit:

| Limit | Value (status) | Unit | Read by |
| --- | --- | --- | --- |
| `NO_GOOD_MATCH_RMS` | 0.15 (partly measured; see [SPE-16](../../../../documentation/library_design/validation/spectroscopy.md#spe-16)) | Relative RMS | `is_classification_poor_match`, the checkpoint 2 `classification_rms` metric, the `spectral_classification` gate |
| `UNRELIABLE_MATCH_RMS` | 0.45 (partly measured; see [SPE-17](../../../../documentation/library_design/validation/spectroscopy.md#spe-17)) | Relative RMS | The classifier (above it, no type is named) |
| `AMBIGUOUS_RMS_GAP` | 0.02 (measured; see [SPE-18](../../../../documentation/library_design/validation/spectroscopy.md#spe-18)) | Relative RMS (a difference of two RMS values) | `is_classification_class_ambiguous` (class level), the checkpoint 2 class-gap metric, the checkpoint 3 subtype-gap metric, the `spectral_classification` gate (class level only) |
| `DIFFERS_FROM_CATALOG_SUBTYPES` | 20 (partly measured; see [SPE-19](../../../../documentation/library_design/validation/spectroscopy.md#spe-19)) | Subtype steps | `compare_to_catalog`, the checkpoint 3 `catalog_type_steps_apart` metric, the `catalog_agreement` gate |

The modules in this stage import these values and define none of their own. A test (`test/test_threshold_single_definition.py`) reads their source and fails if one defines a second copy.

The color limits in `compare_to_catalog.py` are used only there and stay in that file. `COLOUR_DISAGREEMENT_MAGNITUDES` is 0.4 mag (partly measured; see [SPE-27](../../../../documentation/library_design/validation/spectroscopy.md#spe-27)). `MAXIMUM_CALIBRATED_B_MINUS_V` is 1.7 (design estimate; see [spectroscopy design estimates](../../../../documentation/library_design/validation/spectroscopy.md#design-estimates)).

`CatalogComparison` also stores `b_minus_v_difference_mag`, the spectrum's B-V minus the catalog's B-V in magnitudes (positive means the spectrum is redder). It is empty where the color is not compared. Rows saved before this field existed load with it empty.

The Gaia XP limits are in `compare_to_gaia_xp.py`. The residual RMS limit is 0.05 (design estimate; see [spectroscopy design estimates](../../../../documentation/library_design/validation/spectroscopy.md#design-estimates)). The tilt limit is 3% per 1000 Å (design estimate; see [spectroscopy design estimates](../../../../documentation/library_design/validation/spectroscopy.md#design-estimates)). The wavelength shift limit is 11 Å (design estimate; see [spectroscopy design estimates](../../../../documentation/library_design/validation/spectroscopy.md#design-estimates)). The run gate needs at least 3 compared stars (design estimate; see [spectroscopy design estimates](../../../../documentation/library_design/validation/spectroscopy.md#design-estimates)). See [Check against Gaia XP](#check-against-gaia-xp) for the reasons.

## Quality metrics

`output_quality_checkpoint` in `assess_output_quality.py` builds the final `StageQualityCheckpoint` (stage `post_processing`) from the classifier's numbers and the catalog comparison. For an unclassified spectrum every classification value is `None` and the checkpoint carries the `unclassified` flag. The best match's distance (`classification_rms`) and the gap to the next class (`rms_gap_to_next_class`) are checked against their limits at checkpoint 2 (see the [processing README](../processing/README.md#quality-metrics)), so they are not repeated here as metrics.

| Field | Unit | Limit (status) | High or low means | Read by |
| --- | --- | --- | --- | --- |
| `catalog_type_steps_apart` | subtype steps | `DIFFERS_FROM_CATALOG_SUBTYPES` (20), lower is better (partly measured; see [SPE-19](../../../../documentation/library_design/validation/spectroscopy.md#spe-19)) | The distance on the O-to-M ladder between the measured and the catalog type (B0 is 10, A5 is 25). | The star panel's catalog chip; reviewers |
| `rms_gap_to_second_best` | relative RMS | `AMBIGUOUS_RMS_GAP` (0.02), higher is better (measured; see [SPE-18](../../../../documentation/library_design/validation/spectroscopy.md#spe-18)) | How much closer the best reference is than the runner-up. A gap below the limit means the subtype is uncertain. This is common, because neighboring subtypes often fit about equally well. | The star panel's separation line; reviewers |
| `colour_difference_mag` | magnitude | `COLOUR_DISAGREEMENT_MAGNITUDES` (0.4), lower is better (partly measured; see [SPE-27](../../../../documentation/library_design/validation/spectroscopy.md#spe-27)) | The size of the spectrum's B-V minus the catalog B-V. Empty when the color is not compared. | Reviewers |
| `gaia_xp_residual_rms_fraction` | fraction | 0.05, lower is better (design estimate; see [spectroscopy design estimates](../../../../documentation/library_design/validation/spectroscopy.md#design-estimates)) | The RMS of (observed / Gaia XP - 1) over 4200 to 8000 Å. See [Check against Gaia XP](#check-against-gaia-xp) below. Empty when the spectrum was not compared. | Reviewers |
| `gaia_xp_slope_percent_per_1000_angstrom` | percent per 1000 Å | 3, lower is better (design estimate; see [spectroscopy design estimates](../../../../documentation/library_design/validation/spectroscopy.md#design-estimates)) | The absolute tilt of observed / Gaia XP. A tilt points at the instrument response or the airmass correction. | Reviewers |
| `gaia_xp_wavelength_shift_angstrom` | Å | 11, lower is better (design estimate; see [spectroscopy design estimates](../../../../documentation/library_design/validation/spectroscopy.md#design-estimates)) | The absolute shift of the spectrum's features against Gaia XP's. 11 Å is one pixel of dispersion. A shift points at the wavelength scale. | Reviewers |
| `gaia_xp_available` | flag | none (reported only) | 1 when the spectrum was compared with Gaia XP, 0 when not. The note gives the reason. | Reviewers |

The Gaia XP metrics are filled for classified and unclassified spectra alike, because they test the calibration, not the classification. When a Gaia XP metric fails, the checkpoint carries the `gaia_xp_disagrees` flag.

For a classified spectrum, checkpoint 3 also raises one flag for each failed check: `poor_match` and `class_ambiguous` (repeating the failed checkpoint 2 checks), `subtype_ambiguous`, `catalog_disagrees` (the measured type is too far from the catalog type) and `colour_disagrees` (the B-V color is too far from the catalog). Each flag only names a failed check; there is no overall trust flag.

`record_quality.py` also rolls the four checkpoints of all spectra up into the run summary's `stage_quality_summary` and builds the `processing_quality` gate. `wavelength_scale.py` reads the wavelength zero-point metrics from the pre-processing checkpoints and builds the run-level `wavelength_scale_summary` and the `wavelength_scale` gate. The [pipeline README](../README.md) describes all three.

## Files

- `compare_to_catalog.py`: compares the measured type and color with the catalog values (step 1).
- `assess_output_quality.py`: builds the checkpoint 3 record, and the list of classifications a reviewer should look at.
- `compare_to_gaia_xp.py`: compares the calibrated spectrum with Gaia XP (step 4).
- `record_quality.py`: builds the run-level gates and the `stage_quality_summary`, and turns the run's numbers and gates into quality measurements (`spectroscopy_run_measurements`) and stores them (`record_spectroscopy_quality`).
- `wavelength_scale.py`: builds the run-level wavelength zero-point summary and the `wavelength_scale` gate.

For exact behavior, read the code.
