# Processing

This step lines up and combines frames. Siril stacks the individual frames (see `stack_runner.py` one level up). The files here handle what Siril does not: stacks of different exposure lengths and spectral frames.

## What it does

- `exposure_groups.py` splits frames by exposure length, then combines the per-group stacks into one image. Each group is weighted by its noise, and a group is left out where it is saturated or clipped at zero. The step also puts every group on the brightness scale of the reference group (see "The brightness scale between exposure groups" below). `saturated_pixel_mask` marks those pixels, so a later check can tell which stars had their cores replaced.
- `group_alignment.py` finds the shift between group stacks, by the zero-order star or by phase correlation, and moves them into line. For imaging stacks it then refines the shift: it matches the stars of the two stacks and fits a shift, a rotation and a scale about the image centre (`refine_alignment_with_stars`). Stacks from different nights differ by a small rotation and scale, which a shift cannot remove. On M 57 and M 27 the stars of two group stacks were 0.6 to 1.1 pixels apart on average after the best shift (up to 1.9 pixels toward the frame edges), and the fit brought that to 0.04 to 0.06 pixels. When correlation fails, or its shift is too far off for the stars to pair up, the stacks are lined up from patterns of stars instead (`find_alignment_from_star_patterns`, with `star_pattern_candidates.py`). Small triangles of stars have a shape that does not change with shift, rotation or scale, so matching triangles gives a first guess with no starting offset. The guess is kept only if it lines up at least 30 stars, with a scale within 0.5 % of one and a leftover under 1.5 pixels. Any rotation is allowed, including the half turn of a mount flip at the meridian. On 2026-10-13 this lined up the Nikon D5300 groups of M 106, NGC 7023, M 51, NGC 2024 and NGC 6992, which correlation had refused (correlation 0.01 to 0.26).
- `spectral_frame_alignment.py` aligns spectral frames that have no usable star pattern.
- `group_derotation.py` turns spectral group stacks to one common trail tilt.

The refinement keeps the plain shift when it cannot trust the fit: fewer than 30 matched stars, a rotation over 0.5 degrees, a scale off by more than 0.5%, or stars that still miss by more than 0.5 pixel. The group manifest records the result for each group: `alignment_rotation_degrees`, `alignment_scale`, `alignment_star_pairs` and `alignment_residual_pixels`. Spectral stacks keep the plain shift.

Alignment accuracy matters most here. On the groups measured, these steps place frames within 0.05 pixels, while Siril's registration of the same groups was 0.06 to 3 pixels off. For that reason these steps stay in Python.

Pixel rejection when Siril combines the frames is set before this step runs. The limits come from `rejection_bounds` in `utilities/rejection_thresholds.py`, and the stacking README ("Pixel rejection limits") describes them and the measurements behind them.

## Finding the zero-order target

The zero order is the undispersed image of the target in a slitless spectrum frame. Three functions in `group_alignment.py` find it. Each returns a position as (row, column), in pixels, or `None` when it finds no clear target.

1. `find_zero_order_position` looks for a point source. It smooths the image by 2 pixels and takes the brightest spot within 150 pixels of the image centre. It accepts the spot only when no spot more than 40 pixels away is brighter than half of it. The position is good to a fraction of a pixel. The stack aligner and the trail-tilt measurement use this function, and its behaviour does not change.
2. `find_extended_zero_order_position` looks for a broad glow, such as a globular cluster, which has no single bright spot. It follows these steps:
   - It averages the image in 4 by 4 pixel blocks and subtracts the sky (the median of the blocks).
   - It smooths with a Gaussian of 25 pixels.
   - It takes the brightest local maximum within 150 pixels of the image centre. A local maximum is a pixel no lower than any pixel within 50 pixels of it.
   - It compares that peak with the next brightest local maximum within 300 pixels of the centre, and accepts the peak only when the rival is at most one third of its height.
   - It returns the brightness-weighted centre of the smoothed pixels that are at least half the peak height.
3. `locate_zero_order` runs function 1 and, if that returns `None`, function 2. It returns a `ZeroOrderPosition` with the row, the column and `is_extended_target`. `is_extended_target` is `True` when function 2 supplied the position. An extended-target position marks the middle of a glow tens of pixels wide, so it is good to a few pixels and no better. Callers that need sub-pixel accuracy, such as alignment on the star, should keep using function 1.

**Quality values.** The rival fraction (rival height divided by peak height, both above the sky, no units) decides the second search. Near 0 means one clear glow. Near 1 means two similar glows, and the search returns `None`. On the six M 13 spectroscopy frames (five single frames and the stack), function 1 returned `None` because its rival spot reached 0.61 to 0.64 of the peak, against a limit of 0.5. Function 2 found the cluster on all six, with a rival fraction of 0.08 and positions within about 1 pixel of each other. They sat 3 to 4 pixels from the fixed cluster-core point (row 1493, column 1485) used by the golden tests, mostly along the columns. The limit of one third, the 25 pixel smoothing and the 300 pixel rival window were chosen from those frames alone. Other targets are not measured. Other peaks of similar height lie 380 pixels and farther from the centre on the M 13 frames, so a target more than about 150 pixels off the frame centre is not reliably found.

`measure_spectral_frame_file` (in `pipelines/spectroscopy/pre_processing/spectral_frame_check.py`) uses `locate_zero_order` and reports the result as `zero_order_source`: `point source` or `extended target`. A caller who knows where the target is can pass the position and skip the search, and the source is then `given`. For exact thresholds and edge cases, read the code.

## The brightness scale between exposure groups

Siril leaves each group stack with its own overall brightness. Before the groups are averaged, `exposure_groups.py` divides each group's counts-per-second image by a gain (a single number) so that all groups read the same.

**How the gain is measured.** The reference group is the group with the most weight. For every other group, the step measures the same isolated stars in both groups (`measure_star_flux_ratios`):

1. It finds up to 3,000 stars in the reference and keeps those with no other detected star within 25 pixels.
2. It leaves out a star with any pixel in its circle or sky ring that is uncovered, saturated, or above half the camera's clip ceiling (`star_clip_level`). The ceiling comes from the camera profile (`clip_ceiling_adu`). Stars near the ceiling read low, which biases the gain.
3. It measures each star's flux with `measure_aperture_photometry`: the light inside a 14 pixel circle minus the sky median of the ring from 19 to 25 pixels. The circle is wide enough that a star a pixel wider in one group keeps the same share of its light.
4. It measures the noise a flux ratio carries, from empty apertures on the difference of the two groups, and keeps stars at least 10 times above that noise (`STAR_GAIN_MINIMUM_SIGNAL_TO_NOISE`). A faint star's ratio is noisy, and ranking by a noisy flux makes the faintest stars look too bright.
5. The gain is the median of (group flux / reference flux) over the remaining stars. The step also takes the median over the faintest quarter and over the brightest quarter of the stars.

A group that shares fewer than 16 such stars with the reference, such as a slitless spectrum with one star, falls back to the pixel method: the median of (group pixel / reference pixel) over source pixels at least 10 background noises above the sky, between the 20th and 80th percentile of their brightness, after each group's own sky level (`estimate_sky_level`) is subtracted.

**Why stars, not pixels.** On the Nikon D5300 stacks the 10 noise floor of the pixel method sits at 5 to 8% of the sky level, so the source pixels are mostly sky gradients and differences in star width. The pixel gain of NGC 6888 60 s read 1.39 against 1.04 from star fluxes. On M 51, the pixel gain of the 60 s group (1.007) equalled its sky ratio (1.00), while its stars read 2.15.

**Combining.** A group with a measured gain is sky-subtracted, divided by its gain and placed on the reference's sky level, so its stars match the reference and its sky does not leave a step where the weights change. A group whose gain could not be measured is combined as it is.

**The linearity check.** A camera is linear when faint and bright stars give the same ratio. The check measures how far the faint-quarter and the bright-quarter ratios sit from the gain, as a fraction of the gain, and takes the larger: `max(abs(faint-quarter ratio - gain), abs(bright-quarter ratio - gain)) / gain`. It has no units. It is the error that applying the one gain makes on the faint or bright stars. When it is larger than `exposure_group_gain_tolerance` in `[Processing.Siril]` (default 0.05, which is 5%), one scale factor does not describe the group. The stack runner (`stack_runner.py`) then leaves that group out of the combined image and combines the remaining groups, the same fallback it uses for a group that cannot be lined up. The reference group is never left out. On the real stacks, the worst quarter of good pairs sat 0.7 to 4.5% from the gain, and M 51's 60 s group sat 8% from it; the tolerance is a design choice that those pairs sit well inside.

**Spectral stacks.** A slitless spectrum has no field of stars, and its short groups are clipped at zero, so their sky per second is not a sky brightness. The stack runner passes `use_star_fluxes=False` for spectral stacks, which keeps the pixel method and skips the sky check.

**The sky check.** Each group's sky level per second is divided by the reference's (`sky_ratio`). A group whose sky is more than `SKY_RATIO_LIMIT` (3) times brighter is left out. Good Nikon groups read 0.5 to 2.0; the groups with a flooded or foreign sky read 3.9 and 7.6. The limit rests on those two groups.

**What the quality summary records.** Each entry in `exposureGroups` holds four fields:

| Field | Meaning | Unit | Who reads it |
|---|---|---|---|
| `gainMidRange` | The gain applied to the group: median star-flux ratio (median pixel ratio on the fallback) | None (a ratio of two counts-per-second values) | People reading the summary; the UI |
| `gainBrightEndRatio` | The same ratio on the brightest quarter of the stars (the brightest 1% of pixels on the fallback), never applied | None | People reading the summary; the UI |
| `gainDisagreement` | The larger distance of the faint-quarter and bright-quarter ratios from `gainMidRange`, divided by `gainMidRange` (`abs(gainBrightEndRatio - gainMidRange) / gainMidRange` on the pixel fallback) | Fraction (0.05 is 5%) | The linearity gate |
| `gainNonlinear` | `true` when `gainDisagreement` is above the tolerance | None | The linearity gate; `leftOutReason` repeats it in words |

A group with too few shared pixels, or a gain outside 0.25 to 4, keeps a gain of 1 and has no `gainDisagreement`, because there is nothing to compare. A group that fails the check is left out, and its `leftOutReason` names both ratios. The `exposure_group_linearity` gate fails with that sentence, which flags the stack (`flagged` and `flagReasons`). The same numbers are in the manifest in the `groups` folder.

For exact behavior, read the code.
