# Astrometrics quality measurements

*Version 1.0 · 2026-10-23 · Status: current*

## Abstract

A pipeline that reduces telescope data can say "pass" or "fail" without saying how far a result is from the limit, or why. This paper describes the numbers that the Astrometrics library measures from its own data so that a reader, or another program, can judge the data for themselves. They cover the noise a camera adds, the noise that a stack of frames should have in empty sky and the noise it does have, what the two halves of a stack share at fine scale, the shape and orientation of the stars in each frame, the pointing drift and dither step of a run, the clipping of the target, and the catalog zero point of each frame. The paper gives the principles that all these measurements share, and then the theory of each one: what it measures, the equation behind it, and what it leaves out. It states no tuned values and names no code. It is the companion of the [architecture paper](Astrometrics_Library_Architecture.md), which keeps the theory of the pipelines themselves.

Terms are defined in the [glossary](../Glossary.md). This document also defines each term the first time it appears.

Symbols and their units are listed in the [notation appendix](#appendix-a-notation). The [implementation paper](Astrometrics_Library_Implementation.md) names the code behind each section and gives the tuned values.

## 1. Introduction

**Statement of need.** A gate that reads "stack quality: passed" hides what was measured. An observer who wants to know whether another hour of exposure will help needs the noise and its parts. An observer who wants to know whether the mount is well aligned needs the rotation of the field. A program that plans the next night needs the same numbers, with their uncertainty, without a pass or fail decision made for it. This paper explains the measurements that give those numbers, so that a reader can trust them, repeat them, and know what they leave out.

**Approach.** Each measurement is a fact about one frame, one stack or one session (the frames of one target from one continuous run with the same camera, gain, offset and filter). It carries an uncertainty and says when it could not be made. Whether a value is good enough for a purpose is a judgment, and judgments belong to the observatory-control library, which knows the equipment and the goal (Section 2).

**Scope.** This paper explains the theory: what each measurement is, why it is defined as it is, and where it fails. It gives no tuned settings. Those are in `Astrometrics_Library_Implementation.md`, which also maps each idea to code. The evidence behind each measurement is in the validation register ([validation/index.md](validation/index.md)), and Section 7 lists the entries. The pass or fail gates of each pipeline are in the architecture paper, Sections 3.4, 4.4, 5.4, 6.4 and 7.4.

## 2. Principles

Four rules hold for every measurement in this paper.

**Measure, never interpret.** A measurement states a number and its uncertainty. It does not say whether the night was photometric, whether the polar alignment is good enough, or whether an exposure was too long. Those judgments depend on a limit, and a limit depends on the equipment and the goal. A judgment is recomputed from stored measurements when the limit changes, so no measurement goes stale. The observatory-control library holds the judgments, including any summary over a night and any correction for airmass (the amount of atmosphere the light crossed).

**Parts, not ratios.** When a result is a comparison of two numbers, such as the measured noise of a stack against the noise its frames predict, both numbers are stored, and the ratio is left to the reader. A stored ratio hides which part moved. The same holds for the noise of the sky and the read noise of the camera: they are two parts of the noise, and they stay apart.

**Not measured, with a reason.** A measurement that cannot be made is stored as not measured, with the reason. It is never replaced by a default value, and it is never stored as a pass. A value that rests on an assumption is stored as assumed, and the assumption is named.

**Every measurement has a purpose.** A *decision* measurement feeds a warning or a recommendation. A *context* measurement explains other numbers, as the sensor temperature explains the dark current. An *exploratory* measurement answers a stated question, and it has a review date, after which it is either promoted or removed. An exploratory measurement is never shown as advice.

## 3. Camera noise from calibration frames

Two properties of a camera set the noise floor of every frame: its conversion gain and its read noise. The *conversion gain* $g$ converts the stored count, in ADU (one step of the stored pixel value), to electrons. The *read noise* is the electronic noise added when a frame is read out. Both belong to one gain setting, one offset and one binning, and both come from calibration frames.

Light arrives in random packets, so the variance (squared scatter) of a bright, even frame, counted in electrons, equals the signal. In ADU, the conversion gain follows from the signal and the variance:

$$
g = \frac{S}{\sigma^2 - \sigma_r^2} \tag{1}
$$

where $S$ is the signal above the bias, $\sigma^2$ is the variance of one frame, and $\sigma_r^2$ is the variance of the read noise, all in ADU. This is the photon-transfer method [1], [2]. A single frame does not give $\sigma^2$ cleanly, since the vignette, the dust shadows and the pixel-to-pixel sensitivity add a fixed pattern. The difference of two consecutive flat frames cancels the pattern, so half the variance of the difference is the noise variance of one frame. The same difference of two bias frames, which hold only read noise, gives $\sigma_r$. Using many pairs gives a spread as well as a value. A flat that is not in the linear range of the sensor breaks Equation (1), so such flats are refused. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.sensor_noise.measure_sensor_noise -->

## 4. Noise of a stack in empty sky

A stack's noise says how well its frames added up. Four questions follow. What noise should the frames give (Section 4.1)? Where is the noise measured (Section 4.2)? What part of the measured noise does not average away (Section 4.3)? How does the noise fall as frames are added (Section 4.4)?

### 4.1 The noise the frames predict

Averaging independent frames lowers random noise, and the noise of one frame in one pixel is known from the camera. Frame $j$ has $S_j$ sky electrons in a pixel (the sky rate times the exposure), a read noise of $\sigma_{r,j}$ electrons and a gain of $g_j$. Photon noise is Poisson, so its variance is the signal itself. The variance of the pixel, in ADU squared, is

$$
v_j = \frac{S_j + \sigma_{r,j}^2}{g_j^2} \tag{2}
$$

A stack is a weighted mean with weights $w_j$. The noise of different frames is independent, so one pixel of the stack has the variance

$$
V = \frac{\sum_j w_j^2\, v_j}{\left(\sum_j w_j\right)^2} \tag{3}
$$

The sky part and the read part are summed separately, so they can be stored apart (Section 2).

The noise is measured in a circular aperture, a sum over a circle less the sky taken from a ring around it. With independent pixels, the circle's sum has the variance of one pixel times the sum of the squared overlaps of the pixels with the circle (a partly covered pixel counts for less). The ring's median is itself noisy. For $n$ independent pixels with bell-curve noise, a median has a variance of about $(\pi/2)/n$ times the pixel variance, and it is subtracted once for every unit of circle area. Together the two pieces give an *effective aperture area* $A_{\text{eff}}$ in pixels, the number by which the pixel variance is multiplied:

$$
V_{\text{ap}} = A_{\text{eff}}\, V, \qquad A_{\text{eff}} = \sum_i d_i^2 + \frac{\pi}{2}\,\frac{\left(\sum_i d_i\right)^2}{n_{\text{ring}}} \tag{4}
$$

Here $d_i$ is the overlap of pixel $i$ with the circle and $n_{\text{ring}}$ is the number of pixels in the ring. The stack's brightness scale also has to be known, since a stacking tool may divide each frame by a fixed full-scale count before it averages. The check is the stack's own sky: the weighted mean of the frames' sky, in the same units, must be close to the median of the stack. <!-- impl: astrometricslib.pipelines.stacking.post_processing.expected_noise.expected_stack_noise --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.expected_noise.effective_aperture_area_px -->

The measured noise sits above this prediction, and the gap is expected. The model leaves out the following.

- The stacking tool matches each frame's background level and scale to a reference before it averages, which changes the weights slightly.
- A one-shot color camera's mosaic is filled in by interpolation, so neighboring pixels are correlated.
- Pixel rejection throws away some data, which makes the stack noisier than the weights alone say.
- The master dark and bias hold a little read noise, and the master flat adds a fixed pattern (Section 4.3).
- Faint stars and nebulosity sit in every frame, so they do not average away (Section 4.2).

Both the prediction and the measurement are stored, and their ratio is not (Section 2). The prediction is a floor that averaging alone cannot go below. <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_noise.measure_stack_noise -->

### 4.2 Choosing empty apertures

The measured noise is the scatter of the sums over many circles on empty sky. If the circles are chosen by their own sums, the noise is biased low: dropping every circle with a large sum keeps the quiet ones. A circle must be chosen by a rule that is blind to its own sum.

The library therefore builds a significance mask from the stack's pixels before it places any circle. It removes the smooth background, smooths the rest at several widths so that both compact and extended sources show, and flags a pixel when any smoothed value lies well above the noise measured at that width. The noise is the robust spread of the smoothed pixels, from their median absolute deviation (MAD), which a few sources do not inflate [3]. Circles stay clear of every flagged pixel, with a margin that keeps the sky ring clear too. This leaves out the faint stars and the nebulosity that the star finder misses. Bright stars are removed by the star finder first. <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_noise.faint_source_distance --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_noise.place_empty_apertures -->

Three limits follow from this rule.

- A mask cannot remove structure below its threshold. Any excess left over is reported, not removed.
- A noise peak that happens to rise above the threshold excludes the circles near it. This lowers the measured noise by a small amount, which is a bias of the rule and not of the sums.
- Too few clean circles means the noise is not measured, and the reason is stored.

To compare the noise of two stacks of one target, they must also use the same circles. The circles are chosen once, stored as sky positions, and found again in each later stack through the stack's coordinate solution. A circle that is moved by a wrong solution lands on different sky. A stored set is replaced when a much deeper stack of the target appears. The noise is divided by the brightness of a fixed set of reference stars, measured in a large aperture so that the seeing hardly changes it, because a stacking tool's brightness scale differs from stack to stack.

### 4.3 The master flat's pattern in an aperture

Dividing every light frame by the master flat copies the flat's own noise into the frame. The same pattern is in every frame, so averaging does not remove it. A dithered stack (frames moved a few pixels between exposures) averages the pattern over its frames, and for such a stack the figure below is an upper bound.

When the average of the flat frames is noisy, the stacking tool blurs it with a Gaussian before the division. Blurring white noise with a kernel $G$ multiplies the noise by the square root of the sum of the squared kernel weights, and it makes neighboring pixels correlated. A relative flat error $e$ at a pixel changes the sky there by the factor $1-e$. The error is a fixed pattern, so over a circle the changes add up. Write $\sigma_f$ for the relative noise of the averaged flat before the blur: one frame's noise divided by the square root of the number of frames averaged. The standard deviation of the flat's contribution to an aperture sum is then

$$
\sigma_{\text{flat}} = \bar{s}\,\sigma_f \sqrt{\sum_i \left(G * d\right)_i^2} \tag{5}
$$

where $\bar{s}$ is the sky level of the stack. The circle's weights $d_i$ are blurred with the same kernel and the squares summed, because the blur correlates the pattern. <!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_noise_contribution.flat_noise_contribution --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_noise_contribution.smoothed_noise_factor -->

The figure counts the noise of the master flat only. It leaves out the ring, which adds noise of its own and also removes the part of a blurred pattern shared by the circle and its ring. It leaves out dust that moved and a flat taken at another focus. The master flat itself is not kept by the stacking step, so its noise is derived from the flat assessment and the blur width, not measured on the master.

### 4.4 How the noise falls with frame count

For independent frames, the noise of a stack falls as one over the square root of its *effective* number of frames, the number of equal frames that would give the same noise. Weights make the effective number smaller than the count. The library tests the expectation by stacking subsets of the same frames and measuring each subset in the same circles. Leaving out one night in turn shows what that night contributed, and the share of the summed weights that night holds says what it should have contributed. A pattern that repeats in every frame would make the noise fall more slowly than this expectation. The library measures the fall and does not assume it.

### 4.5 The difference of two half stacks

The empty-aperture noise of Section 4.2 mixes two parts: random noise that more frames average away, and structure that repeats in every frame and does not average away. Subtracting one stack from another separates them. The frames are split into two disjoint halves, which alternate in time so that slow changes in sky and seeing fall on both. Both halves are stacked on one registration reference, and that reference frame is shared by both halves. The grid is therefore the same, and the two stacks can be subtracted pixel by pixel.

The difference cancels everything the frames share: the sky structure, the faint sources, the nebula, and the flat's pattern. It keeps the random noise. It also keeps noise that is correlated between neighboring pixels by the registration, because each frame is resampled on its own. The difference is measured in the same empty circles as the stack's noise. A frame that sits in both halves, the reference, appears in both stacks and cancels in the difference. This lowers the difference's variance below the sum of the two halves' variances, and the calculation must account for it.

If every frame has the same noise, the random noise of the full stack follows from the difference. The variance of the difference is the frame variance times a factor that depends only on the weights, so dividing by that factor gives the frame variance, and the full stack's variance follows from the full stack's weights. A second form lets each frame have its own noise from the model of Section 4.1, and takes the ratio of the measured difference to the difference the model predicts. This checks whether unequal frame noise explains an excess.

The difference is stored beside the empty-aperture noise and never replaces it. Comparing the implied random noise with the prediction of Section 4.1 shows how much of the excess is random, and comparing it with the empty-aperture noise shows how much is shared structure. A stack does not start this measurement. A caller, the job that tests how the noise falls with frame count, runs it on a stack that already exists. <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_noise.measure_difference_noise --> <!-- impl: astrometricslib.pipelines.stacking.subset_noise_run.run_half_difference_noise -->

The measurement needs enough frames in each half for pixel rejection to work. It assumes the halves are matched in sky and seeing, which the alternation makes likely but does not guarantee. Its evidence is [STK-42](validation/stacking.md#stk-42).

### 4.6 What the halves share at fine scale

The shared structure of Section 4.5 may include a pattern fixed on the sensor, such as pixel-to-pixel gain differences that the flat did not remove. More frames do not average such a pattern away, but a dither or a drift of the pointing can smear it. The two half stacks give a measure of what is left of such a pattern. Each half is high-pass filtered, so that only structure a few pixels wide stays. The covariance of the two filtered halves, taken in sky clear of flagged sources, is the variance they share at that scale. It is the part of any fixed pattern that stays coherent in both halves, plus faint sky structure that the mask does not flag. It is not a bound on the sensor pattern, because a dither that gives each exposure its own pointing takes pattern out of the shared part.

The reference frame is in both halves, so its own random noise is shared. The calculation removes it: the frame variance times the reference's weight share in each half. The result is divided by the random variance of the full stack at the same scale, which is the difference variance over the variance factor of Section 4.5 and over the effective number of frames. The ratio says how many times the random noise the shared part is. The correction rests on equal noise in every frame and is about a fifth of the uncorrected ratio on the test stack. The covariance, the difference variance and the correction are stored beside the ratio, so a reader can rebuild it. Resampling image tiles gives the errors.

A drift separates sensor pattern from sky. The drift moves the sky across the sensor, so the stack smears a pattern that is fixed on the sensor along the drift, and sky structure is not smeared. The correlation of the halves at one lag along the drift, minus the correlation at the same lag across it, is near zero for sky and positive for a smeared pattern. The sensitivity of the excess is about 25 times coarser than the grain a flat is expected to leave, so a null result is expected and says little about the flat. Stars elongated along the drift can give a positive excess. The excess is not measured when the run has no drift axis (Section 5.5) or when the pointings do not spread over at least twice the lag along it. A pattern that does not move with the pointing counts as sky, and a pattern wider than the filter passes is seen less well. <!-- impl: astrometricslib.pipelines.stacking.post_processing.half_pattern.measure_half_pattern --> <!-- impl: astrometricslib.pipelines.stacking.subset_noise_run._half_pattern_safely --> Its evidence is [STK-45](validation/stacking.md#stk-45).

## 5. Frame geometry and exposure

Four measurements describe each light frame: the width of its stars, how the width varies across the field, how the field is rotated, and whether the target is clipped. A fifth describes the pointing of a whole run.

### 5.1 The effective star width

A star's width is the FWHM (full width at half maximum), the width of its image at half its peak. The library fits each star with a Moffat profile [4], a bright core with slowly fading wings that is closer to a real star than a Gaussian, and takes the median of many isolated, unsaturated stars across the field. A pixel does not sample the light at a point. It integrates the light over its square. The positions inside a square of side $p$ are spread evenly, so each axis adds a variance of $p^2/12$. Variances add under convolution, so for a core of Gaussian width $\sigma_{\text{star}}$ the width that is seen satisfies $\sigma_{\text{eff}}^2 = \sigma_{\text{star}}^2 + p^2/12$. The FWHM of a Gaussian is $2\sqrt{2\ln 2}\,\sigma$, so

$$
\text{FWHM}_{\text{eff}}^2 = \text{FWHM}^2 + c\,p^2, \qquad c = \frac{2\ln 2}{3} \tag{6}
$$

The constant $c$ comes from theory and is not fitted. The term matters when the stars are close to the pixel size, as with a short focal length: an undersampled star looks wider than the optics and the seeing made it. A raw mosaic from a one-shot color camera has its star shapes measured on summed 2 by 2 super-pixels, so that the Bayer pattern does not bend the second moments. The widths are given back in original pixels. <!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.measure_star_profiles -->

The elongation (long axis over short axis) and the direction of the long axis come from the second moments of the same stars. The direction is taken from the average of the stars' ellipticity components, so a round star, whose direction is only noise, counts for little. No star is dropped for being stretched, so a trailed frame reads wide, not sharp.

### 5.2 Star width across the field

The median width of the stars in the center of the frame is compared with the widths at the four corners. Five regions of equal area are used, so the five medians have similar noise. A field that is uneven, with sharp stars in the center and wide stars at one corner, points to a tilted sensor, a field curved by the optics, or a collimation problem. The measurement does not say which. A region with too few stars is not measured. <!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.measure_field_region_widths -->

### 5.3 Field rotation

A mount whose polar axis is not exactly on the sky's pole turns the field slowly as the night goes on. When a stacking tool lines up the frames, it fits a transform for each frame that moves the frame's stars onto the reference frame's stars. The upper left two by two part of that transform holds a rotation and a stretch. The rotation angle $\theta$ is read from the part written as a rotation followed by a stretch:

$$
\theta = \operatorname{atan2}\!\left(h_{10} - h_{01},\; h_{00} + h_{11}\right) \tag{7}
$$

where $h_{ab}$ are the entries of the transform. Unlike $\operatorname{atan2}(h_{10}, h_{00})$, this angle does not change when the fit has a small shear. It is given only when the transform does not mirror the image. A positive angle is counterclockwise when the array is drawn with its first row at the top. The angle is the one that moves the frame onto the reference, so the orientation of the frame relative to the reference has the opposite sign.

The angle depends on which frame is the reference. It compares only within one run. A meridian flip turns the field by about 180°, so across a flip the stored angle jumps by about 360° at the wrap, and a reader unwraps the series before fitting a drift. A spectral stack is registered by shift only and has no rotation. A set of frames that were already registered before the tool read them has zero angles that say nothing. The library stores only the angle of each frame. The conversion from a drift in angle to a polar-alignment error belongs to the observatory-control library. <!-- impl: astrometricslib.pipelines.stacking.post_processing.field_rotation.field_rotation_measurements -->

### 5.4 Target clipping

An exposure that is too long for the target clips the target itself: its brightest pixels stop at the camera's ceiling, or they leave the range where the sensor answers in proportion to the light. A clipped target is flattened in the stack, and its brightness is wrong. The saturation check of the stacking pipeline looks at the brightest star of a frame, which is usually not the target. This measurement looks at the target.

The measurement needs the target's pixel, and it takes it from the stack's own plate solution, never from the commanded pointing, which can be far from where the camera pointed. A source must stand at that place, with its brightest pixel clearly above the local sky noise. Without it, the position is probably wrong, and counting clipped pixels over empty sky would report no clipping for a target that was never looked at. The target's pixel is then moved onto each raw frame with the inverse of the transform that the stacking tool fitted for that frame. In a disc around it, the pixels at the clip ceiling and the pixels beyond the linearity limit are counted, and the two shares are stored. The disc is at least the target's semi-major axis. Every pixel of the disc counts as a target pixel, so in a crowded field the clipped pixels of foreground stars in the disc are counted too, and the value is the clipping of the disc, which is the target's when the target dominates it. The measurement belongs to the astrometry pipeline because it needs the plate solution. <!-- impl: astrometricslib.pipelines.astrometry.post_processing.target_clipping.target_clipping_measurements -->

### 5.5 Registration step and pointing drift

A dither moves the pointing by a few pixels between exposures, so that a sensor pattern lands on different sky each time. No record says whether a run was dithered. The library reads each frame's pointing from the transform that the stacking tool fitted for it: where the transform puts the center of the image, compared with the reference frame. The center is used because the shift of a corner also contains the small turn of the field (Section 5.3).

The step is the distance between the pointings of two exposures that follow each other in one night. A pair across two nights is not a step. A step too long to be real, a failed registration, is left out and counted. The library stores the median step, its spread (the MAD), the number of steps that jump above the median, and the number left out. The number of steps used is the sample count of the median row. A jump is a step above the median plus five times the larger of the robust spread and a floor of a tenth of a pixel. The count shows a dither made only every few exposures, which leaves the median at the size of the drift. A median step of about the size of the fit's own noise does not show a dither: slow drift, jitter between exposures and the noise of the fit give small steps too.

The pointing also drifts, as the mount flexes or the polar alignment is off. A straight line is fitted to each night's pointings against time, after a robust fit has set aside frames far from it. The library stores the step after the line is removed, which is the jitter with the drift taken out, and the drift rate in pixels per hour with its error. The direction of the drift, as an axis from 0 to 180°, and the extent of the pointings along it are stored only when the drift is large against its error and, for several nights, the nights agree on the axis. Section 4.6 uses that axis. The measurement needs enough frames, and a run that is already registered, a stack that joins several exposure groups, or a frame without a usable transform, is not measured, with the reason. The step rows are also not measured when more than a tenth of a night's pairs are too long to be real or more than half of the frames have the identity transform, because those transforms are not pointings. A step measures a distance and not its cause, and the fit gives no error for a single pointing. <!-- impl: astrometricslib.pipelines.stacking.post_processing.registration_shifts.pointing_measurements --> Its evidence is [STK-44](validation/stacking.md#stk-44).

## 6. Catalog zero point of a frame

The zero point ties the camera's counts to the catalog's brightness scale. A star of catalog magnitude $m_{\text{cat}}$ that gives a flux $f$ in a frame of exposure time $t$ has an offset

$$
z = m_{\text{cat}} + 2.5\log_{10}\!\left(\frac{f}{t}\right) \tag{8}
$$

If the frame were clean and the catalog's band matched the camera's, every star in the frame would give the same $z$. Thin cloud lowers $z$ for all stars together. In practice each star has an offset of its own, because of its color, a neighbor's light and the error in its catalog magnitude. This star-to-star scatter is about the same in every frame of a session, so it hides nothing about the sky. Only the change from frame to frame belongs to the sky.

The library therefore reports a *differential zero point*. It picks one fixed set of stars for the whole session. For each star $s$ it takes the session median $\tilde{z}_s$ of the star's offsets as that star's reference. The frame's zero point $Z_k$ is the session level plus the median, over the fixed stars, of how far each star's offset in frame $k$ sits from its own reference:

$$
Z_k = \operatorname{median}_s\!\left(\tilde{z}_s\right) + \operatorname{median}_s\!\left(z_{s,k} - \tilde{z}_s\right) \tag{9}
$$

Using the same stars in every frame keeps a star that drops out of a cloudy frame from moving the median. The standard error of that median is the frame-to-frame precision. The error of the absolute level, which comes from the finite star sample, is reported separately and is much larger. <!-- impl: astrometricslib.pipelines.photometry.processing.catalog_zero_point.session_zero_points --> <!-- impl: astrometricslib.pipelines.photometry.processing.catalog_zero_point.differential_frame_estimates -->

Four rules define what the zero point means here.

- One catalog band is used for each session: Gaia G [5] if enough stars have it, else the V magnitude from SIMBAD [6]. The two bands are different filters, and mixing them adds scatter that is not the sky's.
- No color, airmass or aperture correction is applied. A correction for airmass needs an extinction coefficient, and finding one needs several photometric nights. The level describes a star of the set's median color. A color slope can be fitted and reported where enough stars carry a color, but it is not applied.
- Only the central region of the frame is used, because the photometry frames are not flat-fielded and vignetting dims stars near the corners. Stars beyond the linear range of the sensor are left out.
- The aperture is fixed. A blurrier frame puts less of each star's light inside it, so the zero point can fall with the width of the stars, and a change in focus or seeing can look like a change in transparency.

The measurement does not say whether a night was photometric, and it does not summarize the spread over a night. That judgment belongs to the observatory-control library (Section 2). It is exploratory. The observatory has no night labeled photometric, so no truth exists against which to check the absolute level.

## 7. Validation

The evidence for each measurement is in the validation register. The register labels each entry *measured*, *partly measured* or *design estimate* (see the architecture paper, Section 8).

- Gain and read noise (Section 3): (partly measured; see [STK-35](validation/stacking.md#stk-35)).
- Predicted and measured stack noise, and the empty-aperture mask (Sections 4.1, 4.2 and 4.4): (partly measured; see [STK-36](validation/stacking.md#stk-36)) and (partly measured; see [STK-38](validation/stacking.md#stk-38)). The contribution of each night: (design estimate; see [STK-37](validation/stacking.md#stk-37)). The difference of two half stacks (Section 4.5): (partly measured; see [STK-42](validation/stacking.md#stk-42)).
- The master flat's pattern in an aperture (Section 4.3): (partly measured; see [STK-39](validation/stacking.md#stk-39)).
- Star width and elongation (Section 5.1): (design estimate; see [AST-17](validation/astrometry.md#ast-17)). The pixel term and the widths across the field (Sections 5.1 and 5.2): (design estimate; see [AST-18](validation/astrometry.md#ast-18)).
- Field rotation (Section 5.3): (partly measured; see [STK-43](validation/stacking.md#stk-43)).
- Registration step and pointing drift (Section 5.5): (partly measured; see [STK-44](validation/stacking.md#stk-44)). What the halves share at fine scale (Section 4.6): (partly measured; see [STK-45](validation/stacking.md#stk-45)).
- Target clipping (Section 5.4): (partly measured; see [AST-19](validation/astrometry.md#ast-19)).
- The catalog zero point (Section 6): (partly measured; see [PHO-15](validation/photometry.md#pho-15)).

## 8. Limitations and assumptions

- **Gaussian and independent noise.** Equations (2) to (4) treat the noise of different frames and pixels as independent and bell-shaped. A color camera's interpolation, pixel rejection and the stacking tool's normalization break this in small ways, and the measured noise sits above the prediction for these reasons as well as for the fixed patterns.
- **One equipment setup.** Most measurements were checked on one camera, one telescope and one or a few nights. The register entries say which.
- **Reference-dependent values.** The field rotation depends on the reference frame, and the zero point depends on the star set and the catalog band. Both compare only within one run or session.
- **Noise below the mask threshold.** Faint structure that the mask cannot see is still in the circles, so a measured excess may be real sky and not instrument noise.
- **Online catalogs.** The zero point uses Gaia and SIMBAD. When neither is reachable, the measurement is stored as not measured.
- **No judgments.** The paper explains measurements only. Whether a value is good enough is for the observatory-control library to say.

## Acknowledgments

These measurements build on open-source tools and public services. Siril [7] registers and stacks the frames whose rotation and noise are measured. This research has made use of the SIMBAD database [6], operated at CDS, Strasbourg, France. This work has made use of data from the European Space Agency (ESA) mission Gaia (<https://www.cosmos.esa.int/gaia>), processed by the Gaia Data Processing and Analysis Consortium (DPAC, <https://www.cosmos.esa.int/web/gaia/dpac/consortium>). Funding for the DPAC has been provided by national institutions, in particular the institutions participating in the Gaia Multilateral Agreement.

---

## References

[1] <a id="ref-1"></a>J. R. Janesick, *Photon Transfer*. Bellingham, WA, USA: SPIE Press, 2007, doi: 10.1117/3.725073.

[2] <a id="ref-2"></a>S. B. Howell, *Handbook of CCD Astronomy*, 2nd ed. Cambridge, U.K.: Cambridge Univ. Press, 2006, doi: 10.1017/CBO9780511807909.

[3] <a id="ref-3"></a>P. J. Rousseeuw and C. Croux, "Alternatives to the median absolute deviation," *J. Am. Stat. Assoc.*, vol. 88, no. 424, pp. 1273–1283, 1993, doi: 10.1080/01621459.1993.10476408.

[4] <a id="ref-4"></a>A. F. J. Moffat, "A theoretical investigation of focal stellar images in the photographic emulsion and application to photographic photometry," *Astron. Astrophys.*, vol. 3, pp. 455–461, 1969.

[5] <a id="ref-5"></a>Gaia Collaboration, A. Vallenari et al., "Gaia Data Release 3: Summary of the content and survey properties," *Astron. Astrophys.*, vol. 674, p. A1, 2023, doi: 10.1051/0004-6361/202243940.

[6] <a id="ref-6"></a>M. Wenger et al., "The SIMBAD astronomical database: The CDS reference database for astronomical objects," *Astron. Astrophys. Suppl. Ser.*, vol. 143, no. 1, pp. 9–22, 2000, doi: 10.1051/aas:2000332.

[7] <a id="ref-7"></a>C. Richard, V. Hourdin, C. Melis, and A. Knagg-Baugh, "Siril: An advanced tool for astronomical image processing," *J. Open Source Softw.*, vol. 9, no. 102, p. 7242, 2024, doi: 10.21105/joss.07242.

---

## Appendix A. Notation

Each symbol has one meaning in this paper. The index $j$ runs over frames of a stack, $i$ over pixels, and $s$ over stars; $k$ indexes the frames of a session.

| Symbol | Meaning | Unit | Where used |
| --- | --- | --- | --- |
| $g$, $g_j$ | Conversion gain, overall and of frame $j$ | e⁻/ADU | Eqs. (1), (2) |
| $S$ | Signal above the bias in one frame | ADU | Eq. (1) |
| $\sigma^2$, $\sigma_r^2$ | Variance of one frame, and of the read noise | ADU² | Eq. (1) |
| $S_j$, $\sigma_{r,j}$ | Sky signal per pixel and read noise of frame $j$ | e⁻ | Eq. (2) |
| $v_j$, $V$ | Pixel variance of frame $j$, and of the stack | ADU² | Eqs. (2), (3) |
| $w_j$ | Weight of frame $j$ in the stack | none | Eq. (3) |
| $V_{\text{ap}}$, $A_{\text{eff}}$ | Variance of the aperture sum, and the effective aperture area | ADU², px | Eq. (4) |
| $d_i$, $n_{\text{ring}}$ | Overlap of pixel $i$ with the circle, and the number of pixels in the ring | none, px | Eqs. (4), (5) |
| $\sigma_{\text{flat}}$, $\sigma_f$ | Noise that the master flat adds to an aperture sum, and the relative noise of the averaged flat before the blur | ADU, none | Eq. (5) |
| $\bar{s}$, $G$ | Sky level of the stack, and the blur kernel of the flat | ADU, none | Eq. (5) |
| $p$, $c$ | Pixel side, and the pixel-term constant | px, none | Eq. (6) |
| $\text{FWHM}$, $\text{FWHM}_{\text{eff}}$ | Star width, and star width including the pixel term | px | Eq. (6) |
| $\theta$, $h_{ab}$ | Rotation angle of a frame, and the entries of its registration transform | °, none | Eq. (7) |
| $m_{\text{cat}}$, $f$, $t$ | Catalog magnitude, flux, and exposure time | mag, ADU, s | Eq. (8) |
| $z$, $z_{s,k}$, $\tilde{z}_s$ | Offset of a star, in frame $k$, and its session median | mag | Eqs. (8), (9) |
| $Z_k$ | Differential zero point of frame $k$ | mag | Eq. (9) |
