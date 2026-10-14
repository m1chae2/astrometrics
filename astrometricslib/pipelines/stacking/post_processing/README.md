# Post-processing

This step judges the finished stack and makes a picture of it for people. It never changes the stack file.

## What it does

1. **Output judgement** (`assess_output_quality.py`). Turns the measurements made on the stack into a `StackingOutputQuality` (`models/stacking_quality.py`).
2. **Thresholds** (`stack_quality.py`). The limits for the rejected-pixel share, the zero-pixel share and the negative-pixel warning.
3. **Saturation check** (`exposure_saturation.py`). Finds whether an exposure length saturates the brightest star and recommends one that does not.
4. **Exposure-group report** (`exposure_group_report.py`). Describes each exposure group in the summary.
5. **Preview** (`stack_preview.py`, `sky_level.py`, `star_tone.py`, `bright_object.py`). Makes the stretched JPEG. See `../README.md`, step 7.
6. **Previous stack** (`previous_stack.py`). Before a restack overwrites a stack, moves the old stack and its companion files (rejection map, registration table, preview, stretched FITS) into a staging folder. If the restack works, those files go into `_previous` and replace that stack's older version; the other stacks of the target keep theirs. If it fails, everything moves back. Only one previous version is kept. `discard_previous_stack` deletes it and `swap_with_previous_stack` exchanges it with the current stack. The setting `keep_previous_stack_enabled` (default `true`) turns this off.
7. **Stack comparison** (`stack_comparison.py`). Measures two stacks the same way (sky level, pixel noise, large-scale flatness, star width) and reports the change in each as a number and a sentence. It does not pick a winner: new flats should lower the flatness number and leave the noise alone, while more frames should lower the noise and leave the flatness alone.

## The quality metrics

- **Rejected pixel fraction.** The share of pixel values the stacker threw out, 0 to 1. A high value means many artifacts or a poor alignment.
- **Saturated pixel fraction.** The share of pixels at the camera's saturation level.
- **Zero pixel fraction.** The share of exactly-zero pixels. For a colour stack it is the share in the worst channel. A mostly-zero stack is blank, because calibration removed more than the sky.
- **Negative pixel maximum percent.** The worst "many negative pixels" warning Siril printed after dark subtraction.
- **Stacked, median input and expected FWHM.** Star width (full width at half maximum, in pixels, from a Gaussian fit to each star) in the stack and in a sample of the input frames spread over the whole set, images only. The expected width is the root mean square of the sampled frame widths, which is the width a stack of those frames should have. A stack more than 1.2 times wider than expected means alignment blurred it. A stack combined from exposure groups is measured without the stars whose cores were replaced by a shorter group's data. Those patched cores are the brightest stars in the combined image, because it has no saturated plateau left for the check to skip, and measured alone they read too wide. On the M 57 stack of 2026-10-03 they read 3.40 px against 2.38 px for the other stars.
- **Spectral registration concern count.** Spectral frames the registration check questioned.
- **Flag reasons.** One sentence per failed threshold. They feed the summary's flag reasons.

For exact thresholds, read the code.

## Colour calibration compared with Siril's

The preview uses Siril's PCC by default and falls back to the star-based method when PCC cannot run. The comparison below is why PCC was chosen as the default: it was the user's choice, made after seeing the pictures, not the most accurate method. SPCC is more accurate in principle, because it uses the camera's own sensitivity curves, but it takes about 45 seconds and the pictures differed little. The difference between PCC and SPCC pictures comes mostly from the white reference each assumes, and SPCC can be set to a warmer white reference.

`colour_calibration.py` balances the colours of a colour stack from its own stars. Siril has two calibrations of its own, both run here through `siril-cli` on the same gradient-flattened data (Siril 1.4.4):

- `pcc` (photometric colour calibration) matches the stars to the Gaia DR3 catalogue.
- `spcc` (spectrophotometric colour calibration) also uses the camera's measured sensitivity curves. It was run with the "Nikon D5300" sensor and the "Average Spiral Galaxy" white reference.

Gains are the factors for red and blue with green fixed at 1 (Siril reports factors for all three channels, so its red and blue are divided by its green).

| Target | Star-based red / blue | PCC red / blue | SPCC red / blue |
|---|---|---|---|
| M 51 | 1.48 / 1.34 | 1.79 / 1.10 | 1.68 / 1.29 |
| M 31 | 1.78 / 1.33 | 1.85 / 1.29 | 1.60 / 1.29 |
| M 106 | 1.69 / 1.33 | 1.93 / 1.24 | 1.72 / 1.29 |
| NGC 6992 | 1.42 / 1.56 | 1.58 / 1.61 | 1.61 / 1.41 |
| IC 1805 | 1.51 / 1.66 | 1.62 / 1.74 | 1.78 / 1.41 |
| NGC 6888 | 1.60 / 1.51 | 1.84 / 1.52 | 1.86 / 1.30 |

What the numbers show:

- **The three methods agree on the direction and rough size.** Every method raises red by 1.4 to 1.9 and blue by 1.1 to 1.7. Uncalibrated, these stacks are strongly green.
- **They disagree with each other by 10 to 20 percent.** PCC and SPCC differ from each other as much as the star-based gains differ from either. The blue gain of M 51 is 1.10 (PCC) against 1.29 (SPCC). Which one is right depends on the white reference and the colour model, not on arithmetic.
- **The star-based gains are close to SPCC on galaxy fields and off on Milky Way fields.** On M 51, M 31 and M 106 the blue gain is within 3 percent of SPCC. On NGC 6992, IC 1805 and NGC 6888, which lie in the Milky Way, it is 11 to 18 percent higher. A likely reason, not tested: dust reddens the stars in those fields, so the median star is redder than a typical star, and the star-based method then adds too much blue. Its red gain is 2 to 15 percent below SPCC in five of six fields.
- **The printed uncertainty of the star-based gain (0.1 to 1.2 percent) is the scatter between stars only.** It leaves out the assumption that the median star is neutral, which is the larger error.
- **Siril needs a plate solution, an online catalogue and 10 seconds (PCC) to 45 seconds (SPCC) per stack.** The star-based method takes a few seconds and works offline. Siril warns that PCC and SPCC give an imprecise solution when the image still holds a gradient, so both were run after GraXpert.
- **The pictures differ little.** With a shared stretch, M 51 and M 31 look alike under all three. PCC gives the warmest, yellowest galaxy core. The star-based and SPCC pictures are more neutral.
