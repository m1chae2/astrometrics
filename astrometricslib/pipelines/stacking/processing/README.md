# Processing

This step lines up and combines frames. Siril stacks the individual frames (see `stack_runner.py` one level up). The files here handle what Siril does not: stacks of different exposure lengths and spectral frames.

## What it does

- `exposure_groups.py` splits frames by exposure length, then combines the per-group stacks into one image. Each group is weighted by its noise, and a group is left out where it is saturated or clipped at zero. The step also puts every group on the brightness scale of the reference group (see "The brightness scale between exposure groups" below). `saturated_pixel_mask` marks those pixels, so a later check can tell which stars had their cores replaced.
- `group_alignment.py` finds the shift between group stacks, by the zero-order star or by phase correlation, and moves them into line. For imaging stacks it then refines the shift: it matches the stars of the two stacks and fits a shift, a rotation and a scale about the image centre (`refine_alignment_with_stars`). Stacks from different nights differ by a small rotation and scale, which a shift cannot remove. On M 57 and M 27 the stars of two group stacks were 0.6 to 1.1 pixels apart on average after the best shift (up to 1.9 pixels toward the frame edges), and the fit brought that to 0.04 to 0.06 pixels.
- `spectral_frame_alignment.py` aligns spectral frames that have no usable star pattern.
- `group_derotation.py` turns spectral group stacks to one common trail tilt.

The refinement keeps the plain shift when it cannot trust the fit: fewer than 30 matched stars, a rotation over 0.5 degrees, a scale off by more than 0.5%, or stars that still miss by more than 0.5 pixel. The group manifest records the result for each group: `alignment_rotation_degrees`, `alignment_scale`, `alignment_star_pairs` and `alignment_residual_pixels`. Spectral stacks keep the plain shift.

Alignment accuracy matters most here. On the groups measured, these steps place frames within 0.05 pixels, while Siril's registration of the same groups was 0.06 to 3 pixels off. For that reason these steps stay in Python.

Pixel rejection when Siril combines the frames is set before this step runs. The limits come from `rejection_bounds` in `utilities/rejection_thresholds.py`, and the stacking README ("Pixel rejection limits") describes them and the measurements behind them.

## The brightness scale between exposure groups

Siril leaves each group stack with its own overall brightness. Before the groups are averaged, `exposure_groups.py` divides each group's counts-per-second image by a gain (a single number) so that all groups read the same.

**How the gain is measured.** The reference group is the group with the most weight. For every other group, the step takes the reference pixels between the 20th and 80th percentile of brightness. It leaves out pixels that either image saturates or masks. It then takes the median of (group pixel / reference pixel) over those pixels. That median is the gain.

**Why mid-range pixels.** A sensor responds in proportion to light between the sky level and a point near full well (the largest charge a pixel can hold). In that range, two exposures of one scene differ by one factor, the ratio of their light per second, and the median ratio measures that factor. Near full well the response flattens, and a group clipped at zero reads too high at its faint end. At the extremes the ratio measures the sensor's nonlinearity, not the gain. The brightest 1% of pixels sit in the flattened zone. Applying a gain measured there to every pixel changes faint-star fluxes by the wrong amount: gains of 0.65 to 0.88 between groups of one camera, measured on the brightest pixels, changed faint-star fluxes by up to 35%.

**The bright-end ratio.** The step also computes the same median ratio over the brightest 1% of the shared pixels. It never applies this ratio. It compares it with the gain.

**The linearity check.** The check measures `abs(bright-end ratio - gain) / gain`, a fraction without units. When that fraction is larger than `exposure_group_gain_tolerance` in `[Processing.Siril]` (default 0.05, which is 5%), one scale factor does not describe the group. The stack runner (`stack_runner.py`) then leaves that group out of the combined image and combines the remaining groups. This is the same fallback it uses for a group that cannot be lined up. If the group left out is the longest one, the combined image has fewer frames and more noise, but every pixel stays linear. The reference group is never left out, because the other groups are compared with it. If the reference itself is the nonlinear group, the groups that disagree with it are left out, and the quality summary shows both ratios so a person can see which group to doubt. The setting is a design choice and has not been validated against more than one camera.

**What the quality summary records.** Each entry in `exposureGroups` holds four fields:

| Field | Meaning | Unit | Who reads it |
|---|---|---|---|
| `gainMidRange` | The gain applied to the group: median ratio on mid-range pixels | None (a ratio of two counts-per-second values) | People reading the summary; the UI |
| `gainBrightEndRatio` | The same ratio on the brightest 1% of pixels, never applied | None | People reading the summary; the UI |
| `gainDisagreement` | `abs(gainBrightEndRatio - gainMidRange) / gainMidRange` | Fraction (0.05 is 5%) | The linearity gate |
| `gainNonlinear` | `true` when `gainDisagreement` is above the tolerance | None | The linearity gate; `leftOutReason` repeats it in words |

A group with too few shared pixels, or a gain outside 0.25 to 4, keeps a gain of 1 and has no `gainDisagreement`, because there is nothing to compare. A group that fails the check is left out, and its `leftOutReason` names both ratios. The `exposure_group_linearity` gate fails with that sentence, which flags the stack (`flagged` and `flagReasons`). The same numbers are in the manifest in the `groups` folder.

For exact behavior, read the code.
