# Astrometrics Library: Architecture and Design

*Version 2.11 · 2026-10-15 · Status: current*

## Abstract

This document explains how the Astrometrics image-processing system works and why it's built the way it is. It covers five pipelines: stacking, astrometry, photometry, spectroscopy, and moving object detection. Every pipeline organizes its data around one shared idea, called the Observation Target. Each pipeline also checks its own work twice: once before it starts, to catch bad input, and once after it finishes, to catch bad output. For a map from these ideas to the actual code, see [Astrometrics_Library_Implementation.md](./Astrometrics_Library_Implementation.md).

Terms are defined in the [glossary](#appendix-b-glossary) (Appendix B). Each term is also explained in a short phrase where it first appears.

## 1. Introduction

Five separate image-processing pipelines work on the same telescope data. Without a shared way to describe a target, each pipeline would have to redo the same work — figuring out the sky position, listing the frames, tracking data quality — and none of them could reuse another pipeline's results, like a solved sky position or a finished stack. One shared data model lets the pipelines work together on the same target without stepping on each other's data.

The ideas in this document apply the same way whether a pipeline runs from a script or through the interactive graphical tools, because both paths call the exact same underlying code. Anything you can do by clicking through the interface, you can also automate as a script.

A complete, code-level reference for every public class and method is generated automatically from the source code's own documentation. See the Sphinx {doc}`API Reference </api/astrometricslib>`. For a map connecting these ideas to the actual Python files that implement them, see [Astrometrics_Library_Implementation.md](./Astrometrics_Library_Implementation.md).

The rest of this document is organized as follows. Section 2 introduces the four data models every pipeline shares, and two shared records: camera profiles and provenance. Sections 3 through 7 walk through the five pipelines in turn: Stacking, Astrometry, Photometry, Spectroscopy, and Moving Object Detection. Section 8 says how the pipelines were validated and where the evidence is kept. Section 9 concludes. Appendix A lists the tuned values the pipelines use and says which of them have been measured. Appendix B is a glossary of the astronomy and statistics terms used here.

## 2. Observational Data Models

This system organizes everything it knows about the sky into four categories of information. Big professional sky-survey projects organize their data the same way, built around one target at a time [3]. These four categories cover everything the pipelines need to track: sky positions, brightness measurements, light curves, spectra, and quality checks — all connected to a single target.

Every image referenced below is stored as a FITS file, the standard file format for astronomical data. It plays the same role for telescopes that JPEG or PNG plays for ordinary photos. A FITS file bundles the raw pixel data together with a header: a block of metadata recording things like exposure time, filter, and (once solved) sky position.

### 2.1 Information Types

1. **Observation Target:** A specific region of sky, such as M 81 or Vega. M 81 is object 81 in the Messier catalogue, an 18th-century list of bright galaxies, star clusters and nebulae; NGC and IC numbers (NGC 2403, IC 1396) come from two larger catalogues named the same way. A target connects every raw image, calibration frame (an image taken to measure the camera's own errors, Section 3.2), solved sky position, stacked image, detected star, and moving object found for that target.
2. **Stellar Object:** A single star identified in the sky. It stores the star's position, its brightness over time (a light curve), how much its brightness varies, and its extracted spectrum.
3. **Moving Object Candidate:** A possible solar-system object, such as an asteroid or comet. It records the object's path across images, how fast it appears to move, how well that motion fits a straight line, and any match found in an asteroid catalog.
4. **Pipeline Quality Record:** A health report for one processing run — for example, how many images were thrown out, how sharp the stars look, how accurate the positioning was, and how much measurement noise remains.

Table 1 summarizes the four data models: what each one covers, what data it stores, and what job it does. Its symbols mean the following:

* $\alpha$ and $\delta$ are Right Ascension and Declination, the sky's version of longitude and latitude.
* A *light curve* $\hat{F}(t)$ is a star's brightness over time, and a *1D spectrum* $F(\lambda)$ is its brightness at each wavelength $\lambda$.
* An *ephemeris* is a table of where a solar-system object is predicted to be at each time.
* *FWHM* (full width at half maximum) is the width of a star's image at half its peak brightness. Smaller means sharper.
* A *gate* is one recorded quality check: passed, failed, or not checked.
* *Calibration masters* are averages of many calibration frames (Section 3.2).
* $\sigma_{\text{low}}(N)$ and $\sigma_{\text{high}}(N)$ are the pixel-rejection cutoffs for a stack of $N$ frames (Section 3.2, Equation (3)).
* The *FWHM ratio* is a stack's star width divided by the width its input frames predict; above 1 means the stack is blurrier than expected (Section 3.4.2).

**Table 1.** Observational data models summary.

| Observational Data Model | Scope | Data Attributes | Primary Role |
| :--- | :--- | :--- | :--- |
| **Observation Target** | One field of view / sky area | Coordinates $(\alpha_0, \delta_0)$, raw exposures, calibration masters, stacked FITS images | Connects all images and results for one part of the sky |
| **Stellar Object** | Individual stars & catalogs | Coordinates $(\alpha, \delta)$, magnitude $V$, light curves $\hat{F}(t)$, 1D spectra $F(\lambda)$ | Tracks one star's brightness history, spectrum, and variability |
| **Moving Object Candidate** | Solar-system objects passing through | Detection chain $(x_i, y_i, t_i)$, motion rates $(\dot{\alpha}, \dot{\delta})$, ephemeris match | Records an asteroid's motion path and any catalog match |
| **Pipeline Quality Record** | Health of each pipeline run | Frame survivor counts $N$, rejection cutoffs $\sigma_{\text{low}}(N), \sigma_{\text{high}}(N)$, FWHM ratio, gate results (passed, failed, or not checked) | Tracks whether a pipeline run succeeded, and how well |

:::{note}
**Theory.** A star's magnitude ($V$) is its brightness on a scale that runs backwards and by powers of ten: lower numbers mean brighter stars, and each step of 5 magnitudes means a 100x change in brightness. The letter $V$ names the photometric band: the range of wavelengths a standard filter passes. $V$ (visual) is a green-yellow band near 550 nm. This scale goes back to how ancient astronomers ranked stars just by looking at them, and it stuck. It isn't used directly by any equation in this document — it shows up in Table 1 because it's the standard unit for how bright a star's catalog entry says it is.
:::

### 2.2 Data Model Progression Across Pipelines

Table 2 shows how each data model gets filled in as data moves through the five pipelines, from raw images at the top down to final measurements at the bottom.

**Table 2.** Overview matrix of observational data models across pipeline execution levels.

| Pipeline | Observation Target | Stellar Object | Moving Object Candidate | Quality Record |
| :--- | :--- | :--- | :--- | :--- |
| **Stacking** | Raw photos and calibration frames | N/A | The stack whose stars fix each raw frame's sky position (Section 7.1) | Removed bad pixels and overall image sharpness |
| **Astrometry** | Matched sky coordinates and target center | Star locations linked to catalog positions | Sky positions for moving candidates | Solve success status, fit residual, and catalog match counts |
| **Photometry** | Chosen comparison stars | Star brightness changes and light curves | N/A (the rotation-period tool of Section 7.2 could use a track's brightness, but no pipeline step calls it) | Ensemble outlier rejection and comparison-star composition |
| **Spectroscopy** | Zero-order star position (Section 6.1) and camera sensitivity | Calibrated spectra and spectral types | N/A | Zero-order saturation, wavelength-scale, and classification checks |
| **Moving Objects** | The candidates that passed the self-consistency filters (Section 7.2) | N/A (chains that turn out to be stationary stars are discarded) | Path direction, speed, and catalog names | Detection counts and straight-line residuals against position error |

### 2.3 Shared Records: Camera Profiles and Provenance

Two more records sit beside the four data models and serve every pipeline.

* **Camera profile.** Facts about one camera model's sensor, such as the pixel value at which it clips, its gain in e-/ADU (how many electrons make one step of the stored pixel value) and read noise (random noise added each time the sensor is read out), and how sensitive it is to each colour (its quantum efficiency curve: the share of arriving light the sensor records at each wavelength). Keeping them in one place means no pipeline guesses a camera's properties from its name. Every number carries a note saying where it came from: a datasheet, a measurement on the observatory's own frames, or a plain assumption. A camera with no profile gets a generic stand-in instead of an error, and every pipeline's Quality Record names the profile it used and flags the stand-in. <!-- impl: astrometricslib.drivers.camera_profile_store.resolve_camera_profile --> <!-- impl: astrometricslib.pipelines.shared.applied_camera_profile.record_camera_profile -->
* **Provenance.** For each data product, a record of which processing run made it, with which software version, settings, and inputs. It follows the IVOA provenance data model (PROV-DM, the standard the International Virtual Observatory Alliance uses for data lineage, that is, the record of where a data product came from). Every pipeline records its run through one shared step after its Quality Record is built. Recording fails softly: a storage problem is logged and never turns a successful run into a failure. A target's lineage can be exported as PROV-XML. <!-- impl: astrometricslib.pipelines.shared.provenance_recording.record_pipeline_run --> <!-- impl: astrometricslib.drivers.provenance_store.ProvenanceStore --> <!-- impl: astrometricslib.drivers.provenance_store.export_target_lineage_as_prov_xml -->

---

## 3. Image Stacking Pipeline

The stacking pipeline combines many individual exposures into one high-quality image. Combining frames boosts the real signal from stars while averaging out noise and one-off artifacts like satellite trails and cosmic ray hits.

### 3.1 Purpose & Interfaces

* **Inputs:** Raw light exposures, plus three master calibration frames ($M_{\text{bias}}, M_{\text{dark}}, M_{\text{flat}}$, explained below).
* **Outputs:** A calibrated, stacked 2D image (broadband, meaning an ordinary picture through a colour filter, or spectral, meaning each star is spread into a spectrum), an updated FITS header, and a Stack Quality Record.

### 3.2 Major Concepts and Governing Equations

Every raw exposure contains two kinds of unwanted signal: an *additive* error from the camera's electronics and sensor heat, and a *multiplicative* error from the optics, where parts of the image come out dimmer for reasons unrelated to the sky. Before frames are aligned and combined, each raw exposure $I_{\text{raw}}$ is corrected using three calibration images:

* **Master Bias ($M_{\text{bias}}$):** An average of many closed-shutter exposures as short as the camera allows (ideally zero length). It isolates the fixed voltage offset the camera's electronics add to every pixel, regardless of exposure time.
* **Master Dark ($M_{\text{dark}}$):** An average of closed-shutter exposures taken with the same exposure time, gain, and sensor temperature as the light frames. It measures the extra signal ("dark current") produced by heat inside the sensor over time, plus any permanently defective ("hot") pixels.
* **Master Flat ($M_{\text{flat}}$):** An average of exposures of an evenly lit surface (the twilight sky or a flat light panel). It measures how much dimmer some parts of the image are due to the optics — vignetting, dust specks on the sensor glass, and pixel-to-pixel sensitivity differences.

These two categories of error are corrected differently:

* **Additive errors (subtracted):** extra pixel counts added regardless of how much starlight actually reached the sensor.
  * *Electronic bias:* a constant level the camera adds to every pixel. Its size is set by the camera's *offset* setting. It is there so that noise that dips below zero is still recorded instead of being cut off at zero.
  * *Thermal dark current:* extra electrons that build up from heat inside the sensor, growing roughly in proportion to exposure time.
* **Multiplicative errors (divided out):** factors that block or scale down a percentage of the real incoming starlight.
  * *Vignetting and dust shadows:* obstructions in the optical path (lens edges, dust on the sensor glass) that dim parts of the image.
  * *Pixel sensitivity differences:* manufacturing differences in each pixel's quantum efficiency (the share of arriving light it records).

Combining these corrections gives the true, calibrated brightness $I_{\text{calibrated}}$. First subtract the additive errors. Then divide out the multiplicative ones. The flat, with its own bias removed, is divided by its average value $\langle \cdot \rangle$, so it is 1 on average. A pixel the optics dim to 80% has a normalized flat value of 0.8, and dividing by 0.8 restores its full brightness:

$$
I_{\text{calibrated}} = \frac{I_{\text{raw}} - M_{\text{dark}}}{\left(M_{\text{flat}} - M_{\text{bias}}\right) / \langle M_{\text{flat}} - M_{\text{bias}} \rangle} \tag{1}
$$

The numerator subtracts only the master dark, not the master bias. A dark frame is a raw closed-shutter exposure, so it already holds the bias as well as the dark current. Subtracting a bias master too would remove the bias twice and push the faint sky below zero. The bias master is subtracted from the light frames only when no dark master exists. <!-- impl: astrometricslib.drivers.siril_interface.light_calibration_flags -->

The master frames are only as good as the calibration frames chosen for them. Four rules decide which frames are used and how much to trust them:

* **Match the camera settings.** Calibration frames are filed by the settings they were taken at. A frame taken at one gain setting (the camera's amplifier setting, a plain number recorded in the file header) or offset cannot calibrate lights taken at another. Binning merges neighboring pixels, so frames binned differently have different pixels. A binning mismatch therefore blocks that kind of calibration frame instead of applying a wrong one, and the stack is flagged. A mismatch in gain, offset, or temperature applies the nearest frames and flags the stack with a sentence saying which setting differed. <!-- impl: astrometricslib.drivers.calibration_library.calibration_setting_key --> <!-- impl: astrometricslib.drivers.calibration_library.CalibrationLibrary.select_dark_frames -->
* **Match darks by temperature.** Dark current roughly doubles every few degrees. Darks are therefore sorted into temperature slots a few degrees wide, and the slot nearest the lights is used. Darks from different slots are never averaged into one master. <!-- impl: astrometricslib.drivers.calibration_library.temperature_slot_c -->
* **Remove the bias from every flat.** A flat frame carries the bias offset on top of the light. If the offset stays in, the flat looks too uniform, the vignetting looks weaker than it is, and the lights come out under-corrected. The bias master is subtracted from every flat, including a master built from a single flat. A stack of one light frame is calibrated like any other. <!-- impl: astrometricslib.drivers.siril_interface.build_flat_master_commands --> <!-- impl: astrometricslib.drivers.siril_interface.build_single_light_commands -->
* **Flag thin masters.** Averaging and outlier rejection need several frames. A master built from one or two frames keeps their noise and any cosmic ray hit. Such a master is still built, because the library can legitimately hold one flat or one dark, but the stack is flagged so a reader knows the master is weak. The minimum is a design choice (Appendix A). <!-- impl: astrometricslib.drivers.siril_interface.calibration_count_flags -->

After calibration, the pipeline averages $N$ exposures together to raise the signal-to-noise ratio (SNR: the signal divided by its random noise; higher is cleaner). Averaging $N$ frames lowers the random noise by about $\sqrt{N}$.

Averaging has one weakness: a single very bright value, such as a satellite trail, pulls the mean far off. So, at each pixel position, any value far enough from the group's median is thrown out as an outlier (a satellite trail, a cosmic ray hit, a plane). This is called pixel rejection, or sigma clipping when the distance is measured in standard deviations ($\sigma$).

Rather than using one fixed cutoff for every stack — always rejecting anything past $3\sigma$, say — the pipeline adjusts the cutoff based on how many frames $N$ are being combined. It starts from Chauvenet's criterion [4], [5]. The criterion asks: how far out must a value be before fewer than half of one such value is expected by chance among $N$ normal-noise samples? A value past that point is more likely a real outlier than bad luck:

$$
\sigma(N) = \sqrt{2}\,\mathrm{erfc}^{-1}\!\left(\frac{1}{2N}\right) \tag{2}
$$

In words:
* **$\sigma(N)$ (rejection cutoff):** how many standard deviations from the group's median a pixel has to be before it's thrown out (e.g. $1.64\sigma$ at $N = 5$, $2.81\sigma$ at $N = 100$).
* **$N$:** how many light exposures are being combined.
* **$\mathrm{erfc}^{-1}$ (inverse complementary error function):** finds the cutoff at which fewer than 0.5 false-positive pixel rejections are expected across all $N$ frames.

As $N$ grows, so does the chance that some value looks like an outlier by pure chance, so Equation (2) loosens the cutoff accordingly. A fixed $3\sigma$ cutoff instead throws out the same share of good values (about 0.27%) at every $N$, so the number of good values it loses grows with the stack. Equation (2) is stricter than $3\sigma$ below about 185 frames and looser above.

Equation (2) treats the spread of the values as known exactly. A real stacker estimates the spread from the same $N$ values it is judging, and with a handful of frames that estimate is noisy. Used as it stands, Equation (2) throws out good values from small stacks. The pipeline therefore applies two changes:

* **A floor on the high cutoff.** The upper cutoff never drops below a minimum, $\sigma_{\text{floor}}$.
* **A looser low cutoff.** The lower cutoff sits a fixed margin $\Delta_\sigma$ above the upper one. Satellite trails, cosmic ray hits, and hot pixels are brighter than the true value, so the upper cutoff has to catch them. Values far below the median are rare, so a looser lower cutoff removes fewer good values at little cost.

$$
\sigma_{\text{high}}(N) = \max\!\big(\sigma(N),\ \sigma_{\text{floor}}\big), \qquad \sigma_{\text{low}}(N) = \sigma_{\text{high}}(N) + \Delta_\sigma \tag{3}
$$

<!-- impl: astrometricslib.utilities.rejection_thresholds.rejection_bounds --> <!-- impl: astrometricslib.utilities.rejection_thresholds.chauvenet_sigma -->

With the default floor of $2.5\sigma$ (Appendix A), Equation (3) differs from Equation (2) for stacks of fewer than about 40 frames. Above that, the Chauvenet value is already larger than the floor.

:::{warning}
**Checked in simulation, not on real small stacks.** The floor and the margin $\Delta_\sigma$ trade one risk for another. They throw out fewer good values from a small stack, and in exchange a weak outlier survives more often. The size of that trade has been checked only in a simulation of Gaussian noise (partly measured; see [STK-08](validation/stacking.md#stk-08)). <!-- impl: astrometricslib.scripts.rejection_small_n_check -->
:::

Frames taken with different exposure lengths cannot be clipped together, because a long and a short frame hold very different amounts of light in the same pixel, and clipping would discard the best frames' values as outliers. Each exposure length is therefore stacked on its own, and the group stacks are combined afterward. Each group stack is first converted to counts per second. That alone does not make the groups comparable: before clipping, the stacker rescales every frame to a common level (its normalization), so each group stack comes out on its own brightness scale. The groups are therefore put on one brightness scale. Finally they are combined with inverse-variance weights (weights of one over the noise variance), so quieter groups count for more. <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.combine_exposure_group_images -->

The reference group is the one that carries the most weight. The brightness scale of every other group is the median ratio of its stars' fluxes to the same stars' fluxes in the reference group. Only isolated, unsaturated stars well above the noise are used, each measured in a circle wide enough that a star a pixel wider in one group keeps the same share of its light. Stars are used rather than pixels because faint pixels are mostly sky, and the sky changes between nights while the stars do not (measured; see [STK-19](validation/stacking.md#stk-19)). <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.measure_group_gains_with_stars --> <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.measure_star_flux_ratios -->

A group that shares too few such stars with the reference, such as a slitless spectrum with one star, falls back to pixels: the median ratio of its pixels to the reference's, over mid-range source pixels only. The reason for the mid-range is linearity. A sensor is linear (its reading is proportional to the light) between the sky floor and near full well (the most charge a pixel can hold). In that range two exposures of one scene differ by a single factor, and that factor is the scale. Close to full well the response flattens. At the faint end, a group whose raw frames were clipped at zero (a camera offset too low to record the negative side of the noise; Section 3.4.2, check 9) reads high. At those extremes the ratio measures the sensor's nonlinearity, not the scale. <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.measure_group_gains -->

As a check, the same ratio is taken over the faintest quarter and the brightest quarter of the stars (on the pixel fallback, over the brightest pixels) and never applied. A group whose check ratio sits further from its scale than a tolerance is not one scale factor of the reference. It is marked nonlinear and left out of the combined image (partly measured; see [STK-20](validation/stacking.md#stk-20)). A group whose sky is much brighter than the reference's is also left out, because a flooded sky means a different night whose frames break the assumptions of the scale (partly measured; see [STK-21](validation/stacking.md#stk-21)).

A second, separate weighting acts on single frames inside each stack. For sky-limited frames, a frame's squared signal-to-noise ratio grows with its exposure, so equal weights waste the longer frames. The noise penalty of equal weights follows from the exposure times $t$ of all the frames of an imaging stack, $P = \sqrt{\overline{1/t}\;\overline{t}}$ (the bars are means over the frames), which is 1 when every frame has the same length. When $P$ passes a set factor (Appendix A), the stacker weights each frame by its own measured noise. $P$ is computed over the whole stack, before it is split into exposure groups, so it exceeds 1 whenever the stack holds more than one exposure length. Spectral stacks are left to the group handling above. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.exposure_weighting.choose_stack_weight --> <!-- impl: astrometricslib.pipelines.stacking.pre_processing.exposure_weighting.equal_weight_noise_penalty -->

### 3.3 Pipeline Theory of Operations

Combining frames means three things happen together: calibrating each frame, aligning them to each other, and rejecting outlier pixels. Four design choices drive this process:

1. **Calibrate before aligning:** Each raw light frame is corrected using Equation (1) before it's aligned with the others: dark subtraction removes the electronic offset and the heat buildup together (a separate bias subtraction is used only when there is no dark), and flat-field division removes optical dimming and dust shadows.
2. **Adjust the rejection cutoff automatically:** The pixel-rejection cutoffs scale with the number of frames $N$, via Equations (2) and (3), so nobody has to manually tune them per dataset. This keeps small stacks from losing good values and stops deep stacks from over-clipping real starlight.
3. **One quality check, two alignment modes:** The same frame-quality check applies to both ordinary (broadband) images and spectroscopic images. But spectroscopic images are only allowed to shift position (not rotate or stretch) when aligning (registration: lining frames up so each star falls on the same pixel), so the axis the spectrum is spread out along doesn't get distorted relative to the reference star. Ordinary images use full alignment (shifting, rotating, and stretching as needed) to make stars as sharp as possible.
4. **Keep different optical setups separate:** Frames taken with different focal lengths or cameras get stacked separately, never mixed together. Mixing them would break the assumption that one pixel always corresponds to the same patch of sky, and would corrupt sharpness measurements.

Before rejecting outliers, the pipeline also checks that enough frames survived the earlier quality filtering. Too few frames left would make the outlier statistics meaningless. Table 3 lays out the stacking pipeline step by step.

**Table 3.** Stacking pipeline execution sequence and operations.

| Step | Pipeline Phase | Inputs & Outputs | Description |
|---|---|---|---|
| 1 | Frame Screening | **In:** Raw light frames and their headers<br>**Out:** Screened light frame list | Keep only the most common gain setting, move frames with clouds or trailed stars aside, and keep one side of a sudden sky-brightness jump (Section 3.4.1). |
| 2 | Calibration | **In:** Screened exposures, master bias, dark, flat<br>**Out:** Calibrated light frame array | Subtract the master dark, which holds both the electronic bias and the thermal dark current (the bias master alone when there is no dark); divide out flat-field dimming via Eq. (1). Match calibration frames to the lights by camera settings and dark temperature. |
| 3 | Quality Safeguard | **In:** Frame quality metrics, minimum-frame floor<br>**Out:** Filtered light frame list | Keep the sharpest and roundest share of the frames, but never drop below a minimum survivor count (Appendix A). |
| 4 | Alignment & Outlier Rejection | **In:** Filtered light frames<br>**Out:** Aligned arrays & outlier map | Align exposures to each other and reject outlier pixels using the adjustable, floored cutoffs of Eq. (2) and Eq. (3). |
| 5 | Combine into Master Stack | **In:** Surviving pixel arrays<br>**Out:** Stacked 2D FITS image & Quality Record | Average the surviving pixels into one stacked image. When exposure lengths differ, stack each length separately, put the groups on one brightness scale, and combine them with inverse-variance weights. Record how the run went. |

<!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_homogeneity.find_dominant_gain_subset -->

A stack is requested in one place. The request first chooses the light frames: imaging and spectral frames are never mixed, and frames from two cameras are never stacked together. The caller may narrow the choice by filter, file name, time, or camera. The stacking step then runs as a tracked job (a background task whose progress, log and outcome are recorded so it can be watched and reviewed) and holds a stacking slot (a lock that only a set number of stacks may hold at once) while the engine works, so two heavy stacks do not compete for the same machine. <!-- impl: astrometricslib.pipelines.stacking.stack_request.choose_frames_to_stack --> <!-- impl: astrometricslib.pipelines.stacking.stack_request.run_stack --> <!-- impl: astrometricslib.pipelines.stacking.stage.stack_frames -->

The engine (Siril, an open-source stacking program) is reached through a driver interface. The engine needs two of the pipeline's own steps in the middle of a run: the flat check and the fallback alignment for spectral frames. The driver may not depend on pipeline code (the layering rule in `Astrometrics_Library_Implementation.md`), so the pipeline hands these steps to the engine on every run. When too few spectral frames register by star matching, the stack is retried with a relaxed star detection and then with phase correlation (finding the shift between two images by comparing their overall patterns, with no star list needed). The attempt that registered the most frames is kept. <!-- impl: astrometricslib.pipelines.stacking.stack_runner.run_stack -->

The many-target batch run reaches the same stacking step through a wrapper that adds a hard time limit, scaled by the number of frames. <!-- impl: astrometricslib.pipelines.tasks.stack_frames_with_timeout -->

Three supporting tasks sit around the stack itself:

* **Calibration ingest.** A rescan of the calibration library reports which groups of bias, dark, and flat frames were added, changed, or removed, and measures each new set of flats with the same check the stacker runs. <!-- impl: astrometricslib.pipelines.stacking.calibration_ingest.build_ingest_report -->
* **Quarantine and restore.** Frames moved aside for clouds or trailed stars are moved, never deleted. A record next to them says where each came from and why, and a restore step moves them back. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.restore_quarantined_frames -->
* **Preview pictures.** A stacked FITS file holds linear data that looks almost black on screen. A preview picture is made next to each stack: optional gradient removal, colour balancing from the stack's own stars, a stretch (a brightness curve that lifts faint detail) whose sky level depends on the target, and optional noise removal. When StarNet (a program that separates stars from the rest of a picture) is configured, the stars and the rest of the picture are stretched separately and blended back, so a strong stretch of a faint nebula does not bloat the stars. A preview can be remade from the stack without restacking. <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_preview.write_stack_preview --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.starnet_stretch.blend_stars --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.preview_remake.remake_stack_preview -->

### 3.4 Quality Metrics and Integration Planning

The stacking pipeline checks its own work in two rounds: before stacking (screening out bad raw photos) and after stacking (checking the final image). Each check is recorded as passed, failed, or not checked, and a check that could not look is never recorded as passed.

#### 3.4.1 Before Stacking: Screening Raw Photos
* **Clouds and Trailed Stars:** A session here means frames with the same camera, filter, and exposure, with no gap of four hours or more between them. Each frame is judged against the other frames of its own session. A frame with fewer than half the session's median star count is moved aside, and it is called clouded when its sky is also brighter than usual. Roundness here is a star's narrow width divided by its wide width (0 to 1, 1 for a circle). A frame whose median roundness is below 0.80 and at least 0.15 below the session's median is moved aside as trailed (its stars are smeared into short lines). A session with too few frames is not judged. If more than a set share of a session would be moved, nothing is moved and the check fails, because the session itself, not a few frames, is then the problem. The limits are listed in Appendix A. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.judge_batch --> <!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.quarantine_gate -->
* **Gain Homogeneity:** Frames taken at a minority gain setting are set aside. Setting them aside corrects the stack, so the check does not fail. It is recorded as not checked when no frame records its gain. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_homogeneity.gain_homogeneity_gate -->
* **Background Homogeneity:** For each exposure length, the frames' sky brightness levels are sorted and the largest gap between neighbors is compared with the normal spread on either side. A gap much larger than that spread means the session mixes two sky conditions, such as clouds rolling in. Slow changes, such as a rising moon, leave no such gap. The minority side is left out and the check fails, so the split is visible. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.background_homogeneity.detect_background_split --> <!-- impl: astrometricslib.pipelines.stacking.pre_processing.background_homogeneity.background_homogeneity_gate -->
* **Sharpness and Roundness Filter:** The stacker keeps a requested share of the sharpest and roundest frames. This is a relative cut, measured in pixels, so no conversion to arcseconds (1/3600 of a degree) is needed. On a small batch the share is loosened step by step, down to no filter, so that a minimum number of frames survives. <!-- impl: astrometricslib.utilities.stack_filter_floor.resolve_filter_wfwhm_with_floor -->
* **Flat Level and Flat Noise:** The flat frames must be neither too faint nor too bright, and the master flat's own noise must stay below a limit, since dividing by the flat copies its noise into every light frame as a fixed pattern. A noisy master flat from a monochrome (single-colour) sensor is lightly blurred while it is built, enough to bring its noise to the limit. A colour sensor's flat is measured but not blurred, because blurring would mix its red, green, and blue pixels. Both checks are recorded as not checked when the stack used no flats. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.flat_calibration.assess_flats -->
* **Calibration Frame Checks:** Confirms that the calibration frames match the lights' camera settings (gain, offset, binning, and, for darks, temperature) and that each master was built from enough frames. A mismatch or a thin master flags the stack rather than passing silently. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.assess_input_quality.calibration_gates -->

The pipeline does not cross-check image quality against the telescope's reported state (airmass, meaning how much atmosphere the light crossed, 1.0 straight overhead; mount position; focuser position; or sensor temperature). That remains a design goal.

#### 3.4.2 After Stacking: Checking the Final Image
Once stacking finishes, six checks are made on the stacked image, and three more on stacks built from several exposure lengths: <!-- impl: astrometricslib.pipelines.stacking.post_processing.assess_output_quality.output_quality_gates -->

1. **Rejected Pixel Fraction:** The share of pixels the outlier rejection threw out. The flag is set above the share that normal sessions reach, to leave headroom (Appendix A; partly measured, see [STK-24](validation/stacking.md#stk-24)). A much larger share means many frames disagreed with each other, for example through clouds or poor alignment.
2. **Star Sharpness Ratio ($R_{\text{FWHM}}$):** Compares how sharp stars look in the final stacked image ($\text{FWHM}_{\text{stack}}$) to how sharp the input photos predict a stack to be ($\text{FWHM}_{\text{expected}}$). If frames are misaligned, the stack's stars come out wider than its frames' stars, and the ratio rises above 1.

   The expected width needs care. A stack's star is the average of the stars in its frames. For round Gaussian stars, that average has a variance equal to the mean of the individual variances, so the expected width is the root mean square of the input widths. A median would call the normal blur of a stack made on nights of different seeing (the blurring of stars by air turbulence) a failure.

$$
R_{\text{FWHM}} = \frac{\text{FWHM}_{\text{stack}}}{\text{FWHM}_{\text{expected}}}, \qquad \text{FWHM}_{\text{expected}} = \sqrt{\frac{1}{N}\sum_{i=1}^{N}\text{FWHM}_i^{\,2}} \tag{4}
$$

   * *What it means:* A value near $1.0$ means the frames lined up almost perfectly. A value above $1.2$ fails the check: it is a warning sign that small misalignments between frames blurred the final image. A value of exactly $1.2$ passes. The limit is partly measured: it does not fire on good stacks, but whether it catches a genuine registration failure is untested (see [STK-25](validation/stacking.md#stk-25)). The check does not apply to a spectral stack, whose stars are trails. <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_quality.expected_stack_fwhm -->
3. **Spectral Registration:** For a spectral stack (a stack of frames in which each star is spread into a spectrum; Section 6), the zero-order star image (the undispersed point image of the star) is followed from frame to frame. A frame where it jumps or fades is flagged, since such a frame may have been aligned wrongly. <!-- impl: astrometricslib.pipelines.spectroscopy.utilities.registration_quality.evaluate_spectral_registration_quality -->
4. **Saturated Pixel Fraction:** The share of pixels at the camera's saturation level.
5. **Zero Pixel Fraction:** The share of pixels that read exactly zero. A stack that is mostly zero had more subtracted than the sky it held, for example a bias removed twice. Clean spectral stacks have almost no zero pixels, so they get a much stricter limit than imaging stacks. <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_quality.zero_fraction_threshold -->
6. **Negative Pixels:** Fails when the stacker reports that most of a frame went negative after calibration, which points to over-subtraction by a wrong dark or bias frame.

For a stack built from several exposure lengths:

7. **Exposure Group Linearity:** Each group's brightness must be one scale factor of the reference group's (Section 3.2). A group that fails is left out of the combined image. <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.measure_exposure_group_gains --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.exposure_group_report.exposure_group_linearity_gate -->
8. **Exposure Groups Combined:** Fails when a group that had frames is missing from the combined stack, because it failed to stack, to line up, or the linearity check. Without this check, a full-looking stack could hold only part of the data. <!-- impl: astrometricslib.pipelines.stacking.post_processing.exposure_group_report.exposure_groups_combined_gate -->
9. **Raw Frame Clipping:** A frame taken at a camera offset (Section 3.2) below its read noise cannot record the negative side of the noise, so some pixels read exactly zero and the faint sky reads too high. A few raw frames of each group are sampled for this. <!-- impl: astrometricslib.pipelines.stacking.post_processing.exposure_group_report.raw_frame_clipping_gate -->

The time the stack took and whether it hit its time limit are recorded with the stack, but they are not judged. The pipeline does not measure the noise reduction against the $\sqrt{N}$ expectation, and it does not measure mount tracking or use the pier side (which side of the mount's central pier the telescope sits on) to separate a meridian flip (the mount turning the telescope over as the target crosses due south) from a tracking error. Those remain design goals.

---

## 4. Astrometric Calibration Pipeline

The astrometry pipeline figures out exactly where a stacked image is pointed. It matches camera pixels to real sky coordinates: Right Ascension $\alpha$ and Declination $\delta$, the sky's version of longitude and latitude. This is often called "plate solving," a term left over from the days of photographic plates. Positions on the sky are angles, measured in degrees or in arcseconds (1/3600 of a degree). The result is a World Coordinate System (WCS): a mathematical formula that converts any pixel position in the image into a real sky position, and back.

### 4.1 Purpose & Interfaces

* **Inputs:** A calibrated, stacked image, plus a rough estimate of the field's coordinates and pixel scale (plate scale: how many arcseconds of sky one pixel covers).
* **Outputs:** A WCS transformation saved into the image's FITS header, a list of catalog-identified stars, and an Astrometry Quality Record.

### 4.2 Major Concepts and Governing Equations

Plate solving connects pixel coordinates $(x,y)$ to sky coordinates $(\alpha, \delta)$ in six steps:

1. **Star Detection:** Finds star-like points of light with a matched-filter star finder (the DAOFIND method, through the photutils package). A matched filter slides a star-shaped template over the image and responds most strongly where the image looks like a star. The finder works on a background-subtracted image and keeps only sources whose sharpness (how sharply peaked a source is; a hot pixel is too sharp, a galaxy too flat) and roundness (how symmetric it is, on the finder's scale of about −1 to 1, where 0 is round) look like a star. The star finder's own center for each source is the position the rest of the pipeline uses. <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection.SourceDetector.detect -->

   A separate step fits a 2D Gaussian (bell-shaped) profile to bright stars. That fit measures the star width (FWHM), which is used to judge how large a position error is acceptable. It does not set any star position. <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.fwhm.measure_image_fwhm -->
2. **Quad-Star Matching:** Groups nearby stars into four-star shapes ("quads") whose relative side lengths are invariant under rotation, reflection, and scaling. These shapes are compared against a pre-built star index to identify which patch of sky the image shows, even if the telescope was pointed somewhere unexpected — continuing a long tradition of cataloging the sky this way [1]. The Astrometry.net solver [7] does this work. It can run on this computer (the local solver) or as a web service (the online solver), and the pipeline tries them in that order. The local solver reads the image file and finds its own stars. The pipeline's own star list decides whether a local attempt is worth making. When the local attempt fails or is skipped, the brightest 100 stars of that list are sent to the online solver. Only if that also fails is the whole image uploaded. <!-- impl: astrometricslib.drivers.astrometry_net_driver.AstrometryNetPlateSolveDriver.solve --> <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.MAXIMUM_PLATE_SOLVE_SOURCES -->
3. **Fitting the Distortion Map:** Converts pixel offsets from the image center $(x-x_0, y-y_0)$ into sky-coordinate offsets $(\xi, \eta)$, using four coefficients $CD_{i,j}$ plus a correction for lens/mirror distortion, called a Simple Imaging Polynomial (SIP) [11]. Here $(\xi, \eta)$ are positions on the tangent plane: a flat projection of the sky that touches the sphere at the image center.

   In words: a perfect lens would map pixels to sky by a rotation, a scale, and perhaps a mirror flip, which is the linear part. Real optics bend the map a little, mostly near the edges, and the SIP terms add that small bend:

$$
\xi = CD_{1,1}(x - x_0) + CD_{1,2}(y - y_0) + f_{\text{SIP},\xi}(x, y), \qquad \eta = CD_{2,1}(x - x_0) + CD_{2,2}(y - y_0) + f_{\text{SIP},\eta}(x, y) \tag{5}
$$

   * *What each piece means:*
     * $CD_{i,j}$: the four coefficients that together handle the image's rotation, scale, and any left-right flip.
     * $f_{\text{SIP}}(x, y)$: extra polynomial terms that correct for lens or mirror distortion, more noticeable toward the edges of a wide field of view.

   The solved WCS is written back into the image's FITS header (the block of named entries stored with the pixels), so the next program to open the file does not solve it again. The write keeps the SIP terms and the "-SIP" ending of the coordinate-type entries, which tells a reader that the terms are there. Without them, a reader of the file would lose the lens correction near the image edges. Old sky-map keywords are deleted first, so a re-solve leaves no contradictory entries. <!-- impl: astrometricslib.pipelines.shared.session_identification.write_wcs_to_fits_header -->
4. **Naming the Stars:** With the WCS known, each detected star is matched to the nearest entry in the SIMBAD star database [8] (an online database of named objects) or the Gaia catalog [10] (a space telescope's catalog of precise positions for over a billion stars) to attach a real name and ID.

   Gaia lists each star's position for the year 2016.0 (its epoch: the date a catalog position refers to), together with its proper motion: how far the star drifts across the sky each year. A star that drifts 1 arcsecond a year sits 10 arcseconds from its catalog position after ten years, which equals the pipeline's whole match radius. Each Gaia position is therefore moved to the date of the observation before matching. When the catalog rows carry no proper motion, or the image has no readable observation date, the positions stay at 2016.0 and the run records a flag saying so. A single star with no proper motion value stays where it is. <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.propagate_gaia_positions --> <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.StarIdentifier._move_gaia_coords_to_observation_epoch -->
5. **Judging the Fit:** Two numbers come from the solve itself and describe how well the solved WCS agrees with the stars. The pipeline keeps them apart. A third, more independent number comes from the refit of step 6.
   * *Fit residual.* The solver matches stars in the image to stars in its own reference index. For each matched star, the residual (the misfit left after fitting) is the distance between the star's position under the fitted WCS and the reference star's position. The root mean square (RMS) of these distances, in arcseconds, is the fit residual. <!-- impl: astrometricslib.drivers.astrometry_net_driver.read_corr_fit_statistics --> <!-- impl: astrometricslib.pipelines.astrometry.post_processing.run_gates.astrometry_run_gates -->
   * *Catalog match separation.* The RMS of the distance from each detected star to its nearest SIMBAD or Gaia star, counting only stars inside the match radius. Wrong matches and SIMBAD's uneven position precision raise it, and the match radius caps it. It is a rough check, not a measure of the fit. <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.StarIdentifier.get_catalog_match_separation_rms_arcsec -->

   The online solver returns no match table, so for an online solve the fit residual is unknown.
6. **Refitting the Solution to Gaia:** The solver fits its solution to the few dozen bright stars of its own index, whose positions carry the index's errors. A solution can therefore sit a pixel or more from the truth and still agree well with itself.

   After solving, the pipeline matches the detected stars to Gaia stars moved to the observation date, fits a new solution with second-order SIP distortion to those matches, drops stars that sit far from the fit (blends, double stars, wrong matches), and repeats until the set of matched stars stops changing.

   The new solution replaces the solver's only when it fits the Gaia stars better and does not move the field by more than a set amount. Either way, the residual against Gaia is recorded, which gives a measure of the fit that does not depend on the solver. Too few matched stars, or no Gaia stars for the field, keep the solver's solution, and the run records a flag saying the refit was skipped.

   *Which number the quality check judges.* The check takes the first of three numbers that the run measured: the RMS against Gaia from the refit, then the solver's fit residual, then the catalog match separation. It names the one it used in its detail. <!-- impl: astrometricslib.pipelines.astrometry.post_processing.run_gates._residual_and_basis --> <!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.refine_wcs_with_gaia --> <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.StarIdentifier._refine_wcs_against_gaia -->

### 4.3 Pipeline Theory of Operations

Table 4 lays out the astrometry pipeline step by step.

**Table 4.** Astrometry pipeline execution sequence and operations.

| Step | Pipeline Phase | Inputs & Outputs | Description |
|---|---|---|---|
| 1 | Source Detection | **In:** Stacked FITS image<br>**Out:** Star center list $(x_i, y_i)$ | Find stars with a matched-filter star finder, and keep sources that are sharp and round enough to be stars. |
| 2 | Quad Pattern Hashing | **In:** Star list or image<br>**Out:** Geometric quad shapes | Group stars into four-star shapes that stay recognizable under rotation and scaling, and turn each shape into a short numeric code (a hash) that can be looked up in the index. |
| 3 | WCS & Distortion Fit | **In:** Matched star pairs<br>**Out:** WCS header values ($CD_{i,j}, \text{SIP}$) | Fit the pixel-to-sky transformation via Eq. (5), and write it, SIP terms included, into the FITS header. |
| 4 | Catalog Cross-Match | **In:** Solved field, SIMBAD and Gaia catalogs, observation date<br>**Out:** Matched catalog stars $(\alpha_i, \delta_i)$ | Move Gaia positions to the observation date, refit the solution to the Gaia stars when that fits better, then match each detected star to a catalog entry to attach its identity. |
| 5 | Solution Check | **In:** WCS, Gaia refit RMS, fit residual, catalog match separation, match counts<br>**Out:** Astrometry Quality Record | Judge the residual (the Gaia refit RMS, else the solver's fit residual, else the catalog match separation) against the star width, and record how many stars matched. |

### 4.4 Quality Metrics and Solution Validation

Each check below is recorded as passed, failed, or not checked. A check that could not look is never recorded as passed. <!-- impl: astrometricslib.models.gate_result.unchecked_gate -->

#### 4.4.1 Before Solving: Checking the Image
* **Minimum Star Count:** At least four stars must be detected before a plate-solve is even attempted. Below a second, higher count (Appendix A), the local solver is skipped and the pipeline goes straight to the online solver, because a local attempt would spend its whole time limit on a near-certain failure. <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.StarIdentifier.process_image -->
* **Shape Filtering:** Detected sources are filtered by sharpness and roundness before solving, to screen out non-star detections and badly distorted blobs. <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection.SourceDetector.detect -->
* **Scale Hints & Fallback:** The pipeline computes the expected pixel scale from the pixel size, binning, and focal length, and starts with a narrow window around it. The solver widens that window by a further 20% on each side. If the solve still fails quickly, it retries with no scale or position assumption at all. If the hinted solve instead used nearly its whole time limit, the retry is skipped, because an unconstrained search is unlikely to succeed in the same budget. <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.StarIdentifier._calculate_scale_hints --> <!-- impl: astrometricslib.drivers.astrometry_net_driver.AstrometryNetPlateSolveDriver._solve_locally -->

#### 4.4.2 After Solving: Checking the Result
* **Solve Success Flag:** Whether the solver found a WCS at all; if not, the pipeline stops for that target.
* **Matched-Star Count:** The number of detected stars matched to a catalog entry must reach a minimum. With few matches, the residual is an average over too few stars to mean much. <!-- impl: astrometricslib.pipelines.astrometry.post_processing.run_gates.astrometry_run_gates -->
* **Fit Residual:** The residual chosen in Section 4.2, step 6 (the Gaia refit RMS when there is one) must stay below a fraction of the star width. A residual near half a star width means something other than centroid noise (the small random error in finding each star's center) dominates the fit: a wrong match, a bad distortion model, or a wrong field. <!-- impl: astrometricslib.pipelines.astrometry.post_processing.run_gates.astrometry_run_gates -->
* **Network Health Tracking:** Tracks how often lookups to remote star catalogs succeed, fail, or get temporarily paused after repeated failures. The check fails when the pause tripped or half or more of the lookups failed. A run that attempted no lookup is recorded as not checked. This distinguishes a genuinely sparse star field from a network problem. <!-- impl: astrometricslib.pipelines.astrometry.post_processing.run_gates.astrometry_run_gates -->

:::{warning}
**A pass is not a measure of position accuracy.** The residual limit is there to catch a failed solve, not to rank good ones. It was set on catalog match separations, and a fit residual is normally smaller than a separation, so the limit is lenient for the fit residual and the Gaia refit RMS that the check now prefers (partly measured; see [AST-04](validation/astrometry.md#ast-04)). The minimum matched-star count rests on a single look at the saved solves (partly measured; see [AST-05](validation/astrometry.md#ast-05)). The failed-lookup limit is a design estimate (Appendix A).
:::

---

## 5. Stellar Photometry Pipeline

The photometry pipeline tracks how a star's brightness changes across a sequence of individual (unstacked) photos, building a light curve and flagging stars whose brightness varies.

### 5.1 Purpose & Interfaces

* **Inputs:** A sequence of calibrated, unstacked light frames, the stars found on the session's first frame, and a solved WCS.
* **Outputs:** Normalized light curves $\hat{F}_i(t)$ with a 1-sigma uncertainty (one standard deviation) and a mid-exposure time for every point, brightness statistics, a list of variable-star candidates, and a Photometry Quality Record.

### 5.2 Major Concepts and Governing Equations

1. **Tracking a Star Frame to Frame:** The pipeline finds the stars once, on the first frame of an observing session. A session here means the frames of one target taken on one night (counted noon to noon) with the same gain setting and offset. On every later frame, it measures how the frame sits against the first one (a shift, plus a small rotation and change of scale when enough well-spread reference stars are found) from a handful of bright alignment stars. If too few of them are found, it assumes no shift rather than guessing.

   It starts each star at its first-frame position moved by that alignment. Then it re-centers the star on its own brightness-weighted centroid (the average pixel position, weighted by brightness) in a small box. A star's true offset differs slightly from the frame's fitted alignment, so this step keeps the measuring circle on the star. The re-centered position is refused, and the shifted position kept, when the box holds a saturated pixel, holds no light above the sky, reaches past the frame edge, or the centroid moved too far to be the same star. <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry._process_single_frame_worker --> <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.refine_star_centroid -->

   The brightness is measured by aperture photometry: add up the pixel values inside a circle on the star (the aperture), and subtract the sky level, measured as the median of a ring of pixels around it (the sky annulus). The measuring circle and its sky ring sit at the star's exact position, including the fraction of a pixel. A pixel on the circle's edge counts for the fraction of it inside the circle. The position is never rounded to a whole pixel. A circle half a pixel off a typical star loses 1 to 2 percent of its light, and that loss changes as the field drifts. It is the same size as the brightness changes the pipeline searches for. <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.measure_aperture_photometry -->

   Finding the stars once and following them by the frame's alignment only works within one observing session, since framing and pointing stay consistent only while the telescope isn't repositioned. The pipeline therefore tracks stars session by session, not across a target's entire observation history.
2. **Uncertainty of Each Measurement:** Every brightness comes with a 1-sigma uncertainty, so later steps can weight a point by how well it was measured. The uncertainty follows the CCD equation (the noise budget of a camera sensor) [12]. It works in electrons, because the random scatter of a count follows the number of electrons collected.

   The equation adds the variances of independent noise sources. A bright star is limited by its own shot noise; a faint star is limited by the sky and the sensor:

$$
\sigma_F^2 = F + n_{\text{pix}}\left(S + R^2 + D\right) + \frac{n_{\text{pix}}^2}{n_{\text{sky}}}\left(S + R^2\right) \tag{6}
$$

   * *What each piece means:* $F$ is the star's net counts, so the first term is the star's own shot noise (the random scatter of a count: a count of $F$ scatters by about $\sqrt{F}$). $S$ is the sky level per pixel, $R$ the read noise, and $D$ the dark current collected per pixel. $n_{\text{pix}}$ is the area of the circle and $n_{\text{sky}}$ the number of pixels in the sky ring. The second term is the noise of the sky, read-out, and dark counts under the circle. The third is the noise in the sky level itself, which the ring measures from a limited number of pixels. All of $F$, $S$, $R$, and $D$ are in electrons.

   The gain in e-/ADU (electrons per ADU, the step of the stored pixel value; not the camera's gain setting) comes from the camera's profile, then from the frame's header, and only then from an assumption of 1 electron per ADU and no read noise. When the gain is assumed, the uncertainties have the right dependence on brightness but the wrong size, and the light curve says so. The dark current is taken as zero because no source records it. The equation assumes the bias offset has been removed from the frame. <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.aperture_flux_error_adu --> <!-- impl: astrometricslib.pipelines.photometry.pre_processing.detector_noise.resolve_detector_noise -->
3. **Times of Observation:** Each point is stamped with the middle of its exposure as a Julian date (a continuous count of days, used so that times subtract simply) in BJD_TDB (Barycentric Julian Date in Barycentric Dynamical Time, an even-running time scale for the solar system that sits about a minute ahead of UTC) [13]. This is the time the light would reach the center of mass of the solar system (the barycenter), so the date of an event does not depend on where Earth is in its orbit. The shift is up to about 8 minutes, and a long exposure shifts the mid-time by half its length. A frame with a missing or unreadable observation date is rejected from every light curve. It is never given the current time. When the target's position or the observatory's location is unknown, the light curve records which weaker time basis was used. <!-- impl: astrometricslib.pipelines.photometry.pre_processing.observation_times.barycentric_julian_dates --> <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.read_observation_time -->
4. **Ensemble Differential Photometry:** To cancel out passing clouds or changing atmospheric transparency, each star's raw brightness $F_i(t)$ is divided by a signal built from a group ("ensemble") of well-behaved comparison stars in the same field. A thin cloud dims every star in the frame by the same fraction, so the ratio stays the same. Measuring a star relative to others in the same image is called differential photometry. The group is chosen once per session and is the same in every frame. The choice has three steps:
   * *Candidates.* A star qualifies only if it has a positive brightness in every usable frame, is never saturated, and no catalog lists it as variable. Saturated stars are excluded because brightness stops tracking light near full well (the most charge a pixel can hold).
   * *Brightness band.* The candidates are ranked by median brightness. The very brightest are skipped because they come closest to saturation. The band runs down to a fixed share of the candidates, and at most a fixed number of stars enter the next step. A star outside this band is not a comparison star.
   * *Vetting.* Each candidate is compared with an ensemble built from the other candidates. The star that scatters most, relative to the scatter its uncertainties predict, is dropped. The step repeats until no star scatters more than its uncertainties allow, or until a minimum number remain. If more than a cap remain, the stars with the smallest uncertainties are kept.

   The signal in each frame is the weighted mean of the comparison stars' brightness, each divided by its own session mean so that bright and faint stars count on the same scale. Noisier stars get less weight: each weight is one over the star's variance (inverse-variance weighting), which gives the average with the smallest noise. If the sky dims every star by 5% in one frame, $L(t)$ drops by 5% and the ratio $\hat{F}$ does not change:

$$
\hat{F}_i(t) = \frac{F_i(t)}{L(t)}, \qquad L(t) = \bar{m}\,\frac{\sum_{k=1}^{K} w_k(t)\,F_k(t)/m_k}{\sum_{k=1}^{K} w_k(t)}, \qquad w_k(t) = \frac{1}{\sigma_k(t)^2} \tag{7}
$$

   * *What each piece means:* $m_k$ is comparison star $k$'s mean brightness over the session, $\bar{m}$ the mean of those means, and $\sigma_k(t)$ the fractional uncertainty of star $k$ in that frame. $\bar{m}$ puts $L(t)$ back in brightness units (ADU per second), so a star as bright as the average comparison star has $\hat{F}$ near 1. The noise of $K$ equal stars averages down as $1/\sqrt{K}$. A median would not: for stars of different brightness, the median of their raw fluxes is set by whichever star sits in the middle, so it carries that one star's noise.

   *Notation in the photometry review guide.* The guide (`pipelines/photometry.md`) indexes frames by $k$ and stars by $j$. Its dimensionless level $L_k$ is the weighted mean of the ratios $F_k(t)/m_k$ above, and its level in ADU per second, $N_k = \bar{m}\,L_k$, is the $L(t)$ of Equation (7).

   A comparison star is divided by the signal built from the *other* members (a leave-one-out signal), so that its own noise and changes never enter its own divisor. Without this, a member's own dip would also lower its divisor. Its weight in the divisor is about $1/K$, so the dip would look about $1/K$ of its depth shallower than it is.

   Frames in which fewer than half the usual number of stars were measured are left out of the session, as are frames whose signal is a clear outlier compared with the rest (a passing cloud, say). A star's own isolated outlying points (a cosmic ray, a bad centroid) are clipped, but two or more neighboring points on the same side are kept as a real change, such as a dip. <!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.select_comparison_set --> <!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.build_ensemble_signal --> <!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.VariabilityAnalyzer._reject_outlier_frames --> <!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.VariabilityAnalyzer._reject_outlier_measurements_for_star -->

   The pipeline does not fit a star's own brightness against airmass (how much atmosphere the light crossed). Dividing by the ensemble already removes the dimming every star shares. A fit to one star would also remove part of a transit or half a pulsation cycle, because airmass changes steadily over a night.

   An optional correction, off by default, uses the frames' airmass in another way. It fits a straight line of brightness against airmass to each comparison star's normalized light curve and removes the comparison stars' median slope from every star. This removes the airmass dependence the comparison stars share beyond what their weighted mean absorbs, for example when most of them have a colour that differs from the average. It is off by default because it cannot tell a star's own colour term from its variability. <!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.VariabilityAnalyzer.detrend_light_curves_airmass -->


:::{warning}
**Designed, not measured on real fields.** The brightness band, the minimum and cap on the number of comparison stars, the consistency limit used in vetting, and the half-of-typical rule for usable frames are design estimates (Appendix A). Only the method is checked, on synthetic sessions with known truth: it keeps a star's own dip, turns away a varying comparison star, and removes a transparency change that all stars share (partly measured; see [PHO-01](validation/photometry.md#pho-01)). <!-- impl: astrometricslib.pipelines.photometry.test.test_comparison_ensemble_injection -->

:::

5. **Deciding Which Stars Vary:** A star's scatter grows as it gets fainter, because its measurements are noisier. A rule that flags the stars with the most scatter would fill up with faint stars. The pipeline asks a different question: does this star scatter more than a constant star of the same brightness scatters in this field? It fits a noise model of the field: the measured scatter of the field's own stars against their brightness, which gives the expected scatter of a constant star at each brightness. (The scatter the CCD equation predicts for one star, Equation (6), is a different thing, its predicted noise.) Then it measures three indices for every star:
   * *Excess scatter:* the star's measured scatter divided by the model's expected scatter. A constant star is near 1.
   * *Reduced chi-square:* how far the points sit from the star's weighted mean, in units of each point's uncertainty. A constant star with correct uncertainties is near 1. A value of 4 means the points scatter about twice as far as their uncertainties predict.

$$
\chi^2_\nu = \frac{1}{n-1}\sum_{j=1}^{n}\left(\frac{x_j - \bar{x}_w}{s_j}\right)^2 \tag{8}
$$

   * *What each piece means:* $x_j$ is point $j$ of the normalized light curve, in flux units (ratios near 1), not magnitudes, and $s_j$ its uncertainty, $\bar{x}_w$ the uncertainty-weighted mean, and $n$ the number of points. The propagated uncertainties leave out centroid jitter and flat-field error, so they are raised to the noise model's level when that level is higher.
   * *Stetson J:* multiplies the deviations of neighboring points [14]. White noise gives products of random sign, so J stays near 0. A smooth change gives mostly positive products. One bad point gives two products of opposite sign and adds little.

   The noise model and the excess scatter work in magnitudes; chi-square and J work on the flux values.

   The thresholds on chi-square and J are set from the field itself, at 2.326 robust standard deviations above the field's median. 2.326 is the one-sided 99% point of a normal distribution: if the index were normally distributed among constant stars, about 1% of them would pass it by chance. A robust standard deviation is 1.4826 times the median absolute deviation (MAD), which equals the ordinary standard deviation for normally distributed values but is not inflated by a few true variables. Setting the thresholds from the field means a field whose uncertainties are all mis-scaled (an assumed gain, for example) raises its own thresholds. The threshold on excess scatter is fixed. A star is a variable-star candidate only when all three indices exceed their thresholds and the star also passes a false-discovery test: its chi-square value is turned into a false-alarm probability (the chance a constant star would score this high), and the Benjamini–Hochberg method converts those probabilities into q-values that allow for testing every star in the field at once. The q-value must be below 0.01. <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.FALSE_DISCOVERY_LEVEL --> Stetson J acts as a veto: it keeps isolated bad points from creating candidates. <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.assess_variability --> <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.fit_noise_model --> <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.reduced_chi_square --> <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.stetson_j --> <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.calibrate_thresholds -->

   The coefficient of variation stays on every light curve as a plain measure of scatter. It is the standard deviation divided by the mean, so 0.01 means the brightness scatters by about 1%:

$$
C_v = \frac{\sigma_{\hat{F}}}{\langle \hat{F} \rangle} \tag{9}
$$

   When a field has too few stars to fit a noise model, the pipeline falls back to flagging stars whose $C_v$ is unusually high relative to the other stars in the same field, rather than against one fixed threshold used for every field. This mirrors the field-relative approach long-running variable-star observing networks use to flag candidates for follow-up [2]. <!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.flag_variable_stars -->


:::{warning}
**The flag is only as good as the field's noise.** How well a score separates variables from constant stars is measured by the area under the ROC curve (AUC). A ROC (receiver operating characteristic) curve plots the share of true variables flagged against the share of constant stars flagged as the cutoff is swept from strict to loose. The AUC is the chance that a randomly chosen variable scores above a randomly chosen constant star: 1 is perfect and 0.5 is chance. On synthetic fields with known variables, the combined score beats $C_v$ alone and flags no constant star (partly measured; see [PHO-06](validation/photometry.md#pho-06)). That tests the method on a field whose noise is understood, not on the sky. <!-- impl: astrometricslib.pipelines.photometry.test.test_variability_injection --> On the observatory's real library, the older $C_v$-only flag separated catalogued variables from other stars of similar brightness no better than chance (measured; see [PHO-10](validation/photometry.md#pho-10)), so a flag from it is not evidence of the kind of variable the catalogs know. <!-- impl: astrometricslib.scripts.measure_variability_cutoff -->

:::

6. **Matching the Same Star Across Sessions:** Since tracking (concept 1) only holds within one session, the pipeline needs another way to identify the same star across sessions taken weeks or months apart. It does that using each star's sky position: every session's tracked stars are placed on the same sky-coordinate frame and matched against stars already found in earlier sessions, folding one physical star's measurements across sessions into a single, continuous light curve. The merge does not rescale any session. Each session keeps its brightness level as measured, because rescaling one session to match another would remove the very slow change the next step looks for. <!-- impl: astrometricslib.pipelines.photometry.batch._match_and_merge_across_sessions --> <!-- impl: astrometricslib.pipelines.photometry.batch._merge_light_curves -->

   For each star seen in at least two sessions, the pipeline compares the sessions' median normalized brightness. The between-session amplitude is the ratio of the highest to the lowest median, in magnitudes (the astronomer's logarithmic brightness scale; 0.01 magnitude is about a 1% change). The significance is the difference of those two medians divided by their combined expected error. A star is flagged when both are large enough. A flag means the star's level differs between sessions by more than its own scatter explains. It does not rule out the comparison stars as the cause, because each session picks its own comparison group. A shift that every star in the field shares points to the comparison group, not to the star. <!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.identify_long_term_variable_candidates -->
7. **Identifying Stars by Catalog Name (optional):** Each session's reference frame can optionally be run through the same star-catalog matching used by the astrometry pipeline (Section 4), reusing an existing WCS when one is already available instead of solving again. This gives every star a real catalog name instead of a synthetic per-run label, and also improves the cross-session matching described in concept 6.
8. **Searching for a Repeating Pattern:** After the light curves are saved, the pipeline searches the target's own star and the ten brightest other stars for a period. Two searches run on each star. A Lomb-Scargle periodogram fits sine waves of many trial periods to the unevenly spaced measurements and looks for a smooth up-and-down cycle. A box-fitting search (box least squares) looks for a repeating flat-bottomed dip, the shape of an eclipse or a planet transit. Each measurement is weighted by its uncertainty. <!-- impl: astrometricslib.pipelines.photometry.batch.search_periods_and_save --> <!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search.lomb_scargle_search --> <!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search.box_search -->

   A search always finds a best period, even in pure noise, so the period alone means nothing. Each result therefore carries a verdict. From lowest to highest the verdicts are: *insufficient data* (the data cover fewer than two full cycles, so no period is reported), *not detected*, *possible*, and *detected*. A result is at least "possible" when its false-alarm probability is at most 0.05, and "detected" when it is at most 0.01 and at least three cycles (or, for a dip search, three separate dips) were observed. A result can also be marked *unconfirmed*, a label beside the verdict, not a level. The false-alarm probability is the chance that noise alone would give a result at least as strong. It is measured as the share of shuffled light curves (the same values in a random order, so any real period is destroyed) that give a result at least as strong. The shuffle moves blocks of neighboring measurements, so slow drifts from clouds or airmass are not mistaken for a signal (partly measured; see [PHO-11](validation/photometry.md#pho-11)). A pattern seen fewer than three times is at most "possible". The verdict limits are partly measured on synthetic light curves (see [PHO-12](validation/photometry.md#pho-12)). <!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search.verdict_from_false_alarm --> <!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search.correlation_block_length -->

   Two further checks can lower a verdict:

   * *Hold-out.* A hold-out check leaves one night out, finds the period from the other nights, and tests whether it predicts the night left out.
   * *Alias.* Observing once a night creates false echo periods (aliases) beside the real one. An alias check asks whether such an echo is nearly as strong as the best peak.

   Each failed check lowers the verdict one level, for example from "detected" to "possible". A check that could not run leaves the verdict as it is and marks the result unconfirmed.

   Finally, one run makes 22 searches (11 stars, two searches each), so the chance that one of them passes by luck is far larger than the per-search limit. Each false-alarm probability is corrected for the number of searches (the Šidák correction, which tightens each search's limit so that the chance of any false alarm across all 22 stays at the chosen level), and searches that need more shuffles to reach the corrected limit are repeated. The repeat is needed because a shuffle test cannot report a probability smaller than about one over the number of shuffles: with 300 shuffles the smallest is 1/301, about 0.003, and after the correction for 22 searches that becomes about 0.07, above the 0.05 limit, so no verdict could survive without more shuffles. <!-- impl: astrometricslib.pipelines.photometry.processing.period_checks.apply_verdict_checks --> <!-- impl: astrometricslib.pipelines.photometry.processing.family_wise_correction.apply_family_wise_correction -->

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
| 7 | Period Search | **In:** Light curves of the target and the ten brightest stars<br>**Out:** Best period, false-alarm probability, and verdict per search | Run a periodogram and a box-fitting search, judge each against shuffled light curves, apply the hold-out and alias checks, and correct for the number of searches. |

### 5.4 Quality Metrics and Photometric Validation

Each run-level check below is recorded as passed, failed, or not checked, as in Section 4.4. A check that could not look is never recorded as passed. <!-- impl: astrometricslib.pipelines.photometry.post_processing.run_gates.photometry_run_gates -->

#### 5.4.1 Before Measuring: Screening the Input
* **Saturation Ceiling:** A star that is saturated in any frame is never used as a comparison star, since a saturated star's brightness reading can't be trusted.
* **Alignment Confidence Floor:** The frame's drift is measured only when a minimum number of alignment stars are re-located; if too few are found, the pipeline assumes no shift rather than guessing at one it isn't confident about.
* **Capture Times:** A frame with no readable observation date is rejected, and each rejection reason is recorded in the run's quality summary.

#### 5.4.2 After Measuring: Checking the Light Curve
* **Comparison Set:** A session's comparison set must hold at least a minimum number of stars, and the number must be the same in every frame. The check also reports the comparison stars' scatter in magnitudes next to the scatter their uncertainties predict. A value near the prediction means the set is constant at the level of its noise.
* **Ensemble Outlier Rejection:** Frames whose comparison signal is a clear outlier are rejected before being folded into the light curve. The run is flagged when a quarter or more of the frames are rejected (a design estimate; Appendix A).
* **Per-Star Outlier Rejection:** Individual normalized flux points are clipped per star to suppress single-frame artifacts in the light curve, except for runs of neighboring points that look like a real change.
* **Discrimination Check:** Among the field's stars, those that catalogs list as variable should score above the others in the variability score, with an AUC of at least a minimum. The check is not made without enough catalogued variables and unlisted stars to compare.
* **Detectable Amplitude:** The run states the smallest change its variability cutoff could see (peak to peak, in magnitudes). A change smaller than that is invisible to the flag, however real it is. The check fails when that smallest visible change is larger than a set limit (Appendix A), because the run is then blind to most variable stars.
* **Uncertainty Provenance:** The run reports the median per-point uncertainty in magnitudes and whether the gain was assumed.

---

## 6. Stellar Spectroscopy Pipeline

The spectroscopy pipeline extracts, calibrates, and analyzes a star's spectrum from images taken through a transmission grating (a clear plate ruled with many fine parallel lines, here a Star Analyser 200) placed in front of the camera. The grating spreads every star's light into a rainbow (a spectrum) without needing a narrow slit, which is called slitless spectroscopy. Each star leaves its undispersed point image, the zero order, and beside it a first-order spectrum: the first and brightest of the spread-out copies, which the pipeline measures. Some code comments call the grating a grism (a grating combined with a prism); the equations here treat it as a plain grating.

### 6.1 Purpose & Interfaces

* **Inputs:** A stacked spectral image, the position of the "zero-order" star image (the star's normal, undispersed position, used as a fixed reference point), the target's aperture size (different for point-like stars vs. extended objects), the camera's Quantum Efficiency (QE) curve (the share of arriving light the sensor records at each wavelength), the instrument response (explained below), and the airmass from the frame's header.
* **Outputs:** A wavelength-calibrated 1D spectrum $F(\lambda)$ (brightness at each wavelength $\lambda$), any detected hydrogen (Balmer) absorption or emission features (dark or bright lines at hydrogen's known wavelengths), the automatically measured dispersion angle, a record of whether the airmass correction was applied, the closest spectral type (Section 6.2, concept 5) with a measure of how clearly it beats its neighbors, and a Spectroscopy Quality Record.

### 6.2 Major Concepts and Governing Equations

1. **Finding the Spectrum's Angle:** Automatically measures the dispersion angle $\theta_{\text{disp}}$ (the direction in which the grating spreads the light) relative to the sensor's pixel grid, along with orientation (horizontal or vertical, and direction), using the zero-order star position $(x_0, y_0)$ as the anchor point.
2. **Extracting the Spectrum:** Each star's spectrum lies on the image as a short band of light, the spectrum trail (called the trail below; a streak is a different thing, the line a satellite or meteor leaves). At each step along the trail, the pipeline sums pixel intensity across it, inside a reading box, and subtracts the sky background. Each step becomes one point of the 1D spectrum:

$$
F(x) = \sum_{y} w(y)\,\big[\,I(x, y) - S(x)\,\big] \tag{10}
$$

   * *What each piece means:* $I(x,y)$ is the pixel intensity, $S(x)$ the sky level per pixel at that step, and $w(y)$ the fraction of pixel $y$ that lies inside the reading box, from 0 to 1. Pixels at the box edge count in part, and the box center keeps its fractional position. Without this, the box would change size in whole-pixel jumps, and each jump would show up in the spectrum as a step the star did not cause.

   The sky level is measured in two strips, one on each side of the box. A gap separates each strip from the box, so the star's faint outer glow is not counted as sky.

   Each strip is cleaned of outlier pixels (hot pixels, cosmic rays, the edge of a passing satellite streak) and its median is taken. If the two medians agree to within noise, the sky level is their mean. If they differ by more than noise explains, the brighter strip is treated as contaminated, since a neighbor's light can only add to a strip, and the cleaner strip is used alone (partly measured, by simulation; see [SPE-03](validation/spectroscopy.md#spe-03)). If only one strip lies on the image, that strip is used. Without sky subtraction, the faint stars of a crowded field read too red and are classed too cool (partly measured; see [SPE-02](validation/spectroscopy.md#spe-02)).

   Taking the lower of the two medians every time would read low, because the smaller of two noisy numbers sits below their true value, and it would leave sky light in a faint star's total. The extractor counts how often each case occurred. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.measure_sky --> <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.SpectrumExtractor._sum_aperture_minus_sky --> <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.box_pixel_weights -->
   * *Point sources vs. extended objects:* For a point-like star, the box reaches a fixed multiple of the trail's width to each side of its center. The width comes from a Gaussian fitted to the trail's cross-section at each step. That fit is noisy from step to step, so the width is smoothed with a running median before it sizes the box. A box that followed every fit directly would change size at each step, and the spectrum would jump by a couple of percent for reasons that have nothing to do with the star. Where the width cannot be fitted, a fixed radius is used. Extended objects like nebulae or comets get a wider box, whose radius comes from the object's catalogued angular size. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.traced_aperture_half_widths_px --> <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.smooth_aperture_sigmas --> <!-- impl: astrometricslib.pipelines.astrometry.pipeline.AstrometryPipeline._derive_extraction_radius_px -->
3. **Converting Pixels to Wavelength:** Converts the distance $x$ from the zero-order center, measured along the trail, into a wavelength $\lambda$, via the grating equation. The equation has one free parameter, the grating-to-sensor distance $L$. It is fitted once, by a separate calibration step run on a reference star with strong hydrogen lines (Vega), and the fitted value is then used for every spectrum.

   In words: the grating bends longer wavelengths through larger angles. The angle is found from the distance on the sensor and the distance $L$ to the grating, and the grating equation turns that angle into a wavelength:

$$
\lambda(x) = d\,\sin\!\left(\arctan\frac{x_{\text{mm}}}{L}\right) \tag{11}
$$

   Here $d$ is the grating's groove spacing, and $x_{\text{mm}}$ is the distance converted to physical length via the sensor's pixel pitch.
   * *Distance along the trail:* A tilted trail is longer than the number of columns it covers, so the pipeline records, for every sample it keeps, the distance from the zero-order star measured along the trail. Samples that fall off the image or below the camera's shortest wavelength are dropped, so a sample's index is not its distance. The calibration step locates each absorption dip by this recorded distance and never by counting samples from the start. Counting would shift every dip by the number of dropped samples and bias the fitted $L$. <!-- impl: astrometricslib.pipelines.spectroscopy.utilities.calibration_tuner.SpectroscopyCalibrationTuner._fit_grating_distance -->
   * *Reference lines:* $L$ is calibrated using the Balmer series (hydrogen absorption lines) at wavelengths given in ångströms (Å; 1 Å is $10^{-10}$ m, or 0.1 nm): ($\mathrm{H}\beta = 4861.3\text{ Å}, \mathrm{H}\gamma = 4340.5\text{ Å}, \mathrm{H}\delta = 4101.7\text{ Å}$), whose wavelengths are already known precisely. The tuner tries every group of three dips against the three lines and keeps the group with the smallest RMS wavelength error. If no group fits within a coarse tolerance (Appendix A), the fit is rejected with an error and the stored $L$ is left unchanged. <!-- impl: astrometricslib.pipelines.spectroscopy.utilities.calibration_tuner.SpectroscopyCalibrationTuner._fit_grating_distance -->
4. **Correcting for the Instrument and the Air:** Three effects that belong to the equipment and the sky, not to the star, are removed from the extracted flux:
   * *Sensor color sensitivity.* The flux is divided by the sensor's QE curve, $\text{QE}(\lambda)$.
   * *Instrument tilt.* The grating, the telescope's coatings, and the air each favor some colors. Their combined effect is larger than the real differences between stellar types. It is measured by observing a star whose true spectrum is known (Vega), dividing the observation by the reference spectrum, and smoothing the result. This smooth curve is the instrument response, $R(\lambda)$. Dividing any other star's spectrum by it removes the tilt. The response is derived from QE-corrected spectra, so it is only applied to one.
   * *Airmass.* Air dims blue light more than red, and dims both more at higher airmass (how much air the light crossed: 1.0 straight overhead). The response already contains the dimming at the airmass of the standard star. A target observed at another airmass would keep a leftover blue-to-red tilt, so the spectrum is rescaled using an extinction curve $k(\lambda)$ in magnitudes per airmass (how many magnitudes of dimming each extra airmass causes at each wavelength).

   Together, Equation (12) divides out the sensor and the instrument, then multiplies back the extra dimming of the target's air compared with the standard star's. When the two airmasses are equal, the last factor is 1:

$$
F_{\text{cal}}(\lambda) = \frac{F\big(x(\lambda)\big)}{\text{QE}(\lambda)\,R(\lambda)}\;10^{\,0.4\,k(\lambda)\,(X_{\text{target}} - X_{\text{ref}})} \tag{12}
$$

   Here $X_{\text{target}}$ is the airmass of the frame and $X_{\text{ref}}$ the airmass of the standard star. The correction is skipped, and the reason recorded, when the header has no usable airmass or the response records no reference airmass. Without a QE curve, neither the response nor the airmass step is applied.

   The stored extinction curve describes a dry, high-altitude site. A site at lower altitude has somewhat more extinction in the blue, but the correction depends only on the difference between two airmasses, so the error from using another site's curve is a small fraction of the correction. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.quantum_efficiency_correction.apply_quantum_efficiency_correction --> <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.instrument_response.apply_instrument_response --> <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.atmospheric_extinction.apply_extinction_correction -->

   *The order of the corrections.* The QE curve, the response, and the extinction curve all depend on wavelength, so the wavelengths are fixed first, in two steps.

   * *Refraction.* The air bends blue light more than red, so the star's blue and red light enter the grating from slightly different directions (atmospheric differential refraction). When the observatory site, the sky solution, and the exposure time are known, each wavelength is shifted back for this.
   * *Wavelength zero point.* The wavelength zero point (the offset of the whole wavelength scale; not the magnitude zero point of brightness calibration) is then measured from known absorption lines: if the zero-order image's center is off by a few pixels, every wavelength shifts by the same amount, and the shift is removed when at least two significant lines agree (the line significance limit is partly measured; see [SPE-11](validation/spectroscopy.md#spe-11)).

   Only then are the QE, response, and extinction corrections applied, in that order. The extraction errors pass through the same corrections. <!-- impl: astrometricslib.pipelines.spectroscopy.pipeline.SpectroscopyPipeline._apply_result_to_stellar_object --> <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.differential_refraction.correct_differential_refraction --> <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.wavelength_zero_point.measure_wavelength_zero_point -->

   *Interstellar reddening.* Dust between the star and Earth also dims blue light more than red. When a catalog gives the star's colour excess $E(B-V)$ (looked up by the star's Gaia identifier), the reddening is removed from the spectrum before it is classified (dereddening). The colour excess measures how much redder dust has made the star, as the change in the difference between its blue (B) and visual (V) magnitudes. A star with no Gaia identifier is classified on its observed spectrum. <!-- impl: astrometricslib.pipelines.spectroscopy.processing.star_reddening.look_up_star_reddening --> <!-- impl: astrometricslib.pipelines.spectroscopy.processing.interstellar_extinction.deredden_spectrum -->

   *An independent check against Gaia.* The Gaia satellite measured a low-resolution spectrum (the XP spectrum) for millions of stars, calibrated by a different instrument above the atmosphere. When a star has one, the corrected spectrum is compared with it. A straight tilt of the ratio points at the response or the airmass correction, and a shift of the features points at the wavelength scale. <!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.compare_to_gaia_xp.compare_to_gaia_xp -->
5. **Classifying the Spectrum:** A star's spectral type (a letter and number such as A0 or G2 that sorts stars by surface temperature; the number is the subtype) shows in the shape of its spectrum. The letters run O, B, A, F, G, K, M from hottest to coolest, and each letter class has ten subtypes, 0 to 9. A trailing Roman numeral is the luminosity class, which separates stars of the same temperature by size: V is a main-sequence (dwarf) star like the Sun, III a giant. So A0V is a hot main-sequence star like Vega. The corrected spectrum is compared with a library of reference spectra, each blurred to the instrument's resolution and scaled to the same overall brightness. Regions where Earth's atmosphere absorbs light (telluric bands) are left out of the comparison (partly measured; see [SPE-15](validation/spectroscopy.md#spe-15)).

   Each reference gets one score: the root-mean-square (RMS) difference as a fraction of the spectrum's average brightness. Lower is closer, and the best-scoring reference gives the likely spectral type. A score is not a probability.

   The pipeline reports how clearly the best reference wins as a gap in RMS: the second-best reference's RMS minus the best one's. A small gap means the data cannot choose between the two. Two gaps are kept. One is to the runner-up, which is usually a neighboring subtype and says the subtype is uncertain. The other is to the best reference of a different spectral class, which says the class itself is uncertain.

   A best match whose own RMS is too high is called a poor match, and a much worse one gets no type at all. <!-- impl: astrometricslib.pipelines.spectroscopy.processing.spectral_classifier.classify_spectral_type --> <!-- impl: astrometricslib.models.stellar_source.rms_gap_to_second_best --> <!-- impl: astrometricslib.models.stellar_source.rms_gap_to_next_class --> <!-- impl: astrometricslib.models.stellar_source.is_rms_gap_ambiguous -->
6. **An Alternative Path for Unstacked Frames:** Alongside the single-stacked-image approach above, a second path works directly on a target's raw, unstacked frames, grouped into observing sessions the same way the photometry pipeline is (Section 5.2, concept 1). Each session's stars are identified once against a real catalog, reusing an existing WCS from the frame's own file when available (Section 4), and every frame in that session extracts a spectrum for those same identified stars. This gives each extracted spectrum a stable, real star identity shared across the session, rather than each frame's own disconnected, unidentified detection.

:::{warning}
**Classification limits are measured on few stars.** The poor-match limit was set between the scores of stars matched close to their catalog type and stars matched to a wrong type, on only 14 stars. The two M dwarfs among them do not follow that split (partly measured; see [SPE-16](validation/spectroscopy.md#spe-16)). A low score means the reference is close to the spectrum, not that the type is right: known-wrong matches can score low. The gap limit is the smallest RMS distance between two neighboring references in the library, so a gap below it means the spectrum cannot prefer one reference over its neighbor even in principle (measured; see [SPE-18](validation/spectroscopy.md#spe-18)). All limits are listed in Appendix A. <!-- impl: astrometricslib.models.stellar_source.NO_GOOD_MATCH_RMS --> <!-- impl: astrometricslib.models.stellar_source.AMBIGUOUS_RMS_GAP -->
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
| 5 | Instrument and Airmass Correction | **In:** Wavelength map $\lambda(x)$, QE curve, instrument response, frame airmass<br>**Out:** Calibrated spectrum $F_{\text{cal}}(\lambda)$, extinction record | Shift the wavelengths for differential refraction and the measured zero point, divide raw spectral flux by the QE curve and the instrument response, then rescale to the standard star's airmass via Eq. (12). |
| 6 | Classification | **In:** Calibrated spectrum, reference library<br>**Out:** Spectral type, RMS gaps | Remove catalog reddening when known, score every reference by relative RMS, and report the best match with its gap to the runner-up and to the next class. Compare with the star's Gaia XP spectrum when it has one. |

### 6.4 Quality Metrics and Spectral Validation

The spectroscopy pipeline keeps two kinds of record. *Run gates* are recorded as passed, failed, or not checked, as in Section 4.4, and a failed gate flags the run. *Checkpoint metrics* are numbers stored with each spectrum at four points of its processing (the raw frame, extraction and calibration, classification and line features, and the final checks). A metric with a limit can fail, and the processing-quality gate below counts those failures; a metric without a limit is only recorded. There are nine run gates. <!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.run_gates.spectroscopy_run_gates -->

#### 6.4.1 Before Extracting: Checking the Input
* **Zero-Order Saturation (gate):** A star's zero-order center must not approach full-well capacity, or the dispersion origin $(x_0, y_0)$ is unreliable. The gate fails when any star's zero-order image holds a significant share of saturated pixels.
* **Spectra Extracted (gate):** Fails when no star produced a spectrum.
* **Sky Contamination (checkpoint metric, no limit):** The extractor records the share of readings where one sky strip was dropped because a neighbor's light lay beside the trail. A share well above a few percent means the spectrum deserves a closer look. The share, the dominant sky mode, and the count of each mode travel with the star's saved result. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.ExtractionDiagnostics.contaminated_sky_fraction -->

#### 6.4.2 After Extracting: Checking the Spectrum
* **Wavelength Scale (gate):** After the wavelength zero point of each spectrum is corrected, the leftover offsets across the run must scatter by less than about one pixel of dispersion (Appendix A). Not checked with too few spectra. <!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.wavelength_scale.wavelength_scale_gate -->
* **Gaia XP Agreement (gate):** Fails when the median tilt of the observed spectra divided by their Gaia XP spectra is above a limit (Appendix A). Not checked with too few compared stars. <!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.compare_to_gaia_xp.GAIA_XP_SLOPE_LIMIT_PERCENT_PER_1000_ANGSTROM -->
* **Classification Ambiguity (gate):** The classification gate fails when the best match is a poor match, or when its gap to the best reference of another spectral class is below the gap limit. A tie between neighboring subtypes alone does not fail the gate, and the number of such ties is reported. <!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.run_gates.spectroscopy_run_gates -->
* **Catalog Agreement (gate):** Fails when a measured spectral type is more than a set number of subtype steps from the type a catalog lists for the star (Appendix A).
* **Feature Significance (gate):** Fails when a star's line-feature probabilities could not be calibrated against stretches of its own spectrum used as a noise reference.
* **Resolution Measured (gate):** Not checked when no spectrum had its resolution measured.
* **Processing Quality (gate):** Fails when more than half of the classified spectra fail a metric of the classification-and-features checkpoint.
* **Extinction Record (stored with each spectrum, not a gate):** Each spectrum carries a record saying whether the airmass correction was applied, the two airmasses, the curve used, and the reason if it was skipped. When it was skipped, a tilt of roughly the size of the gap between neighboring spectral types can remain, so a classification made without the correction is less certain. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.atmospheric_extinction.ExtinctionCorrection -->

The wavelength-calibration fit of Section 6.2, concept 3, is not a run gate. It runs in the separate calibration step on a reference star. When fewer than three absorption dips are found, the step first relaxes its dip-detection depth and then flattens the continuum before searching again. If the best group of dips still leaves an RMS error above a coarse tolerance, the fit is refused with an error. <!-- impl: astrometricslib.pipelines.spectroscopy.utilities.calibration_tuner.SpectroscopyCalibrationTuner._detect_absorption_dips -->

---

## 7. Moving Object Detection Pipeline

The moving object detection pipeline finds, tracks, and identifies solar-system objects — asteroids and comets — across a sequence of individual (unstacked) photos. Stars stay fixed against each other, while an asteroid shifts a little from one photo to the next.

### 7.1 Purpose & Interfaces

* **Inputs:** A sequence of unstacked light frames, their observation timestamps $t_i$, and a WCS for each frame. A raw frame usually has no solved WCS of its own, so the pipeline estimates one from the mount's reported pointing and then shifts it so the frame's stars line up with the stack's stars. <!-- impl: astrometricslib.pipelines.asteroid_detection.frame_wcs_composer.estimate_frame_wcs_from_mount_pointing --> <!-- impl: astrometricslib.pipelines.asteroid_detection.pipeline._estimate_bulk_pointing_correction_deg -->
* **Outputs:** Straight-line motion tracks, each candidate's velocity $(\dot{\alpha}, \dot{\delta})$, ephemeris cross-matches (matches against predicted positions of known objects), and an Asteroid Recovery Quality Record.

### 7.2 Major Concepts and Governing Equations

Stacking averages moving objects away, since they land in a different spot in every frame. Detection has to work on individual, unstacked photos instead, in five steps:

1. **Single-Frame Detection:** Locates every bright point source in each raw exposure, with the same star finder the astrometry pipeline uses. After the pointing correction, the scatter that remains between a frame's stars and the stack's stars, in arcseconds, is that frame's position error: how far a measured position may sit from the truth. It is much larger than the error of a plate solution, because the frame's sky position comes from the mount's pointing shifted onto the stack, not from a solve of its own. A faint dot's center is less certain than a bright star's, so a further centroid error is added in quadrature (the square root of the sum of squares). <!-- impl: astrometricslib.pipelines.asteroid_detection.pipeline._detect_sources_in_one_frame -->
2. **Chaining Within One Observing Night:** Detections are grouped by observing night, and the dots in each night are linked on their own. A track may take a new dot only if the dot lies within a match radius of the track's last dot. The radius is the fastest plausible rate times the time elapsed, capped at a few arcminutes (an arcminute is 1/60 of a degree, or 60 arcseconds). Without the cap, a gap of hours or weeks grows the radius to a whole field of view, and two unrelated stars seen far apart in time link into a false mover. Each dot goes to the nearest open track. A chain needs detections in at least $M \ge 3$ frames, which eliminates single-frame cosmic ray hits. A mover seen on two nights comes out as two separate candidates, because joining them needs an orbit-style fit over days. Right Ascension wraps from 360° to 0°, so every difference in it is measured the short way around the circle. <!-- impl: astrometricslib.pipelines.asteroid_detection.detection.MovingObjectDetector._chain_detections_by_persistence --> <!-- impl: astrometricslib.utilities.observing_night.observing_night_id --> <!-- impl: astrometricslib.pipelines.shared.angles.wrapped_ra_difference_deg -->
3. **Filtering Out Obvious Non-Movers:** Discards any chain whose position spread is implausibly small, either in pixels (a stuck hot pixel) or in sky coordinates (a stationary star an earlier step missed). This filtering requires no external star catalog. <!-- impl: astrometricslib.pipelines.asteroid_detection.detection.MovingObjectDetector._evaluate_reference_frame_test -->
4. **Fitting a Straight-Line Path and Judging It by Its Residuals:** Over a few hours, an asteroid moves across the sky at a nearly constant speed, so its position against time is a straight line whose slope is its rate. The pipeline independently fits each surviving chain's Right Ascension offset ($\Delta\alpha_m\cos\delta$) and Declination offset ($\Delta\delta_m$) against timestamps $t_m$ via ordinary least-squares regression (choosing the line that minimizes the sum of squared misses). The factor $\cos\delta$ turns a change in Right Ascension into a true angle on the sky, since lines of Right Ascension close together toward the poles:

$$
\Delta\alpha_m \cos\delta = \dot{\alpha}\,t_m + c_\alpha, \qquad \Delta\delta_m = \dot{\delta}\,t_m + c_\delta \tag{13}
$$

   The track is judged by how far its dots miss the fitted line, in arcseconds, compared with the position error of one picture. Four conditions must all hold:
   * *Rate.* The total rate lies inside the allowed range.
   * *Residuals.* On each axis, the RMS distance of the dots from the line is at most a small multiple of the position error.
   * *Displacement.* The fitted line carries the object, between the first and last picture, a few times farther than the position error. Without this, a star whose measured position only jitters would pass, because a flat scatter fits a line with small residuals.
   * *Direction.* Projected onto the direction of the fitted motion, the dots never move backward by more than the combined position error of the two dots compared (the square root of the sum of their squared errors). A real mover only goes forward. This catches a star whose measured center flips between two points, which fits a line well but alternates along it.

   The coefficient of determination, $R^2$ (the share of the position change that the line explains), is still computed and stored. It decides nothing, because it is close to 1 for any chain with a large displacement, even when the dots miss the line by many arcseconds. <!-- impl: astrometricslib.pipelines.asteroid_detection.detection.MovingObjectDetector._fit_rate_linearity --> <!-- impl: astrometricslib.pipelines.asteroid_detection.detection._reverses_direction -->
5. **Checking Against Known Objects:** Matches each surviving track against SkyBoT [9], a solar-system ephemeris service (it predicts where known objects are at a given time), by asking which known minor planets lie near the track's first detection at that detection's time, and again near its last detection at that detection's time. A known object matches only if it lies within a fixed radius of the track at both moments.

   A match confirms the object's identity. A confirmed mover with no match is a track that is not identified as a known body. That is worth a manual look, and it is not by itself a discovery. The matcher counts its queries and its failed queries, so a failed lookup is never mistaken for a field with no known asteroids. <!-- impl: astrometricslib.pipelines.asteroid_detection.ephemeris.EphemerisCrossMatcher.cross_match_candidates -->

Two further tools exist for a confirmed track, but no pipeline step calls either of them yet, so neither runs during a search:

* *Minor Planet Center (MPC) report.* The MPC is the international clearing house for asteroid and comet positions. A track can be written as MPC 80-column observation lines (the classic fixed-width format) or as ADES text (the newer Astrometry Data Exchange Standard): designation, UTC date to 1e-5 day, position, and observatory code. The time written is the middle of the exposure. The detections carry an instrumental brightness, not a calibrated magnitude, so the magnitude column is left blank. Nothing submits a report. <!-- impl: astrometricslib.pipelines.asteroid_detection.mpc_report.mpc_lines_for_candidate -->
* *Rotation period.* Many asteroids tumble, so their brightness rises and falls as they spin. The track's brightness over time can be fed to the same period search the photometry pipeline uses (Section 5.2, concept 8). <!-- impl: astrometricslib.pipelines.asteroid_detection.rotation_period.find_rotation_period --> <!-- impl: astrometricslib.pipelines.asteroid_detection.mpc_report.mpc_lines_for_candidate -->

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
* **Single-Frame Detection Threshold:** Point-source detection uses a fixed multiple of the background noise level, so a candidate must be bright enough to appear on a single unstacked photo. Detected sources are also screened by sharpness and roundness on the star finder's scale (about −1 to 1, 0 for round), not the 0 to 1 roundness of stacking.
* **Enough Frames:** The search is not checked when fewer frames could be searched than a track needs, because no mover could have been found.
* **Pointing Metadata:** A frame that lacks pointing or image-size entries in its header cannot be given a WCS and fails the check.

#### 7.4.2 After Fitting: Checking the Track
* **Straight-Line Residuals:** Each track's RMS residual is compared with the position error of its pictures. When a picture has no measured position error, an assumed value stands in, the track is marked as using it, and the check fails so the result is not taken at face value.
* **Ephemeris Cross-Match:** A track is accepted as a SkyBoT match only if a known object lies within a fixed angular radius at both its first and last detection. The check fails when the database could not be reached.
* **Unmatched Movers:** A mover that moved in a straight line but matched no known asteroid fails this check. The failure is not a verdict on the data. It flags the run so a person looks at the mover, which may be an unknown object or a false track such as a satellite, a hot pixel, or an artifact.

:::{warning}
**What the detector can and cannot find.** A mover has to show on single, unstacked frames. The faintest mover found on synthetic fields sits well above the single-frame detection threshold, and the reason for that gap has not been measured. A slow mover is not found at any brightness: when its whole motion over the sequence keeps every dot within the stationary tolerance of the track's mean position, it is rejected as a star, so a short sequence cannot find slow movers. Recovery by brightness and rate is partly measured on synthetic fields (see [MOV-01](validation/asteroid_detection.md#mov-01) and [MOV-02](validation/asteroid_detection.md#mov-02)), and so is the false-track rate of the residual and direction tests (see [MOV-03](validation/asteroid_detection.md#mov-03)). The synthetic frames share one pointing, so they test detection and chaining, not the position error of real frames. On real fields the false-track rate is not measured (see [MOV-04](validation/asteroid_detection.md#mov-04)). <!-- impl: astrometricslib.scripts.validate_asteroid_detection -->
:::

---

## 8. Validation

Every tuned value in this document carries one of three labels. *Measured* means a measurement on real data chose or confirmed it. *Partly measured* means the measurement is incomplete: synthetic data only, one target, or a small sample. *Design estimate* means the value was chosen by reasoning and has not been measured. Appendix A gives the label of each value.

The evidence behind the labels is kept out of this paper, in a separate validation register ([validation/index.md](validation/index.md)), one file per pipeline. Each entry has an ID, such as STK-24. It gives the library calls that were made, the kind of data the check needs, the result on the observatory's data, and which setting to change for another telescope or camera. A reviewer can therefore judge each number and repeat the check on their own frames, since the observatory's frames are not published.

### 8.1 How the Pipelines Were Checked

Three kinds of evidence were used:

* **Synthetic data with known truth.** Frames or light curves are generated with a known answer, such as an injected dip or a moving dot, and the pipeline is asked to find it. This tests the method on noise that is fully understood. It cannot show how the method behaves on real noise.
* **Real sessions.** Frames from the observatory's own library, taken mostly with a ZWO ASI 533MM Pro monochrome camera ($3.76\,\mu\text{m}$ pixels). Some stacking checks also use a Nikon D5300 colour camera.
* **Library-wide surveys.** One measurement repeated over every stored stack or light curve, such as the share of blank pixels in every stack or the variability flag over every star.

Table 8 lists what each register file covers.

**Table 8.** Validation register files and what they cover.

| Pipeline | Register file | What was checked |
|---|---|---|
| Stacking | [validation/stacking.md](validation/stacking.md) | Frame screening and quarantine, calibration masters, pixel rejection, exposure groups and their brightness scale, the after-stacking checks, colour calibration and the preview stretch |
| Astrometry | [validation/astrometry.md](validation/astrometry.md) | Solver residuals against an independent Gaia check, the Gaia refit, the solution checks, star detection, catalog matching radii and the Gaia service limits |
| Photometry | [validation/photometry.md](validation/photometry.md) | The comparison ensemble, the error model and camera noise, frame alignment, the variability indices and cutoffs, the period search and cross-session matching |
| Spectroscopy | [validation/spectroscopy.md](validation/spectroscopy.md) | Sky subtraction, the reading box, resolution and errors, wavelength calibration, the instrument response, classification limits and the Gaia XP comparison |
| Moving objects | [validation/asteroid_detection.md](validation/asteroid_detection.md) | Recovery of injected movers, false tracks, real fields far from the ecliptic, the pointing correction and the chaining step |

### 8.2 The First Validation Campaign

The first campaign ran eight real observing sessions through the pipelines named for each, and ran each result through its pipeline's own after-the-fact quality check (Table 9). A session here means one target's data set, which for M 81 later grew to several nights. One further target, M 45 (the Pleiades), was plate-solved as an extra astrometry check; its frame count is not recorded.

**Table 9.** Sessions of the first validation campaign.

| Session | Field | Light frames; total exposure | Pipelines | Register entries |
|---|---|---|---|---|
| Vega | Bright standard star, grating | 160; 270 s | Spectroscopy | [SPE-12](validation/spectroscopy.md#spe-12) |
| M 13 | Dense star cluster, grating | 86; 2,520 s | Spectroscopy, stacking | [SPE-29](validation/spectroscopy.md#spe-29) |
| Alcor | Double star, grating | 138; 1,080 s | Spectroscopy | — |
| NGC 2244 | Open star cluster, time series | 29; 10,800 s | Photometry | [PHO-13](validation/photometry.md#pho-13) |
| M 81 | Spiral galaxy | 46; 7,230 s at first, later 258 frames in 8 sessions (2023-05 to 2026-05) | Photometry, stacking, moving objects | [STK-24](validation/stacking.md#stk-24), [STK-25](validation/stacking.md#stk-25), [PHO-14](validation/photometry.md#pho-14), [MOV-07](validation/asteroid_detection.md#mov-07) |
| NGC 2903 | Galaxy, time series | 36; 14,400 s | Photometry | [PHO-14](validation/photometry.md#pho-14) (single-session baseline) |
| NGC 2403 | Wide galaxy field | 70; 13,740 s | Stacking, astrometry, moving objects | [STK-24](validation/stacking.md#stk-24), [STK-25](validation/stacking.md#stk-25), [MOV-04](validation/asteroid_detection.md#mov-04), [MOV-05](validation/asteroid_detection.md#mov-05) |
| NGC 1893 | Open cluster | 49; 7,800 s | Stacking, astrometry | [AST-14](validation/astrometry.md#ast-14) |

:::{warning}
**The first campaign predates the current algorithms.** Its results were measured with earlier versions of the stacking rejection cutoffs and sharpness check, the plate-solve gates, the comparison ensemble and variability flag, the spectrum extraction, and the track tests. The plate solves in particular predate the matched-star minimum and the residual check of Section 4.4.2. Each register entry says whether its result has been repeated with the current algorithms. Treat a result that has not been repeated as evidence about the data and the shape of the method, not as current performance.
:::

### 8.3 Future Recommended Target Additions

The following target types are recommended for future testing:

1. **Eclipsing Binary or Exoplanet Transit Target (Photometry):** Observations of a star that dims on a known period, to test the pipeline's ability to detect a periodic dimming pattern and measure its depth on real data. The period search is so far checked only on synthetic light curves ([PHO-12](validation/photometry.md#pho-12)). An eclipsing binary (two stars orbiting each other, each passing in front of the other) such as *Algol / $\beta$ Persei* dims by more than a magnitude in each eclipse and tests the period search on a large, sharp dip. A star with a transiting planet such as *WASP-12* (host of the planet WASP-12b) dims by only about $\Delta m \approx 0.015\text{ mag}$ (about 1.4%) and tests the faint end.
2. **Emission-Line Nebula Target (Spectroscopy):** A planetary nebula (e.g., *Ring Nebula / M 57* or *Dumbbell Nebula / M 27*) to test wavelength calibration against bright emission lines — $[\mathrm{OIII}]$ ($500.7\text{ nm}$; doubly ionized oxygen, where the brackets mark a "forbidden" line seen only in very thin gas) and $\mathrm{H}\alpha$ ($656.3\text{ nm}$) — alongside Vega's absorption-line spectrum. The library already holds grating stacks of both nebulae, but the wavelength calibration uses only absorption lines and has not been tested on their emission lines.
3. **Asteroid Field Near the Ecliptic (Moving Object Detection):** A field along the ecliptic (the sky path the planets and most asteroids follow) containing known, cataloged asteroids, to test matching against an official minor-planet catalog alongside SkyBoT. The real fields checked so far lie far from the ecliptic, where almost no known asteroid is expected ([MOV-04](validation/asteroid_detection.md#mov-04)).

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

This appendix lists the tuned values the pipelines use and gives each one a status label. *Measured* means a measurement on real data chose or confirmed the value. *Partly measured* means the measurement is incomplete, for example synthetic data only or a single target. *Design estimate* means the value is a reasoned choice that has not been measured. Each label links to the validation register (Section 8): to the entry that holds the evidence, or to the pipeline's table of design estimates, which says how the value could be measured. Some values are configuration settings and others are fixed in the code; the register entry says which.

**Table A1.** Design parameters and their validation status.

| Pipeline | Parameter | Default | Status |
|---|---|---|---|
| Stacking | Floor on the high rejection cutoff | 2.5 $\sigma$ | Partly measured ([STK-08](validation/stacking.md#stk-08)); simulation only |
| Stacking | Margin of the low cutoff above the high one | 0.5 $\sigma$ | Partly measured ([STK-08](validation/stacking.md#stk-08)); simulation only |
| Stacking | Minimum frames per master calibration frame | 3 | Design estimate ([stacking table](validation/stacking.md#design-estimates)) |
| Stacking | Frame screening: session gap | 4 h | Design estimate ([stacking table](validation/stacking.md#design-estimates)) |
| Stacking | Frame screening: fewest frames to judge | 8 | Partly measured ([STK-01](validation/stacking.md#stk-01)) |
| Stacking | Frame screening: star count for a clouded frame; roundness for a trailed frame (0 to 1 scale) | below 0.5 of the session median; below 0.80 and at least 0.15 under the session median | Partly measured ([STK-01](validation/stacking.md#stk-01)) |
| Stacking | Frame screening: roundness drop for a colour camera | at least 0.20 under the session median | Measured ([STK-02](validation/stacking.md#stk-02)) |
| Stacking | Largest share of a session moved aside | 25% | Partly measured ([STK-03](validation/stacking.md#stk-03)) |
| Stacking | Dark temperature slot width | 3 °C | Partly measured ([STK-06](validation/stacking.md#stk-06)) |
| Stacking | Minimum frames kept after quality filtering | 5 | Partly measured ([STK-10](validation/stacking.md#stk-10)) |
| Stacking | Brightness scale: fewest shared stars before the pixel fallback | 16 | Measured ([STK-19](validation/stacking.md#stk-19)) |
| Stacking | Brightness scale, pixel fallback: pixels between percentiles; bright-end fraction | 20th to 80th; 1% of pixels | Design estimate ([stacking table](validation/stacking.md#design-estimates)) |
| Stacking | Linearity tolerance between exposure groups | 5% | Partly measured ([STK-20](validation/stacking.md#stk-20)) |
| Stacking | Brightest sky allowed for an exposure group, relative to the reference | 3 times | Partly measured ([STK-21](validation/stacking.md#stk-21)) |
| Stacking | Noise penalty of equal weights above which frames are noise-weighted | 1.05 | Design estimate ([stacking table](validation/stacking.md#design-estimates)); the benefit of weighting is in [STK-07](validation/stacking.md#stk-07) |
| Stacking | Share of spectral frames that must register before the alignment fallback | 0.9 | Design estimate ([stacking table](validation/stacking.md#design-estimates)); registration counts are in [STK-12](validation/stacking.md#stk-12) |
| Stacking | Rejected pixel share that flags a stack | 15% | Partly measured ([STK-24](validation/stacking.md#stk-24)) |
| Stacking | Star sharpness warning ratio (fails above) | 1.2 | Partly measured ([STK-25](validation/stacking.md#stk-25)) |
| Astrometry | Star detection threshold; sharpness and roundness ranges | 5 $\sigma$; 0.2 to 1.0; $\pm$0.6 | Design estimate ([astrometry table](validation/astrometry.md#design-estimates)) |
| Astrometry | Minimum stars to attempt a solve | 4 | Design estimate ([astrometry table](validation/astrometry.md#design-estimates)) |
| Astrometry | Minimum stars for a local solve | 10 | Partly measured ([AST-13](validation/astrometry.md#ast-13)) |
| Astrometry | Pixel-scale window; further widening by the solver | $\pm$5%; 20% | Design estimate ([astrometry table](validation/astrometry.md#design-estimates)) |
| Astrometry | Brightest stars sent to the online solver | 100 | Design estimate ([astrometry table](validation/astrometry.md#design-estimates)) |
| Astrometry | Gaia refit: fewest matched stars | 40 | Design estimate ([astrometry table](validation/astrometry.md#design-estimates)) |
| Astrometry | Gaia refit: largest allowed shift of the field | 30 arcsec | Partly measured ([AST-02](validation/astrometry.md#ast-02)) |
| Astrometry | Gaia refit: first and later match radius; outlier clip; most passes; distortion order | 4 px and 1.5 px; 3 $\sigma$; 5; second-order SIP | Partly measured ([AST-02](validation/astrometry.md#ast-02)); used, not varied |
| Astrometry | Gaia query time limits; depth steps on a time-out | 45 s and 60 s for the two services; 2 steps of 1 mag | Partly measured ([AST-08](validation/astrometry.md#ast-08)) |
| Astrometry | Gaia search depth: margin; limits; default | 1.0 mag; G 14 to 20; G 18 | Design estimate ([astrometry table](validation/astrometry.md#design-estimates)) |
| Astrometry | Catalog match radius | 10 arcsec | Design estimate ([astrometry table](validation/astrometry.md#design-estimates)) |
| Astrometry | Same-star position tolerance between catalogs | 2 arcsec | Measured ([AST-11](validation/astrometry.md#ast-11)) |
| Astrometry | Radius for unresolved catalog companions | 3 arcsec | Partly measured ([AST-10](validation/astrometry.md#ast-10)) |
| Astrometry | Variable-star catalog match radius | 3 arcsec | Partly measured ([AST-12](validation/astrometry.md#ast-12)) |
| Astrometry | Centre star of an unsolved spectral frame: pointing search radius; search area; faintest peak | 5 arcmin; 0.10 of the shorter side; 0.10 of the brightest pixel | Partly measured ([AST-09](validation/astrometry.md#ast-09)) |
| Astrometry | Minimum catalog-matched stars | 20 | Partly measured ([AST-05](validation/astrometry.md#ast-05)) |
| Astrometry | Residual limit | 0.5 of a star width | Partly measured ([AST-04](validation/astrometry.md#ast-04)) |
| Astrometry | Failed catalog lookups allowed | under 50% | Design estimate ([astrometry table](validation/astrometry.md#design-estimates)) |
| Photometry | Aperture radius; sky ring radii | 4 px; 7 to 12 px | Design estimate ([photometry table](validation/photometry.md#design-estimates)) |
| Photometry | Re-centering box; refinement passes; largest allowed move | 11 px; 3; 1.5 px | Design estimate ([photometry table](validation/photometry.md#design-estimates)) |
| Photometry | Comparison band: brightest skipped; band ends at | 2%; 30% of candidates | Design estimate ([photometry table](validation/photometry.md#design-estimates)) |
| Photometry | Candidates vetted; minimum and cap of comparison stars | 100; 5 and 20 | Design estimate ([photometry table](validation/photometry.md#design-estimates)) |
| Photometry | Vetting consistency limit; share of typical stars for a usable frame | 3 $\sigma$; one half | Design estimate ([photometry table](validation/photometry.md#design-estimates)) |
| Photometry | Frame outlier clip; per-star outlier clip | 3 $\sigma$ (at least 5 frames); 5 $\sigma$ | Design estimate ([photometry table](validation/photometry.md#design-estimates)) |
| Photometry | Share of frames rejected before the run is flagged | 25% | Design estimate ([photometry table](validation/photometry.md#design-estimates)) |
| Photometry | Noise model: minimum stars; stars per bin; most bins | 20; 10; 10 | Design estimate ([photometry table](validation/photometry.md#design-estimates)) |
| Photometry | Thresholds: excess scatter; chi-square and Stetson J | 1.5; field median + 2.326 robust $\sigma$ | Partly measured ([PHO-06](validation/photometry.md#pho-06)); synthetic fields only |
| Photometry | Small-field cutoff on the coefficient of variation | median + 7.4 MAD (unscaled; about 5 robust $\sigma$, since 7.4 / 1.4826 = 5.0), never below 0.02 | Measured ([PHO-10](validation/photometry.md#pho-10)) |
| Photometry | Period verdicts: false-alarm limit for "possible"; for "detected"; shuffles | 0.05; 0.01 with at least 3 cycles; 300 | Partly measured ([PHO-12](validation/photometry.md#pho-12)); synthetic only |
| Photometry | Between-session flag: significance; amplitude | above 3; at least 0.02 mag | Design estimate ([photometry table](validation/photometry.md#design-estimates)) |
| Photometry | Discrimination AUC floor | 0.7 | Design estimate ([photometry table](validation/photometry.md#design-estimates)) |
| Photometry | Largest acceptable value of the smallest detectable change (peak to peak) | 0.3 mag | Design estimate ([photometry table](validation/photometry.md#design-estimates)); the smallest change found on a real field is in [PHO-09](validation/photometry.md#pho-09) |
| Spectroscopy | Reading box half-width; width smoothing | 2.5 times the trail width; 61 steps | Design estimate ([spectroscopy table](validation/spectroscopy.md#design-estimates)); the effect of smoothing is in [SPE-04](validation/spectroscopy.md#spe-04) |
| Spectroscopy | Minimum signal-to-noise for analysing a spectrum | 1.5 | Partly measured ([SPE-10](validation/spectroscopy.md#spe-10)) |
| Spectroscopy | Line significance for the wavelength zero point | 6 | Partly measured ([SPE-11](validation/spectroscopy.md#spe-11)); synthetic only |
| Spectroscopy | Sky strip gap and width | 6 px; 10 px | Partly measured ([SPE-01](validation/spectroscopy.md#spe-01)) |
| Spectroscopy | Sky contamination limit | 3 $\sigma$ | Partly measured ([SPE-03](validation/spectroscopy.md#spe-03)); simulation only |
| Spectroscopy | Fewest pixels in a sky strip | 4 | Design estimate ([spectroscopy table](validation/spectroscopy.md#design-estimates)) |
| Spectroscopy | Poor-match limit | 0.15 relative RMS | Partly measured ([SPE-16](validation/spectroscopy.md#spe-16)) |
| Spectroscopy | No-type limit | 0.45 relative RMS | Partly measured ([SPE-17](validation/spectroscopy.md#spe-17)) |
| Spectroscopy | Ambiguity gap | 0.02 relative RMS | Measured ([SPE-18](validation/spectroscopy.md#spe-18)) |
| Spectroscopy | Catalog disagreement | 20 subtype steps (two whole letter classes) | Partly measured ([SPE-19](validation/spectroscopy.md#spe-19)) |
| Spectroscopy | Wavelength-fit rejection tolerance | 10 nm RMS | Design estimate ([spectroscopy table](validation/spectroscopy.md#design-estimates)) |
| Spectroscopy | Wavelength-scale limit (scatter of the corrected offsets) | 11 Å (one pixel of dispersion) | Design estimate ([spectroscopy table](validation/spectroscopy.md#design-estimates)) |
| Spectroscopy | Gaia XP agreement: largest median tilt | 3% per 1000 Å | Design estimate ([spectroscopy table](validation/spectroscopy.md#design-estimates)) |
| Spectroscopy | Processing quality: largest share of spectra failing a metric | 50% | Design estimate ([spectroscopy table](validation/spectroscopy.md#design-estimates)) |
| Moving objects | Frames needed for a track | 3 | Design estimate ([asteroid detection table](validation/asteroid_detection.md#design-estimates)) |
| Moving objects | Rate range; chaining radius cap | 1 to 300 arcsec/h; 300 arcsec | Design estimate ([asteroid detection table](validation/asteroid_detection.md#design-estimates)) |
| Moving objects | Stationary tolerances | 1.5 px; 3 arcsec | Design estimate ([asteroid detection table](validation/asteroid_detection.md#design-estimates)); their effect on slow movers is in [MOV-02](validation/asteroid_detection.md#mov-02) |
| Moving objects | Residual multiple; displacement multiple | 2; 3 times the position error | Partly measured ([MOV-03](validation/asteroid_detection.md#mov-03)); synthetic only |
| Moving objects | Assumed position error; centroid error | 10 arcsec; 0.5 px | Design estimate ([asteroid detection table](validation/asteroid_detection.md#design-estimates)) |
| Moving objects | Ephemeris match radius | 10 arcsec | Design estimate ([asteroid detection table](validation/asteroid_detection.md#design-estimates)); close to a single frame's position error ([MOV-06](validation/asteroid_detection.md#mov-06)) |
| Moving objects | Detection threshold; detection width | 5 $\sigma$; 4 px | Partly measured ([MOV-01](validation/asteroid_detection.md#mov-01)); synthetic only |

<!-- impl: astrometricslib.utilities.rejection_thresholds.rejection_bounds -->
<!-- impl: astrometricslib.drivers.calibration_library.DEFAULT_DARK_TEMPERATURE_TOLERANCE_C -->
<!-- impl: astrometricslib.utilities.stack_filter_floor.DEFAULT_MINIMUM_SURVIVING_FRAMES -->
<!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.GAIN_MID_RANGE_PERCENTILES -->
<!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.GAIN_BRIGHTEST_FRACTION -->
<!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.DEFAULT_GAIN_DISAGREEMENT_TOLERANCE -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_quality.DEFAULT_FWHM_DEGRADATION_RATIO -->
<!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection.SourceDetector -->
<!-- impl: astrometricslib.drivers.astrometry_net_driver.MINIMUM_SOURCES_FOR_LOCAL_SOLVE -->
<!-- impl: astrometricslib.pipelines.shared.catalog_star_identity.CATALOG_MATCH_RADIUS_ARCSEC -->
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
<!-- impl: astrometricslib.pipelines.spectroscopy.processing.spectrum_signal.MINIMUM_SPECTRUM_SIGNAL_TO_NOISE -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.wavelength_zero_point.MINIMUM_LINE_SIGNIFICANCE -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.SKY_BAND_GAP_PX -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.SKY_BAND_WIDTH_PX -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.SKY_CONTAMINATION_SIGMA -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.SKY_BAND_MINIMUM_SAMPLE_COUNT -->
<!-- impl: astrometricslib.models.stellar_source.NO_GOOD_MATCH_RMS -->
<!-- impl: astrometricslib.models.stellar_source.UNRELIABLE_MATCH_RMS -->
<!-- impl: astrometricslib.models.stellar_source.AMBIGUOUS_RMS_GAP -->
<!-- impl: astrometricslib.models.stellar_source.DIFFERS_FROM_CATALOG_SUBTYPES -->
<!-- impl: astrometricslib.models.moving_object_config.MovingObjectConfig -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.SESSION_GAP_SECONDS -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.MINIMUM_FRAMES_TO_JUDGE -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.CLOUD_STAR_FRACTION -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.TRAILED_ROUNDNESS_LIMIT -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.ROUNDNESS_DROP_BELOW_MEDIAN -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.MAXIMUM_QUARANTINED_FRACTION -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.exposure_weighting.MINIMUM_PENALTY_TO_WEIGHT -->
<!-- impl: astrometricslib.pipelines.stacking.stack_runner.MINIMUM_REGISTERED_FRACTION -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_quality.DEFAULT_REJECTED_FRACTION_FLAG_THRESHOLD -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.MAXIMUM_PLATE_SOLVE_SOURCES -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.MINIMUM_MATCHED_STARS -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.MAXIMUM_SHIFT_ARCSEC -->
<!-- impl: astrometricslib.pipelines.photometry.post_processing.run_gates.MINIMUM_ENSEMBLE_REJECTION_FRACTION_TO_FLAG -->
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.DEFAULT_VARIABILITY_SIGMA_THRESHOLD -->
<!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search.FALSE_ALARM_FOR_DETECTION -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.COLOUR_CAMERA_ROUNDNESS_DROP_BELOW_MEDIAN -->
<!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.STAR_GAIN_MINIMUM_STARS -->
<!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.SKY_RATIO_LIMIT -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.INITIAL_MATCH_RADIUS_PIXELS -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.REFINED_MATCH_RADIUS_PIXELS -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.CLIP_SIGMAS -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.MAXIMUM_PASSES -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.SIP_DEGREE -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.GAIA_VIZIER_TIMEOUT_SECONDS -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.GAIA_ESA_TIMEOUT_SECONDS -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.GAIA_DEPTH_STEP_MAGNITUDES -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.GAIA_DEPTH_STEPS_DOWN -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.GAIA_MAGNITUDE_LIMIT_MARGIN -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.GAIA_MINIMUM_MAGNITUDE_LIMIT -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.GAIA_MAXIMUM_MAGNITUDE_LIMIT -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.GAIA_DEFAULT_MAGNITUDE_LIMIT -->
<!-- impl: astrometricslib.pipelines.shared.catalog_star_identity.SAME_STAR_POSITION_TOLERANCE_ARCSEC -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.UNRESOLVED_COMPANION_RADIUS_ARCSEC -->
<!-- impl: astrometricslib.pipelines.shared.variable_star_catalogs.MATCH_RADIUS_ARCSEC -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.POINTING_ERROR_SEARCH_RADIUS_ARCSEC -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.CENTRE_STAR_SEARCH_RADIUS_FRACTION -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.CENTRE_STAR_MINIMUM_PEAK_FRACTION -->
<!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search.verdict_from_false_alarm -->
<!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.wavelength_scale.WAVELENGTH_SCALE_LIMIT_ANGSTROM -->
<!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.compare_to_gaia_xp.GAIA_XP_SLOPE_LIMIT_PERCENT_PER_1000_ANGSTROM -->
<!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.run_gates.PROCESSING_QUALITY_MAXIMUM_FAILED_FRACTION -->

---

## Appendix B. Glossary

Terms used across the Astrometrics library documents, in alphabetical order.

| Term | Definition |
|---|---|
| Absorption line / emission line | A narrow dark dip (absorption) or bright peak (emission) in a spectrum at a wavelength set by one kind of atom. Cool gas in front of a hot source absorbs; hot thin gas emits. |
| ADU (analog-to-digital unit) | The unit of a raw pixel value as the camera stores it. The gain converts ADU to electrons. |
| Airmass | How much atmosphere the light passed through, relative to looking straight up. It is 1.0 overhead and about 2 at 30° above the horizon. |
| Alias | A false period that appears beside the real one because of the gaps in the sampling, for example from observing once a night. |
| Ångström (Å) | A unit of wavelength: $10^{-10}$ m, or 0.1 nm. Visible light runs from about 4000 Å (violet) to 7000 Å (red). |
| Annulus (sky) | A ring of pixels around a star's measuring circle. Its median sets the sky level that is subtracted from the star. |
| Aperture photometry | Measuring a star's brightness by adding up the pixel values inside a circle on the star and subtracting the sky. |
| Arcminute | An angle of 1/60 of a degree, or 60 arcseconds. |
| Arcsecond | An angle of 1/3600 of a degree. Star positions and star widths on the sky are usually given in arcseconds. |
| Astrometry | Measuring the positions of objects on the sky. In this library, it means finding the sky position of every pixel in an image. |
| AUC (area under the ROC curve) | A score from 0 to 1 for how well a number separates two groups. 1 means every member of one group scores above every member of the other; 0.5 is chance. |
| B, V, BP, RP, G bands / B−V | Standard photometric bands. B (blue, near 440 nm) and V (visual, green-yellow, near 550 nm) are the classic filter bands. G, BP (blue), and RP (red) are the Gaia satellite's own wide bands. B−V is a colour: the B magnitude minus the V magnitude, a difference of two magnitudes (so a ratio of fluxes). A larger B−V means a redder, cooler star. |
| Balmer lines | Absorption or emission lines of hydrogen at known wavelengths (Hβ 4861 Å, Hγ 4340 Å, Hδ 4102 Å; 486.1, 434.1, and 410.2 nm). They serve as a wavelength ruler. |
| Barycentric correction / BJD_TDB | Barycentric Julian Date in Barycentric Dynamical Time: the time the light would have reached the center of mass of the solar system. It removes the up to 8-minute shift caused by Earth's position in its orbit. |
| Benjamini–Hochberg / false discovery rate / q-value | A way to test many stars at once. The false discovery rate is the expected share of flagged stars that are false alarms. A star's q-value is the smallest false discovery rate at which it would still be flagged. |
| Bias frame | A closed-shutter exposure as short as the camera allows. It records the offset the camera electronics add to every pixel. |
| Binning | Adding neighboring pixels together on the sensor, so that a 2×2 bin makes one larger pixel from four. |
| Blend | Two or more stars so close in an image that their light overlaps and they are measured as one source. |
| Box least squares | A period search that fits a repeating flat-bottomed dip, the shape of an eclipse or a planet transit, to a light curve. |
| Camera profile | The stored facts about one camera model's sensor (clipping level, gain, read noise, quantum efficiency), each with a note saying where it came from. |
| CCD equation | The noise budget of a camera measurement: star shot noise, sky noise, read noise, and dark current noise, added in quadrature. |
| Centroid | The brightness-weighted average position of a star's pixels; the star's center. |
| Chauvenet's criterion | A rule for rejecting outliers: throw out a value when fewer than half a value that extreme is expected among $N$ samples from a normal distribution. |
| Chi-square (reduced) | The average squared distance of data points from a model, each in units of its own uncertainty. A value near 1 means the scatter matches the uncertainties; a much larger value means extra variation. |
| CMOS | The type of image sensor in most current astronomy cameras (complementary metal-oxide-semiconductor). Each pixel has its own amplifier, unlike the older CCD. |
| Coefficient of variation | The standard deviation divided by the mean: the scatter as a fraction of the average level. |
| Comparison ensemble | A group of steady stars in the same field, combined into one reference signal. Dividing a star's brightness by it cancels changes that every star shares, such as thin cloud. |
| Dark frame | A closed-shutter exposure with the same length and temperature as the light frames. It records dark current (signal made by heat in the sensor) and hot pixels. |
| Declination / Right Ascension | The sky's latitude and longitude. Declination (Dec, $\delta$) runs from −90° at the south celestial pole to +90° at the north. Right Ascension (RA, $\alpha$) runs around the sky from 0° to 360°, often written in hours (24 h = 360°). |
| Dereddening / reddening / extinction | Extinction is the dimming of starlight by air or dust. Dust dims blue light more than red, so it makes a star look redder (reddening). Dereddening removes that effect from a spectrum. |
| Differential photometry | Measuring a star's brightness relative to other stars in the same image, so that changes in the sky affect both and cancel. |
| Dispersion | Spreading light out by wavelength into a spectrum. The dispersion angle is the direction of that spread on the sensor. |
| E(B−V) (colour excess) | How much redder interstellar dust has made a star: its observed B−V minus the B−V it would have without dust, in magnitudes. |
| Empirical status labels | The three labels the documents use for where a tuned value came from, defined in the validation register (`validation/index.md`). *Measured* means a measurement on real data chose or confirmed it. *Partly measured* means the measurement is incomplete: synthetic data only, one target, or a small sample. *Design estimate* means a reasoned choice not yet measured; older wording such as *designed*, *design value*, *design choice* and *unvalidated* means the same. |
| Ephemeris | A table of where a solar-system object is predicted to be at given times. |
| Epoch | The date a catalog position refers to. Gaia positions are for epoch 2016.0 and are moved to the observation date using proper motion. |
| F_λ | Flux per unit wavelength: how much light energy arrives in each small slice of wavelength. |
| False-alarm probability | The chance that noise alone would give a result at least as strong as the one found. |
| Family-wise correction | A correction for running several tests at once, so that the chance of any false alarm across the whole set (the family) stays at the chosen level. The Šidák correction is one. |
| Filter | A coloured glass placed in front of the sensor that passes only one band of wavelengths. A luminance filter passes all visible light. |
| FITS / FITS header | The standard file format for astronomical images. The header is a block of named text entries (exposure time, filter, sky solution) stored before the pixel data. |
| Flat frame | An exposure of an evenly lit surface. It records how much each part of the image is dimmed by the optics, dust, and pixel-to-pixel differences. |
| Full well | The largest charge a pixel can hold. Near full well the pixel stops responding in proportion to the light (saturation). |
| FWHM (full width at half maximum) | The width of a star's image measured at half its peak brightness. Smaller means sharper. |
| Gaia / Gaia XP | Gaia is a European space telescope that measured precise positions, motions, and brightnesses for over a billion stars. Gaia XP are its low-resolution spectra, calibrated above the atmosphere. |
| Gain (e-/ADU) | The number of electrons that make one ADU, in electrons per ADU. It converts stored pixel values to collected light. Not the same as the gain setting. |
| Gain setting | The camera's amplifier setting: a plain number (no unit) recorded in the FITS header, such as 100. Raising it makes each electron count for more ADU, which lowers the gain in e-/ADU. On a DSLR camera the ISO setting plays this role. |
| Gate (quality gate) | A recorded pass, fail, or not-checked verdict on one quality condition of a pipeline run. |
| Golden test | A test that compares a program's output with a saved reference output and fails on any difference. |
| Header card | One named entry of a FITS header, such as EXPTIME = 300 (an 80-character line in the file). |
| Instrumental magnitude | A magnitude computed from the camera's own counts, $-2.5\log_{10}$ of the flux in ADU per second, with no magnitude zero point applied. It compares stars in one field but does not say how bright a star is in catalog terms. |
| Inverse-variance weighting | Averaging values with weights of one over each value's variance, so more precise values count for more. |
| Leave-one-out | Judging one member of a group against a result built from all the other members, so that the member cannot influence its own reference. |
| Light curve | A star's brightness plotted against time. |
| Light frame | An exposure of the sky target itself, as opposed to a calibration frame (bias, dark, or flat). |
| Line spread | How wide the instrument smears a single, infinitely narrow wavelength. It limits how fine a detail a spectrum can show. |
| Lomb–Scargle periodogram | A period search that fits sine waves of many periods to unevenly spaced measurements and reports how strongly each period fits. |
| Luminance frame | A light frame taken through a luminance (clear) filter, which passes all visible light. |
| MAD (median absolute deviation) | The median distance of values from their median. A spread measure that a few outliers cannot inflate. |
| Magnitude | A backwards, logarithmic brightness scale. Lower is brighter, and 5 magnitudes is a factor of 100 in brightness. It is always measured in a named photometric band, such as V. |
| Master frame | The average of many calibration frames of one kind (a master bias, master dark, or master flat), which has much less noise than any single frame. |
| Messier, NGC, and IC catalogues | Catalogues of galaxies, star clusters, and nebulae. Messier (M) numbers come from an 18th-century list of about 110 bright objects (M 13, M 81). NGC (New General Catalogue) and IC (Index Catalogue) are larger lists (NGC 2403, IC 1396). |
| MPC (Minor Planet Center) | The international clearing house that collects position reports of asteroids and comets. It defines the 80-column and ADES report formats. |
| Noise model / predicted noise | In photometry, the noise model is the field's measured scatter against brightness, which gives the scatter of a constant star at each brightness. The predicted noise is what the CCD equation gives for one measurement from its counts, sky, and read noise. |
| Observing session | Frames taken in one continuous run with the same setup. Each pipeline groups frames into sessions a little differently and says so where the term first appears (for example, photometry uses one night with one gain setting and offset). |
| Offset | A constant level the camera adds to every pixel so that noise below zero is still recorded instead of being cut off at zero. It is set on the camera; the bias frame records it. |
| Period verdict | The label a period search gives its best period, from lowest to highest: insufficient data (fewer than two cycles covered), not detected, possible, detected. A failed hold-out or alias check lowers it one level. Unconfirmed is a separate label meaning a check could not run. |
| Phase / folded light curve | For a trial period P, the phase of a measurement at time t is the fraction of a cycle elapsed, (t mod P)/P, from 0 to 1. A folded light curve plots brightness against phase, so all cycles lie on top of each other. |
| Phase correlation | A way to find the shift between two images by comparing their overall patterns in the frequency domain, with no star list needed. |
| Photometric band | A fixed range of wavelengths that a standard filter passes. A magnitude is always measured in a named band, such as V. |
| Plate scale | How much sky one pixel covers, in arcseconds per pixel. It follows from the pixel size and the telescope's focal length. |
| Plate solving | Finding exactly where an image points on the sky by matching its star patterns to a catalog. The result is a WCS. |
| Proper motion | The slow drift of a star across the sky, in arcseconds (or milliarcseconds) per year. |
| PSF (point spread function) | The shape a single point of light takes in the image, set by the optics and the air. A star's image is its PSF. |
| Quantum efficiency (QE) | The share of arriving photons a sensor turns into electrons, as a function of wavelength. |
| Ratchet test | A test that keeps an allowlist of known exceptions that may only shrink. A new exception, or a stale entry, fails it. |
| Read noise | Random noise added each time the sensor is read out, in electrons. It does not depend on exposure length. |
| Reference star | A star used as a reference; the documents say which kind. Alignment stars line frames up with each other. Comparison stars form the photometry ensemble. A spectroscopic standard star (Vega here) has a known spectrum and calibrates the instrument response. |
| Registration | Aligning images so that the same star falls on the same pixel in each one. |
| Rejection (pixel) / sigma clipping / Winsorized | Pixel rejection throws out values that disagree with the rest of a stack. Sigma clipping rejects values more than a set number of standard deviations from the center. A Winsorized version first pulls extreme values in to a limit, so they do not inflate the spread used for the cut. |
| Residual | The misfit left after fitting a model: a data point minus the model's value at that point. |
| RMS (root mean square) | The square root of the mean of the squared values. For residuals, a typical size of the misfit. |
| ROC curve (receiver operating characteristic) | A plot of the share of true cases a score flags against the share of false cases it flags, as the cutoff is swept from strict to loose. Its area is the AUC. |
| Roundness | How round a star image is. Two scales are used. Stacking uses the narrow width over the wide width, 0 to 1, where 1 is a circle. The DAOFIND star finder (astrometry, moving objects) uses a symmetry measure from about −1 to 1, where 0 is round. |
| Saturation | A pixel that reached its largest value. Its true brightness is unknown. |
| Šidák correction | A correction for running many tests. It tightens each test's false-alarm limit so that the chance of any false alarm in the whole set stays at the chosen level. |
| SIMBAD | An online database of named astronomical objects, run by the Strasbourg astronomical data center (CDS). |
| SIP (Simple Imaging Polynomial) | Extra polynomial terms in a WCS that describe lens or mirror distortion. |
| SkyBoT | An online service that lists which known asteroids and comets lie in a given patch of sky at a given time. |
| Slitless spectroscopy / zero order / first order | Spectroscopy without a slit: every star in the field makes its own spectrum. The zero order is the undispersed point image of a star, used as a fixed reference. The first order is the first and brightest spread-out copy of the star's light beside it, the spectrum the pipeline measures. |
| SNR (signal-to-noise ratio) | The signal divided by its noise. Higher means a cleaner measurement. |
| Spectral type / luminosity class | A star's spectral type is a letter and a number that sort stars by surface temperature: O, B, A, F, G, K, M from hottest to coolest, each split into ten subtypes 0 to 9 (A0, G2). Early types are the hot end (O, B, A), late types the cool end (K, M); "late K" means K5 to K9. A trailing Roman numeral is the luminosity class: V is a main-sequence (dwarf) star such as the Sun, which is burning hydrogen in its core; III is a giant. A0V is a hot main-sequence star like Vega. |
| Spectrum trail | The band of light a star's first-order spectrum makes on the image in slitless spectroscopy. Called the trail in the spectroscopy documents. |
| Stacking | Aligning and averaging many exposures into one image, which raises the signal-to-noise ratio and removes one-off artifacts. |
| Standard star | A star whose true spectrum or brightness is known well, observed to calibrate the instrument. Vega is the spectroscopic standard here. |
| Stetson J | A variability index that multiplies the deviations of neighboring points. Random noise keeps it near 0; a smooth, real change makes it positive. |
| Streak | A straight line of light in an image left by a satellite, meteor, or aircraft, or by a star smeared when the mount drifted (a trailed star). Not a spectrum trail. |
| Stretch | A brightness curve applied to a picture for display that lifts faint detail. Stacked data are linear and look almost black on screen until stretched. |
| Subtype | The number after the letter of a spectral type (the 2 in G2). One subtype is one step on the spectral-type ladder; ten steps make one letter class. |
| Tangent plane | A flat plane that touches the sky's sphere at the image center. Positions near the center are projected onto it so they can be measured as flat x, y offsets. |
| Telluric bands | Wavelength ranges where Earth's own atmosphere (oxygen, water) absorbs light. They are not part of the star's spectrum. |
| Transmission grating | A clear plate ruled with many fine parallel lines, placed in front of the camera, that spreads each star's light into a short spectrum. The Star Analyser 200 used here is one. Some code comments call it a grism, which strictly is a grating combined with a prism. |
| WCS (World Coordinate System) | The set of FITS header entries that convert a pixel position into a sky position, and back. |
| Zero point (magnitude / wavelength) | Two different offsets share this name. The magnitude zero point converts instrumental magnitudes to catalog magnitudes. The wavelength zero point is the shift of a spectrum's whole wavelength scale. |
