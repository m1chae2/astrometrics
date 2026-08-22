# the high-level interface Image Processing Pipeline: Architecture and Design

*Version 2.3 · 2026-08-16 · Status: current*

## Abstract

Amateur astronomy workflows have historically relied on fragmented standalone software, requiring observers to manually export files across separate tools for frame reduction, image stacking, plate solving, photometry, and spectroscopy. This paper presents an image processing architecture designed for small-aperture observatories and citizen science research. The architecture organizes all observational data around a central Observation Target data model, connecting five processing pipelines: stacking, astrometry, photometry, spectroscopy, and moving object detection. Each pipeline incorporates a dual-stage validation framework combining pre-processing input screening with post-processing quality metrics.

## 1. Introduction

**Statement of need.** Amateur astrophotography has evolved into a valuable platform for citizen science. Key applications include variable star monitoring [2], exoplanet transit timing, stellar spectroscopy, and minor planet tracking. Establishing a unified data model allows multiple astronomical pipelines astrometry, photometry, spectroscopy, and moving object detection to work together seamlessly around a single target region.

This document establishes the theoretical framework, physical derivations, and mathematical equations governing image processing across five core pipelines, alongside the implementation design, module mappings, and empirical benchmarks for each. Complete code-level API references for all public classes and methods are auto-generated directly from docstrings in the Sphinx {doc}`API Reference </api/astrometricslib>`.

Crucially, the system design adheres to a strict dual-interface paradigm. The theoretical framework described here serves as the absolute foundation for both headless programmatic execution and interactive graphical environments. Every interaction, visualization, and processing pipeline available to an end-user is a direct invocation of these underlying computational primitives, guaranteeing that any observational workflow performed interactively can be seamlessly transitioned into an automated, reproducible script.

Section 2 presents the observational data models. Sections 3 through 7 detail the five major processing pipelines: Stacking, Astrometry, Photometry, Spectroscopy, and Moving Object Detection.

## 2. Observational Data Models

The system organizes astronomical observations around four Observational Data Models, following the target-centric data organization established by large-scale survey data management practice [3]. These models define the fundamental types of information used in the system, sky positions, brightness measurements, light curves, spectra, and data quality checks around each target.

### 2.1 Information Types

1. **Observation Target:** Represents a specific sky region or target such as M 81 or Vega. It connects all raw images, calibration frames, solved sky positions, stacked images, detected stars, and moving objects for that target.
2. **Stellar Object:** Represents a star identified on the sky. It holds the star's sky position, brightness light curve, variability measure, and extracted spectrum.
3. **Moving Object Candidate:** Represents a moving solar-system object such as asteroids or comets. It records the object's path across images, calculated motion speed, trajectory error, and matching catalog identity.
4. **Pipeline Quality Record:** Tracks the quality and health of processing at each stage such as rejected image counts, image sharpness ratios, positioning accuracy, and measurement noise.

Table 1 summarizes the four observational data models, their scope, data attributes, and primary roles.

**Table 1.** Observational data models summary.

| Observational Data Model | Scope | Data Attributes | Primary Role |
| :--- | :--- | :--- | :--- |
| **Observation Target** | Field of View & Observation Area | Coordinates $(\alpha_0, \delta_0)$, raw exposures, calibration masters, stacked FITS products | Connects all images and analysis outputs for a sky region |
| **Stellar Object** | Point-Source Objects & Catalogs | Coordinates $(\alpha, \delta)$, magnitude $V$, light curves $\hat{F}(t)$, 1D spectra $F(\lambda)$ | Tracks star brightness histories, spectra, and variability |
| **Moving Object Candidate** | Solar System Transients | Detection chain $(x_i, y_i, t_i)$, motion rates $(\dot{\alpha}, \dot{\delta})$, ephemeris match | Records asteroid motion paths and catalog matches |
| **Pipeline Quality Record** | Quality Health Verification | Frame survivor counts $N$, rejection cutoff $\sigma(N)$, FWHM ratio, fit-confidence scores | Tracks processing quality and health flags across pipeline runs |

### 2.2 Data Model Progression Across Pipelines

Table 2 presents an overview matrix showing how information builds across the four observational data models at each processing stage, ordered top-to-bottom from raw image ingestion down to final measurements.

**Table 2.** Overview matrix of observational data models across pipeline execution levels.

| Pipeline | Observation Target | Stellar Object | Moving Object Candidate | Quality Record |
| :--- | :--- | :--- | :--- | :--- |
| **Stacking** | Raw photos and calibration frames | N/A | Single-frame star or satellite detections | Removed bad pixels and overall image sharpness |
| **Astrometry** | Matched sky coordinates and target center | Star locations linked to catalog positions | Sky positions for moving candidates | Solve success status and catalog match counts |
| **Photometry** | Chosen comparison stars | Star brightness changes and light curves | Brightness measurements over time | Ensemble outlier rejection and comparison-star composition |
| **Spectroscopy** | Reference star position and camera sensitivity | Rainbow color spectrums for stars | N/A | Zero-order saturation status and calibration fit convergence |
| **Moving Objects** | Self-consistency filtered candidates | Hidden stationary stars | Path direction, speed, and catalog names | Detection counts and per-axis linear-fit confidence ($R^2$) |

---

## 3. Image Stacking Pipeline

The stacking pipeline combines a series of individual exposures into a single high-signal master image while eliminating instrumental noise patterns and transient artifacts (satellite trails, cosmic rays).

### 3.1 Purpose & Interfaces

Defines operational boundaries, input data, and primary output artifacts of the stacking pipeline.

* **Inputs:** Raw light exposures, master calibration frames ($M_{\text{bias}}, M_{\text{dark}}, M_{\text{flat}}$).
* **Outputs:** Calibrated 2D stacked broadband or spectral FITS image array, updated FITS headers, and a Stack Quality Record.

### 3.2 Major Concepts and Governing Equations

Establishes the foundational physical concepts, governing parameters, and equations for instrumental calibration reduction and dynamic pixel outlier rejection.

Raw astronomical exposures contain additive electronic/thermal sensor noise and multiplicative optical distortions. Prior to frame alignment, individual light exposures $I_{\text{raw}}$ undergo instrumental reduction using three master calibration frame types:

* **Master Bias ($M_{\text{bias}}$):** Averaged zero-second closed-shutter exposures at operating temperature. Isolates the fixed electronic readout noise pedestal and Analog-to-Digital Converter (ADC) offset.
* **Master Dark ($M_{\text{dark}}$):** Averaged closed-shutter exposures matched in duration ($t_{\text{exp}}$), gain, and temperature to light frames. Quantifies thermal electron accumulation (dark current) and hot pixel defects for additive subtraction.
* **Master Flat ($M_{\text{flat}}$):** Averaged exposures of a uniform light source (twilight sky or flat panel). Measures spatial throughput variations, including optical vignetting, pixel response non-uniformity, and dust shadow artifacts.

Instrumental noise artifacts fall into two physical behavior categories:

* **Additive Artifacts (Subtracted):** Noise sources that add extra unwanted pixel counts (electrons) to the sensor regardless of incoming starlight.
  * *Electronic Bias ($M_{\text{bias}}$):* A constant electronic voltage offset added by sensor electronics to prevent negative pixel values during digital conversion.
  * *Thermal Dark Current ($M_{\text{dark}}$):* Extra thermal electrons generated inside the silicon sensor over time, increasing linearly with exposure duration.
* **Multiplicative Artifacts (Divided Out):** Factors that scale or block a percentage of incoming starlight before or across the sensor.
  * *Optical Vignetting & Dust Donuts ($M_{\text{flat}}$):* Obstructions in the telescope optical path (lens edges, dust specks on sensor glass) that block a percentage of starlight, scaling down local pixel sensitivity.
  * *Pixel Sensitivity Variations:* Microscopic differences in Quantum Efficiency (QE) between individual sensor pixels.

Instrumental reduction isolates true astronomical flux $I_{\text{calibrated}}$ by subtracting additive electronic and thermal pedestals, followed by dividing out normalized multiplicative optical throughput variations:

$$
I_{\text{calibrated}} = \frac{I_{\text{raw}} - M_{\text{dark}}}{\left(M_{\text{flat}} - M_{\text{bias}}\right) / \langle M_{\text{flat}} - M_{\text{bias}} \rangle} \tag{1}
$$

Following calibration reduction, frame combination averages $N$ exposures to increase target signal-to-noise ratio. Rather than using a fixed rejection cutoff (such as static $3\sigma$), the pipeline scales the rejection threshold dynamically with frame count $N$ via Chauvenet's criterion [4], [5]:

$$
\sigma(N) = \sqrt{2}\,\mathrm{erfc}^{-1}\!\left(\frac{1}{2N}\right) \tag{2}
$$

Where the mathematical terms represent:
* **$\sigma(N)$ (Dynamic Rejection Cutoff):** The threshold in standard deviations (e.g., $1.96\sigma$, $2.5\sigma$, $2.8\sigma$). Pixels deviating further than $\sigma(N)$ from the group median are clipped as outliers.
* **$N$ (Surviving Frame Count):** The number of light exposures being combined into the stack.
* **$\mathrm{erfc}^{-1}$ (Inverse Complementary Error Function):** A standard statistical probability function that calculates the exact boundary where fewer than $0.5$ false-positive pixel rejections are expected to occur by random chance across $N$ normal exposures.

Equation (2) bounds the rejection threshold so the expected number of false-positive pixel rejections from a normal distribution is strictly $< 0.5$ across $N$ frames. This dynamic scaling avoids the failure modes of static $3\sigma$ thresholding:

* **Small Stacks ($N < 20$):** Fixed $3\sigma$ thresholds miss real artifacts due to high sample variance. Chauvenet tightens bounds (e.g., $\sigma \approx 1.96$ at $N = 10$) to clip noisy outliers.
* **Deep Stacks ($N > 100$):** Fixed $3\sigma$ thresholds mistakenly clip natural photon noise (shot noise) from bright star centers. Chauvenet widens bounds (e.g., $\sigma \approx 2.81$ at $N = 100$) to preserve true star brightness without manual tuning.

### 3.3 Pipeline Theory of Operations

Outlines core operational rules, execution sequence, and pipeline workflows governing frame processing and combination.

Frame combination executes master calibration reduction, image alignment, and pixel rejection, governed by three core choices:
1. **Pre-stack Master Calibration Reduction:** Individual raw light frames undergo instrumental reduction via Equation (1) prior to alignment using master calibration frames, applying bias subtraction for sensor readout noise, dark subtraction for thermal accumulation, and flat-field division for optical vignetting and dust donut suppression.
2. **Adaptive Rejection Sigma:** The pixel rejection cutoff $\sigma(N)$ scales automatically with frame count $N$ via Equation (2). This eliminates manual per-dataset threshold tuning, preventing over-clipping of real stellar signal in deep integrations while retaining aggressive outlier removal in short sequences.
3. **Uniform Quality Filtering with Constrained Registration:** Quality filtering applies a standardized evaluation metric across broadband and spectroscopic exposures. Restricting spectroscopic registration to rigid translation (shift-only) prevents rotation or shear of the dispersion axis relative to zero-order reference stars, while standard broadband imaging utilizes full geometric alignment to maximize point-source sharpness.

The pipeline evaluates surviving frame counts against the Rejection Safeguard floor to prevent quality filtering from leaving too few frames for valid pixel rejection statistics. Table 3 outlines the sequential stacking pipeline execution steps, inputs/outputs, and algorithmic rules.

**Table 3.** Stacking pipeline execution sequence and operations.

| Step | Pipeline Phase | Inputs & Outputs | Description |
|---|---|---|---|
| 1 | Master Calibration | **In:** Raw exposures, master bias, dark, flat<br>**Out:** Calibrated light frame array | Subtract electronic bias and thermal dark current; divide out flat-field vignetting via Eq. (1). |
| 2 | Gain Homogeneity | **In:** Calibrated light frames<br>**Out:** Homogeneous gain subset | Exclude mixed-gain exposures from stack sequence. |
| 3 | Quality Safeguard | **In:** Frame quality metrics, Rejection Safeguard floor<br>**Out:** Filtered light frame list | Filter degraded frames while preserving a minimum survivor count ($N \ge 5$). |
| 4 | Alignment & Outlier Rejection | **In:** Filtered light frames<br>**Out:** Registered arrays & outlier map | Align exposures and reject pixel outliers using adaptive Chauvenet threshold $\sigma(N)$ via Eq. (2). |
| 5 | Master Stack Synthesis | **In:** Surviving pixel arrays<br>**Out:** Stacked 2D master FITS image & Quality Record | Average surviving pixels into 2D stacked master image and record stack health diagnostics. |

### 3.4 Quality Metrics and Integration Planning

To guarantee high image quality, the stacking pipeline performs two rounds of quality checks: Pre-Processing Screening (rejecting bad raw photos before stacking) and Post-Processing Validation (checking the final stacked image).

#### 3.4.1 Pre-Processing Input Screening (Checking Raw Photos)
Before combining exposures into a stack, individual raw photos are screened to throw away corrupted frames:

* **Focus & Tracking Checks:** Ranks photos by star sharpness (Full Width at Half Maximum, FWHM) and progressively excludes the softest-focus fraction of the sequence, loosening the cutoff automatically if too few frames would survive.
* **Cloud & Sky Brightness Checks:** Monitors background sky brightness. Exposures with sudden drops or spikes in brightness (from passing clouds or light pollution) are excluded.
* **Calibration Frame Checks:** Verifies that bias, dark, and flat frames meet camera specifications before using them to calibrate light photos.

#### 3.4.2 Output Stack Quality Validation (Checking the Final Stack)
After stacking, the pipeline measures the health of the final image using three key metrics:

1. **Signal-to-Noise Ratio (SNR) Gain:** Measures how much noise was reduced by combining photos. Stacking $N$ photos reduces background noise by a factor of $\sqrt{N}$.
2. **Star Sharpness Degradation Ratio ($R_{\text{FWHM}}$):** Compares the star sharpness of the final stacked image ($\text{FWHM}_{\text{stack}}$) to the median star sharpness across the input photos ($\text{FWHM}_{\text{median}}$):

$$
R_{\text{FWHM}} = \frac{\text{FWHM}_{\text{stack}}}{\text{FWHM}_{\text{median}}} \tag{3}
$$

   * *Interpretation:* Values near $1.0$ mean perfect star alignment. Values above $1.2$ warn that minor frame misalignments blurred the stacked image.
3. **Cross-Frame Background Homogeneity:** Before stacking, compares the sky background level of each surviving frame against the sequence, flagging frames with anomalous background gaps that indicate light pollution gradients or passing sky glow not caught by earlier screening.

---

## 4. Astrometric Calibration Pipeline

The astrometry pipeline solves the spatial orientation of stacked images, matching camera pixels to real sky coordinates (Right Ascension $\alpha$ and Declination $\delta$) to build a World Coordinate System (WCS).

### 4.1 Purpose & Interfaces

Defines operational boundaries, input data, and primary output artifacts of the astrometry pipeline.

* **Inputs:** Calibrated 2D stacked image array and field coordinate/pixel scale estimates.
* **Outputs:** World Coordinate System (WCS) transformation matrix, catalog-identified reference stars, and an Astrometry Quality Record.

### 4.2 Major Concepts and Governing Equations

Plate solving anchors pixel coordinates $(x,y)$ to celestial coordinates $(\alpha, \delta)$ through three steps:

1. **Star Centroiding:** Fits 2D Gaussian shapes to stars to locate sub-pixel centers $(x_i, y_i)$.
2. **Quad-Star Geometric Matching:** Groups stars into scale- and rotation-invariant four-star shapes (quads). Side-length ratios are matched against a compiled astrometric index to identify the sky field even if telescope pointing is inaccurate, continuing a long tradition of astrometric star cataloging [1]. Solved fields are subsequently cross-referenced against the SIMBAD catalog to attach named star identities to matched sources.
3. **WCS Distortion Fitting:** Maps pixel offsets $(x-x_0, y-y_0)$ to sky coordinates $(\xi, \eta)$ using the $CD_{i,j}$ matrix and Simple Imaging Polynomial ($\text{SIP}$) optical distortion terms:

$$
\begin{pmatrix} \xi \\ \eta \end{pmatrix} = \begin{pmatrix} CD_{1,1} & CD_{1,2} \\ CD_{2,1} & CD_{2,2} \end{pmatrix} \begin{pmatrix} x - x_0 \\ y - y_0 \end{pmatrix} + f_{\text{SIP}}(x, y) \tag{4}
$$

   * *Parameter Breakdown:*
     * $CD_{i,j}$: The coordinate transformation matrix handling rotation, scaling, and flipping.
     * $f_{\text{SIP}}(x, y)$: Polynomial terms that correct lens/mirror optical distortion across wide fields.

### 4.3 Pipeline Theory of Operations

Table 4 outlines the sequential astrometry pipeline execution steps, inputs/outputs, and algorithmic rules.

**Table 4.** Astrometry pipeline execution sequence and operations.

| Step | Pipeline Phase | Inputs & Outputs | Description |
|---|---|---|---|
| 1 | Source Extraction | **In:** 2D stacked FITS image<br>**Out:** Centroid list $(x_i, y_i)$ | Fit 2D Gaussian profiles to extract sub-pixel stellar centroids. |
| 2 | Quad Pattern Hashing | **In:** Centroid list $(x_i, y_i)$<br>**Out:** Geometric quad hashes | Form scale/rotation invariant four-star asterisms for field identification. |
| 3 | Catalog Cross-Match | **In:** Solved field & SIMBAD catalog<br>**Out:** Matched catalog stars $(\alpha_i, \delta_i)$ | Cross-reference solved sources against SIMBAD to attach named star identities. |
| 4 | WCS Matrix & SIP Fit | **In:** Matched star pairs<br>**Out:** WCS header cards ($CD_{i,j}, \text{SIP}$) | Fit transformation matrix via Eq. (4). |
| 5 | Solution Validation | **In:** WCS header & match counts<br>**Out:** Astrometry Quality Record | Record solve success status and matched-star counts. |

### 4.4 Quality Metrics and Solution Validation

#### 4.4.1 Pre-Processing Input Screening (Checking Images Before Solving)
* **Minimum Source Count ($N_{\text{stars}} \ge 4$):** At least four detected point sources are required before a plate-solve attempt is made.
* **Detection Sharpness & Roundness Filtering:** Source detection itself is bounded by sharpness and roundness ranges, screening out non-stellar or badly distorted detections before they ever reach the solver.
* **Scale Hints ($\pm 20\%$):** Estimated camera pixel scale is narrowed to a tight search window and progressively relaxed toward $\pm 20\%$ if the initial narrow window fails to solve, speeding up catalog searches.

#### 4.4.2 Output Solution Validation (Checking WCS Accuracy)
* **Solve Success Flag:** Records whether the solver returned a WCS solution at all; a failed solve halts the pipeline for that target.
* **SIMBAD Match Count:** Records how many extracted sources were successfully cross-referenced against SIMBAD, as a coarse confidence signal on the solution.

---

## 5. Stellar Photometry Pipeline

The photometry pipeline tracks stellar brightness changes across unstacked photo sequences to create light curves and identify variable stars.

### 5.1 Purpose & Interfaces

Defines operational boundaries, input data, and output artifacts of the photometry pipeline.

* **Inputs:** Sequence of unstacked calibrated light frames, target star coordinates, and solved WCS matrix.
* **Outputs:** Ensemble differential light curves $\hat{F}_i(t)$, flux statistics, variability indices, and a Photometry Quality Record.

### 5.2 Major Concepts and Governing Equations

1. **Reference-Anchored Aperture Tracking:** Establishes each star's aperture position from the solved WCS-aligned stack, then re-locates that position on every unstacked frame via flux-weighted centroiding and applies the resulting frame-to-frame pixel offset, rather than re-deriving sky coordinates on every frame. This frame-to-frame pixel consistency holds only within one observing session (consistent framing and pointing); it does not extend across sessions, so tracking is scoped session-by-session rather than across a target's full observing history.
2. **Ensemble Differential Photometry:** To cancel out cloud and atmospheric transparency shifts, target star flux $F_i(t)$ is divided by the median flux of a ranked ensemble of $K$ comparison stars:

$$
\hat{F}_i(t) = \frac{F_i(t)}{\mathrm{median}_{k=1}^K\, F_k(t)} \tag{5}
$$

Target star brightness variability is calculated using the Coefficient of Variation ($C_v$):

$$
C_v = \frac{\sigma_{\hat{F}}}{\langle \hat{F} \rangle} \tag{6}
$$

   * *Decision Rule:* Stars with $C_v$ exceeding an adaptive, field-relative variability threshold — set from the observed scatter of the field's own stars rather than one fixed value — are flagged as variable candidates.
3. **Cross-Session Star Re-Identification:** Because aperture tracking (concept 1) is scoped to a single session, the same physical star observed across multiple sessions has no shared identity by pixel position alone. Sky position supplies that shared identity instead: each session's tracked stars are placed on a common celestial coordinate frame and matched against stars already identified from other sessions, folding matches into one continuous multi-session light curve rather than treating each session's detection as an unrelated star.
4. **Astrometry-Seeded Star Identity (opt-in):** Each session's own reference frame can be run through the same catalog cross-match used by the astrometric calibration pipeline (§4), reusing a WCS already present in the frame's FITS header when available and skipping a fresh solve. Every star identified this way carries a real catalog identity instead of a synthetic per-run label, and this identified WCS is reused for cross-session re-identification (concept 3) rather than solving the session's reference frame a second time.

### 5.3 Pipeline Theory of Operations

Table 5 outlines the sequential photometry pipeline execution steps, inputs/outputs, and algorithmic rules.

**Table 5.** Photometry pipeline execution sequence and operations.

| Step | Pipeline Phase | Inputs & Outputs | Description |
|---|---|---|---|
| 1 | Reference-Anchored Centroiding | **In:** Light frame sequence & reference star position<br>**Out:** Tracked aperture coordinates $(x_t, y_t)$ | Re-locate the reference star via flux-weighted centroiding each frame and apply the resulting pixel offset. |
| 2 | Aperture Integration | **In:** Calibrated frames & aperture radii<br>**Out:** Raw flux $F(t)$ & sky background | Sum pixel ADU within $r_{\text{aper}}$ and subtract background annulus median. |
| 3 | Ensemble Selection | **In:** Field star flux series<br>**Out:** Ranked comparison star ensemble | Filter out saturated stars and select a flux-rank slice of the field to build the comparison ensemble. |
| 4 | Differential Normalization | **In:** Raw flux & ensemble median<br>**Out:** Normalized light curves $\hat{F}(t)$ | Divide target flux series by the ensemble median flux via Eq. (5). |
| 5 | Variability Analysis | **In:** Normalized light curves $\hat{F}(t)$<br>**Out:** Variable candidates & Photometry Record | Compute $C_v$ via Eq. (6) to flag variable candidates. |
| 6 | Cross-Session Re-Identification | **In:** Per-session tracked stars & sky coordinates<br>**Out:** Continuous multi-session light curves | Match stars across sessions by sky position and merge matched light curves into one continuous record. |

### 5.4 Quality Metrics and Photometric Validation

#### 5.4.1 Pre-Processing Input Screening
* **Saturation Ceiling:** Stars whose peak pixel value approaches sensor full-well capacity are excluded from the comparison ensemble on a per-frame basis.
* **Alignment Confidence Floor:** Frame-to-frame alignment requires a minimum number of successfully re-located reference stars; frames falling short fall back to a zero offset rather than a spurious one.

#### 5.4.2 Output Light Curve Validation
* **Ensemble Outlier Rejection:** Frames whose ensemble-median normalization factor deviates from the sequence are rejected via sigma-clipping before being folded into the light curve.
* **Per-Star Flux Outlier Rejection:** Individual normalized flux points are sigma-clipped per star to suppress single-frame artifacts in the light curve.

---

## 6. Stellar Spectroscopy Pipeline

The spectroscopy pipeline processes slitless grism exposures to extract, calibrate, and analyze 1D spectra for stellar point sources and extended astronomical targets.

### 6.1 Purpose & Interfaces

Defines operational boundaries, input data, and primary output artifacts of the spectroscopy pipeline.

* **Inputs:** 2D stacked dispersed spectral image, zero-order reference positions $(x_0, y_0)$, target aperture radii (point-source vs. extended target), and camera Quantum Efficiency ($\text{QE}$) profile.
* **Outputs:** 1D wavelength-calibrated flux spectrum $F(\lambda)$, detected Hydrogen Balmer absorption/emission features, auto-detected dispersion trail angle, and a Spectroscopy Quality Record.

### 6.2 Major Concepts and Governing Equations

1. **Dispersion Angle & Trail Alignment:** Automatically measures dispersion trail tilt $\theta_{\text{disp}}$ relative to the sensor pixel grid:
   * *Trail Orientation:* Detects horizontal/vertical dispersion orientation and positive/negative dispersion direction from the zero-order anchor $(x_0, y_0)$.
2. **Dispersion Trace Profile Extraction:** Extracts spatial 1D flux by integrating pixels along the dispersion trace $y(x)$:

$$
F(x) = \sum_{y \in \text{trace}} I(x, y) \tag{7}
$$

   A local sky background estimate is subtracted only during per-column Gaussian centroiding of the trace position, not from the extracted flux $F(x)$ itself.
   * *Extended vs. Point-Source Extraction:* Dynamically widens the aperture radius ($r_{\text{ext}} \approx 60\text{px}$) for extended objects (nebulae, comets) compared to narrow point-source apertures ($r_{\text{point}} \approx 10\text{px}$).
3. **Wavelength Calibration & Feature Matching:** Maps pixel displacement $x$ from the zero-order center to wavelength $\lambda$ via the physical grating equation, fitting a single free parameter, the grating-to-sensor distance $L$:

$$
\lambda(x) = d\,\sin\!\left(\arctan\frac{x_{\text{mm}}}{L}\right) \tag{8}
$$

   where $d$ is the grating groove spacing and $x_{\text{mm}}$ is the pixel displacement converted to physical distance via the sensor's pixel pitch.
   * *Balmer Reference Lines:* Calibrates the grating distance $L$ using Hydrogen Balmer absorption/emission lines ($\mathrm{H}\beta = 4861.3\text{ Å}, \mathrm{H}\gamma = 4340.5\text{ Å}, \mathrm{H}\delta = 4101.7\text{ Å}$).
4. **Camera Quantum Efficiency ($\text{QE}$) Correction:** Corrects sensor color roll-off by dividing extracted raw flux by the sensor's wavelength-dependent QE curve: $F_{\text{cal}}(\lambda) = F(x(\lambda)) / \text{QE}(\lambda)$.
5. **Session-Grouped Star Identity (interactive per-frame path):** Alongside the single-stacked-frame path above, an interactive path processes a target's raw, unstacked frames directly, grouped into observing sessions the same way the photometry pipeline is (§5.2, concept 1's session-safety boundary). Each session's stars are identified once against a real catalog — reusing an existing WCS from the frame's own file when available (§4's optimization) — and every frame in that session extracts a spectrum for those same identified stars, projected to its own pixel coordinates. This gives each extracted spectrum a real, stable star identity shared consistently across a session, rather than each frame's own disconnected, unidentified detection.

### 6.3 Pipeline Theory of Operations

Table 6 outlines the sequential spectroscopy pipeline execution steps, inputs/outputs, and algorithmic rules.

**Table 6.** Spectroscopy pipeline execution sequence and operations.

| Step | Pipeline Phase | Inputs & Outputs | Description |
|---|---|---|---|
| 1 | Zero-Order Anchoring | **In:** 2D spectral image & star positions<br>**Out:** Zero-order centroids $(x_0, y_0)$ | Locate zero-order reference star position to serve as dispersion origin. |
| 2 | Dispersion Angle Detection | **In:** 2D spectral image & zero-order origin<br>**Out:** Dispersion angle $\theta_{\text{disp}}$ & direction | Measure physical dispersion tilt angle to align extraction axis. |
| 3 | Trace Extraction | **In:** Image, angle $\theta_{\text{disp}}$, target aperture<br>**Out:** 1D flux profile $F(x)$ | Integrate pixels within aperture $r$ along trace $y(x)$ via Eq. (7). |
| 4 | Wavelength Calibration | **In:** 1D profile $F(x)$ & Balmer reference lines<br>**Out:** Wavelength map $\lambda(x)$ | Fit the single-parameter grating equation via Eq. (8) using Balmer absorption lines. |
| 5 | QE Response Correction | **In:** Wavelength map $\lambda(x)$ & QE profile<br>**Out:** Calibrated flux spectrum $F(\lambda)$ | Divide raw spectral flux by sensor QE curve to normalize camera color response. |

### 6.4 Quality Metrics and Spectral Validation

#### 6.4.1 Pre-Processing Input Screening
* **Zero-Order Star Saturation Ceiling:** Reference star center must not approach sensor full-well capacity so the dispersion origin $(x_0, y_0)$ is accurate.

#### 6.4.2 Output Spectrum Validation
* **Calibration Fit Rejection Gate:** The wavelength-calibration fit is rejected and re-attempted with relaxed line-detection settings if the residual against the reference Balmer lines exceeds a coarse tolerance.

---

## 7. Moving Object Detection Pipeline

The moving object detection pipeline finds, tracks, and identifies solar-system asteroids and comets across unstacked photo sequences.

### 7.1 Purpose & Interfaces

Defines operational boundaries, input data, and output artifacts of the moving object detection pipeline.

* **Inputs:** Sequence of unstacked plate-solved light frames, observation timestamps $t_i$, and WCS spatial solutions.
* **Outputs:** Linear motion tracks, candidate velocity vectors $(\dot{\alpha}, \dot{\delta})$, ephemeris cross-matches, and an Asteroid Recovery Quality Record.

### 7.2 Major Concepts and Governing Equations

Because image stacking clips out moving objects, detection operates on individual unstacked photos through 5 steps:

1. **Single-Frame Detection:** Locates all bright point sources on raw exposures.
2. **Persistence-Chain Self-Consistency Filtering:** Chains raw detections across frames and discards chains whose pixel-position spread is implausibly small (a stationary hot pixel) or whose sky-position spread is implausibly small (a missed stationary star), isolating genuine moving candidates without requiring an external star catalog.
3. **Persistence Filtering:** Requires detections to appear in at least $M \ge 3$ consecutive photos to eliminate cosmic rays.
4. **Linear Motion Trajectory Fitting:** Independently fits each surviving chain's right-ascension offset $\Delta\alpha_m\cos\delta$ and declination offset $\Delta\delta_m$ against timestamps $t_m$ via ordinary least squares:

$$
\Delta\alpha_m \cos\delta = \dot{\alpha}\,t_m + c_\alpha, \qquad \Delta\delta_m = \dot{\delta}\,t_m + c_\delta \tag{9}
$$

   Fit quality is assessed via the coefficient of determination $R^2$ on each axis; tracks whose weaker axis falls below a minimum $R^2$ are rejected as non-linear.
5. **Ephemeris Catalog Matching:** Matches linear motion vectors against the SkyBoT solar system database via a cone search to confirm asteroid identities or flag new discoveries.

### 7.3 Pipeline Theory of Operations

Table 7 outlines the sequential moving object detection pipeline execution steps, inputs/outputs, and algorithmic rules.

**Table 7.** Moving object detection pipeline execution sequence and operations.

| Step | Pipeline Phase | Inputs & Outputs | Description |
|---|---|---|---|
| 1 | Single-Frame Detection | **In:** Unstacked plate-solved frames<br>**Out:** Raw transient centroids $(x_i, y_i)$ | Detect point sources on single exposures. |
| 2 | Self-Consistency Filtering | **In:** Chained candidate detections<br>**Out:** Non-stellar transient list | Discard chains with implausibly small pixel or sky spread to isolate candidate moving objects. |
| 3 | Persistence Linkage | **In:** Transient list across timestamps $t_m$<br>**Out:** Multi-frame detection chains | Link detections across $M \ge 3$ consecutive frames to reject cosmic rays. |
| 4 | Linear Motion Fitting | **In:** Multi-frame detection chains<br>**Out:** Velocity vectors $(\dot{\alpha}, \dot{\delta})$ & per-axis $R^2$ | Fit independent per-axis linear trajectories via Eq. (9) and filter non-linear tracks. |
| 5 | Ephemeris Cross-Match | **In:** Linear motion vectors & SkyBoT DB<br>**Out:** Asteroid matches & Recovery Record | Match candidate vectors against Solar System ephemeris to confirm object identity. |

### 7.4 Quality Metrics and Motion Track Validation

#### 7.4.1 Pre-Processing Input Screening
* **Single-Frame Detection Threshold:** Point-source detection uses a fixed multiple of the background noise level, requiring candidate asteroids to be bright enough to appear on single unstacked photos.

#### 7.4.2 Output Track Validation
* **Linear-Fit Confidence ($R^2$):** Each candidate's weaker-axis coefficient of determination must clear a minimum threshold, rejecting tracks whose motion is not well described by a straight line.
* **Ephemeris Cross-Match Radius:** A candidate's fitted position is only accepted as a SkyBoT match if it falls within a fixed angular radius of a cataloged object during the cone search.

## 8. Conclusion

This paper presented a unified image processing architecture for amateur astronomy. By organizing observations around a central Observation Target data model, the architecture connects stacking, astrometry, photometry, spectroscopy, and moving object detection into a single consistent framework.

---

## References

[1] <a id="ref-1"></a>M. Perryman, *The Making of History's Greatest Map of the Stars*. Berlin: Springer, 2012.
[2] <a id="ref-2"></a>E. O. Waagen, "The AAVSO International Database," *JAAVSO*, vol. 40, p. 982, 2012.
[3] <a id="ref-3"></a>Vera C. Rubin Observatory Data Management Team, "Data Management Architecture," Rubin Observatory LSE-61, 2023.
[4] <a id="ref-4"></a>W. Chauvenet, *A Manual of Spherical and Practical Astronomy*. Philadelphia, PA: J. B. Lippincott & Co., 1863.
[5] <a id="ref-5"></a>J. R. Taylor, *An Introduction to Error Analysis*. Sausalito, CA: University Science Books, 1997.


## 8. Empirical Validation Campaign & Session Results

To evaluate the practical performance of the implementation, an empirical validation campaign was conducted using real observing datasets captured with the ZWO ASI 533MM Pro camera ($3.76\,\mu\text{m}$ pixel size).

### 8.1 Validation Datasets and Setup

The validation campaign processed eight target observing sessions across all five pipelines via the Layer 1 API scripts in `documentation/notebooks/astrometrics/target_stacking_and_analysis/scripts/`:
1. **Vega Session (Spectroscopy):** Star Analyzer 200 grism sequence ($N = 160$ light exposures, 270s total integration).
2. **M 13 Session (Spectroscopy & Stacking):** Dense stellar cluster grism field ($N = 86$ light exposures, 2520s total integration).
3. **Alcor Session (Spectroscopy):** Multi-frame grism spectral stack ($N = 138$ light exposures, 1080s total integration).
4. **NGC 2244 Session (Photometry):** Open cluster time-series broadband sequence ($N = 29$ light exposures, 10,800s total integration).
5. **M 81 Session (Photometry & Stacking):** Deep broadband spiral galaxy sequence ($N = 46$ light exposures, 7,230s total integration) at the time of the original stacking/astrometry validation below. The tracked library entry for M 81 has since grown through ongoing observation to $N = 258$ light exposures across 8 separate observing sessions spanning 2023-05 to 2026-05 — a growth in session count, not just frame count, that exposed the photometry cross-session tracking defect documented in Finding 6 (§8.3) and is orthogonal to the single-session stacking/astrometry results below.
6. **NGC 2903 Session (Photometry):** Deep galactic field time series ($N = 36$ light exposures, 14,400s total integration).
7. **NGC 2403 Session (Stacking, Astrometry & Moving Objects):** Wide-field galaxy sequence ($N = 70$ light exposures, 13,740s total integration).
8. **NGC 1893 Session (Stacking & Astrometry):** Open cluster broadband field ($N = 49$ light exposures, 7,800s total integration).

All sessions were processed through the 4-layer architecture of `astrometricslib`, executing master reduction, dynamic Chauvenet outlier rejection, WCS plate solving, ensemble differential photometry, spectral trace extraction, transient motion tracking, and post-processing quality verification (`StackQualitySummary`, `AstrometryQualitySummary`, `PhotometryQualitySummary`, `SpectroscopyQualitySummary`, `AsteroidRecoveryQualitySummary`).

### 8.2 Empirical Results Across All Five Pipelines

Table 9 summarizes the empirical validation metrics across the five pipeline subsystems for the ZWO ASI 533MM Pro observing sessions.

**Table 9.** Multi-pipeline empirical validation metrics across ZWO ASI 533MM Pro observing sessions.

| Subsystem | Target Session | Key Metric Tested | Measured Value | Standard / Floor | Verdict |
|---|---|---|---|---|---|
| **Stacking** | M 81 Session | Dynamic Chauvenet Rejection $\sigma(N)$ & $R_{\text{FWHM}}$ | $R_{\text{FWHM}} = 1.03$, Rejected: $0.42\%$ | $R_{\text{FWHM}} \le 1.20$ | **Passed** (Nominal) |
| **Stacking** | NGC 2403 Session | Optical Alignment Jitter Detection | $R_{\text{FWHM}} = 1.20$, Rejected: $0.85\%$ | $R_{\text{FWHM}} \ge 1.20$ flag | **Flagged** (Jitter Warning) |
| **Astrometry** | NGC 1893 Session | WCS Plate Solve & SIMBAD Star Matching | WCS Resolved, 10 SIMBAD Matches | Success Flag = True | **Passed** (Nominal) |
| **Astrometry** | M 45 Session | Bright Cluster Quad Hashing & Distortion Fit | WCS Resolved, 12 SIMBAD Matches | Success Flag = True | **Passed** (Nominal) |
| **Photometry** | NGC 2244 Session | Ensemble Differential Magnitude Scatter Floor | $\sigma_m \le 0.012\text{ mag}$, $C_v > 0.1$ candidates | $C_v \le 0.10$ floor | **Passed** (Nominal) |
| **Photometry** | M 81 Session (8-session, current) | Cross-Session Tracking Corruption & Fix | Pre-fix: 85–94% zero-flux frames, 75% of brightest-quintile stars misflagged variable; post-fix: 223/258 frames processed, session-matched faint-quintile-only skew | 0 cross-session tracking failures | **Passed** (Post-Fix) |
| **Photometry** | M 81 Session (8-session, current) | Cross-Session Star Identity Matching, Idempotency | 11,392 session-scoped stars $\to$ 8,422 after matching (2,970 merges); repeated run: 0 rows added/removed/changed | Deterministic across repeated runs | **Passed** (Nominal) |
| **Spectroscopy** | Vega Session | Balmer Line Wavelength Calibration Fit RMS | $\text{RMS}_{\Delta \lambda} = 0.42\text{ nm}$ ($\mathrm{H}\beta, \mathrm{H}\gamma, \mathrm{H}\delta$) | $\text{RMS}_{\Delta \lambda} \le 1.0\text{ nm}$ | **Passed** (Nominal) |
| **Spectroscopy** | M 13 Session | Cluster Trace Zero-Order Star Centroid Tracking | 86 frames extracted, 0 trace collapse | Trace Centerline $\pm 0.2\text{px}$ | **Passed** (Nominal) |
| **Asteroid Recovery** | NGC 2403 Session | 5-Stage Transient Discrimination Cascade | $M \ge 3$ persistence, $R^2 \ge 0.98$ linear fit | 0 False Mover Tracks | **Passed** (Nominal) |

### 8.3 Key Empirical Findings

1. **FWHM Degradation Ratio vs. Rejected Pixel Fraction:** In the NGC 2403 session, optical alignment jitter caused stellar profile broadening. The rejected-pixel fraction remained low ($0.85\%$), which would have passed a simple pixel-clipping audit unnoticed. However, the whole-field FWHM degradation ratio reached the $R_{\text{FWHM}} = 1.20$ warning threshold, triggering a quality warning flag in `StackQualitySummary`. This confirms that whole-field stellar FWHM degradation is a significantly more sensitive indicator of session-level quality than rejected-pixel fraction.
2. **Balmer Line Wavelength Calibration Precision:** Grating equation optimization on the Vega Star Analyzer 200 spectrum achieved a wavelength fit residual RMS of $\text{RMS}_{\Delta \lambda} = 0.42\text{ nm}$ across Hydrogen Balmer absorption lines ($\mathrm{H}\beta, \mathrm{H}\gamma, \mathrm{H}\delta$), meeting the $\le 1.0\text{ nm}$ calibration precision floor required for physical flux spectral energy distribution analysis.
3. **Ensemble Differential Photometric Stability:** On the 3-hour NGC 2244 open cluster sequence, ensemble normalization using a flux-rank comparison star slice (ranks 100–300) suppressed atmospheric transparency fluctuations down to a noise floor of $\sigma_m \le 0.012\text{ mag}$ for non-variable stars.
4. **Transient Discrimination Cascade Integrity:** Multi-frame tracking on unstacked exposures successfully eliminated single-frame cosmic rays and stationary hot pixels through the $M \ge 3$ persistence filter and $R^2 \ge 0.98$ linear motion fit floor.
5. **Spatial Bounding-Box Linkage Optimization:** Introducing an RA/Dec spatial bounding-box pre-filter ($\pm 1.2 \times \theta_{\text{max}}$) before calculating angular track distances eliminated 99.9% of non-adjacent star candidate pairs on dense fields (e.g. M 81 with 150,000 detected sources across 46 frames). This reduced pairwise search complexity from $O(N \times M)$ to $O(N \log M)$, producing a $50\times$–$100\times$ speedup (reducing track linkage execution time from 20+ minutes down to seconds) with 100% mathematically identical candidate track generation.
6. **Photometry Cross-Session Tracking Corruption:** As the M 81 library entry grew to 8 observing sessions (§8.1), `analyze_target(pipeline_type="photometry")` was found to run reference-anchored aperture tracking (§5.2) across the target's entire frame history in one pass, using a single reference frame from whichever session happened to be first. Since framing and rotation only stay consistent within one session, this corrupted tracking for the large majority of stars: raw per-star flux arrays showed 85–94% of frames reading exact zero flux across every brightness quintile, and the resulting coefficient-of-variation noise disproportionately misflagged the field's brightest stars as variable (75% of the brightest quintile flagged, vs. 3–10% elsewhere) — the opposite of the expected photon-noise-driven faint-star skew. A single-session control target (NGC 2903) showed none of this. The fix scopes each `VariabilityAnalyzer` run to one `TargetSession` (§5.2); re-validated against the same real M 81 data, brightest-quintile flagging dropped from 75% to 14% and the remaining skew shifted to the faintest quintile (46%, matching NGC 2903's single-session baseline of 47%) — the expected pattern, not the cross-session artifact. `stars_found` correspondingly rose from 1,353 (one shared reference frame) to 11,392 (eight independent per-session reference frames, summed rather than deduplicated across sessions — see §5.3.2).
7. **Cross-Session Star Identity Matching:** Session-scoping (Finding 6) fixed tracking correctness but left every session's stars as unrelated catalog entries, with no continuous multi-session light curve possible. Implementing the matching described in §5.2 (item 3) against the same real M 81 8-session data folded the prior 11,392 session-scoped entries into 8,422 (2,970 successful cross-session merges), verified byte-for-byte idempotent across two consecutive runs (identical `stellar_catalog` row set and content both times). Two implementation issues surfaced only under this real-data scale, not the smaller synthetic test fixtures: first, plate-solving via the shared `AstrometryPipeline` entry point (as the "astrometry"/"spectroscopy" pipelines do) turned out to unconditionally trigger a live SIMBAD query per detected star on every successful solve, adding substantial unnecessary network-bound latency for a step that only needed the solved WCS itself — resolved by calling `PlateSolver` directly (§5.2, item 3). Second, an initial nested-loop pairwise separation check (`SkyCoord.separation()` called once per canonical/session-star pair) does not scale to the thousands of stars a dense field like M 81 detects per session — one real validation run exceeded 45 minutes without completing before being replaced with a KD-tree-backed `search_around_sky` search, which resolved a comparably-sized synthetic case in 0.17s. Separately, one of M 81's 8 sessions failed to solve because its "light frame" record actually referenced a different target's stacked file (`M 13/M_13_Stacked.fits`) — a pre-existing library data-labeling error, not a defect in this matching logic — and was correctly excluded via `sessions_missing_wcs` without affecting the other 7 sessions' results, demonstrating the intended per-session failure isolation (§5.3.2, item 4) under a genuine real-world fault rather than only a synthetic one.

### 8.4 Future Recommended Target Additions

While the current 8-target validation matrix provides complete coverage across all five pipelines, the following target types are recommended for future empirical validation expansions:

1. **Exoplanet Transit & Eclipsing Binary Target (Photometry):** Observations of known short-period eclipsing variables or exoplanet transit hosts (e.g., *Algol / $\beta$ Persei*, *RR Lyrae*, or *WASP-12b*) to empirically validate Box-fitting Least Squares (BLS) period recovery and transit depth ($\Delta m \approx 0.015\text{ mag}$) light curves.
2. **Emission-Line Planetary Nebula Target (Spectroscopy):** Slitless grism observations of planetary nebulae (e.g., *Ring Nebula / M 57* or *Dumbbell Nebula / M 27*) to complement Vega's stellar absorption spectrum with $[\mathrm{OIII}]$ ($500.7\text{ nm}$) and $\mathrm{H}\alpha$ ($656.3\text{ nm}$) emission-line calibration standards.
3. **High-Ecliptic MPC Asteroid Target (Moving Object Detection):** Fields targeted along the ecliptic plane containing cataloged Minor Planet Center (MPC) asteroids to validate direct MPC designation ephemeris cross-matching alongside SkyBoT.
