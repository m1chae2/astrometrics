# Astrometrics quality measurements

*Version 1.1 · 2026-10-23 · Status: current*

## Abstract

A pipeline that reduces telescope data can say "pass" or "fail" without saying how far a result is from the limit, or why. This paper describes the numbers the Astrometrics library measures from its own data, so a reader or another program can judge the data. They cover the noise a camera adds, the noise that a stack of frames should have in empty sky and the noise it does have, what the two halves of a stack share at fine scale, the shape and orientation of the stars in each frame, the pointing drift and dither step of a run, the clipping of the target, the catalog zero point of each frame, the position error that noise alone leaves, the noise of a photometric measurement by source, what a run's schedule can tell apart, the size of the target's change, and the signal-to-noise at which a spectrum can be classified. The paper gives the shared principles, then the theory of each measurement: what it measures, its equation, and what it leaves out. It states no tuned values and names no code. It is the companion of the [architecture paper](Astrometrics_Library_Architecture.md), which keeps the theory of the pipelines themselves.

Terms are defined in the [glossary](../Glossary.md). This document also defines each term the first time it appears.

Symbols and their units are listed in the [notation appendix](#appendix-a-notation). The [implementation paper](Astrometrics_Library_Implementation.md) names the code behind each section and gives the tuned values.

## 1. Introduction

**Statement of need.** A gate that reads "stack quality: passed" hides what was measured. An observer deciding whether another hour of exposure will help needs the noise and its parts. A program that plans the next night needs the same numbers, with their uncertainty and no decision made for it. This paper explains the measurements behind them, so a reader can trust them, repeat them, and know what they leave out.

**Approach.** Each measurement is a fact about one frame, one stack or one session (the frames of one target from one continuous run with the same camera, gain, offset and filter). It carries an uncertainty and says when it could not be made. Whether a value is good enough is a judgment for the observatory-control library (Section 2).

**Scope.** This paper explains what each measurement is, why it is defined as it is, and where it fails. Tuned settings and the map to code are in `Astrometrics_Library_Implementation.md`. The evidence is in the validation register ([validation/index.md](validation/index.md)); Section 11 lists the entries. The pipelines' pass or fail gates are in the architecture paper, Sections 3.4, 4.4, 5.4, 6.4 and 7.4.

## 2. Principles

Four rules hold for every measurement in this paper.

**Measure, never interpret.** A measurement states a number and its uncertainty. It does not say whether the night was photometric, the polar alignment is good enough, or an exposure was too long. Those judgments depend on a limit, which depends on the equipment and the goal, and they are recomputed from stored measurements when the limit changes. The observatory-control library holds them, including any summary over a night and any correction for airmass (the amount of atmosphere the light crossed).

**Parts, not ratios.** When a result compares two numbers, such as a stack's measured noise and the noise its frames predict, both are stored and the ratio is left to the reader, because a ratio hides which part moved. Sky noise and read noise likewise stay apart.

**Not measured, with a reason.** A measurement that cannot be made is stored as not measured, with the reason. It is never replaced by a default value or stored as a pass. A value that rests on an assumption is stored as assumed, with the assumption named.

**Every measurement has a purpose.** A *decision* measurement feeds a warning or a recommendation. A *context* measurement explains other numbers. An *exploratory* measurement answers a stated question and has a review date, after which it is promoted or removed. It is never shown as advice.

## 3. Camera noise from calibration frames

Two camera properties set the noise floor of every frame. The *conversion gain* $g$ converts the stored count, in ADU (one step of the stored pixel value), to electrons. The *read noise* is the electronic noise added when a frame is read out. Both belong to one gain setting, offset and binning, and come from calibration frames.

Light arrives in random packets, so the variance (squared scatter) of a bright, even frame, in electrons, equals the signal. In ADU, the gain follows from the signal and the variance:

$$
g = \frac{S}{\sigma^2 - \sigma_r^2} \tag{1}
$$

where $S$ is the signal above the bias, $\sigma^2$ is the variance of one frame, and $\sigma_r^2$ is the variance of the read noise, all in ADU. This is the photon-transfer method [1], [2]. A single frame has a fixed pattern from the vignette, dust shadows and pixel sensitivity. The difference of two consecutive flats cancels it, so half the variance of the difference is the noise variance of one frame. The same difference of two bias frames, which hold only read noise, gives $\sigma_r$. A flat outside the sensor's linear range breaks Equation (1) and is refused. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.sensor_noise.measure_sensor_noise -->

## 4. Noise of a stack in empty sky

A stack's noise says how well its frames added up. What noise should the frames give (Section 4.1)? Where is it measured (Section 4.2)? What part does not average away (Sections 4.3, 4.5 and 4.6)? How does it fall as frames are added (Section 4.4)?

### 4.1 The noise the frames predict

Frame $j$ has $S_j$ sky electrons in a pixel, a read noise of $\sigma_{r,j}$ electrons and a gain of $g_j$. Photon noise is Poisson, so its variance is the signal. The variance of the pixel, in ADU squared, is

$$
v_j = \frac{S_j + \sigma_{r,j}^2}{g_j^2} \tag{2}
$$

A stack is a weighted mean with weights $w_j$. The noise of different frames is independent, so one pixel of the stack has the variance

$$
V = \frac{\sum_j w_j^2\, v_j}{\left(\sum_j w_j\right)^2} \tag{3}
$$

The sky and read parts are summed separately and stored apart (Section 2).

The noise is measured in a circular aperture, a sum over a circle less the sky taken from a ring around it. With independent pixels, the circle's sum has the variance of one pixel times the sum of the squared overlaps of the pixels with the circle. The ring's median is itself noisy: for $n$ independent pixels with bell-curve noise it has a variance of about $(\pi/2)/n$ times the pixel variance, and it is subtracted once for every unit of circle area. Together the two pieces give an *effective aperture area* $A_{\text{eff}}$ in pixels, the number by which the pixel variance is multiplied:

$$
V_{\text{ap}} = A_{\text{eff}}\, V, \qquad A_{\text{eff}} = \sum_i d_i^2 + \frac{\pi}{2}\,\frac{\left(\sum_i d_i\right)^2}{n_{\text{ring}}} \tag{4}
$$

Here $d_i$ is the overlap of pixel $i$ with the circle (a partly covered pixel counts for less) and $n_{\text{ring}}$ is the number of pixels in the ring. The stack's brightness scale must be known, since a stacking tool may divide each frame by a fixed full-scale count; the weighted mean of the frames' sky must be close to the stack's median. <!-- impl: astrometricslib.pipelines.stacking.post_processing.expected_noise.expected_stack_noise --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.expected_noise.effective_aperture_area_px -->

The measured noise sits above this prediction. The model leaves out the stacking tool's matching of each frame's background and scale to a reference, the interpolated mosaic of a one-shot color camera (which correlates neighboring pixels), the data pixel rejection discards, the read noise in the master dark and bias, the master flat's fixed pattern (Section 4.3), and the faint stars and nebulosity in every frame (Section 4.2).

Both the prediction and the measurement are stored, not their ratio (Section 2). The prediction is a floor that averaging alone cannot go below. <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_noise.measure_stack_noise -->

### 4.2 Choosing empty apertures

The measured noise is the scatter of the sums over many circles on empty sky. Choosing circles by their own sums biases it low, because dropping circles with large sums keeps the quiet ones. The rule must be blind to the sum.

The library therefore builds a significance mask from the stack's pixels before it places any circle. It removes the smooth background, smooths the rest at several widths so that compact and extended sources show, and flags a pixel when any smoothed value lies well above the noise measured at that width. That noise is the robust spread of the smoothed pixels, from their median absolute deviation (MAD), which a few sources do not inflate [3]. Circles stay clear of every flagged pixel, with a margin that keeps the sky ring clear too. This leaves out the faint stars and nebulosity the star finder misses; it removes bright stars first. <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_noise.faint_source_distance --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_noise.place_empty_apertures -->

Three limits follow. A mask cannot remove structure below its threshold, so any excess left over is reported, not removed. A noise peak above the threshold excludes the circles near it, which lowers the measured noise slightly, a bias of the rule and not of the sums. Too few clean circles means the noise is not measured, and the reason is stored.

Two stacks of one target must use the same circles, so they are chosen once, stored as sky positions, and found again in each later stack through its coordinate solution. A wrong solution moves a circle onto different sky. The noise is divided by the brightness of a fixed set of reference stars in a large aperture, because a stacking tool's brightness scale differs from stack to stack.

### 4.3 The master flat's pattern in an aperture

Dividing every light frame by the master flat copies the flat's noise into every frame, so averaging does not remove it. A dithered stack (frames moved a few pixels between exposures) averages the pattern, so for it the figure below is an upper bound.

When the average of the flat frames is noisy, the stacking tool blurs it with a Gaussian before the division. Blurring white noise with a kernel $G$ multiplies the noise by the square root of the sum of the squared kernel weights, and makes neighboring pixels correlated. A relative flat error $e$ at a pixel changes the sky there by the factor $1-e$, and the fixed pattern adds up over a circle. Write $\sigma_f$ for the relative noise of the averaged flat before the blur: one frame's noise over the square root of the number of frames averaged. The standard deviation of the flat's contribution to an aperture sum is then

$$
\sigma_{\text{flat}} = \bar{s}\,\sigma_f \sqrt{\sum_i \left(G * d\right)_i^2} \tag{5}
$$

where $\bar{s}$ is the sky level of the stack. The circle's weights $d_i$ are blurred with the same kernel and the squares summed, because the blur correlates the pattern. <!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_noise_contribution.flat_noise_contribution --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_noise_contribution.smoothed_noise_factor -->

The figure counts the master flat's noise only and leaves out the ring. The stacking step does not keep the master flat, so the figure comes from the flat assessment and the blur width.

### 4.4 How the noise falls with frame count

For independent frames, the noise of a stack falls as one over the square root of its *effective* number of frames, the number of equal frames that would give the same noise. Weights make it smaller than the count. The library stacks subsets of the same frames, measures each in the same circles, and leaves out one night in turn to show what that night contributed against its share of the summed weights. A pattern that repeats in every frame slows the fall. The fall is measured, not assumed.

### 4.5 The difference of two half stacks

The empty-aperture noise of Section 4.2 mixes random noise that more frames average away with structure that repeats in every frame. Subtracting one stack from another separates them. The frames are split into two disjoint halves that alternate in time, so slow changes in sky and seeing fall on both. Both halves are stacked on one shared registration reference, so the grid is the same and the stacks subtract pixel by pixel.

The difference cancels everything the frames share: sky structure, faint sources, nebula and the flat's pattern. It keeps the random noise, and the pixel correlation that registration adds by resampling each frame. It is measured in the same empty circles as the stack's noise. The reference frame sits in both halves and cancels, which lowers the variance below the sum of the halves' variances, and the calculation accounts for it.

If every frame has the same noise, the variance of the difference is the frame variance times a factor that depends only on the weights. Dividing by the factor gives the frame variance, and the full stack's weights give its variance. A second form gives each frame its own noise from Section 4.1 and takes the ratio of the measured difference to the modeled one, which checks whether unequal frame noise explains an excess.

The difference never replaces the empty-aperture noise. Comparing the implied random noise with Section 4.1's prediction shows how much of the excess is random, and comparing it with the empty-aperture noise shows how much is shared structure. <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_noise.measure_difference_noise --> <!-- impl: astrometricslib.pipelines.stacking.subset_noise_run.run_half_difference_noise -->

Each half needs enough frames for pixel rejection to work, and the halves are assumed matched in sky and seeing, which alternation makes likely, not certain. Its evidence is [STK-42](validation/stacking.md#stk-42).

### 4.6 What the halves share at fine scale

The shared structure of Section 4.5 may include a pattern fixed on the sensor, such as pixel-to-pixel gain differences the flat did not remove. More frames do not average it away, but a dither or drift of the pointing can smear it. Each half is high-pass filtered, so only structure a few pixels wide stays. The covariance of the two filtered halves, in sky clear of flagged sources, is the variance they share at that scale: the fixed pattern that stays coherent in both halves, plus faint sky structure the mask misses. It does not bound the sensor pattern, because a dither takes pattern out of the shared part.

The reference frame's random noise is in both halves, so the calculation removes it: the frame variance times the reference's weight share in each half. The result is divided by the full stack's random variance at that scale (the difference variance over the variance factor of Section 4.5 and the effective number of frames), which says how many times the random noise the shared part is. The correction assumes equal noise in every frame. The covariance, difference variance and correction are stored beside the ratio; resampling tiles gives the errors.

A drift smears a sensor-fixed pattern along the drift but not sky structure. The correlation of the halves at one lag along the drift, minus that at the same lag across it, is near zero for sky and positive for a smeared pattern. The sensitivity is about 25 times coarser than the grain a flat is expected to leave, so a null result says little about the flat. Stars elongated along the drift can give a positive excess. The excess is not measured when the run has no drift axis (Section 5.5) or the pointings do not spread over at least twice the lag along it. A pattern that does not move with the pointing counts as sky, and a pattern wider than the filter passes is seen less well. <!-- impl: astrometricslib.pipelines.stacking.post_processing.half_pattern.measure_half_pattern --> <!-- impl: astrometricslib.pipelines.stacking.subset_noise_run._half_pattern_safely --> Its evidence is [STK-45](validation/stacking.md#stk-45).

## 5. Frame geometry and exposure

Four measurements describe each light frame: star width, its variation across the field, field rotation and target clipping. A fifth describes the pointing of a whole run.

### 5.1 The effective star width

A star's width is the FWHM (full width at half maximum), the width of its image at half its peak. The library fits each star with a Moffat profile [4], a bright core with slowly fading wings, closer to a real star than a Gaussian, and takes the median of many isolated, unsaturated stars across the field. A pixel integrates the light over its square. Positions inside a square of side $p$ are spread evenly, so each axis adds a variance of $p^2/12$. Variances add under convolution, so for a core of Gaussian width $\sigma_{\text{star}}$ $\sigma_{\text{eff}}^2 = \sigma_{\text{star}}^2 + p^2/12$. The FWHM of a Gaussian is $2\sqrt{2\ln 2}\,\sigma$, so

$$
\text{FWHM}_{\text{eff}}^2 = \text{FWHM}^2 + c\,p^2, \qquad c = \frac{2\ln 2}{3} \tag{6}
$$

The constant $c$ comes from theory and is not fitted. The term matters for undersampled stars, which look wider than the optics and seeing made them. A one-shot color camera's raw mosaic is measured on summed 2 by 2 super-pixels, so the Bayer pattern does not bend the second moments. Widths are in original pixels. <!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.measure_star_profiles -->

The elongation (long axis over short axis) and its direction come from the second moments of the same stars; the direction is the average of the stars' ellipticity components, so a round star counts for little. No star is dropped for being stretched, so a trailed frame reads wide.

### 5.2 Star width across the field

The median star width at the center of the frame is compared with the widths at the four corners, in five regions of equal area so the medians have similar noise. Sharp stars in the center and wide stars at one corner point to a tilted sensor, field curvature or collimation, and the measurement does not say which. A region with too few stars is not measured. <!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.measure_field_region_widths -->

### 5.3 Field rotation

A mount whose polar axis is off the sky's pole turns the field slowly through the night. A stacking tool fits a transform that moves each frame's stars onto the reference frame's stars. Its upper left two by two part holds a rotation and a stretch. The rotation angle $\theta$ is read from it written as a rotation followed by a stretch:

$$
\theta = \operatorname{atan2}\!\left(h_{10} - h_{01},\; h_{00} + h_{11}\right) \tag{7}
$$

where $h_{ab}$ are the entries of the transform. Unlike $\operatorname{atan2}(h_{10}, h_{00})$, this angle does not change when the fit has a small shear. It is given only when the transform does not mirror the image. The angle moves the frame onto the reference, so the frame's orientation has the opposite sign.

The angle depends on the reference frame, so it compares only within one run. A meridian flip turns the field by about 180°, so a reader unwraps the series before fitting a drift. A spectral stack is registered by shift only and has no rotation. The library stores only each frame's angle; the observatory-control library converts a drift in angle to a polar-alignment error. <!-- impl: astrometricslib.pipelines.stacking.post_processing.field_rotation.field_rotation_measurements -->

### 5.4 Target clipping

An exposure that is too long for the target clips the target itself: its brightest pixels stop at the camera's ceiling, or leave the range where the sensor answers in proportion to the light. A clipped target is flattened in the stack, and its brightness is wrong. The stacking saturation check looks at the brightest star, usually not the target.

The target's pixel comes from the stack's own plate solution, never from the commanded pointing, which can be far from where the camera pointed. A source must stand there, with its brightest pixel clearly above the local sky noise. Otherwise the position is probably wrong, and empty sky would report no clipping for a target never looked at. The inverse of each frame's fitted transform moves the pixel onto that raw frame. In a disc around it (at least the target's semi-major axis), the pixels at the clip ceiling and those beyond the linearity limit are counted, and the two shares are stored. Every pixel of the disc counts, so in a crowded field the clipped pixels of foreground stars count too. The measurement belongs to the astrometry pipeline because it needs the plate solution. <!-- impl: astrometricslib.pipelines.astrometry.post_processing.target_clipping.target_clipping_measurements -->

### 5.5 Registration step and pointing drift

A dither moves the pointing by a few pixels between exposures, so a sensor pattern lands on different sky each time. No record says whether a run was dithered. The library reads each frame's pointing from its fitted transform: where it puts the center of the image, compared with the reference frame. A corner would also contain the small turn of the field (Section 5.3).

A step is the distance between the pointings of two consecutive exposures in one night; a pair across two nights is not a step. A step too long to be real, a failed registration, is left out and counted. The library stores the median step, its spread (the MAD), the number of steps that jump above the median, and the number left out. The steps used are its sample count. A jump is a step above the median plus five times the larger of the robust spread and a floor of a tenth of a pixel. The count shows a dither made only every few exposures. A median step near the fit's own noise does not show a dither, because drift, jitter and fit noise give small steps too.

The pointing also drifts, as the mount flexes or the polar alignment is off. A line is fitted to each night's pointings against time, after a robust fit sets aside frames far from it. The library stores the step with the line removed (the jitter) and the drift rate in pixels per hour with its error. The drift axis, from 0 to 180°, and the extent of the pointings along it are stored only when the drift is large against its error and, over several nights, the nights agree on the axis. Section 4.6 uses that axis. A run that is already registered, a stack that joins several exposure groups, a frame without a usable transform, or too few frames is not measured, with the reason. The step rows are also not measured when over a tenth of a night's pairs are too long to be real or over half of the frames have the identity transform, which is not a pointing. A step measures a distance, not its cause, and a single pointing has no fit error. <!-- impl: astrometricslib.pipelines.stacking.post_processing.registration_shifts.pointing_measurements --> Its evidence is [STK-44](validation/stacking.md#stk-44).

## 6. Catalog zero point of a frame

The zero point ties the camera's counts to the catalog's brightness scale. A star of catalog magnitude $m_{\text{cat}}$ that gives a flux $f$ in a frame of exposure time $t$ has an offset

$$
z = m_{\text{cat}} + 2.5\log_{10}\!\left(\frac{f}{t}\right) \tag{8}
$$

If the catalog's band matched the camera's, every star in a clean frame would give the same $z$, and thin cloud would lower $z$ for all stars together. In practice each star has an offset of its own, from its color, a neighbor's light and its catalog error. This scatter is about the same in every frame of a session, so only the change between frames belongs to the sky.

The library therefore reports a *differential zero point*. It picks one fixed set of stars for the whole session and takes each star's session median offset $\tilde{z}_s$ as its reference. The frame's zero point $Z_k$ is the session level plus the median, over the fixed stars, of how far each star's offset in frame $k$ sits from its own reference:

$$
Z_k = \operatorname{median}_s\!\left(\tilde{z}_s\right) + \operatorname{median}_s\!\left(z_{s,k} - \tilde{z}_s\right) \tag{9}
$$

The standard error of that median is the frame-to-frame precision. The much larger error of the absolute level, from the finite star sample, is reported separately. <!-- impl: astrometricslib.pipelines.photometry.processing.catalog_zero_point.session_zero_points --> <!-- impl: astrometricslib.pipelines.photometry.processing.catalog_zero_point.differential_frame_estimates -->

Four rules define what the zero point means here.

- One catalog band is used per session: Gaia G [5] if enough stars have it, else the V magnitude from SIMBAD [6]. Mixing these different filters adds scatter that is not the sky's.
- No color, airmass or aperture correction is applied. An airmass correction needs an extinction coefficient, which needs several photometric nights. The level describes a star of the set's median color; a fitted color slope is reported, not applied.
- Only the central region is used, because the photometry frames are not flat-fielded and vignetting dims stars near the corners. Stars beyond the sensor's linear range are left out.
- The aperture is fixed. A blurrier frame puts less of each star's light inside it, so a change in focus or seeing can look like a change in transparency.

The measurement does not say whether a night was photometric (Section 2). It is exploratory: no night is labeled photometric, so no truth exists for the absolute level.

## 7. Expected position residual

After the Gaia refit, each star sits some distance from its Gaia position. Part of it is noise no star finder can avoid, stored as three parts.

**Centroid noise.** For a Gaussian star on white-noise sky, the smallest error any estimator can reach along each axis is the Cramér–Rao bound [8]:

$$
\sigma_x=\sqrt{2/\pi}\,\frac{n}{A}=\frac{\sqrt{2}\,s}{\mathrm{SNR}} \tag{10}
$$

Here $A$ is the star's peak above the sky, $n$ the standard deviation of the sky pixels, $s$ the Gaussian star width, and SNR the signal-to-noise ratio of an optimal aperture, $A\sqrt{\pi}\,s/n$. Only $A/n$ is needed. The bound counts sky noise only, and a real star finder scatters above it. <!-- impl: astrometricslib.pipelines.astrometry.post_processing.expected_residual.centroid_error_per_axis_px -->

**Pixel phase.** A pixel integrates the light, so a centroid errs by an amount that depends on where the star sits inside its pixel. The error averages to zero and grows steeply as the star narrows toward the pixel size. Injected noise-free stars give it for a given star finder. <!-- impl: astrometricslib.pipelines.astrometry.post_processing.expected_residual.pixel_phase_rms_per_axis_px -->

**Gaia epoch.** Proper motion (a star's yearly drift on the sky) carries a Gaia position to the image date. Its error is the position error plus the proper-motion error times the years elapsed, in quadrature.

With $\ell$ the pixel scale in arcseconds per pixel and two axes, the parts combine as

$$
\rho_{\text{c}}=\ell\sqrt{2\overline{\sigma_x^2}},\qquad \rho^2=\rho_{\text{c}}^2+\rho_{\text{p}}^2+\rho_{\text{G}}^2 \tag{11}
$$

where the overline is the mean over the refit stars, and $\rho_{\text{p}}$ and $\rho_{\text{G}}$ are the pixel-phase and epoch root mean square distances. A residual several times $\rho$ is distortion, tilt or a bad match, not noise. <!-- impl: astrometricslib.pipelines.astrometry.post_processing.expected_residual.expected_residual -->

## 8. Noise of a photometric measurement

One star in one frame has four noise sources, stored apart at stated catalog magnitudes. A reader adds their variances.

**Photon, sky and read noise.** Photometry subtracts a sky level measured in a ring, so the variance of the star's electrons $F$ is

$$
\sigma_F^2=F+\Big(n_{\text{ap}}+\frac{n_{\text{ap}}^2}{n_{\text{ring}}}\Big)\big(B+R^2\big) \tag{12}
$$

with $n_{\text{ap}}$ the pixels in the circle, $B$ the sky electrons per pixel and $R$ the read noise in electrons. The three terms are the photon, sky and read parts. Each saved error is built from this equation, so the background term is the error squared less $F$; the read part follows from the camera and the rest is sky. Dark current is taken as zero. A relative error converts to magnitudes by

$$
\sigma_m=\frac{2.5}{\ln 10}\,\frac{\sigma_F}{F} \tag{13}
$$

<!-- impl: astrometricslib.pipelines.photometry.processing.noise_budget.noise_variances_at_magnitude -->

**Scintillation.** Turbulence high in the air makes a star twinkle. Young's formula [9] with the airmass power of Dravins et al. [10] gives the fractional noise

$$
\sigma_{\text{sc}}=c_Y D^{-2/3}X^{\beta}e^{-h/h_0}(2t)^{-1/2} \tag{14}
$$

where $D$ is the aperture diameter, $X$ the airmass, $h$ the site height, $h_0$ the scale height of the turbulence, $t$ the exposure time, and $c_Y$ and $\beta$ literature constants. The noise is a fraction of the star's light, so it is the same in magnitudes for every star and independent of the camera. Fitted sites scatter around it by tens of percent. The airmass comes from the frame headers and is never defaulted. An unknown site height is taken as sea level, the largest value, so the result is an upper bound. The model does not include the twinkling that differential photometry removes. <!-- impl: astrometricslib.pipelines.photometry.processing.noise_budget.scintillation_relative_noise -->

## 9. What a run can tell apart

### 9.1 The window function of the observing times

Observing a few hours each night puts an echo of every real signal about one cycle per day away. The *window function* is the power spectrum of the times alone, with every brightness set equal [11], [12]:

$$
W(f)=\Big|\frac{1}{N}\sum_{k=1}^{N}e^{-2\pi i f t_k}\Big|^2 \tag{15}
$$

where $t_k$ are the $N$ frame times. It is 1 at zero frequency and peaks at each spacing the schedule cannot tell from none: two periods whose frequencies differ by a peak frequency cannot be separated by the run. Groups of nights $\Delta$ days apart add a peak at $1/\Delta$ per day. The strongest peaks below a frequency limit are stored. <!-- impl: astrometricslib.pipelines.photometry.processing.observing_window.strongest_window_peaks -->

### 9.2 The target's amplitude

A reader judging a light curve needs the size of the change the target is known to make. Two sources give it; they are never mixed.

*From the light curve.* The curve is folded on a period and cut into equal phase bins. The amplitude is the largest bin median less the smallest. The period is the star's own periodogram result if the search found one, else the catalog period. A curve that covers part of the cycle reads low, so a value is stored only when enough bins hold enough points, with the coverage beside it. Binning smooths narrow eclipses.

*From the catalog.* The magnitude range of the AAVSO Variable Star Index [13] is stored with its band. It is often old and not in the telescope's band.

Only a target named as a single star gets an amplitude, from the identified star that carries that name. A set distance from the target's position refuses a wrong match. Other targets are not measured. <!-- impl: astrometricslib.pipelines.photometry.processing.target_amplitude.folded_amplitude -->

## 10. Signal-to-noise and the spectral class

**At the classifying lines.** A subtype rests on a few lines and bands, so a whole-spectrum signal-to-noise says little. For a resolution element $e$ (the narrowest width the instrument resolves, from the measured line spread) it is

$$
\mathrm{SNR}_e=\frac{\sum_{i\in e}I_i}{\sqrt{\sum_{i\in e}\varepsilon_i^2}} \tag{16}
$$

for brightnesses $I_i$ with independent errors $\varepsilon_i$. The stored value is the median, over the lines, of the median $\mathrm{SNR}_e$ in each line core. A core touching an atmospheric absorption band is skipped. <!-- impl: astrometricslib.pipelines.spectroscopy.processing.spectral_line_indices.line_window_signal_to_noise -->

**Class gap against signal-to-noise.** The classifier ranks reference spectra by a relative RMS distance $d$. The *gap* is the distance of the nearest reference of another class (or the second-best one) less that of the best:

$$
\Delta=d_{\text{other}}-d_{\text{best}} \tag{17}
$$

A gap below a fixed limit is called ambiguous. Injection finds the signal-to-noise that gives a larger gap. Stored spectra of high signal-to-noise and known type get white Gaussian noise until the project's estimator reads the target value, and the project's classifier classifies each copy. A level is *reached* where at least a set share of the copies pass at that value and at every higher one. If no level passes, the verdict is "confidence-limited at this resolution": the gap stays small, though the classes may still differ. Each row also gives the share of copies that keep the spectrum's own class, with intervals. Resampling star fields, not spectra, gives the uncertainty [14], because spectra of one field share a night and instrument state. <!-- impl: astrometricslib.pipelines.spectroscopy.processing.class_gap_calibration.class_gap_table --> <!-- impl: astrometricslib.pipelines.spectroscopy.processing.class_gap_calibration.reachable_snr -->

## 11. Validation

The register labels each entry *measured*, *partly measured* or *design estimate* (architecture paper, Section 8).

- Gain and read noise (Section 3): partly measured, [STK-35](validation/stacking.md#stk-35).
- Stack noise, the empty-aperture mask and frame count (Sections 4.1, 4.2, 4.4): partly measured, [STK-36](validation/stacking.md#stk-36) and [STK-38](validation/stacking.md#stk-38). Each night's contribution: design estimate, [STK-37](validation/stacking.md#stk-37). The flat's pattern (Section 4.3): partly measured, [STK-39](validation/stacking.md#stk-39). The half-stack difference (Section 4.5): partly measured, [STK-42](validation/stacking.md#stk-42). What the halves share at fine scale (Section 4.6): partly measured, [STK-45](validation/stacking.md#stk-45).
- Star width and elongation (Section 5.1): design estimate, [AST-17](validation/astrometry.md#ast-17). The pixel term and widths across the field (Sections 5.1, 5.2): design estimate, [AST-18](validation/astrometry.md#ast-18).
- Field rotation (Section 5.3): partly measured, [STK-43](validation/stacking.md#stk-43). Target clipping (Section 5.4): partly measured, [AST-19](validation/astrometry.md#ast-19). Registration step and pointing drift (Section 5.5): partly measured, [STK-44](validation/stacking.md#stk-44).
- The catalog zero point (Section 6): partly measured, [PHO-15](validation/photometry.md#pho-15).
- The expected position residual (Section 7): partly measured, [AST-21](validation/astrometry.md#ast-21).
- The noise model (Section 8): partly measured, [PHO-17](validation/photometry.md#pho-17).
- The window function (Section 9.1): partly measured, [PHO-18](validation/photometry.md#pho-18). The target amplitude (Section 9.2): design estimate, [PHO-19](validation/photometry.md#pho-19).
- The signal-to-noise at the lines and the class gap (Section 10): partly measured, [SPE-30](validation/spectroscopy.md#spe-30).

## 12. Limitations and assumptions

- **Independent noise.** Equations (2) to (4) treat the noise of frames and pixels as independent and bell-shaped (Section 4.1 lists what breaks this).
- **One equipment setup.** Most measurements were checked on one camera, one telescope and a few nights (see the register).
- **Reference-dependent values.** The field rotation depends on the reference frame, and the zero point on the star set and catalog band.
- **Noise below the mask threshold.** Faint structure the mask cannot see is still in the circles, so a measured excess may be real sky.
- **Models.** Equations (10) to (14) assume a Gaussian star, white noise and a literature scintillation law; measured scatter sits above them.

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

[8] <a id="ref-8"></a>I. R. King, "Accuracy of measurement of star images on a pixel array," *Publ. Astron. Soc. Pac.*, vol. 95, pp. 163–168, 1983, doi: 10.1086/131139.

[9] <a id="ref-9"></a>A. T. Young, "Photometric error analysis. VI. Confirmation of Reiger's theory of scintillation," *Astron. J.*, vol. 72, p. 747, 1967, doi: 10.1086/110355.

[10] <a id="ref-10"></a>D. Dravins, L. Lindegren, E. Mezey, and A. T. Young, "Atmospheric intensity scintillation of stars. III. Effects for different telescope apertures," *Publ. Astron. Soc. Pac.*, vol. 110, pp. 610–633, 1998, doi: 10.1086/316167.

[11] <a id="ref-11"></a>T. J. Deeming, "Fourier analysis with unequally-spaced data," *Astrophys. Space Sci.*, vol. 36, pp. 137–158, 1975, doi: 10.1007/BF00214999.

[12] <a id="ref-12"></a>J. T. VanderPlas, "Understanding the Lomb–Scargle periodogram," *Astrophys. J. Suppl. Ser.*, vol. 236, no. 1, p. 16, 2018, doi: 10.3847/1538-4365/aab766.

[13] <a id="ref-13"></a>C. Watson, A. A. Henden, and A. Price, "The International Variable Star Index (VSX)," in *Proc. Soc. Astron. Sci. 25th Annu. Symp.*, 2006, p. 47.

[14] <a id="ref-14"></a>B. Efron, "Bootstrap methods: Another look at the jackknife," *Ann. Statist.*, vol. 7, no. 1, pp. 1–26, 1979, doi: 10.1214/aos/1176344552.

---

## Appendix A. Notation

Each symbol has one meaning in this paper. The index $j$ runs over frames of a stack, $i$ over pixels, and $s$ over stars; $k$ indexes the frames of a session.

| Symbol | Meaning | Unit |
| --- | --- | --- |
| $g$, $g_j$ | Conversion gain, overall and of frame $j$ | e⁻/ADU |
| $S$, $\sigma^2$, $\sigma_r^2$ | Signal above the bias in one frame; variance of one frame, and of the read noise | ADU, ADU² |
| $S_j$, $\sigma_{r,j}$ | Sky signal per pixel and read noise of frame $j$ | e⁻ |
| $v_j$, $V$, $w_j$ | Pixel variance of frame $j$, and of the stack; weight of frame $j$ | ADU², ADU², none |
| $V_{\text{ap}}$, $A_{\text{eff}}$ | Variance of the aperture sum, and the effective aperture area | ADU², px |
| $d_i$, $n_{\text{ring}}$ | Overlap of pixel $i$ with the circle, and the number of pixels in the ring | none, px |
| $\sigma_{\text{flat}}$, $\sigma_f$ | Noise that the master flat adds to an aperture sum, and the relative noise of the averaged flat before the blur | ADU, none |
| $\bar{s}$, $G$ | Sky level of the stack, and the blur kernel of the flat | ADU, none |
| $p$, $c$, $\text{FWHM}$, $\text{FWHM}_{\text{eff}}$ | Pixel side; pixel-term constant; star width, and star width including the pixel term | px, none, px |
| $\theta$, $h_{ab}$ | Rotation angle of a frame, and the entries of its registration transform | °, none |
| $m_{\text{cat}}$, $f$, $t$ | Catalog magnitude, flux, and exposure time | mag, ADU, s |
| $z$, $z_{s,k}$, $\tilde{z}_s$, $Z_k$ | Offset of a star, in frame $k$, and its session median; differential zero point of frame $k$ | mag |
| $A$, $n$, $s$, SNR | Star peak above the sky, sky standard deviation, Gaussian star width, aperture signal-to-noise ratio | ADU, ADU, px, none |
| $\sigma_x$, $\ell$ | Centroid error per axis, and pixel scale | px, arcsec/px |
| $\rho$, $\rho_{\text{c}}$, $\rho_{\text{p}}$, $\rho_{\text{G}}$ | Expected position distance: total, centroid noise, pixel phase, Gaia epoch | arcsec |
| $F$, $B$, $R$, $n_{\text{ap}}$, $\sigma_F$, $\sigma_m$ | Star electrons, sky electrons per pixel, read noise, pixels in the circle; error of the star's electrons, and in magnitudes | e⁻, e⁻, e⁻, px; e⁻, mag |
| $\sigma_{\text{sc}}$, $c_Y$, $\beta$, $h_0$, $D$, $X$, $h$, $t$ | Scintillation noise, Young's coefficient, airmass power, turbulence scale height; aperture diameter, airmass, site height, exposure time | none, none, none, m; mm, none, m, s |
| $W$, $f$, $t_k$, $N$ | Window function, frequency, time of frame $k$, frame count | none, 1/d, d, none |
| $\mathrm{SNR}_e$, $I_i$, $\varepsilon_i$, $\Delta$, $d$ | Signal-to-noise per resolution element, brightness and error of sample $i$; class gap, and relative RMS distance to a reference | none, ADU, ADU; none, none |
