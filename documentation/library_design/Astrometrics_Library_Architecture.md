# Astrometrics Library: Architecture and Design

*Version 2.7 · 2026-10-10 · Status: current*

## Abstract

This document explains how the Astrometrics image-processing system works and why it's built the way it is. It covers five pipelines: stacking, astrometry, photometry, spectroscopy, and moving object detection. Every pipeline organizes its data around one shared idea, called the Observation Target. Each pipeline also checks its own work twice: once before it starts, to catch bad input, and once after it finishes, to catch bad output. For a map from these ideas to the actual code, see [Astrometrics_Library_Implementation.md](./Astrometrics_Library_Implementation.md).

## 1. Introduction

Five separate image-processing pipelines work on the same telescope data. Without a shared way to describe a target, each pipeline would have to redo the same work — figuring out the sky position, listing the frames, tracking data quality — and none of them could reuse another pipeline's results, like a solved sky position or a finished stack. One shared data model lets the pipelines work together on the same target without stepping on each other's data.

The ideas in this document apply the same way whether a pipeline runs from a script or through the interactive graphical tools, because both paths call the exact same underlying code. Anything you can do by clicking through the interface, you can also automate as a script.

A complete, code-level reference for every public class and method is generated automatically from the source code's own documentation. See the Sphinx {doc}`API Reference </api/astrometricslib>`. For a map connecting these ideas to the actual Python files that implement them, see [Astrometrics_Library_Implementation.md](./Astrometrics_Library_Implementation.md).

The rest of this document is organized as follows. Section 2 introduces the four data models every pipeline shares. Sections 3 through 7 walk through the five pipelines in turn: Stacking, Astrometry, Photometry, Spectroscopy, and Moving Object Detection. Section 8 reports results from testing the pipelines on real telescope data. Section 9 concludes. Appendix A lists the tuned values the pipelines use and says which of them have been measured.

## 2. Observational Data Models

This system organizes everything it knows about the sky into four categories of information. Big professional sky-survey projects organize their data the same way, built around one target at a time [3]. These four categories cover everything the pipelines need to track: sky positions, brightness measurements, light curves, spectra, and quality checks — all connected to a single target.

Every image referenced below is stored as a FITS file, the standard file format for astronomical data. It plays the same role for telescopes that JPEG or PNG plays for ordinary photos. A FITS file bundles the raw pixel data together with a header: a block of metadata recording things like exposure time, filter, and (once solved) sky position.

### 2.1 Information Types

1. **Observation Target:** A specific region of sky, such as M 81 or Vega. It connects every raw image, calibration frame, solved sky position, stacked image, detected star, and moving object found for that target.
2. **Stellar Object:** A single star identified in the sky. It stores the star's position, its brightness over time (a light curve), how much its brightness varies, and its extracted spectrum.
3. **Moving Object Candidate:** A possible solar-system object, such as an asteroid or comet. It records the object's path across images, how fast it appears to move, how well that motion fits a straight line, and any match found in an asteroid catalog.
4. **Pipeline Quality Record:** A health report for one processing run — for example, how many images were thrown out, how sharp the stars look, how accurate the positioning was, and how much measurement noise remains.

Table 1 summarizes the four data models: what each one covers, what data it stores, and what job it does.

**Table 1.** Observational data models summary.

| Observational Data Model | Scope | Data Attributes | Primary Role |
| :--- | :--- | :--- | :--- |
| **Observation Target** | One field of view / sky area | Coordinates $(\alpha_0, \delta_0)$, raw exposures, calibration masters, stacked FITS images | Connects all images and results for one part of the sky |
| **Stellar Object** | Individual stars & catalogs | Coordinates $(\alpha, \delta)$, magnitude $V$, light curves $\hat{F}(t)$, 1D spectra $F(\lambda)$ | Tracks one star's brightness history, spectrum, and variability |
| **Moving Object Candidate** | Solar-system objects passing through | Detection chain $(x_i, y_i, t_i)$, motion rates $(\dot{\alpha}, \dot{\delta})$, ephemeris match | Records an asteroid's motion path and any catalog match |
| **Pipeline Quality Record** | Health of each pipeline run | Frame survivor counts $N$, rejection cutoffs $\sigma_{\text{low}}(N), \sigma_{\text{high}}(N)$, FWHM ratio, gate results (passed, failed, or not checked) | Tracks whether a pipeline run succeeded, and how well |

:::{note}
**Theory.** A star's magnitude ($V$) is its brightness on a scale that runs backwards and by powers of ten: lower numbers mean brighter stars, and each step of 5 magnitudes means a 100x change in brightness. This scale goes back to how ancient astronomers ranked stars just by looking at them, and it stuck. It isn't used directly by any equation in this document — it shows up in Table 1 because it's the standard unit for how bright a star's catalog entry says it is.
:::

### 2.2 Data Model Progression Across Pipelines

Table 2 shows how each data model gets filled in as data moves through the five pipelines, from raw images at the top down to final measurements at the bottom.

**Table 2.** Overview matrix of observational data models across pipeline execution levels.

| Pipeline | Observation Target | Stellar Object | Moving Object Candidate | Quality Record |
| :--- | :--- | :--- | :--- | :--- |
| **Stacking** | Raw photos and calibration frames | N/A | Single-frame star or satellite detections | Removed bad pixels and overall image sharpness |
| **Astrometry** | Matched sky coordinates and target center | Star locations linked to catalog positions | Sky positions for moving candidates | Solve success status, fit residual, and catalog match counts |
| **Photometry** | Chosen comparison stars | Star brightness changes and light curves | Brightness measurements over time | Ensemble outlier rejection and comparison-star composition |
| **Spectroscopy** | Reference star position and camera sensitivity | Rainbow color spectrums for stars | N/A | Zero-order saturation status and calibration fit convergence |
| **Moving Objects** | Self-consistency filtered candidates | Hidden stationary stars | Path direction, speed, and catalog names | Detection counts and straight-line residuals against position error |

---

## 3. Image Stacking Pipeline

The stacking pipeline combines many individual exposures into one high-quality image. Combining frames boosts the real signal from stars while averaging out noise and one-off artifacts like satellite trails and cosmic ray hits.

### 3.1 Purpose & Interfaces

* **Inputs:** Raw light exposures, plus three master calibration frames ($M_{\text{bias}}, M_{\text{dark}}, M_{\text{flat}}$, explained below).
* **Outputs:** A calibrated, stacked 2D image (broadband or spectral), an updated FITS header, and a Stack Quality Record.

### 3.2 Major Concepts and Governing Equations

Every raw exposure contains two kinds of unwanted signal: an *additive* error from the camera's electronics and sensor heat, and a *multiplicative* error from the optics, where parts of the image come out dimmer for reasons unrelated to the sky. Before frames are aligned and combined, each raw exposure $I_{\text{raw}}$ is corrected using three calibration images:

* **Master Bias ($M_{\text{bias}}$):** An average of many zero-length, closed-shutter exposures. It isolates the fixed voltage offset the camera's electronics add to every pixel, regardless of exposure time.
* **Master Dark ($M_{\text{dark}}$):** An average of closed-shutter exposures taken with the same exposure time, gain, and sensor temperature as the light frames. It measures the extra signal ("dark current") produced by heat inside the sensor over time, plus any permanently defective ("hot") pixels.
* **Master Flat ($M_{\text{flat}}$):** An average of exposures of an evenly lit surface (the twilight sky or a flat light panel). It measures how much dimmer some parts of the image are due to the optics — vignetting, dust specks on the sensor glass, and pixel-to-pixel sensitivity differences.

These two categories of error are corrected differently:

* **Additive errors (subtracted):** extra pixel counts added regardless of how much starlight actually reached the sensor.
  * *Electronic bias:* a constant voltage offset the camera adds so pixel values never go negative during digitization.
  * *Thermal dark current:* extra electrons that build up from heat inside the sensor, growing roughly in proportion to exposure time.
* **Multiplicative errors (divided out):** factors that block or scale down a percentage of the real incoming starlight.
  * *Vignetting and dust shadows:* obstructions in the optical path (lens edges, dust on the sensor glass) that dim parts of the image.
  * *Pixel sensitivity differences:* manufacturing differences in each pixel's quantum efficiency.

Combining these corrections gives the true, calibrated brightness $I_{\text{calibrated}}$. First subtract the additive errors. Then divide out the (normalized) multiplicative ones:

$$
I_{\text{calibrated}} = \frac{I_{\text{raw}} - M_{\text{dark}}}{\left(M_{\text{flat}} - M_{\text{bias}}\right) / \langle M_{\text{flat}} - M_{\text{bias}} \rangle} \tag{1}
$$

The master frames are only as good as the calibration frames chosen for them. Four rules decide which frames are used and how much to trust them:

* **Match the camera settings.** Calibration frames are filed by the settings they were taken at. A frame taken at one gain or offset cannot calibrate lights taken at another. Binning merges neighboring pixels, so frames binned differently have different pixels. A binning mismatch therefore blocks that kind of calibration frame instead of applying a wrong one, and the stack is flagged. A mismatch in gain, offset, or temperature applies the nearest frames and flags the stack with a sentence saying which setting differed. <!-- impl: astrometricslib.drivers.calibration_library.calibration_setting_key --> <!-- impl: astrometricslib.drivers.calibration_library.CalibrationLibrary.select_dark_frames -->
* **Match darks by temperature.** Dark current roughly doubles every few degrees. Darks are therefore sorted into temperature slots a few degrees wide, and the slot nearest the lights is used. Darks from different slots are never averaged into one master. <!-- impl: astrometricslib.drivers.calibration_library.temperature_slot_c -->
* **Remove the bias from every flat.** A flat frame carries the bias offset on top of the light. If the offset stays in, the flat looks too uniform, the vignetting looks weaker than it is, and the lights come out under-corrected. The bias master is subtracted from every flat, including a master built from a single flat. A stack of one light frame is calibrated like any other. <!-- impl: astrometricslib.drivers.siril_interface.build_flat_master_commands --> <!-- impl: astrometricslib.drivers.siril_interface.build_single_light_commands -->
* **Flag thin masters.** Averaging and outlier rejection need several frames. A master built from one or two frames keeps their noise and any cosmic ray hit. Such a master is still built, because the library can legitimately hold one flat or one dark, but the stack is flagged so a reader knows the master is weak. The minimum is a design choice (Appendix A). <!-- impl: astrometricslib.drivers.siril_interface.calibration_count_flags -->

After calibration, the pipeline averages $N$ exposures together to raise the signal-to-noise ratio. When averaging, any pixel far enough from the group's median value is thrown out as an outlier (a satellite trail, a cosmic ray hit, a plane). Rather than using one fixed cutoff for every stack — always rejecting anything past $3\sigma$, say — the pipeline adjusts the cutoff based on how many frames $N$ are being combined. It starts from Chauvenet's criterion [4], [5]:

$$
\sigma(N) = \sqrt{2}\,\mathrm{erfc}^{-1}\!\left(\frac{1}{2N}\right) \tag{2}
$$

In words:
* **$\sigma(N)$ (rejection cutoff):** how many standard deviations from the group's median a pixel has to be before it's thrown out (e.g. $1.64\sigma$ at $N = 5$, $2.81\sigma$ at $N = 100$).
* **$N$:** how many light exposures are being combined.
* **$\mathrm{erfc}^{-1}$ (inverse complementary error function):** finds the cutoff at which fewer than 0.5 false-positive pixel rejections are expected across all $N$ frames.

As $N$ grows, so does the chance that some pixel looks like an outlier by pure chance, so Equation (2) loosens the cutoff accordingly. For deep stacks ($N > 100$) this avoids clipping real, bright star centers, which a fixed $3\sigma$ cutoff would mistake for outliers.

Equation (2) treats the spread of the values as known exactly. A real stacker estimates the spread from the same $N$ values it is judging, and with a handful of frames that estimate is noisy. Used as it stands, Equation (2) throws out good values from small stacks. The pipeline therefore applies two changes:

* **A floor on the high cutoff.** The upper cutoff never drops below a minimum, $\sigma_{\text{floor}}$.
* **A looser low cutoff.** The lower cutoff sits a fixed margin $\Delta_\sigma$ above the upper one. Satellite trails, cosmic ray hits, and hot pixels are brighter than the true value, so the upper cutoff has to catch them. Values far below the median are rare, so a looser lower cutoff removes fewer good values at little cost.

$$
\sigma_{\text{high}}(N) = \max\!\big(\sigma(N),\ \sigma_{\text{floor}}\big), \qquad \sigma_{\text{low}}(N) = \sigma_{\text{high}}(N) + \Delta_\sigma \tag{3}
$$

<!-- impl: astrometricslib.utilities.rejection_thresholds.rejection_bounds --> <!-- impl: astrometricslib.utilities.rejection_thresholds.chauvenet_sigma -->

With the default floor, Equation (3) differs from Equation (2) for stacks of fewer than about 40 frames. Above that, the Chauvenet value is already larger than the floor.

:::{warning}
**Designed, not measured on real data.** The floor and the margin $\Delta_\sigma$ are design choices (Appendix A). They were checked only in a simulation of pure Gaussian noise that models the stacker's clipping approximately. In that simulation, with five frames, the plain Chauvenet cutoff on both sides rejected 7.4% of good samples. The floored, asymmetric cutoffs rejected 2.4%. The price is that weak outliers survive more often: a 5-sigma outlier in one of five frames was removed 83% of the time by the plain cutoff and 60% of the time with the floor. No sharpness measurement exists for stacks of 5 to 15 frames. <!-- impl: astrometricslib.scripts.rejection_small_n_check -->
:::

Frames taken with different exposure lengths cannot be clipped together, because a long and a short frame hold very different amounts of light in the same pixel, and clipping would discard the best frames' values as outliers. Each exposure length is therefore stacked on its own, and the group stacks are combined afterward. Each group stack is first converted to counts per second. The groups are then put on one brightness scale, because the stacker leaves each stack with its own, and combined with weights of one over the noise variance, so quieter groups count for more. <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.combine_exposure_group_images -->

The brightness scale of a group is the median ratio of its pixels to the reference group's pixels, measured only over mid-range pixels. A sensor is linear between the sky floor and near full well. In that range two exposures of one scene differ by a single factor, and that factor is the scale. Close to full well the response flattens, and in a group clipped at zero the faint end reads high. At those extremes the ratio measures the sensor's nonlinearity, not the scale. As a check, the ratio over the brightest pixels is measured too and never applied. A group whose two ratios disagree by more than a tolerance is not one scale factor of the reference. It is marked nonlinear and left out of the combined image. <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.measure_group_gains -->

### 3.3 Pipeline Theory of Operations

Combining frames means three things happen together: calibrating each frame, aligning them to each other, and rejecting outlier pixels. Four design choices drive this process:

1. **Calibrate before aligning:** Each raw light frame is corrected using Equation (1) before it's aligned with the others — bias subtraction for the electronic offset, dark subtraction for heat buildup, and flat-field division for optical dimming and dust shadows.
2. **Adjust the rejection cutoff automatically:** The pixel-rejection cutoffs scale with the number of frames $N$, via Equations (2) and (3), so nobody has to manually tune them per dataset. This keeps small stacks from losing good values and stops deep stacks from over-clipping real starlight.
3. **One quality check, two alignment modes:** The same frame-quality check applies to both ordinary (broadband) images and spectroscopic images. But spectroscopic images are only allowed to shift position (not rotate or stretch) when aligning, so the axis the spectrum is spread out along doesn't get distorted relative to the reference star. Ordinary images use full alignment (shifting, rotating, and stretching as needed) to make stars as sharp as possible.
4. **Keep different optical setups separate:** Frames taken with different focal lengths or cameras get stacked separately, never mixed together. Mixing them would break the assumption that one pixel always corresponds to the same patch of sky, and would corrupt sharpness measurements.

Before rejecting outliers, the pipeline also checks that enough frames survived the earlier quality filtering. Too few frames left would make the outlier statistics meaningless. Table 3 lays out the stacking pipeline step by step.

**Table 3.** Stacking pipeline execution sequence and operations.

| Step | Pipeline Phase | Inputs & Outputs | Description |
|---|---|---|---|
| 1 | Calibration | **In:** Raw exposures, master bias, dark, flat<br>**Out:** Calibrated light frame array | Subtract the electronic bias and thermal dark current; divide out flat-field dimming via Eq. (1). Match calibration frames to the lights by camera settings and dark temperature. |
| 2 | Gain Check | **In:** Calibrated light frames<br>**Out:** Matching-gain subset | Drop any exposures taken at a different sensor gain setting than the rest. |
| 3 | Quality Safeguard | **In:** Frame quality metrics, minimum-frame floor<br>**Out:** Filtered light frame list | Filter out degraded frames, but never drop below a minimum survivor count (Appendix A). |
| 4 | Alignment & Outlier Rejection | **In:** Filtered light frames<br>**Out:** Aligned arrays & outlier map | Align exposures to each other and reject outlier pixels using the adjustable, floored cutoffs of Eq. (2) and Eq. (3). |
| 5 | Combine into Master Stack | **In:** Surviving pixel arrays<br>**Out:** Stacked 2D FITS image & Quality Record | Average the surviving pixels into one stacked image. When exposure lengths differ, stack each length separately, put the groups on one brightness scale, and combine them with inverse-variance weights. Record how the run went. |

<!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_homogeneity.find_dominant_gain_subset -->

### 3.4 Quality Metrics and Integration Planning

The stacking pipeline checks its own work in two rounds: before stacking (screening out bad raw photos) and after stacking (checking the final image). Each check is recorded as passed, failed, or not checked, and a check that could not look is never recorded as passed.

#### 3.4.1 Before Stacking: Screening Raw Photos
* **Focus & Tracking Checks:** Measures each raw photo's star sharpness (FWHM, Full Width at Half Maximum) and shape (roundness), then ranks photos accordingly. FWHM is converted from pixels into arcseconds using the camera's plate scale, so the same cutoff works regardless of camera. The softest-focus fraction of photos is excluded. The cutoff loosens automatically if too few frames would otherwise survive.
* **Hardware Context:** Image quality problems are cross-checked against the telescope's own reported state. Airmass (the atmospheric path length to the target, which grows quickly near the horizon), altitude, and azimuth separate normal atmospheric degradation from an actual mount problem. Focuser position and sensor temperature let the pipeline detect focus drift and improve automatic temperature-compensation settings over time.
* **Cloud & Sky Brightness Checks:** Tracks the background sky brightness and the fraction of saturated pixels before alignment. Exposures with a sudden brightness spike or drop — passing clouds, stray light — are excluded.
* **Calibration Frame Checks:** Confirms that the calibration frames match the lights' camera settings (gain, offset, binning, and, for darks, temperature) and that each master was built from enough frames. A mismatch or a thin master flags the stack rather than passing silently. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.assess_input_quality.calibration_gates -->
* **Exposure Group Linearity:** When a stack mixes exposure lengths, each group's brightness must be one scale factor of the reference group's. A group that fails is left out of the combined image. <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.measure_exposure_group_gains -->

#### 3.4.2 After Stacking: Checking the Final Image
Once stacking finishes, five checks measure how healthy the result is:

1. **Signal-to-Noise Ratio (SNR) Gain:** How much background noise was reduced by combining photos. Combining $N$ photos reduces noise by a factor of $\sqrt{N}$.
2. **Star Sharpness Ratio ($R_{\text{FWHM}}$):** Compares how sharp stars look in the final stacked image ($\text{FWHM}_{\text{stack}}$) to how sharp the input photos predict a stack to be ($\text{FWHM}_{\text{expected}}$). A stack's star is the average of the stars in its frames. For round Gaussian stars, that average has a variance equal to the mean of the individual variances, so the expected width is the root mean square of the input widths. A median would call the normal blur of a stack made on nights of different seeing a failure.

$$
R_{\text{FWHM}} = \frac{\text{FWHM}_{\text{stack}}}{\text{FWHM}_{\text{expected}}}, \qquad \text{FWHM}_{\text{expected}} = \sqrt{\frac{1}{N}\sum_{i=1}^{N}\text{FWHM}_i^{\,2}} \tag{4}
$$

   * *What it means:* A value near $1.0$ means the frames lined up almost perfectly. A value above $1.2$ is a warning sign that small misalignments between frames blurred the final image. The $1.2$ limit did not fire on any of six real stacks, whose ratios ranged from 0.77 to 1.16. Whether it catches a genuine registration failure is untested, because no real failed-registration session exists in the library. <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_quality.expected_stack_fwhm -->
3. **Background Consistency Across Frames:** Before stacking, compares each surviving frame's background sky brightness to the rest of the sequence, flagging any frame with an unusual gap — a sign of light pollution or passing sky glow that earlier checks missed.
4. **Processing Time Tracking:** Tracks how long stacking took and whether it hit a timeout, building up a baseline over time that helps tune how many stacking jobs can safely run at once.
5. **Mount Tracking Analysis:** Measures how far stars shifted from frame to frame, in arcseconds, to gauge how well the telescope mount tracked the sky. The pipeline knows which side of the telescope's pier the mount was on at each moment, and uses that to exclude the expected jump from a meridian flip. That way, only real tracking problems (a snagged cable, wind, or polar-alignment drift) show up as errors.

---

## 4. Astrometric Calibration Pipeline

The astrometry pipeline figures out exactly where a stacked image is pointed. It matches camera pixels to real sky coordinates: Right Ascension $\alpha$ and Declination $\delta$, the sky's version of longitude and latitude. This is often called "plate solving," a term left over from the days of photographic plates. The result is a World Coordinate System (WCS): a mathematical formula that converts any pixel position in the image into a real sky position, and back.

### 4.1 Purpose & Interfaces

* **Inputs:** A calibrated, stacked image, plus a rough estimate of the field's coordinates and pixel scale.
* **Outputs:** A WCS transformation saved into the image's FITS header, a list of catalog-identified reference stars, and an Astrometry Quality Record.

### 4.2 Major Concepts and Governing Equations

Plate solving connects pixel coordinates $(x,y)$ to sky coordinates $(\alpha, \delta)$ in five steps:

1. **Star Detection:** Finds star-like points of light with a matched-filter star finder (the DAOFIND method, through the photutils package). The finder works on a background-subtracted image and keeps only sources whose sharpness and roundness look like a star. The star finder's own center for each source is the position the rest of the pipeline uses. <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection.SourceDetector.detect --> A separate step fits a 2D Gaussian to bright stars. That fit measures the star width (FWHM), which is used to judge how large a position error is acceptable. It does not set any star position. <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.fwhm.measure_image_fwhm -->
2. **Quad-Star Matching:** Groups nearby stars into four-star shapes ("quads") whose relative side lengths are invariant under rotation, reflection, and scaling. These shapes are compared against a pre-built star index to identify which patch of sky the image shows, even if the telescope was pointed somewhere unexpected — continuing a long tradition of cataloging the sky this way [1]. The Astrometry.net solver [7] does this work. The usual local solver reads the image file and finds its own stars. The pipeline's own star list decides whether a local attempt is worth making, and it is the list sent to the online solver when the local attempt fails. <!-- impl: astrometricslib.drivers.astrometry_net_driver.AstrometryNetPlateSolveDriver.solve -->
3. **Fitting the Distortion Map:** Converts pixel offsets from the image center $(x-x_0, y-y_0)$ into sky-coordinate offsets $(\xi, \eta)$, using four coefficients $CD_{i,j}$ plus a correction for lens/mirror distortion, called a Simple Imaging Polynomial (SIP) [11]:

$$
\xi = CD_{1,1}(x - x_0) + CD_{1,2}(y - y_0) + f_{\text{SIP},\xi}(x, y), \qquad \eta = CD_{2,1}(x - x_0) + CD_{2,2}(y - y_0) + f_{\text{SIP},\eta}(x, y) \tag{5}
$$

   * *What each piece means:*
     * $CD_{i,j}$: the four coefficients that together handle the image's rotation, scale, and any left-right flip.
     * $f_{\text{SIP}}(x, y)$: extra polynomial terms that correct for lens or mirror distortion, more noticeable toward the edges of a wide field of view.

   The solved WCS is written back into the image's FITS header, so the next program to open the file does not solve it again. The write keeps the SIP terms and the SIP marker in the coordinate type. Without them, a reader of the file would lose the lens correction near the image edges. Old sky-map keywords are deleted first, so a re-solve leaves no contradictory entries. <!-- impl: astrometricslib.pipelines.shared.session_identification.write_wcs_to_fits_header -->
4. **Naming the Stars:** With the WCS known, each detected star is matched to the nearest entry in the SIMBAD star database [8] or the Gaia catalog [10] to attach a real name and ID. Gaia lists each star's position for the year 2016.0, together with its proper motion: how far the star drifts across the sky each year. A star that drifts 1 arcsecond a year sits 10 arcseconds from its catalog position after ten years, which equals the pipeline's whole match radius. Each Gaia position is therefore moved to the date of the observation before matching. When the catalog rows carry no proper motion, or the image has no readable observation date, the positions stay at 2016.0 and the run records a flag saying so. A single star with no proper motion value stays where it is. <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.propagate_gaia_positions --> <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.StarIdentifier._move_gaia_coords_to_observation_epoch -->
5. **Judging the Fit:** Two different numbers describe how well the solved WCS agrees with the stars, and the pipeline keeps them apart.
   * *Fit residual.* The solver matches stars in the image to stars in its own reference index. For each matched star, the residual is the distance between the star's position under the fitted WCS and the reference star's position. The root mean square (RMS) of these distances, in arcseconds, is the fit residual. This is the number the quality check judges. <!-- impl: astrometricslib.drivers.astrometry_net_driver.read_corr_fit_statistics --> <!-- impl: astrometricslib.pipelines.astrometry.post_processing.run_gates.astrometry_run_gates -->
   * *Catalog match separation.* The RMS of the distance from each detected star to its nearest SIMBAD or Gaia star, counting only stars inside the match radius. Wrong matches and SIMBAD's uneven position precision raise it, and the match radius caps it. It is a rough check, not a measure of the fit. <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.StarIdentifier.get_catalog_match_separation_rms_arcsec -->

   The online solver returns no match table, so for an online solve the fit residual is unknown. The quality check then falls back to the catalog match separation and says so in its detail. <!-- impl: astrometricslib.pipelines.astrometry.post_processing.run_gates._residual_and_basis -->

### 4.3 Pipeline Theory of Operations

Table 4 lays out the astrometry pipeline step by step.

**Table 4.** Astrometry pipeline execution sequence and operations.

| Step | Pipeline Phase | Inputs & Outputs | Description |
|---|---|---|---|
| 1 | Source Detection | **In:** Stacked FITS image<br>**Out:** Star center list $(x_i, y_i)$ | Find stars with a matched-filter star finder, and keep sources that are sharp and round enough to be stars. |
| 2 | Quad Pattern Hashing | **In:** Star list or image<br>**Out:** Geometric quad shapes | Group stars into four-star shapes that stay recognizable under rotation and scaling. |
| 3 | WCS & Distortion Fit | **In:** Matched star pairs<br>**Out:** WCS header values ($CD_{i,j}, \text{SIP}$) | Fit the pixel-to-sky transformation via Eq. (5), and write it, SIP terms included, into the FITS header. |
| 4 | Catalog Cross-Match | **In:** Solved field, SIMBAD and Gaia catalogs, observation date<br>**Out:** Matched catalog stars $(\alpha_i, \delta_i)$ | Move Gaia positions to the observation date, then match each detected star to a catalog entry to attach its identity. |
| 5 | Solution Check | **In:** WCS, fit residual, catalog match separation, match counts<br>**Out:** Astrometry Quality Record | Judge the fit residual against the star width, report the catalog match separation next to it, and record how many stars matched. |

### 4.4 Quality Metrics and Solution Validation

Each check below is recorded as passed, failed, or not checked. A check that could not look is never recorded as passed. <!-- impl: astrometricslib.models.gate_result.unchecked_gate -->

#### 4.4.1 Before Solving: Checking the Image
* **Minimum Star Count:** At least four stars must be detected before a plate-solve is even attempted. Below a second, higher count (Appendix A), the local solver is skipped and the pipeline goes straight to the online solver, because a local attempt would spend its whole time limit on a near-certain failure. <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.StarIdentifier.process_image -->
* **Shape Filtering:** Detected sources are filtered by sharpness and roundness before solving, to screen out non-star detections and badly distorted blobs. <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection.SourceDetector.detect -->
* **Scale Hints & Fallback:** The pipeline computes the expected pixel scale from the pixel size, binning, and focal length, and starts with a narrow window around it. The solver widens that window by a further 20% on each side. If the solve still fails quickly, it retries with no scale or position assumption at all. If the hinted solve instead used nearly its whole time limit, the retry is skipped, because an unconstrained search is unlikely to succeed in the same budget. <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.StarIdentifier._calculate_scale_hints --> <!-- impl: astrometricslib.drivers.astrometry_net_driver.AstrometryNetPlateSolveDriver._solve_locally -->

#### 4.4.2 After Solving: Checking the Result
* **Solve Success Flag:** Whether the solver found a WCS at all; if not, the pipeline stops for that target.
* **Matched-Star Count:** The number of detected stars matched to a catalog entry must reach a minimum. With few matches, the residual is an average over too few stars to mean much. <!-- impl: astrometricslib.pipelines.astrometry.post_processing.run_gates.astrometry_run_gates -->
* **Fit Residual:** The solver's fit residual must stay below a fraction of the star width. A residual near half a star width means something other than centroid noise dominates the fit: a wrong match, a bad distortion model, or a wrong field. <!-- impl: astrometricslib.pipelines.astrometry.post_processing.run_gates.astrometry_run_gates -->
* **Network Health Tracking:** Tracks how often lookups to remote star catalogs succeed, fail, or get temporarily paused after repeated failures. The check fails when the pause tripped or half or more of the lookups failed. A run that attempted no lookup is recorded as not checked. This distinguishes a genuinely sparse star field from a network problem. <!-- impl: astrometricslib.pipelines.astrometry.post_processing.run_gates.astrometry_run_gates -->

:::{warning}
**Designed, not measured.** The minimum matched-star count (20), the residual limit (half a star width), and the failed-lookup limit are design estimates (Appendix A). The residual limit was set on catalog match separations, which ranged from 0.14 to 0.44 of a star width on the 8 saved solves that carry a plate scale. A fit residual is normally smaller than a separation, so the limit is lenient for fit residuals, and it has not been re-measured on them. The limit is there to catch a failed solve, not to rank good ones. The four saved solves with fewer than 20 matched stars have among the five largest residuals (5.8 to 9.0 arcseconds), which is the only evidence behind the star-count minimum. Do not read a pass as a measure of position accuracy.
:::

---

## 5. Stellar Photometry Pipeline

The photometry pipeline tracks how a star's brightness changes across a sequence of individual (unstacked) photos, building a light curve and flagging stars whose brightness varies.

### 5.1 Purpose & Interfaces

* **Inputs:** A sequence of calibrated, unstacked light frames, the stars found on the session's first frame, and a solved WCS.
* **Outputs:** Normalized light curves $\hat{F}_i(t)$ with a 1-sigma uncertainty and a mid-exposure time for every point, brightness statistics, a list of variable-star candidates, and a Photometry Quality Record.

### 5.2 Major Concepts and Governing Equations

1. **Tracking a Star Frame to Frame:** The pipeline finds the stars once, on the first frame of an observing session. On every later frame, it measures the frame's overall drift from a handful of bright reference stars. If too few of them are found, it assumes no shift rather than guessing. It starts each star at its first-frame position plus that drift. Then it re-centers the star on its own brightness-weighted centroid (the average pixel position, weighted by brightness) in a small box. A star's true offset differs slightly from the frame's average drift, so this step keeps the measuring circle on the star. The re-centered position is refused, and the shifted position kept, when the box holds a saturated pixel, holds no light above the sky, reaches past the frame edge, or the centroid moved too far to be the same star. <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry._process_single_frame_worker --> <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.refine_star_centroid -->

   The measuring circle and its sky ring sit at the star's exact position, including the fraction of a pixel. A pixel on the circle's edge counts for the fraction of it inside the circle. The position is never rounded to a whole pixel. A circle half a pixel off a typical star loses 1 to 2 percent of its light, and that loss changes as the field drifts. It is the same size as the brightness changes the pipeline searches for. <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.measure_aperture_photometry -->

   This shortcut only holds within one observing session, since framing and pointing stay consistent only while the telescope isn't repositioned. The pipeline therefore tracks stars session by session, not across a target's entire observation history.
2. **Uncertainty of Each Measurement:** Every brightness comes with a 1-sigma uncertainty, so later steps can weight a point by how well it was measured. The uncertainty follows the CCD equation (the noise budget of a camera sensor) [12]. It works in electrons, because the random scatter of a count follows the number of electrons collected:

$$
\sigma_F^2 = F + n_{\text{pix}}\left(S + R^2 + D\right) + \frac{n_{\text{pix}}^2}{n_{\text{sky}}}\left(S + R^2\right) \tag{6}
$$

   * *What each piece means:* $F$ is the star's net counts, so the first term is the star's own shot noise (the random scatter of a count). $S$ is the sky level per pixel, $R$ the read noise, and $D$ the dark current collected per pixel. $n_{\text{pix}}$ is the area of the circle and $n_{\text{sky}}$ the number of pixels in the sky ring. The second term is the noise of the sky, read-out, and dark counts under the circle. The third is the noise in the sky level itself, which the ring measures from a limited number of pixels. All of $F$, $S$, $R$, and $D$ are in electrons.

   The gain (electrons per ADU, the step of the stored pixel value) comes from the camera's profile, then from the frame's header, and only then from an assumption of 1 electron per ADU and no read noise. When the gain is assumed, the uncertainties have the right dependence on brightness but the wrong size, and the light curve says so. The dark current is taken as zero because no source records it. The equation assumes the bias offset has been removed from the frame. <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.aperture_flux_error_adu --> <!-- impl: astrometricslib.pipelines.photometry.pre_processing.detector_noise.resolve_detector_noise -->
3. **Times of Observation:** Each point is stamped with the middle of its exposure as a Julian date in BJD_TDB (Barycentric Julian Date in Barycentric Dynamical Time) [13]. This is the time the light would reach the center of mass of the solar system, so the date of an event does not depend on where Earth is in its orbit. The shift is up to about 8 minutes, and a long exposure shifts the mid-time by half its length. A frame with a missing or unreadable observation date is rejected from every light curve. It is never given the current time. When the target's position or the observatory's location is unknown, the light curve records which weaker time basis was used. <!-- impl: astrometricslib.pipelines.photometry.pre_processing.observation_times.barycentric_julian_dates --> <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.read_observation_time -->
4. **Ensemble Differential Photometry:** To cancel out passing clouds or changing atmospheric transparency, each star's raw brightness $F_i(t)$ is divided by a signal built from a group ("ensemble") of well-behaved comparison stars in the same field. The group is chosen once per session and is the same in every frame. The choice has three steps:
   * *Candidates.* A star qualifies only if it has a positive brightness in every usable frame, is never saturated, and no catalog lists it as variable. Saturated stars are excluded because brightness stops tracking light near full well.
   * *Brightness band.* The candidates are ranked by median brightness. The very brightest are skipped because they come closest to saturation. The band runs down to a fixed share of the candidates, and at most a fixed number of stars enter the next step. A star outside this band is not a comparison star.
   * *Vetting.* Each candidate is compared with an ensemble built from the other candidates. The star that scatters most, relative to the scatter its uncertainties predict, is dropped. The step repeats until no star scatters more than its uncertainties allow, or until a minimum number remain. If more than a cap remain, the stars with the smallest uncertainties are kept.

   The signal in each frame is the weighted mean of the comparison stars' brightness, each divided by its own session mean so that bright and faint stars count on the same scale. Noisier stars get less weight:

$$
\hat{F}_i(t) = \frac{F_i(t)}{L(t)}, \qquad L(t) = \bar{m}\,\frac{\sum_{k=1}^{K} w_k(t)\,F_k(t)/m_k}{\sum_{k=1}^{K} w_k(t)}, \qquad w_k(t) = \frac{1}{\sigma_k(t)^2} \tag{7}
$$

   * *What each piece means:* $m_k$ is comparison star $k$'s mean brightness over the session, $\bar{m}$ the mean of those means, and $\sigma_k(t)$ the fractional uncertainty of star $k$ in that frame. $\bar{m}$ puts $L(t)$ back in brightness units, so a star as bright as the average comparison star has $\hat{F}$ near 1. Equal stars average down as $1/\sqrt{K}$. A median of raw brightness from stars of different brightness has only the noise of one star.

   A comparison star is divided by the signal built from the *other* members, so that its own noise and changes never enter its own divisor. Without this, a dip in one member would be hidden by about $1/K$ of its depth. Frames in which fewer than half the usual number of stars were measured are left out of the session, as are frames whose signal is a clear outlier compared with the rest (a passing cloud, say). A star's own isolated outlying points (a cosmic ray, a bad centroid) are clipped, but two or more neighboring points on the same side are kept as a real change, such as a dip. <!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.select_comparison_set --> <!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.build_ensemble_signal --> <!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.VariabilityAnalyzer._reject_outlier_frames --> <!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.VariabilityAnalyzer._reject_outlier_measurements_for_star -->

   The pipeline does not fit a star's own brightness against airmass. Dividing by the ensemble already removes the dimming every star shares. A fit to one star would also remove part of a transit or half a pulsation cycle, because airmass changes steadily over a night. <!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.VariabilityAnalyzer.detrend_light_curves_airmass -->


:::{warning}
**Designed, not measured on real fields.** The brightness band, the minimum and cap on the number of comparison stars, the consistency limit used in vetting, and the half-of-typical rule for usable frames are design choices (Appendix A). Only the method is checked, on synthetic sessions with known truth: it recovered a 1 percent, 2 hour dip to within 3 percent of its depth, rejected a comparison candidate that carried a 5 percent sinusoid, matched the comparison scatter to the propagated uncertainty within 20 percent, and removed a 3 percent frame-to-frame transparency change from every curve. <!-- impl: astrometricslib.pipelines.photometry.test.test_comparison_ensemble_injection -->

:::

5. **Deciding Which Stars Vary:** A star's scatter grows as it gets fainter, because its measurements are noisier. A rule that flags the stars with the most scatter would fill up with faint stars. The pipeline asks a different question: does this star scatter more than a constant star of the same brightness scatters in this field? It fits a noise model of the field, which gives the expected scatter of a constant star at each brightness, from the field's own stars. Then it measures three indices for every star:
   * *Excess scatter:* the star's measured scatter divided by the model's expected scatter. A constant star is near 1.
   * *Reduced chi-square:* how far the points sit from the star's weighted mean, in units of each point's uncertainty. A constant star with correct uncertainties is near 1.

$$
\chi^2_\nu = \frac{1}{n-1}\sum_{j=1}^{n}\left(\frac{x_j - \bar{x}_w}{s_j}\right)^2 \tag{8}
$$

   * *What each piece means:* $x_j$ is point $j$ of the normalized light curve, $s_j$ its uncertainty, $\bar{x}_w$ the uncertainty-weighted mean, and $n$ the number of points. The propagated uncertainties leave out centroid jitter and flat-field error, so they are raised to the noise model's level when that level is higher.
   * *Stetson J:* multiplies the deviations of neighboring points [14]. White noise gives products of random sign, so J stays near 0. A smooth change gives mostly positive products. One bad point gives two products of opposite sign and adds little.

   The thresholds on chi-square and J are set from the field itself, at a fixed number of robust standard deviations above the field's median, so a field whose uncertainties are all mis-scaled (an assumed gain, for example) raises its own thresholds. The threshold on excess scatter is fixed. A star is a variable-star candidate only when all three indices exceed their thresholds. Stetson J acts as a veto: it keeps isolated bad points from creating candidates. <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.assess_variability --> <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.fit_noise_model --> <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.reduced_chi_square --> <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.stetson_j --> <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.calibrate_thresholds -->

   The coefficient of variation stays on every light curve as a plain measure of scatter:

$$
C_v = \frac{\sigma_{\hat{F}}}{\langle \hat{F} \rangle} \tag{9}
$$

   When a field has too few stars to fit a noise model, the pipeline falls back to flagging stars whose $C_v$ is unusually high relative to the other stars in the same field, rather than against one fixed threshold used for every field. This mirrors the field-relative approach long-running variable-star observing networks use to flag candidates for follow-up [2]. <!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.flag_variable_stars -->


:::{warning}
**The flag is only as good as the field's noise.** On synthetic fields with known variables, the combined score ranked the injected variables above constant stars with an area under the ROC curve (AUC) of 0.94, against 0.84 for $C_v$ alone. The rule flagged none of 9,120 constant stars and 64 percent of the injected variables. These numbers are from synthetic data, not the sky. They test the method on a field whose noise is understood. The thresholds are design values (Appendix A). <!-- impl: astrometricslib.pipelines.photometry.test.test_variability_injection --> No AUC for the new score has been measured on the observatory's real library. The older $C_v$-only flag, measured on the library (46,259 stars with light curves in 32 targets, 2,374 of them listed as variable by SIMBAD, Gaia, or VSX), separated catalogued variables from other stars no better than chance (AUC 0.46 between stars of similar brightness; 0.5 is chance). A flag in most of those runs was not evidence of the kind of variable the catalogs know. <!-- impl: astrometricslib.scripts.measure_variability_cutoff -->

:::

6. **Matching the Same Star Across Sessions:** Since tracking (concept 1) only holds within one session, the pipeline needs another way to identify the same star across sessions taken weeks or months apart. It does that using each star's sky position: every session's tracked stars are placed on the same sky-coordinate frame and matched against stars already found in earlier sessions, folding one physical star's measurements across sessions into a single, continuous light curve. The merge does not rescale any session. Each session keeps its brightness level as measured, because rescaling one session to match another would remove the very slow change the next step looks for. <!-- impl: astrometricslib.pipelines.photometry.batch._match_and_merge_across_sessions --> <!-- impl: astrometricslib.pipelines.photometry.batch._merge_light_curves -->

   For each star seen in at least two sessions, the pipeline compares the sessions' median normalized brightness. The between-session amplitude is the ratio of the highest to the lowest median, in magnitudes. The significance is the difference of those two medians divided by their combined expected error. A star is flagged when both are large enough. A flag means the star's level differs between sessions by more than its own scatter explains. It does not rule out the comparison stars as the cause, because each session picks its own comparison group. A shift that every star in the field shares points to the comparison group, not to the star. <!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.identify_long_term_variable_candidates -->
7. **Identifying Stars by Catalog Name (optional):** Each session's reference frame can optionally be run through the same star-catalog matching used by the astrometry pipeline (Section 4), reusing an existing WCS when one is already available instead of solving again. This gives every star a real catalog name instead of a synthetic per-run label, and also improves the cross-session matching described in concept 6.

### 5.3 Pipeline Theory of Operations

Table 5 lays out the photometry pipeline step by step.

**Table 5.** Photometry pipeline execution sequence and operations.

| Step | Pipeline Phase | Inputs & Outputs | Description |
|---|---|---|---|
| 1 | Star Tracking | **In:** Light frame sequence & first-frame star positions<br>**Out:** Tracked position $(x_t, y_t)$ of every star in each frame | Shift each star by the frame's drift, then re-center it on its own brightness-weighted centroid. |
| 2 | Aperture Photometry | **In:** Calibrated frames, aperture radius, gain and read noise, exposure start times<br>**Out:** Brightness $F(t)$, its uncertainty via Eq. (6), sky background, BJD_TDB mid-exposure time | Sum pixel ADU within a circle at the exact star position, subtract the surrounding sky background, and compute the uncertainty and the time. |
| 3 | Ensemble Selection | **In:** Brightness of every star in the field<br>**Out:** Fixed, vetted comparison-star set | Exclude saturated and catalogued variable stars, take a brightness band of the remaining stars, and drop candidates that scatter more than their uncertainties allow. |
| 4 | Differential Normalization | **In:** Raw brightness & comparison set<br>**Out:** Normalized light curves $\hat{F}(t)$ with uncertainties | Divide each star's raw brightness by the comparison ensemble's weighted-mean signal, via Eq. (7). |
| 5 | Variability Analysis | **In:** Normalized light curves $\hat{F}(t)$<br>**Out:** Variable-star candidates & Photometry Record | Fit the field's noise model, compute excess scatter, reduced chi-square (Eq. (8)), and Stetson J, and flag stars that exceed all three thresholds. |
| 6 | Cross-Session Matching | **In:** Per-session tracked stars & sky coordinates<br>**Out:** Continuous multi-session light curves & between-session amplitude | Match the same star across sessions by sky position, join its measurements without rescaling, and measure how its level differs between sessions. |

### 5.4 Quality Metrics and Photometric Validation

Each run-level check below is recorded as passed, failed, or not checked, as in Section 4.4. A check that could not look is never recorded as passed. <!-- impl: astrometricslib.pipelines.photometry.post_processing.run_gates.photometry_run_gates -->

#### 5.4.1 Before Measuring: Screening the Input
* **Saturation Ceiling:** A star that is saturated in any frame is never used as a comparison star, since a saturated star's brightness reading can't be trusted.
* **Alignment Confidence Floor:** The frame's drift is measured only when a minimum number of reference stars are re-located; if too few are found, the pipeline assumes no shift rather than guessing at one it isn't confident about.
* **Capture Times:** A frame with no readable observation date is rejected, and each rejection reason is recorded in the run's quality summary.

#### 5.4.2 After Measuring: Checking the Light Curve
* **Comparison Set:** A session's comparison set must hold at least a minimum number of stars, and the number must be the same in every frame. The check also reports the comparison stars' scatter in magnitudes next to the scatter their uncertainties predict. A value near the prediction means the set is constant at the level of its noise.
* **Ensemble Outlier Rejection:** Frames whose comparison signal is a clear outlier are rejected before being folded into the light curve. The run is flagged when many frames are rejected.
* **Per-Star Outlier Rejection:** Individual normalized flux points are clipped per star to suppress single-frame artifacts in the light curve, except for runs of neighboring points that look like a real change.
* **Discrimination Check:** Among the field's stars, those that catalogs list as variable should score above the others in the variability score, with an AUC of at least a minimum. The check is not made without enough catalogued variables and unlisted stars to compare.
* **Detectable Amplitude:** The run states the smallest change its variability cutoff could see. A change smaller than that is invisible to the flag, however real it is.
* **Uncertainty Provenance:** The run reports the median per-point uncertainty in magnitudes and whether the gain was assumed.

---

## 6. Stellar Spectroscopy Pipeline

The spectroscopy pipeline extracts, calibrates, and analyzes a star's spectrum from images taken through a grism. A grism is a lens-like attachment that spreads a star's light out into a rainbow (a spectrum) without needing a narrow slit.

### 6.1 Purpose & Interfaces

* **Inputs:** A stacked spectral image, the position of the "zero-order" star image (the star's normal, undispersed position, used as a fixed reference point), the target's aperture size (different for point-like stars vs. extended objects), the camera's Quantum Efficiency (QE) curve, the instrument response (explained below), and the airmass from the frame's header.
* **Outputs:** A wavelength-calibrated 1D spectrum $F(\lambda)$, any detected hydrogen (Balmer) absorption or emission features, the automatically measured dispersion angle, a record of whether the airmass correction was applied, the closest spectral type with a measure of how clearly it beats its neighbors, and a Spectroscopy Quality Record.

### 6.2 Major Concepts and Governing Equations

1. **Finding the Spectrum's Angle:** Automatically measures the dispersion angle $\theta_{\text{disp}}$ relative to the sensor's pixel grid, along with orientation (horizontal or vertical, and direction), using the zero-order star position $(x_0, y_0)$ as the anchor point.
2. **Extracting the Spectrum:** Sums pixel intensity across the trail at each step along the dispersion trace, inside a reading box, and subtracts the sky background from every reading:

$$
F(x) = \sum_{y} w(y)\,\big[\,I(x, y) - S(x)\,\big] \tag{10}
$$

   * *What each piece means:* $I(x,y)$ is the pixel intensity, $S(x)$ the sky level per pixel at that step, and $w(y)$ the fraction of pixel $y$ that lies inside the reading box, from 0 to 1. Pixels at the box edge count in part, and the box center keeps its fractional position. Without this, the box would change size in whole-pixel jumps, and each jump would show up in the spectrum as a step the star did not cause.

   The sky level is measured in two strips, one on each side of the box. A gap separates each strip from the box, so the star's faint outer glow is not counted as sky. Each strip is cleaned of outlier pixels (hot pixels, cosmic rays, the edge of a passing trail) and its median is taken. If the two medians agree to within noise, the sky level is their mean. If they differ by more than noise explains, the brighter strip is treated as contaminated, since a neighbor's light can only add to a strip, and the cleaner strip is used alone. If only one strip lies on the image, that strip is used. Taking the lower of the two medians every time would read low, because the smaller of two noisy numbers sits below their true value, and it would leave sky light in a faint star's total. The extractor counts how often each case occurred. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.measure_sky --> <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.SpectrumExtractor._sum_aperture_minus_sky --> <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.box_pixel_weights -->
   * *Point sources vs. extended objects:* For a point-like star, the box reaches a fixed multiple of the trail's width to each side of its center. The width comes from a Gaussian fitted to the trail's cross-section at each step. That fit is noisy from step to step, so the width is smoothed with a running median before it sizes the box. A box that followed every fit directly would change size at each step, and the spectrum would jump by a couple of percent for reasons that have nothing to do with the star. Where the width cannot be fitted, a fixed radius is used. Extended objects like nebulae or comets get a wider box, whose radius comes from the object's catalogued angular size. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.traced_aperture_half_widths_px --> <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.smooth_aperture_sigmas --> <!-- impl: astrometricslib.pipelines.astrometry.pipeline.AstrometryPipeline._derive_extraction_radius_px -->
3. **Converting Pixels to Wavelength:** Converts the distance $x$ from the zero-order center, measured along the trail, into a wavelength $\lambda$, via the grating equation, fitting one free parameter: the grating-to-sensor distance $L$.

$$
\lambda(x) = d\,\sin\!\left(\arctan\frac{x_{\text{mm}}}{L}\right) \tag{11}
$$

   Here $d$ is the grating's groove spacing, and $x_{\text{mm}}$ is the distance converted to physical length via the sensor's pixel pitch.
   * *Distance along the trail:* A tilted trail is longer than the number of columns it covers, so the pipeline records, for every sample it keeps, the distance from the zero-order star measured along the trail. Samples that fall off the image or below the camera's shortest wavelength are dropped, so a sample's index is not its distance. The calibration step locates each absorption dip by this recorded distance and never by counting samples from the start. Counting would shift every dip by the number of dropped samples and bias the fitted $L$. <!-- impl: astrometricslib.pipelines.spectroscopy.utilities.calibration_tuner.SpectroscopyCalibrationTuner._fit_grating_distance -->
   * *Reference lines:* $L$ is calibrated using the Balmer series ($\mathrm{H}\beta = 4861.3\text{ Å}, \mathrm{H}\gamma = 4340.5\text{ Å}, \mathrm{H}\delta = 4101.7\text{ Å}$), whose wavelengths are already known precisely. The tuner tries every group of three dips against the three lines and keeps the group with the smallest RMS wavelength error. If no group fits within a coarse tolerance, the fit is rejected with an error. <!-- impl: astrometricslib.pipelines.spectroscopy.utilities.calibration_tuner.SpectroscopyCalibrationTuner._fit_grating_distance -->
4. **Correcting for the Instrument and the Air:** Three effects that belong to the equipment and the sky, not to the star, are removed from the extracted flux:
   * *Sensor color sensitivity.* The flux is divided by the sensor's QE curve, $\text{QE}(\lambda)$.
   * *Instrument tilt.* The grating, the telescope's coatings, and the air each favor some colors. Their combined effect is larger than the real differences between stellar types. It is measured by observing a star whose true spectrum is known (Vega), dividing the observation by the reference spectrum, and smoothing the result. This smooth curve is the instrument response, $R(\lambda)$. Dividing any other star's spectrum by it removes the tilt. The response is derived from QE-corrected spectra, so it is only applied to one.
   * *Airmass.* Air dims blue light more than red, and dims both more at higher airmass (how much air the light crossed: 1.0 straight overhead). The response already contains the dimming at the airmass of the standard star. A target observed at another airmass would keep a leftover blue-to-red tilt, so the spectrum is rescaled using an extinction curve $k(\lambda)$ in magnitudes per airmass.

   Together:

$$
F_{\text{cal}}(\lambda) = \frac{F\big(x(\lambda)\big)}{\text{QE}(\lambda)\,R(\lambda)}\;10^{\,0.4\,k(\lambda)\,(X_{\text{target}} - X_{\text{ref}})} \tag{12}
$$

   Here $X_{\text{target}}$ is the airmass of the frame and $X_{\text{ref}}$ the airmass of the standard star. The correction is skipped, and the reason recorded, when the header has no usable airmass or the response records no reference airmass. Without a QE curve, neither the response nor the airmass step is applied. The stored extinction curve describes a dry, high-altitude site. A site at lower altitude has somewhat more extinction in the blue, but the correction depends only on the difference between two airmasses, so the error from using another site's curve is a small fraction of the correction. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.quantum_efficiency_correction.apply_quantum_efficiency_correction --> <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.instrument_response.apply_instrument_response --> <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.atmospheric_extinction.apply_extinction_correction -->
5. **Classifying the Spectrum:** The corrected spectrum is compared with a library of reference spectra, each blurred to the instrument's resolution and scaled to the same overall brightness. Regions where Earth's atmosphere absorbs light are left out of the comparison. Each reference gets one score: the root-mean-square (RMS) difference as a fraction of the spectrum's average brightness. Lower is closer, and the best-scoring reference gives the likely spectral type. A score is not a probability. The pipeline reports how clearly the best reference wins as a gap in RMS: the second-best reference's RMS minus the best one's. A small gap means the data cannot choose between the two. Two gaps are kept. One is to the runner-up, which is usually a neighboring subtype and says the subtype is uncertain. The other is to the best reference of a different spectral class, which says the class itself is uncertain. A best match whose own RMS is too high is called a poor match, and a much worse one gets no type at all. <!-- impl: astrometricslib.pipelines.spectroscopy.processing.spectral_classifier.classify_spectral_type --> <!-- impl: astrometricslib.models.stellar_source.rms_gap_to_second_best --> <!-- impl: astrometricslib.models.stellar_source.rms_gap_to_next_class --> <!-- impl: astrometricslib.models.stellar_source.is_rms_gap_ambiguous -->
6. **An Alternative Path for Unstacked Frames:** Alongside the single-stacked-image approach above, a second path works directly on a target's raw, unstacked frames, grouped into observing sessions the same way the photometry pipeline is (Section 5.2, concept 1). Each session's stars are identified once against a real catalog, reusing an existing WCS from the frame's own file when available (Section 4), and every frame in that session extracts a spectrum for those same identified stars. This gives each extracted spectrum a stable, real star identity shared across the session, rather than each frame's own disconnected, unidentified detection.

:::{warning}
**Classification limits are measured on few stars.** The poor-match limit separates stars matched within three spectral-type steps of their catalog type (scores 0.046 to 0.105) from stars matched to a wrong type (0.181 to 0.283), but only 14 stars were used. A low score means the reference is close to the spectrum, not that the type is right: two known-wrong matches scored low. The gap limit is the smallest RMS distance between two neighboring references in the library, so a gap below it means the spectrum cannot prefer one reference over its neighbor even in principle. All limits are listed in Appendix A. <!-- impl: astrometricslib.models.stellar_source.NO_GOOD_MATCH_RMS --> <!-- impl: astrometricslib.models.stellar_source.AMBIGUOUS_RMS_GAP -->
:::

### 6.3 Pipeline Theory of Operations

Table 6 lays out the spectroscopy pipeline step by step.

**Table 6.** Spectroscopy pipeline execution sequence and operations.

| Step | Pipeline Phase | Inputs & Outputs | Description |
|---|---|---|---|
| 1 | Zero-Order Anchoring | **In:** Spectral image & star positions<br>**Out:** Zero-order centroid $(x_0, y_0)$ | Locate the star's normal (undispersed) position to serve as the dispersion origin. |
| 2 | Angle Detection | **In:** Spectral image & zero-order origin<br>**Out:** Dispersion angle $\theta_{\text{disp}}$ & direction | Measure the physical dispersion tilt to align the extraction axis. |
| 3 | Trace Extraction | **In:** Image, angle $\theta_{\text{disp}}$, aperture rule<br>**Out:** 1D flux profile $F(x)$, sky-measurement record | Sum pixels inside the reading box along the trace and subtract the sky at every step via Eq. (10). The box follows the smoothed trail width. |
| 4 | Wavelength Calibration | **In:** 1D profile $F(x)$, recorded distances along the trail & known Balmer lines<br>**Out:** Wavelength map $\lambda(x)$ | Fit the grating equation (Eq. (11)) using the known Balmer absorption lines, locating each dip by its recorded distance. |
| 5 | Instrument and Airmass Correction | **In:** Wavelength map $\lambda(x)$, QE curve, instrument response, frame airmass<br>**Out:** Calibrated spectrum $F_{\text{cal}}(\lambda)$, extinction record | Divide raw spectral flux by the QE curve and the instrument response, then rescale to the standard star's airmass via Eq. (12). |
| 6 | Classification | **In:** Calibrated spectrum, reference library<br>**Out:** Spectral type, RMS gaps | Score every reference by relative RMS and report the best match with its gap to the runner-up and to the next class. |

### 6.4 Quality Metrics and Spectral Validation

Each run-level check is recorded as passed, failed, or not checked, as in Section 4.4. <!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.run_gates.spectroscopy_run_gates -->

#### 6.4.1 Before Extracting: Checking the Input
* **Zero-Order Saturation Ceiling:** The reference star's center must not approach full-well capacity, or the dispersion origin $(x_0, y_0)$ is unreliable.
* **Sky Contamination:** The extractor records the share of readings where one sky strip was dropped because a neighbor's light lay beside the trail. A share well above a few percent means the spectrum deserves a closer look. The share, the dominant sky mode, and the count of each mode travel with the star's saved result. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.ExtractionDiagnostics.contaminated_sky_fraction -->

#### 6.4.2 After Extracting: Checking the Spectrum
* **Calibration Fit Rejection Gate:** When fewer than three absorption dips are found, the tuner first relaxes its dip-detection depth and then flattens the continuum before searching again. The wavelength-calibration fit is rejected if the best group of dips still leaves an RMS error above a coarse tolerance. <!-- impl: astrometricslib.pipelines.spectroscopy.utilities.calibration_tuner.SpectroscopyCalibrationTuner._detect_absorption_dips -->
* **Extinction Record:** Each spectrum carries a record saying whether the airmass correction was applied, the two airmasses, the curve used, and the reason if it was skipped. When it was skipped, a tilt of roughly the size of the gap between neighboring spectral types can remain, so a classification made without the correction is less certain. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.atmospheric_extinction.ExtinctionCorrection -->
* **Classification Ambiguity:** The classification gate fails when the best match is a poor match, or when its gap to the best reference of another spectral class is below the gap limit. A tie between neighboring subtypes alone does not fail the gate, and the number of such ties is reported. <!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.run_gates.spectroscopy_run_gates -->

---

## 7. Moving Object Detection Pipeline

The moving object detection pipeline finds, tracks, and identifies solar-system objects — asteroids and comets — across a sequence of individual (unstacked) photos.

### 7.1 Purpose & Interfaces

* **Inputs:** A sequence of unstacked light frames, their observation timestamps $t_i$, and a WCS for each frame. A raw frame usually has no solved WCS of its own, so the pipeline estimates one from the mount's reported pointing and then shifts it so the frame's stars line up with the stack's stars. <!-- impl: astrometricslib.pipelines.asteroid_detection.frame_wcs_composer.estimate_frame_wcs_from_mount_pointing --> <!-- impl: astrometricslib.pipelines.asteroid_detection.pipeline._estimate_bulk_pointing_correction_deg -->
* **Outputs:** Straight-line motion tracks, each candidate's velocity $(\dot{\alpha}, \dot{\delta})$, ephemeris cross-matches, an Asteroid Recovery Quality Record, and, on request, Minor Planet Center (MPC) observation lines.

### 7.2 Major Concepts and Governing Equations

Stacking averages moving objects away, since they land in a different spot in every frame. Detection has to work on individual, unstacked photos instead, in five steps:

1. **Single-Frame Detection:** Locates every bright point source in each raw exposure, with the same star finder the astrometry pipeline uses. After the pointing correction, the scatter that remains between a frame's stars and the stack's stars gives that frame's position error. A faint dot's center is less certain than a bright star's, so a further centroid error is added in quadrature (the square root of the sum of squares). <!-- impl: astrometricslib.pipelines.asteroid_detection.pipeline._detect_sources_in_one_frame -->
2. **Chaining Within One Observing Night:** Detections are grouped by observing night, and the dots in each night are linked on their own. A track may take a new dot only if the dot lies within a match radius of the track's last dot. The radius is the fastest plausible rate times the time elapsed, capped at a few arcminutes. Without the cap, a gap of hours or weeks grows the radius to a whole field of view, and two unrelated stars seen far apart in time link into a false mover. Each dot goes to the nearest open track. A chain needs detections in at least $M \ge 3$ frames, which eliminates single-frame cosmic ray hits. A mover seen on two nights comes out as two separate candidates, because joining them needs an orbit-style fit over days. Right Ascension wraps from 360° to 0°, so every difference in it is measured the short way around the circle. <!-- impl: astrometricslib.pipelines.asteroid_detection.detection.MovingObjectDetector._chain_detections_by_persistence --> <!-- impl: astrometricslib.utilities.observing_night.observing_night_id --> <!-- impl: astrometricslib.pipelines.shared.angles.wrapped_ra_difference_deg -->
3. **Filtering Out Obvious Non-Movers:** Discards any chain whose position spread is implausibly small, either in pixels (a stuck hot pixel) or in sky coordinates (a stationary star an earlier step missed). This filtering requires no external star catalog. <!-- impl: astrometricslib.pipelines.asteroid_detection.detection.MovingObjectDetector._evaluate_reference_frame_test -->
4. **Fitting a Straight-Line Path and Judging It by Its Residuals:** Independently fits each surviving chain's Right Ascension offset ($\Delta\alpha_m\cos\delta$) and Declination offset ($\Delta\delta_m$) against timestamps $t_m$ via ordinary least-squares regression:

$$
\Delta\alpha_m \cos\delta = \dot{\alpha}\,t_m + c_\alpha, \qquad \Delta\delta_m = \dot{\delta}\,t_m + c_\delta \tag{13}
$$

   The track is judged by how far its dots miss the fitted line, in arcseconds, compared with the position error of one picture. Four conditions must all hold:
   * *Rate.* The total rate lies inside the allowed range.
   * *Residuals.* On each axis, the RMS distance of the dots from the line is at most a small multiple of the position error.
   * *Displacement.* The fitted line carries the object, between the first and last picture, a few times farther than the position error. Without this, a star whose measured position only jitters would pass, because a flat scatter fits a line with small residuals.
   * *Direction.* Projected onto the direction of the fitted motion, the dots never move backward by more than the combined position error of the two dots compared. A real mover only goes forward. This catches a star whose measured center flips between two points, which fits a line well but alternates along it.

   The coefficient of determination, $R^2$, is still computed and stored. It decides nothing, because it is close to 1 for any chain with a large displacement, even when the dots miss the line by many arcseconds. <!-- impl: astrometricslib.pipelines.asteroid_detection.detection.MovingObjectDetector._fit_rate_linearity --> <!-- impl: astrometricslib.pipelines.asteroid_detection.detection._reverses_direction -->
5. **Checking Against Known Objects:** Matches each surviving track against SkyBoT [9], a solar-system ephemeris service, by asking which known minor planets lie near the track's first detection at that detection's time, and again near its last detection at that detection's time. A known object matches only if it lies within a fixed radius of the track at both moments. A match confirms the object's identity. A confirmed mover with no match is a real moving object that is not yet identified as a known body. That is worth a manual look, and it is not by itself a discovery. The matcher counts its queries and its failed queries, so a failed lookup is never mistaken for a field with no known asteroids. <!-- impl: astrometricslib.pipelines.asteroid_detection.ephemeris.EphemerisCrossMatcher.cross_match_candidates -->

A confirmed track can be written as MPC observation lines: designation, UTC date to 1e-5 day, position, and observatory code. The time written is the middle of the exposure. The detections carry an instrumental brightness, not a calibrated magnitude, so the magnitude column is left blank. Nothing in the pipeline submits a report. <!-- impl: astrometricslib.pipelines.asteroid_detection.mpc_report.mpc_lines_for_candidate -->

### 7.3 Pipeline Theory of Operations

Table 7 lays out the moving object detection pipeline step by step.

**Table 7.** Moving object detection pipeline execution sequence and operations.

| Step | Pipeline Phase | Inputs & Outputs | Description |
|---|---|---|---|
| 1 | Single-Frame Detection | **In:** Unstacked frames with estimated WCS<br>**Out:** Raw point-source positions $(x_i, y_i)$ with a position error each | Detect point sources on each individual exposure. |
| 2 | Per-Night Chaining & Persistence | **In:** Detections grouped by observing night, with timestamps $t_m$<br>**Out:** Multi-frame detection chains | Link dots within each night using a capped match radius, and keep only chains detected in $M \ge 3$ frames, to reject cosmic ray hits. |
| 3 | Filtering Non-Movers | **In:** Detection chains<br>**Out:** Likely moving candidate list | Discard chains whose position spread is implausibly small, in pixels or sky coordinates, to isolate real moving candidates. |
| 4 | Straight-Line Fitting | **In:** Candidate chains<br>**Out:** Velocity $(\dot{\alpha}, \dot{\delta})$, residual RMS per axis & $R^2$ | Fit a straight-line path via Eq. (13) on each axis. Reject tracks whose residuals, displacement, rate, or direction fail. |
| 5 | Catalog Cross-Match | **In:** Motion paths & SkyBoT database<br>**Out:** Asteroid matches & Recovery Record | Check each motion path against known solar-system object positions at its first and last detection to confirm its identity. |

### 7.4 Quality Metrics and Motion Track Validation

Each run-level check is recorded as passed, failed, or not checked, as in Section 4.4. A search that finds nothing says little unless it could have found something, so the first check records whether it could. <!-- impl: astrometricslib.pipelines.asteroid_detection.run_gates.asteroid_run_gates -->

#### 7.4.1 Before Fitting: Checking Detections
* **Single-Frame Detection Threshold:** Point-source detection uses a fixed multiple of the background noise level, so a candidate must be bright enough to appear on a single unstacked photo.
* **Enough Frames:** The search is not checked when fewer frames could be searched than a track needs, because no mover could have been found.
* **Pointing Metadata:** A frame that lacks pointing or image-size entries in its header cannot be given a WCS and fails the check.

#### 7.4.2 After Fitting: Checking the Track
* **Straight-Line Residuals:** Each track's RMS residual is compared with the position error of its pictures. When a picture has no measured position error, an assumed value stands in, the track is marked as using it, and the check fails so the result is not taken at face value.
* **Ephemeris Cross-Match:** A track is accepted as a SkyBoT match only if a known object lies within a fixed angular radius at both its first and last detection. The check fails when the database could not be reached.
* **Unmatched Movers:** A mover that moved in a straight line but matched no known asteroid is reported for a manual look.

:::{warning}
**What the detector can and cannot find.** On synthetic fields (eight frames 300 seconds apart, 12 fields per cell, with the SkyBoT query stubbed out), a mover was recovered 67% to 100% of the time when its peak was at least 8 sigma above the sky noise and it moved 20 to 150 arcseconds per hour. A mover was not found below about 8 sigma. A slow mover was not found at any brightness: at 5 arcseconds per hour it moves only 1.6 pixels in the 40 minutes of such a sequence, less than the 1.5 pixel tolerance for calling a track stationary, so it is rejected as a star. In 40 synthetic fields with no mover, the residual test alone confirmed one false track, a star whose center flipped between two positions 2.8 pixels apart. The direction check now rejects that field. The other 39 fields and the recovery table were not re-run after the direction check was added. The synthetic frames share one pointing, so these numbers test detection and chaining, not the position error of real frames. The limits of the residual test are not validated on real fields. <!-- impl: astrometricslib.scripts.validate_asteroid_detection -->
:::

---

## 8. Empirical Validation Campaign & Session Results

To check how well this design works in practice, it was tested on real observing sessions captured with a ZWO ASI 533MM Pro camera ($3.76\,\mu\text{m}$ pixel size).

### 8.1 Validation Datasets and Setup

Eight observing sessions were processed through all five pipelines:
1. **Vega Session (Spectroscopy):** A Star Analyzer 200 grism sequence ($N = 160$ light exposures, 270s total exposure time).
2. **M 13 Session (Spectroscopy & Stacking):** A dense star cluster grism field ($N = 86$ light exposures, 2520s total exposure time).
3. **Alcor Session (Spectroscopy):** A multi-frame grism sequence ($N = 138$ light exposures, 1080s total exposure time).
4. **NGC 2244 Session (Photometry):** An open star cluster time-series sequence ($N = 29$ light exposures, 10,800s total exposure time).
5. **M 81 Session (Photometry & Stacking):** A deep spiral-galaxy sequence, originally $N = 46$ light exposures (7,230s total exposure time) at the time of the stacking/astrometry results below. Since then, ongoing observation grew the same target's data to $N = 258$ light exposures across 8 separate observing sessions (2023-05 through 2026-05). That growth in the *number of sessions*, not just the number of frames, is what exposed the photometry cross-session tracking problem covered in Finding 6 (Section 8.3). It's a separate result from the single-session stacking/astrometry numbers below.
6. **NGC 2903 Session (Photometry):** A deep galaxy field time series ($N = 36$ light exposures, 14,400s total exposure time).
7. **NGC 2403 Session (Stacking, Astrometry & Moving Objects):** A wide-field galaxy sequence ($N = 70$ light exposures, 13,740s total exposure time).
8. **NGC 1893 Session (Stacking & Astrometry):** An open cluster field ($N = 49$ light exposures, 7,800s total exposure time).

All eight sessions were run through the full pipeline sequence described in Sections 3–7: calibration and outlier rejection, sky-position solving, brightness comparison, spectrum extraction, and moving-object tracking. Each session then went through its pipeline's own after-the-fact quality check.

### 8.2 Empirical Results Across All Five Pipelines

Table 8 summarizes the results.

**Table 8.** Multi-pipeline empirical validation metrics across ZWO ASI 533MM Pro observing sessions.

| Subsystem | Target Session | Key Metric Tested | Measured Value | Standard / Floor | Verdict |
|---|---|---|---|---|---|
| **Stacking** | M 81 Session | Rejection Cutoff $\sigma(N)$ & Sharpness Ratio $R_{\text{FWHM}}$ | $R_{\text{FWHM}} = 1.03$, Rejected: $0.42\%$ | $R_{\text{FWHM}} \le 1.20$ | **Passed** (Nominal) |
| **Stacking** | NGC 2403 Session | Optical Alignment Jitter Detection | $R_{\text{FWHM}} = 1.20$, Rejected: $0.85\%$ | $R_{\text{FWHM}} \ge 1.20$ flag | **Flagged** (Jitter Warning) |
| **Astrometry** | NGC 1893 Session | WCS Solve & SIMBAD Star Matching | WCS Resolved, 10 SIMBAD Matches | Success Flag = True | **Passed** (Nominal) |
| **Astrometry** | M 45 Session | Bright Cluster Star Matching & Distortion Fit | WCS Resolved, 12 SIMBAD Matches | Success Flag = True | **Passed** (Nominal) |
| **Photometry** | NGC 2244 Session | Ensemble Brightness Scatter Floor | $\sigma_m \le 0.012\text{ mag}$ for non-variable stars, measured with the 100th- to 300th-brightest stars combined by median | Scatter near the propagated uncertainty (checked on synthetic data only) | **Not repeated** (current comparison set) |
| **Photometry** | M 81 Session (8-session, current) | Cross-Session Tracking Bug & Fix | Before fix: 85–94% zero-brightness frames, 75% of the brightest fifth of stars wrongly flagged variable. After fix: 223/258 frames processed correctly, remaining false flags matched the faintest stars as expected | 0 cross-session tracking failures | **Passed** (Post-Fix) |
| **Photometry** | M 81 Session (8-session, current) | Cross-Session Star Matching, Repeatability | 11,392 session-scoped stars matched down to 8,422 (2,970 merges); a repeat run added, removed, or changed 0 rows | Same result every time it's run | **Passed** (Nominal) |
| **Spectroscopy** | Vega Session | Balmer Line Wavelength Calibration Fit | $\text{RMS}_{\Delta \lambda} = 0.42\text{ nm}$ ($\mathrm{H}\beta, \mathrm{H}\gamma, \mathrm{H}\delta$) | $\text{RMS}_{\Delta \lambda} \le 1.0\text{ nm}$ | **Passed** (Nominal) |
| **Spectroscopy** | M 13 Session | Cluster Zero-Order Star Tracking | 86 frames extracted, 0 tracking failures | Position stable to $\pm 0.2\text{px}$ | **Passed** (Nominal) |
| **Asteroid Recovery** | NGC 2403 Session | Full Detection & Filtering Sequence | One confirmed mover that matched no known asteroid, on a field far from the ecliptic (35-frame run). On synthetic fields: 67% to 100% recovery of movers of 8 sigma or brighter at 20 to 150 arcsec/h; 0% at 5 arcsec/h | 0 unexplained moving-object tracks | **Flagged** (unexplained mover; false-track rate not measured) |

:::{warning}
**Table 8 predates the current algorithms.** The stacking, plate-solve, photometry, spectroscopy, and moving-object figures above were measured with earlier versions of the stacking rejection cutoffs, the plate-solve gates, the comparison ensemble and variability flag, the spectrum extraction, and the track tests. They have not been repeated with the current algorithms, except where a row says so. Treat them as evidence about the data and the shape of the method, not as current performance. The astrometry rows in particular predate the minimum matched-star count and the residual check of Section 4.4.2, and have not been judged against them.
:::

### 8.3 Key Empirical Findings

1. **Sharpness Ratio Catches What Rejection Rate Misses:** In the NGC 2403 session, small alignment jitter between frames blurred the stars. Only 0.85% of pixels were rejected as outliers — low enough that a simple "how many pixels got thrown out" check would have missed the problem entirely. But the whole-image sharpness score correctly flagged it, hitting the 1.20 warning threshold. This shows that measuring overall star sharpness catches session-level quality problems that a pixel-rejection count alone would miss.
2. **Wavelength Calibration Precision:** Fitting the grating equation to the Vega spectrum came out very close to correct: the average error across the reference hydrogen lines used for calibration was 0.42 nm, comfortably inside the 1.0 nm accuracy needed for reliable results.
3. **Ensemble Photometry Stability:** On the 3-hour NGC 2244 sequence, each star's brightness was compared to a group of similarly bright stars in the same field. This canceled out atmospheric brightness changes so well that non-variable stars showed less than 0.012 magnitudes of noise. That measurement used 200 stars (the 100th- to 300th-brightest) combined by their median. The pipeline now builds a fixed comparison set another way. It skips the brightest 2 percent of the usable candidates, takes the band down to the brightest 30 percent, passes at most the 100 brightest stars of that band to a constancy check, keeps at most 20 stars that survive it, and combines them with an inverse-variance weighted mean (Section 5.2, concept 4). The 0.012 magnitude figure has not been repeated with this selection. For the current selection, the method itself is checked on synthetic sessions with known truth, not on NGC 2244. <!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.select_comparison_set --> <!-- impl: astrometricslib.pipelines.photometry.test.test_comparison_ensemble_injection -->
4. **Moving-Object Filtering: What the Evidence Shows:** Requiring a candidate to appear in at least 3 frames, and its motion to fit a straight line with residuals no larger than a small multiple of the position error, removes single-frame cosmic rays and stationary hot pixels by design. The evidence for how well it works is limited. On synthetic fields with known movers, movers of 8 sigma or brighter at 20 to 150 arcseconds per hour were recovered 67% to 100% of the time, and slow or faint ones were not found (Section 7.4). On the real NGC 2403 sequence (35 frames), one confirmed mover matched no known asteroid, on a field far from the ecliptic where such a mover is almost certainly not an asteroid. The pipeline's false-track rate on real fields has therefore not been measured, and the real-field counts predate the residual test and the per-night chaining. <!-- impl: astrometricslib.scripts.validate_asteroid_detection -->
5. **A Faster Way to Match Nearby Stars:** Before comparing angular distances between stars, the pipeline now first narrows the search to only the stars that could plausibly be close together on the sky, using a quick bounding-box check. On a dense field like M 81 (150,000 detected sources across 46 frames), this eliminated 99.9% of star pairs that were never going to be close enough to match anyway. It cut the total run time for this step from over 20 minutes down to a few seconds — a 50 to 100 times speedup — while producing exactly the same matches every time as the slower, exhaustive approach.
6. **A Real Bug in Cross-Session Photometry:** As the M 81 target's data grew to 8 observing sessions, testing found a bug: its brightness-tracking step was tracking stars across the *entire* observation history in one pass, using a single reference frame from whichever session happened to come first. A star's exact pixel position is only stable within one observing session, so this corrupted almost all of the affected stars' measurements. 85–94% of frames read exactly zero brightness for a given star, across every brightness range. The resulting noise measurements wrongly flagged the brightest stars far too often: 3 out of 4 of the brightest fifth of stars got flagged as variable, versus fewer than 1 in 10 of the rest. That's backwards — noise problems are supposed to hit faint stars hardest, not bright ones. A single-session comparison target (NGC 2903) showed none of this problem. The fix scopes each brightness-tracking run to one observing session at a time (Section 5.2). Re-tested against the same real M 81 data, incorrect "brightest star" flagging dropped from 75% down to 14%, and the remaining variability correctly shifted to the faintest fifth of stars (46%, closely matching NGC 2903's own baseline of 47%) — the expected pattern once the bug was fixed. The number of distinct stars found also rose, from 1,353 (one shared reference frame for all 8 sessions) to 11,392 (each of the 8 sessions using its own reference frame, counted separately rather than merged — see Finding 7).
7. **Matching the Same Star Across Sessions:** Fixing Finding 6 made each session's measurements correct on their own. But it left every session's stars as unrelated entries, with no way to combine them into one continuous light curve per star. The cross-session star-matching described in Section 5.2 (concept 6) was implemented and tested against the same real 8-session M 81 data. It combined the previous 11,392 separate entries down to 8,422 — 2,970 successful merges of the same star seen in different sessions. Running the exact same match twice in a row produced identical results both times, confirming it's fully repeatable. Testing at this real-world scale (thousands of stars per session) surfaced two problems that smaller test data hadn't. First, identifying stars by catalog name was accidentally also triggering a live lookup to an online star catalog for every single star on every successful run. That added a lot of unnecessary network delay for a step that only actually needed the already-solved sky position, so the lookup was removed once it was no longer needed. Second, an early version of the cross-session matching compared every star to every other star one pair at a time, which doesn't scale: on the real M 81 data, this took over 45 minutes without finishing. It was replaced with a much faster search that solved an equivalent test case in 0.17 seconds. Separately, one of M 81's 8 sessions turned out to reference a different target's stacked image altogether, due to a pre-existing data-labeling mistake in the library unrelated to the matching logic itself. This pipeline correctly excluded just that one session from the results, confirming that one bad session doesn't spoil the other seven (Table 5, step 6).

### 8.4 Future Recommended Target Additions

While the current 8-target validation set covers all five pipelines, the following additional target types are recommended for future testing:

1. **Eclipsing Binary or Exoplanet Transit Target (Photometry):** Observations of a known short-period eclipsing star pair or exoplanet host (e.g., *Algol / $\beta$ Persei*, *RR Lyrae*, or *WASP-12b*) to test the pipeline's ability to detect a periodic dimming pattern and measure its depth (about $\Delta m \approx 0.015\text{ mag}$).
2. **Emission-Line Nebula Target (Spectroscopy):** Grism observations of a planetary nebula (e.g., *Ring Nebula / M 57* or *Dumbbell Nebula / M 27*) to test wavelength calibration against bright emission lines — $[\mathrm{OIII}]$ ($500.7\text{ nm}$) and $\mathrm{H}\alpha$ ($656.3\text{ nm}$) — alongside Vega's absorption-line spectrum.
3. **Asteroid Field Near the Ecliptic (Moving Object Detection):** A field along the ecliptic plane (the sky path the planets and most asteroids follow) containing known, cataloged asteroids, to test matching against an official minor-planet catalog alongside SkyBoT.

## 9. Conclusion

This document presented a single, unified design for amateur astronomy image processing. By organizing every pipeline around one shared Observation Target, the design connects stacking, astrometry, photometry, spectroscopy, and moving object detection into one consistent system.

---

## Acknowledgments

This design builds on several open-source tools and public services rather than reimplementing their work. Siril handles frame stacking and registration. Astropy [6] and its affiliated packages photutils and specutils handle FITS handling, source detection, aperture photometry, and spectral data structures. Astrometry.net [7] provides blind plate solving. The SIMBAD astronomical database [8] and the Gaia catalog [10] provide star identification and star motions. SkyBoT [9] provides solar-system ephemeris cross-matching.

---

## References

[1] <a id="ref-1"></a>M. Perryman, *The Making of History's Greatest Map of the Stars*. Berlin: Springer, 2012.
[2] <a id="ref-2"></a>E. O. Waagen, "The AAVSO International Database," *JAAVSO*, vol. 40, p. 982, 2012.
[3] <a id="ref-3"></a>Vera C. Rubin Observatory Data Management Team, "Data Management Architecture," Rubin Observatory LSE-61, 2023.
[4] <a id="ref-4"></a>W. Chauvenet, *A Manual of Spherical and Practical Astronomy*. Philadelphia, PA: J. B. Lippincott & Co., 1863.
[5] <a id="ref-5"></a>J. R. Taylor, *An Introduction to Error Analysis*. Sausalito, CA: University Science Books, 1997.
[6] <a id="ref-6"></a>Astropy Collaboration et al., "The Astropy Project: Sustaining and Growing a Community-Oriented Open-Source Project and the Latest Major Release (v5.0)," *Astrophys. J.*, vol. 935, no. 2, p. 167, 2022.
[7] <a id="ref-7"></a>D. Lang, D. W. Hogg, K. Mierle, M. Blanton, and S. Roweis, "Astrometry.net: Blind Astrometric Calibration of Arbitrary Astronomical Images," *Astron. J.*, vol. 139, no. 5, pp. 1782–1800, 2010.
[8] <a id="ref-8"></a>M. Wenger et al., "The SIMBAD Astronomical Database," *Astron. Astrophys. Suppl. Ser.*, vol. 143, no. 1, pp. 9–22, 2000.
[9] <a id="ref-9"></a>J. Berthier, F. Vachier, W. Thuillot, et al., "SkyBoT, a New VO Service to Identify Solar System Objects," in *Astronomical Data Analysis Software and Systems XV*, ASP Conf. Ser., vol. 351, p. 367, 2006.
[10] <a id="ref-10"></a>Gaia Collaboration, A. Vallenari et al., "Gaia Data Release 3: Summary of the content and survey properties," *Astron. Astrophys.*, vol. 674, p. A1, 2023.
[11] <a id="ref-11"></a>D. L. Shupe et al., "The SIP Convention for Representing Distortion in FITS Image Headers," in *Astronomical Data Analysis Software and Systems XIV*, ASP Conf. Ser., vol. 347, p. 491, 2005.
[12] <a id="ref-12"></a>S. B. Howell, *Handbook of CCD Astronomy*, 2nd ed. Cambridge: Cambridge University Press, 2006.
[13] <a id="ref-13"></a>J. Eastman, R. Siverd, and B. S. Gaudi, "Achieving Better Than 1 Minute Accuracy in the Heliocentric and Barycentric Julian Dates," *Publ. Astron. Soc. Pac.*, vol. 122, no. 894, pp. 935–946, 2010.
[14] <a id="ref-14"></a>P. B. Stetson, "On the Automatic Determination of Light-Curve Parameters for Cepheid Variables," *Publ. Astron. Soc. Pac.*, vol. 108, p. 851, 1996.

---

## Appendix A. Design Parameters and Validation Status

This appendix lists the tuned values the pipelines use and says which ones have been measured. *Measured* means a test or validation script produced the supporting number, and the source is named. *Designed* means the value is a reasoned choice that nothing has yet verified on real data. Values are defaults and can be changed in the configuration.

**Table A1.** Design parameters and their validation status.

| Pipeline | Parameter | Default | Empirically validated? |
|---|---|---|---|
| Stacking | Floor on the high rejection cutoff | 2.5 $\sigma$ | Designed. Checked only in a Gaussian-noise simulation. |
| Stacking | Margin of the low cutoff above the high one | 0.5 $\sigma$ | Designed. The simulation cannot show the benefit for real, mostly bright, outliers. |
| Stacking | Minimum frames per master calibration frame | 3 | Designed. |
| Stacking | Dark temperature slot width | 3 °C | Designed. A first-pass estimate, not fitted to the camera's dark-current curve. Checked against one real incident. |
| Stacking | Minimum frames kept after quality filtering | 5 | Partly measured. On one real session (M 13), stacks of 5 to 20 frames degraded smoothly, with no failure at 5. The exact value is not calibrated. |
| Stacking | Brightness scale measured on pixels between percentiles | 20th to 80th | Designed. |
| Stacking | Bright-end fraction and nonlinearity tolerance | 1% of pixels; 5% | Designed. |
| Stacking | Star sharpness warning ratio | 1.2 | Measured on six real stacks (none fired). Sensitivity to a real failure is untested. |
| Astrometry | Star detection threshold; sharpness and roundness ranges | 5 $\sigma$; 0.2 to 1.0; $\pm$0.6 | Designed. |
| Astrometry | Minimum stars to attempt a solve; minimum for a local solve | 4; 10 | Designed. The local minimum comes from one incident of repeated time-outs. |
| Astrometry | Pixel-scale window; further widening by the solver | $\pm$5%; 20% | Designed. |
| Astrometry | Catalog match radius | 10 arcsec | Designed. |
| Astrometry | Minimum catalog-matched stars | 20 | Designed. Supported only by the four saved solves with fewer matches having among the five largest residuals. |
| Astrometry | Residual limit | 0.5 of a star width | Designed. Set on catalog separations (0.14 to 0.44 of a star width on 8 saved solves), not on solver fit residuals. |
| Astrometry | Failed catalog lookups allowed | under 50% | Designed. |
| Photometry | Aperture radius; sky ring radii | 4 px; 7 to 12 px | Designed. |
| Photometry | Re-centering box; refinement passes; largest allowed move | 11 px; 3; 1.5 px | Designed. |
| Photometry | Comparison band: brightest skipped; band ends at | 2%; 30% of candidates | Designed. |
| Photometry | Candidates vetted; minimum and cap of comparison stars | 100; 5 and 20 | Designed. |
| Photometry | Vetting consistency limit; share of typical stars for a usable frame | 3 $\sigma$; one half | Designed. |
| Photometry | Frame outlier clip; per-star outlier clip | 3 $\sigma$ (at least 5 frames); 5 $\sigma$ | Designed. |
| Photometry | Noise model: minimum stars; stars per bin; most bins | 20; 10; 10 | Designed. |
| Photometry | Thresholds: excess scatter; chi-square and Stetson J | 1.5; field median + 2.326 robust $\sigma$ | Designed. Checked on synthetic fields only. |
| Photometry | Between-session flag: significance; amplitude | above 3; at least 0.02 mag | Designed. |
| Photometry | Discrimination AUC floor | 0.7 | Designed. |
| Photometry | Largest change the variability flag can see | about 0.3 mag | Designed. |
| Spectroscopy | Reading box half-width; width smoothing | 2.5 times the trail width; 61 steps | Designed. Chosen by judgement. |
| Spectroscopy | Sky strip gap and width | 6 px; 10 px | Measured on one frame (Vega), then used unchanged on a crowded field. |
| Spectroscopy | Sky contamination limit; fewest pixels in a strip | 3 $\sigma$; 4 | Designed. |
| Spectroscopy | Poor-match limit | 0.15 relative RMS | Measured on 14 stars with known types. |
| Spectroscopy | No-type limit | 0.45 relative RMS | Measured on 55 classified stars. |
| Spectroscopy | Ambiguity gap | 0.02 relative RMS | Measured as the smallest RMS distance between neighboring reference spectra. |
| Spectroscopy | Catalog disagreement | 20 subtype steps | Measured on 29 stars with a catalog type. |
| Spectroscopy | Wavelength-fit rejection tolerance | 10 nm RMS | Designed. |
| Moving objects | Frames needed for a track | 3 | Designed. |
| Moving objects | Rate range; chaining radius cap | 1 to 300 arcsec/h; 300 arcsec | Designed. |
| Moving objects | Stationary tolerances | 1.5 px; 3 arcsec | Designed. |
| Moving objects | Residual multiple; displacement multiple | 2; 3 times the position error | Designed. Synthetic fields only. |
| Moving objects | Assumed position error; centroid error | 10 arcsec; 0.5 px | Designed. |
| Moving objects | Ephemeris match radius | 10 arcsec | Designed. Close to a single frame's position error. |
| Moving objects | Detection threshold; detection width | 5 $\sigma$; 4 px | Designed. Recovery measured on synthetic fields only. |

<!-- impl: astrometricslib.utilities.rejection_thresholds.rejection_bounds -->
<!-- impl: astrometricslib.drivers.calibration_library.DEFAULT_DARK_TEMPERATURE_TOLERANCE_C -->
<!-- impl: astrometricslib.utilities.stack_filter_floor.DEFAULT_MINIMUM_SURVIVING_FRAMES -->
<!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.GAIN_MID_RANGE_PERCENTILES -->
<!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.GAIN_BRIGHTEST_FRACTION -->
<!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.DEFAULT_GAIN_DISAGREEMENT_TOLERANCE -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_quality.DEFAULT_FWHM_DEGRADATION_RATIO -->
<!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection.SourceDetector -->
<!-- impl: astrometricslib.drivers.astrometry_net_driver.MINIMUM_SOURCES_FOR_LOCAL_SOLVE -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.CATALOG_MATCH_RADIUS_ARCSEC -->
<!-- impl: astrometricslib.pipelines.astrometry.post_processing.run_gates.MINIMUM_MATCHED_STARS -->
<!-- impl: astrometricslib.pipelines.astrometry.post_processing.run_gates.MAXIMUM_RESIDUAL_FRACTION_OF_FWHM -->
<!-- impl: astrometricslib.pipelines.astrometry.post_processing.run_gates.MAXIMUM_FAILED_LOOKUP_FRACTION -->
<!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.measure_aperture_photometry -->
<!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.CENTROID_BOX_HALF_WIDTH_PX -->
<!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.MAX_CENTROID_SHIFT_PX -->
<!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.CENTROID_REFINEMENT_PASSES -->
<!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.BRIGHTEST_SKIPPED_FRACTION -->
<!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.FAINTEST_BAND_FRACTION -->
<!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.MAXIMUM_VETTING_CANDIDATES -->
<!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.MINIMUM_COMPARISON_STARS -->
<!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.DEFAULT_MAXIMUM_COMPARISON_STARS -->
<!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.EXCESS_SCATTER_SIGMA -->
<!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.MINIMUM_USABLE_FRAME_FRACTION -->
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.VariabilityAnalyzer._reject_outlier_frames -->
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.MINIMUM_STARS_FOR_NOISE_MODEL -->
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.MINIMUM_STARS_PER_NOISE_BIN -->
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.MAXIMUM_NOISE_BINS -->
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.EXCESS_SCATTER_THRESHOLD -->
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.CALIBRATION_SIGMAS -->
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.MINIMUM_LONG_TERM_AMPLITUDE_MAG -->
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.DEFAULT_LONG_TERM_SIGNIFICANCE_THRESHOLD -->
<!-- impl: astrometricslib.pipelines.photometry.post_processing.variability_skill.MINIMUM_DISCRIMINATION_AUC -->
<!-- impl: astrometricslib.pipelines.photometry.post_processing.run_gates.MAXIMUM_DETECTABLE_AMPLITUDE_MAG -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.APERTURE_SIGMA_MULTIPLIER -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.APERTURE_SIGMA_SMOOTHING_STEPS -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.SKY_BAND_GAP_PX -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.SKY_BAND_WIDTH_PX -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.SKY_CONTAMINATION_SIGMA -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.SKY_BAND_MINIMUM_SAMPLE_COUNT -->
<!-- impl: astrometricslib.models.stellar_source.NO_GOOD_MATCH_RMS -->
<!-- impl: astrometricslib.models.stellar_source.UNRELIABLE_MATCH_RMS -->
<!-- impl: astrometricslib.models.stellar_source.AMBIGUOUS_RMS_GAP -->
<!-- impl: astrometricslib.models.stellar_source.DIFFERS_FROM_CATALOG_SUBTYPES -->
<!-- impl: astrometricslib.models.moving_object_config.MovingObjectConfig -->
