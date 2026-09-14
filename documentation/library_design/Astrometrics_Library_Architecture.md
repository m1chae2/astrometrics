# Astrometrics Library: Architecture and Design

*Version 2.4 · 2026-09-14 · Status: current*

## Abstract

This document explains the physics, algorithms, and design decisions behind the Astrometrics image-processing pipelines — stacking, astrometry, photometry, spectroscopy, and moving object detection — for engineers and astronomers who want to understand how the system processes an observation, not just how to call it. Every pipeline organizes its data around one shared idea, the Observation Target, and each pipeline checks its own work twice: once before processing starts, to reject bad input, and once after it finishes, to catch bad output. For a map from these ideas to the actual code, see `Astrometrics_Library_Implementation.md`.

## 1. Introduction

**Statement of need.** Five separate image-processing pipelines working on the same telescope data need a shared way to describe a target, or each pipeline ends up re-deriving the same sky position, frame list, and quality state on its own, with no way to reuse another pipeline's results (a solved sky position, a calibrated stack). Using one shared data model lets several astronomy pipelines work together on the same target without stepping on each other's data. The ideas in this document apply the same way whether a pipeline is run from a script or through the interactive graphical tools, because both paths call the exact same underlying code. Anything you can do by clicking through the interface, you can also automate as a script.

A complete, code-level reference for every public class and method is generated automatically from the source code's own documentation; see the Sphinx {doc}`API Reference </api/astrometricslib>`. For a map connecting the ideas in this document to the actual Python files that implement them, see `Astrometrics_Library_Implementation.md`.

The rest of this document is organized as follows. Section 2 introduces the four data models every pipeline shares. Sections 3 through 7 walk through the five pipelines in turn: Stacking, Astrometry, Photometry, Spectroscopy, and Moving Object Detection. Section 8 reports results from testing the pipelines on real telescope data, and Section 9 concludes.

## 2. Observational Data Models

The system organizes astronomical observations around four data models, following the target-centric approach used by large professional sky-survey projects [3]. These four models cover everything the pipelines need to track: sky positions, brightness measurements, light curves, spectra, and quality checks, all connected to a single target.

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
| **Pipeline Quality Record** | Health of each pipeline run | Frame survivor counts $N$, rejection cutoff $\sigma(N)$, FWHM ratio, fit-confidence scores | Tracks whether a pipeline run succeeded, and how well |

### 2.2 Data Model Progression Across Pipelines

Table 2 shows how each data model gets filled in as data moves through the five pipelines, from raw images at the top down to final measurements at the bottom.

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

The stacking pipeline combines many individual exposures into one high-quality image. Combining frames boosts the real signal from stars while averaging out noise and one-off artifacts like satellite trails and cosmic ray hits.

### 3.1 Purpose & Interfaces

* **Inputs:** Raw light exposures, plus three master calibration frames ($M_{\text{bias}}, M_{\text{dark}}, M_{\text{flat}}$, explained below).
* **Outputs:** A calibrated, stacked 2D image (broadband or spectral), an updated FITS header, and a Stack Quality Record.

### 3.2 Major Concepts and Governing Equations

Every raw exposure from the camera contains two kinds of unwanted signal: noise added by the camera's electronics and heat (additive), and a scaling error introduced by the optics, where some parts of the image come out dimmer than others for reasons that have nothing to do with the sky (multiplicative). Before frames are aligned and combined, each raw exposure $I_{\text{raw}}$ is corrected using three calibration images:

* **Master Bias ($M_{\text{bias}}$):** An average of many zero-length, closed-shutter exposures. It isolates the fixed voltage offset the camera's electronics add to every pixel, regardless of exposure time.
* **Master Dark ($M_{\text{dark}}$):** An average of closed-shutter exposures taken with the same exposure time, gain, and sensor temperature as the light frames. It measures the extra signal ("dark current") produced by heat inside the sensor over time, plus any permanently defective ("hot") pixels.
* **Master Flat ($M_{\text{flat}}$):** An average of exposures of an evenly lit surface (the twilight sky or a flat light panel). It measures how much dimmer some parts of the image are due to the optics — vignetting (darkened corners), dust specks on the sensor glass, and pixel-to-pixel sensitivity differences.

These two categories of error are corrected differently:

* **Additive errors (subtracted):** extra pixel counts added regardless of how much starlight actually reached the sensor.
  * *Electronic bias:* a constant voltage offset the camera adds so pixel values never go negative during digitization.
  * *Thermal dark current:* extra electrons that build up from heat inside the sensor, growing roughly in proportion to exposure time.
* **Multiplicative errors (divided out):** factors that block or scale down a percentage of the real incoming starlight.
  * *Vignetting and dust shadows:* obstructions in the optical path (lens edges, dust on the sensor glass) that dim parts of the image.
  * *Pixel sensitivity differences:* tiny manufacturing differences in how efficiently each pixel converts light into signal.

Combining these corrections gives the true, calibrated brightness $I_{\text{calibrated}}$: subtract the additive errors, then divide out the (normalized) multiplicative ones:

$$
I_{\text{calibrated}} = \frac{I_{\text{raw}} - M_{\text{dark}}}{\left(M_{\text{flat}} - M_{\text{bias}}\right) / \langle M_{\text{flat}} - M_{\text{bias}} \rangle} \tag{1}
$$

After calibration, the pipeline averages $N$ exposures together to raise the signal-to-noise ratio. When averaging, any pixel far enough from the group's median value is thrown out as an outlier (a satellite trail, a cosmic ray hit, a plane). Rather than using one fixed cutoff for every stack (such as always rejecting anything past $3\sigma$), the pipeline adjusts the cutoff based on how many frames $N$ are being combined, using a formula called Chauvenet's criterion [4], [5]:

$$
\sigma(N) = \sqrt{2}\,\mathrm{erfc}^{-1}\!\left(\frac{1}{2N}\right) \tag{2}
$$

In words:
* **$\sigma(N)$ (rejection cutoff):** how many standard deviations away from the group's median a pixel has to be before it's thrown out (e.g. $1.96\sigma$, $2.5\sigma$, $2.8\sigma$).
* **$N$:** how many light exposures are being combined.
* **$\mathrm{erfc}^{-1}$ (inverse complementary error function):** a standard statistics function. Here it finds the cutoff at which we'd expect fewer than half a false alarm — fewer than 0.5 pixels wrongly rejected purely by chance — across all $N$ frames.

In short: the more frames being combined, the more chances there are for one of them to look like an outlier by pure random chance, so Equation (2) loosens the cutoff as $N$ grows. This avoids two failure modes that one fixed threshold runs into:

* **Small stacks ($N < 20$):** A fixed $3\sigma$ cutoff is too loose and misses real artifacts, because with few frames there's more random variation to hide behind. Chauvenet's criterion tightens the cutoff instead (about $1.96\sigma$ at $N = 10$).
* **Deep stacks ($N > 100$):** A fixed $3\sigma$ cutoff becomes too strict and starts clipping real, bright star centers, mistaking ordinary photon noise for outliers. Chauvenet's criterion loosens the cutoff instead (about $2.81\sigma$ at $N = 100$) so real star brightness survives without any manual tuning.

### 3.3 Pipeline Theory of Operations

Combining frames means three things happen together: calibrating each frame, aligning them to each other, and rejecting outlier pixels. Four design choices drive this process:

1. **Calibrate before aligning:** Each raw light frame is corrected using Equation (1) before it's aligned with the others — bias subtraction for the electronic offset, dark subtraction for heat buildup, and flat-field division for optical dimming and dust shadows.
2. **Adjust the rejection cutoff automatically:** The pixel-rejection cutoff $\sigma(N)$ scales with the number of frames $N$, via Equation (2), so nobody has to manually tune it per dataset. This keeps small stacks strict and stops deep stacks from over-clipping real starlight.
3. **One quality check, two alignment modes:** The same frame-quality check applies to both ordinary (broadband) images and spectroscopic images. But spectroscopic images are only allowed to shift position (not rotate or stretch) when aligning, so the axis the spectrum is spread out along doesn't get distorted relative to the reference star. Ordinary images use full alignment (shifting, rotating, and stretching as needed) to make stars as sharp as possible.
4. **Keep different optical setups separate:** Frames taken with different focal lengths or cameras get stacked separately, never mixed together. Mixing them would break the assumption that one pixel always corresponds to the same patch of sky, and would corrupt sharpness measurements.

Before rejecting outliers, the pipeline also checks that enough frames survived the earlier quality filtering — too few frames left would make the outlier statistics meaningless. Table 3 lays out the stacking pipeline step by step.

**Table 3.** Stacking pipeline execution sequence and operations.

| Step | Pipeline Phase | Inputs & Outputs | Description |
|---|---|---|---|
| 1 | Calibration | **In:** Raw exposures, master bias, dark, flat<br>**Out:** Calibrated light frame array | Subtract the electronic bias and thermal dark current; divide out flat-field dimming via Eq. (1). |
| 2 | Gain Check | **In:** Calibrated light frames<br>**Out:** Matching-gain subset | Drop any exposures taken at a different sensor gain setting than the rest. |
| 3 | Quality Safeguard | **In:** Frame quality metrics, minimum-frame floor<br>**Out:** Filtered light frame list | Filter out degraded frames, but never drop below a minimum survivor count ($N \ge 5$). |
| 4 | Alignment & Outlier Rejection | **In:** Filtered light frames<br>**Out:** Aligned arrays & outlier map | Align exposures to each other and reject outlier pixels using the adjustable cutoff $\sigma(N)$ from Eq. (2). |
| 5 | Combine into Master Stack | **In:** Surviving pixel arrays<br>**Out:** Stacked 2D FITS image & Quality Record | Average the surviving pixels into one stacked image and record how the run went. |

### 3.4 Quality Metrics and Integration Planning

The stacking pipeline checks its own work in two rounds: before stacking (screening out bad raw photos) and after stacking (checking the final image).

#### 3.4.1 Before Stacking: Screening Raw Photos
* **Focus & Tracking Checks:** Measures each raw photo's star sharpness (FWHM — Full Width at Half Maximum, essentially how wide a star's blur looks) and shape (roundness), and ranks photos accordingly. FWHM is converted from pixels into arcseconds (a unit of angle in the sky; there are 3,600 arcseconds in one degree) using the camera's pixel scale, so the same cutoff works regardless of camera. The softest-focus fraction of photos is excluded, and the cutoff loosens automatically if too few frames would otherwise survive.
* **Hardware Context:** Image quality problems are cross-checked against the telescope's own reported state. Airmass, altitude, and azimuth (how low the target is in the sky, and which direction it's in) separate normal atmospheric degradation — like a target sinking toward the horizon — from an actual mount problem. Focuser position and sensor temperature let the pipeline detect focus drift and improve automatic temperature-compensation settings over time.
* **Cloud & Sky Brightness Checks:** Tracks the background sky brightness and the fraction of overexposed (saturated) pixels before alignment. Exposures with a sudden brightness spike or drop — passing clouds, stray light — are excluded.
* **Calibration Frame Checks:** Confirms that the bias, dark, and flat frames actually match the camera's specifications before they're used to calibrate the light frames.

#### 3.4.2 After Stacking: Checking the Final Image
Once stacking finishes, five checks measure how healthy the result is:

1. **Signal-to-Noise Ratio (SNR) Gain:** How much background noise was reduced by combining photos. Combining $N$ photos reduces noise by a factor of $\sqrt{N}$.
2. **Star Sharpness Ratio ($R_{\text{FWHM}}$):** Compares how sharp stars look in the final stacked image ($\text{FWHM}_{\text{stack}}$) to how sharp they looked, on average, across the input photos ($\text{FWHM}_{\text{median}}$):

$$
R_{\text{FWHM}} = \frac{\text{FWHM}_{\text{stack}}}{\text{FWHM}_{\text{median}}} \tag{3}
$$

   * *What it means:* A value near $1.0$ means the frames lined up almost perfectly. A value above $1.2$ is a warning sign that small misalignments between frames blurred the final image.
3. **Background Consistency Across Frames:** Before stacking, compares each surviving frame's background sky brightness to the rest of the sequence, flagging any frame with an unusual gap — a sign of light pollution or passing sky glow that earlier checks missed.
4. **Processing Time Tracking:** Tracks how long stacking took and whether it hit a timeout, building up a baseline over time that helps tune how many stacking jobs can safely run at once.
5. **Mount Tracking Analysis:** Measures how far stars shifted from frame to frame, in arcseconds, to gauge how well the telescope mount tracked the sky. The pipeline knows which side of the telescope's pier the mount was on at each moment and uses that to exclude the expected jump from a meridian flip (a routine, automatic mount repositioning), so only real tracking problems — a snagged cable, wind, or polar-alignment drift — show up as errors.

---

## 4. Astrometric Calibration Pipeline

The astrometry pipeline figures out exactly where a stacked image is pointed, matching camera pixels to real sky coordinates (Right Ascension $\alpha$ and Declination $\delta$ — the sky's version of longitude and latitude). This is often called "plate solving," a term left over from the days of photographic plates. The result is a World Coordinate System (WCS): a mathematical formula that converts any pixel position in the image into a real sky position, and back.

### 4.1 Purpose & Interfaces

* **Inputs:** A calibrated, stacked image, plus a rough estimate of the field's coordinates and pixel scale (how many arcseconds each pixel covers).
* **Outputs:** A WCS transformation, a list of catalog-identified reference stars, and an Astrometry Quality Record.

### 4.2 Major Concepts and Governing Equations

Plate solving connects pixel coordinates $(x,y)$ to sky coordinates $(\alpha, \delta)$ in three steps:

1. **Star Centroiding:** Fits a 2D Gaussian bell-curve shape to each detected star to pin down its center $(x_i, y_i)$ more precisely than just picking the brightest pixel.
2. **Quad-Star Matching:** Groups nearby stars into four-star shapes ("quads") whose relative side lengths stay the same no matter how the image is rotated, flipped, or scaled. These shapes are compared against a pre-built star index to identify which patch of sky the image shows, even if the telescope was pointed somewhere unexpected — continuing a long tradition of cataloging the sky this way [1]. Once the field is identified, the matched stars are cross-checked against the SIMBAD star catalog to attach real star names and IDs to the sources found in the image.
3. **Fitting the Distortion Map:** Converts pixel offsets from the image center $(x-x_0, y-y_0)$ into sky-coordinate offsets $(\xi, \eta)$, using a transformation matrix $CD_{i,j}$ plus a correction for lens/mirror distortion, called a Simple Imaging Polynomial (SIP):

$$
\begin{pmatrix} \xi \\ \eta \end{pmatrix} = \begin{pmatrix} CD_{1,1} & CD_{1,2} \\ CD_{2,1} & CD_{2,2} \end{pmatrix} \begin{pmatrix} x - x_0 \\ y - y_0 \end{pmatrix} + f_{\text{SIP}}(x, y) \tag{4}
$$

   * *What each piece means:*
     * $CD_{i,j}$: the matrix that handles the image's rotation, scale, and any left-right flip.
     * $f_{\text{SIP}}(x, y)$: extra polynomial terms that correct for lens or mirror distortion, which becomes more noticeable toward the edges of a wide field of view.

### 4.3 Pipeline Theory of Operations

Table 4 lays out the astrometry pipeline step by step.

**Table 4.** Astrometry pipeline execution sequence and operations.

| Step | Pipeline Phase | Inputs & Outputs | Description |
|---|---|---|---|
| 1 | Source Extraction | **In:** Stacked FITS image<br>**Out:** Star center list $(x_i, y_i)$ | Fit a 2D Gaussian to each star to find its precise center. |
| 2 | Quad Pattern Hashing | **In:** Star center list $(x_i, y_i)$<br>**Out:** Geometric quad shapes | Group stars into four-star shapes that stay recognizable under rotation and scaling. |
| 3 | Catalog Cross-Match | **In:** Solved field & SIMBAD catalog<br>**Out:** Matched catalog stars $(\alpha_i, \delta_i)$ | Cross-check solved stars against SIMBAD to attach real star identities. |
| 4 | WCS & Distortion Fit | **In:** Matched star pairs<br>**Out:** WCS header values ($CD_{i,j}, \text{SIP}$) | Fit the pixel-to-sky transformation via Eq. (4). |
| 5 | Solution Check | **In:** WCS & match counts<br>**Out:** Astrometry Quality Record | Record whether the solve succeeded and how many stars matched. |

### 4.4 Quality Metrics and Solution Validation

#### 4.4.1 Before Solving: Checking the Image
* **Minimum Star Count ($N_{\text{stars}} \ge 4$):** At least four stars must be detected before a plate-solve is even attempted.
* **Shape Filtering:** Detected sources are filtered by sharpness and roundness before solving, to screen out non-star detections and badly distorted blobs.
* **Scale Hints & Fallback:** The pipeline starts with a narrow guess at the pixel scale and widens it up to $\pm 20\%$ if needed. If that guess turns out to be wrong for the actual optical setup, the pipeline falls back to an unconstrained search with no scale assumption at all.

#### 4.4.2 After Solving: Checking the Result
* **Solve Success Flag:** Whether the solver found a WCS at all; if not, the pipeline stops for that target.
* **Network Health Tracking:** Tracks how often lookups to remote star catalogs succeed, fail, or get temporarily paused after repeated failures. This distinguishes a genuinely sparse star field from a network problem, and helps tune how long the pipeline should wait before giving up on a lookup.
* **SIMBAD Match Count:** How many detected stars were successfully matched to a real catalog entry — a rough measure of how trustworthy the solution is.

---

## 5. Stellar Photometry Pipeline

The photometry pipeline tracks how a star's brightness changes across a sequence of individual (unstacked) photos, building a light curve and flagging stars whose brightness varies.

### 5.1 Purpose & Interfaces

* **Inputs:** A sequence of calibrated, unstacked light frames, the target star's coordinates, and a solved WCS.
* **Outputs:** Normalized light curves $\hat{F}_i(t)$, brightness statistics, a list of variable-star candidates, and a Photometry Quality Record.

### 5.2 Major Concepts and Governing Equations

1. **Tracking a Star Frame to Frame:** The pipeline finds each star's position once, from the solved, WCS-aligned stack. On every individual frame after that, it re-locates that same star by finding its brightness-weighted center, then shifts every other star's expected position by that same offset, rather than recalculating sky coordinates from scratch on every frame. This shortcut only works within one observing session, because framing and pointing only stay consistent while the telescope isn't repositioned between sessions; the pipeline therefore tracks stars session by session, not across a target's entire observation history.
2. **Ensemble Differential Photometry:** To cancel out the effect of passing clouds or changing atmospheric clarity, each star's raw brightness $F_i(t)$ is divided by the median brightness of a group ("ensemble") of $K$ other, well-behaved comparison stars in the same field:

$$
\hat{F}_i(t) = \frac{F_i(t)}{\mathrm{median}_{k=1}^K\, F_k(t)} \tag{5}
$$

How much a star's normalized brightness varies is measured with the coefficient of variation ($C_v$): the star's brightness spread divided by its average brightness.

$$
C_v = \frac{\sigma_{\hat{F}}}{\langle \hat{F} \rangle} \tag{6}
$$

   * *Decision rule:* A star is flagged as a variable-star candidate if its $C_v$ is unusually high compared to the other stars measured in the same field, rather than compared to one fixed number used for every field, following the same field-relative approach long-running variable-star observing networks use to flag candidates for follow-up [2].
3. **Matching the Same Star Across Sessions:** Since tracking (concept 1) only works within one session, the pipeline needs another way to recognize "this is the same star" across sessions taken weeks or months apart. It does that using each star's sky position: every session's tracked stars are placed on the same sky-coordinate map and matched against stars already found in earlier sessions, so all the individual sessions' measurements of one physical star get combined into a single, continuous light curve.
4. **Identifying Stars by Catalog Name (optional):** Each session's reference frame can optionally be run through the same star-catalog matching used by the astrometry pipeline (Section 4). When the frame's own WCS is already known (from an earlier solve), this step reuses it instead of solving again. Doing this gives every star a real catalog name instead of a made-up label for that run, and also improves the cross-session matching described in concept 3.

### 5.3 Pipeline Theory of Operations

Table 5 lays out the photometry pipeline step by step.

**Table 5.** Photometry pipeline execution sequence and operations.

| Step | Pipeline Phase | Inputs & Outputs | Description |
|---|---|---|---|
| 1 | Star Tracking | **In:** Light frame sequence & reference star position<br>**Out:** Tracked position $(x_t, y_t)$ each frame | Re-locate the reference star each frame by its brightness-weighted center, and shift the expected position of every other star to match. |
| 2 | Aperture Photometry | **In:** Calibrated frames & aperture radius<br>**Out:** Raw brightness $F(t)$ & sky background | Add up the pixel brightness values (ADU, the camera's raw brightness units) within a circle around each star, and subtract the surrounding sky background. |
| 3 | Ensemble Selection | **In:** Brightness of every star in the field<br>**Out:** Ranked comparison-star ensemble | Exclude overexposed stars, then pick a group of stars in a chosen brightness range to serve as the comparison ensemble. |
| 4 | Differential Normalization | **In:** Raw brightness & ensemble median<br>**Out:** Normalized light curves $\hat{F}(t)$ | Divide each star's raw brightness by the comparison ensemble's median brightness, via Eq. (5). |
| 5 | Variability Analysis | **In:** Normalized light curves $\hat{F}(t)$<br>**Out:** Variable-star candidates & Photometry Record | Compute $C_v$ via Eq. (6) and flag unusually variable stars. |
| 6 | Cross-Session Matching | **In:** Per-session tracked stars & sky coordinates<br>**Out:** Continuous multi-session light curves | Match the same star across sessions by sky position, and merge its measurements into one continuous light curve. |

### 5.4 Quality Metrics and Photometric Validation

#### 5.4.1 Before Measuring: Screening the Input
* **Saturation Ceiling:** Any star whose brightest pixel is close to the sensor's maximum value is excluded from the comparison ensemble for that frame, since an overexposed star's brightness reading can't be trusted.
* **Alignment Confidence Floor:** Re-locating the reference star must succeed on a minimum number of stars per frame; if too few are found, the pipeline assumes no shift happened rather than guessing at a shift it isn't confident about.

#### 5.4.2 After Measuring: Checking the Light Curve
* **Ensemble Outlier Rejection:** Frames whose comparison-ensemble brightness looks unusual compared to the rest of the sequence are excluded before being folded into the light curve.
* **Per-Star Outlier Rejection:** Individual brightness readings that look like one-off glitches are excluded from each star's light curve.

---

## 6. Stellar Spectroscopy Pipeline

The spectroscopy pipeline processes images taken through a grism — a lens-like attachment that spreads a star's light out into a rainbow (a spectrum) without needing a narrow slit — to extract, calibrate, and analyze that spectrum for stars and other targets.

### 6.1 Purpose & Interfaces

* **Inputs:** A stacked spectral image, the position of the "zero-order" star image (the star's normal, undispersed position, used as a fixed reference point), the target's aperture size (different for point-like stars vs. extended objects), and the camera's Quantum Efficiency (QE) curve — how sensitive the sensor is at each wavelength of light.
* **Outputs:** A wavelength-calibrated 1D spectrum $F(\lambda)$, any detected hydrogen (Balmer) absorption or emission features, the automatically measured spread angle of the spectrum, and a Spectroscopy Quality Record.

### 6.2 Major Concepts and Governing Equations

1. **Finding the Spectrum's Angle:** Automatically measures the angle $\theta_{\text{disp}}$ at which the spectrum is spread out relative to the sensor's pixel grid, along with which direction (horizontal or vertical, and which way) it runs, using the zero-order star position $(x_0, y_0)$ as the anchor point.
2. **Extracting the Spectrum:** Adds up pixel brightness along the spectrum's path $y(x)$ to build a 1D brightness profile:

$$
F(x) = \sum_{y \in \text{trace}} I(x, y) \tag{7}
$$

   The local sky background is subtracted only while pinpointing the spectrum's exact position on each column (by fitting a small Gaussian), not from the final extracted brightness $F(x)$ itself.
   * *Point sources vs. extended objects:* The width of the extraction aperture is wider for extended objects like nebulae or comets (about 60 pixels) than for point-like stars (about 10 pixels).
3. **Converting Pixels to Wavelength:** Converts the pixel distance $x$ from the zero-order center into a wavelength $\lambda$, using the physical grating equation and fitting one unknown, the distance $L$ between the grating and the sensor:

$$
\lambda(x) = d\,\sin\!\left(\arctan\frac{x_{\text{mm}}}{L}\right) \tag{8}
$$

   Here $d$ is the spacing between grooves on the grating, and $x_{\text{mm}}$ is the pixel distance converted into physical millimeters using the sensor's pixel size.
   * *Reference lines:* The distance $L$ is calibrated using known hydrogen absorption/emission lines from the Balmer series ($\mathrm{H}\beta = 4861.3\text{ Å}, \mathrm{H}\gamma = 4340.5\text{ Å}, \mathrm{H}\delta = 4101.7\text{ Å}$), since their true wavelengths are already known precisely.
4. **Correcting for Sensor Color Sensitivity:** Cameras aren't equally sensitive to every color of light. The extracted raw brightness is divided by the sensor's own QE curve to correct for this: $F_{\text{cal}}(\lambda) = F(x(\lambda)) / \text{QE}(\lambda)$.
5. **An Alternative Path for Unstacked Frames:** Alongside the single-stacked-image approach above, there's a second path that works directly on a target's raw, unstacked frames, grouped into observing sessions the same way the photometry pipeline does (Section 5.2, concept 1). Each session's stars are identified once against a real star catalog — reusing an existing WCS from that session's own files when one is available, as in Section 4 — and every frame in that session then extracts a spectrum for those same, already-identified stars. This gives each spectrum a stable, real star identity shared across the whole session, instead of every frame producing its own disconnected, unidentified detection.

### 6.3 Pipeline Theory of Operations

Table 6 lays out the spectroscopy pipeline step by step.

**Table 6.** Spectroscopy pipeline execution sequence and operations.

| Step | Pipeline Phase | Inputs & Outputs | Description |
|---|---|---|---|
| 1 | Zero-Order Anchoring | **In:** Spectral image & star positions<br>**Out:** Zero-order center $(x_0, y_0)$ | Find the star's normal (undispersed) position to use as the spectrum's starting point. |
| 2 | Angle Detection | **In:** Spectral image & zero-order position<br>**Out:** Spread angle $\theta_{\text{disp}}$ & direction | Measure the angle at which the spectrum is spread out, to align the extraction. |
| 3 | Trace Extraction | **In:** Image, angle $\theta_{\text{disp}}$, aperture size<br>**Out:** 1D brightness profile $F(x)$ | Add up pixel brightness along the spectrum's path via Eq. (7). |
| 4 | Wavelength Calibration | **In:** 1D profile $F(x)$ & known Balmer lines<br>**Out:** Wavelength map $\lambda(x)$ | Fit the grating equation (Eq. (8)) using the known Balmer absorption lines. |
| 5 | Color Response Correction | **In:** Wavelength map $\lambda(x)$ & QE curve<br>**Out:** Calibrated spectrum $F(\lambda)$ | Divide the raw spectrum by the sensor's QE curve to correct for its color sensitivity. |

### 6.4 Quality Metrics and Spectral Validation

#### 6.4.1 Before Extracting: Checking the Input
* **Zero-Order Saturation Ceiling:** The reference star's brightest pixel must not be close to overexposed, or the zero-order position $(x_0, y_0)$ can't be trusted.

#### 6.4.2 After Extracting: Checking the Spectrum
* **Calibration Fit Check:** If the wavelength calibration doesn't line up well enough with the known Balmer reference lines, it's rejected and retried with looser line-detection settings.

---

## 7. Moving Object Detection Pipeline

The moving object detection pipeline finds, tracks, and identifies solar-system objects — asteroids and comets — across a sequence of individual (unstacked) photos.

### 7.1 Purpose & Interfaces

* **Inputs:** A sequence of unstacked, plate-solved light frames, their observation timestamps $t_i$, and their WCS solutions.
* **Outputs:** Straight-line motion tracks, each candidate's velocity $(\dot{\alpha}, \dot{\delta})$, matches against a database of known object positions (an ephemeris), and an Asteroid Recovery Quality Record.

### 7.2 Major Concepts and Governing Equations

Stacking averages moving objects away — they land in a different spot in every frame, so stacking blurs them into nothing. Detection has to work on individual, unstacked photos instead, in five steps:

1. **Single-Frame Detection:** Finds every bright point source in each raw exposure.
2. **Filtering Out Obvious Non-Movers:** Chains raw detections together across frames and throws out any chain whose position barely changes, either in pixels (a stuck "hot" pixel that's always in the same spot) or in sky coordinates (a normal, stationary star that an earlier step missed). This filtering works without needing to check against any external star catalog.
3. **Persistence Filtering:** Only keeps chains that show up in at least $M \ge 3$ consecutive photos, which rules out one-off cosmic ray hits.
4. **Fitting a Straight-Line Path:** For each surviving chain, fits how its position drifts in Right Ascension ($\Delta\alpha_m\cos\delta$) and Declination ($\Delta\delta_m$) against time $t_m$, using ordinary least-squares regression (the standard "best-fit line" method):

$$
\Delta\alpha_m \cos\delta = \dot{\alpha}\,t_m + c_\alpha, \qquad \Delta\delta_m = \dot{\delta}\,t_m + c_\delta \tag{9}
$$

   How well each fit matches a straight line is measured with the coefficient of determination, $R^2$ (a standard statistic between 0 and 1, where 1 means a perfect straight line). A track is rejected if its weaker-fitting axis (RA or Dec) falls below a minimum $R^2$.
5. **Checking Against Known Objects:** Compares each straight-line motion path against SkyBoT, an online database that predicts where known solar-system objects should be at a given time (an ephemeris), by checking a small circular patch of sky around the candidate's predicted position (a "cone search"). A match confirms the object's identity; no match may mean a new discovery.

### 7.3 Pipeline Theory of Operations

Table 7 lays out the moving object detection pipeline step by step.

**Table 7.** Moving object detection pipeline execution sequence and operations.

| Step | Pipeline Phase | Inputs & Outputs | Description |
|---|---|---|---|
| 1 | Single-Frame Detection | **In:** Unstacked, plate-solved frames<br>**Out:** Raw point-source positions $(x_i, y_i)$ | Detect point sources on each individual exposure. |
| 2 | Filtering Non-Movers | **In:** Chained candidate detections<br>**Out:** Likely moving candidate list | Discard chains whose position barely changes, in pixels or sky coordinates, to isolate real moving candidates. |
| 3 | Persistence Linkage | **In:** Candidate list across timestamps $t_m$<br>**Out:** Multi-frame detection chains | Keep only chains detected across $M \ge 3$ consecutive frames, to reject cosmic ray hits. |
| 4 | Straight-Line Fitting | **In:** Multi-frame detection chains<br>**Out:** Velocity $(\dot{\alpha}, \dot{\delta})$ & per-axis $R^2$ | Fit a straight-line path via Eq. (9) on each axis, and reject tracks that don't fit a line well. |
| 5 | Catalog Cross-Match | **In:** Motion paths & SkyBoT database<br>**Out:** Asteroid matches & Recovery Record | Check each motion path against known solar-system object positions to confirm its identity. |

### 7.4 Quality Metrics and Motion Track Validation

#### 7.4.1 Before Fitting: Checking Detections
* **Single-Frame Detection Threshold:** A candidate must be bright enough — a fixed multiple of the background noise — to show up clearly on a single unstacked photo.

#### 7.4.2 After Fitting: Checking the Track
* **Straight-Line Fit Confidence ($R^2$):** Each candidate's weaker-fitting axis must clear a minimum $R^2$, rejecting motion that isn't well described by a straight line.
* **Catalog Match Radius:** A candidate only counts as a SkyBoT match if its fitted position falls within a fixed angular distance of a cataloged object.

---

## 8. Empirical Validation Campaign & Session Results

To check how well this design works in practice, it was tested on real observing sessions captured with a ZWO ASI 533MM Pro camera ($3.76\,\mu\text{m}$ pixel size).

### 8.1 Validation Datasets and Setup

Eight observing sessions were processed through all five pipelines:
1. **Vega Session (Spectroscopy):** A Star Analyzer 200 grism sequence ($N = 160$ light exposures, 270s total exposure time).
2. **M 13 Session (Spectroscopy & Stacking):** A dense star cluster grism field ($N = 86$ light exposures, 2520s total exposure time).
3. **Alcor Session (Spectroscopy):** A multi-frame grism sequence ($N = 138$ light exposures, 1080s total exposure time).
4. **NGC 2244 Session (Photometry):** An open star cluster time-series sequence ($N = 29$ light exposures, 10,800s total exposure time).
5. **M 81 Session (Photometry & Stacking):** A deep spiral-galaxy sequence, originally $N = 46$ light exposures (7,230s total exposure time) at the time of the stacking/astrometry results below. Since then, ongoing observation grew the same target's data to $N = 258$ light exposures across 8 separate observing sessions (2023-05 through 2026-05). That growth in the *number of sessions*, not just the number of frames, is what exposed the photometry cross-session tracking problem covered in Finding 6 (Section 8.3); it's a separate result from the single-session stacking/astrometry numbers below.
6. **NGC 2903 Session (Photometry):** A deep galaxy field time series ($N = 36$ light exposures, 14,400s total exposure time).
7. **NGC 2403 Session (Stacking, Astrometry & Moving Objects):** A wide-field galaxy sequence ($N = 70$ light exposures, 13,740s total exposure time).
8. **NGC 1893 Session (Stacking & Astrometry):** An open cluster field ($N = 49$ light exposures, 7,800s total exposure time).

All eight sessions were processed through the full pipeline sequence described in Sections 3–7 — calibration and outlier rejection, sky-position solving, brightness comparison, spectrum extraction, and moving-object tracking — followed by each pipeline's own after-the-fact quality check (Sections 3.4.2, 4.4.2, 5.4.2, 6.4.2, and the asteroid-detection equivalent).

### 8.2 Empirical Results Across All Five Pipelines

Table 8 summarizes the results.

**Table 8.** Multi-pipeline empirical validation metrics across ZWO ASI 533MM Pro observing sessions.

| Subsystem | Target Session | Key Metric Tested | Measured Value | Standard / Floor | Verdict |
|---|---|---|---|---|---|
| **Stacking** | M 81 Session | Rejection Cutoff $\sigma(N)$ & Sharpness Ratio $R_{\text{FWHM}}$ | $R_{\text{FWHM}} = 1.03$, Rejected: $0.42\%$ | $R_{\text{FWHM}} \le 1.20$ | **Passed** (Nominal) |
| **Stacking** | NGC 2403 Session | Optical Alignment Jitter Detection | $R_{\text{FWHM}} = 1.20$, Rejected: $0.85\%$ | $R_{\text{FWHM}} \ge 1.20$ flag | **Flagged** (Jitter Warning) |
| **Astrometry** | NGC 1893 Session | WCS Solve & SIMBAD Star Matching | WCS Resolved, 10 SIMBAD Matches | Success Flag = True | **Passed** (Nominal) |
| **Astrometry** | M 45 Session | Bright Cluster Star Matching & Distortion Fit | WCS Resolved, 12 SIMBAD Matches | Success Flag = True | **Passed** (Nominal) |
| **Photometry** | NGC 2244 Session | Ensemble Brightness Scatter Floor | $\sigma_m \le 0.012\text{ mag}$, $C_v > 0.1$ candidates | $C_v \le 0.10$ floor | **Passed** (Nominal) |
| **Photometry** | M 81 Session (8-session, current) | Cross-Session Tracking Bug & Fix | Before fix: 85–94% zero-brightness frames, 75% of the brightest fifth of stars wrongly flagged variable. After fix: 223/258 frames processed correctly, remaining false flags matched the faintest stars as expected | 0 cross-session tracking failures | **Passed** (Post-Fix) |
| **Photometry** | M 81 Session (8-session, current) | Cross-Session Star Matching, Repeatability | 11,392 session-scoped stars matched down to 8,422 (2,970 merges); a repeat run added, removed, or changed 0 rows | Same result every time it's run | **Passed** (Nominal) |
| **Spectroscopy** | Vega Session | Balmer Line Wavelength Calibration Fit | $\text{RMS}_{\Delta \lambda} = 0.42\text{ nm}$ ($\mathrm{H}\beta, \mathrm{H}\gamma, \mathrm{H}\delta$) | $\text{RMS}_{\Delta \lambda} \le 1.0\text{ nm}$ | **Passed** (Nominal) |
| **Spectroscopy** | M 13 Session | Cluster Zero-Order Star Tracking | 86 frames extracted, 0 tracking failures | Position stable to $\pm 0.2\text{px}$ | **Passed** (Nominal) |
| **Asteroid Recovery** | NGC 2403 Session | Full Detection & Filtering Sequence | $M \ge 3$ persistence, $R^2 \ge 0.98$ fit | 0 false moving-object tracks | **Passed** (Nominal) |

### 8.3 Key Empirical Findings

1. **Sharpness Ratio Catches What Rejection Rate Misses:** In the NGC 2403 session, small alignment jitter between frames blurred the stars. Only $0.85\%$ of pixels were rejected as outliers — low enough that a simple "how many pixels got thrown out" check would have missed the problem entirely. But the whole-image sharpness ratio hit the $R_{\text{FWHM}} = 1.20$ warning threshold and correctly flagged it. This confirms that measuring overall star sharpness catches session-level quality problems that pixel-rejection counts alone would miss.
2. **Wavelength Calibration Precision:** Fitting the grating equation to the Vega spectrum achieved a residual error of $\text{RMS}_{\Delta \lambda} = 0.42\text{ nm}$ across the Balmer hydrogen lines used for calibration, comfortably inside the $\le 1.0\text{ nm}$ precision needed for reliable brightness-vs-wavelength analysis.
3. **Ensemble Photometry Stability:** On the 3-hour NGC 2244 sequence, comparing each star's brightness against a group of similarly bright field stars — specifically, the stars ranked 100th to 300th brightest — suppressed atmospheric brightness fluctuations down to a noise floor of $\sigma_m \le 0.012\text{ mag}$ for non-variable stars.
4. **Moving-Object Filtering Works as Designed:** Tracking across unstacked exposures successfully eliminated single-frame cosmic rays and stationary hot pixels, using the $M \ge 3$ persistence requirement and the $R^2 \ge 0.98$ straight-line fit requirement.
5. **A Faster Way to Match Nearby Stars:** Before comparing angular distances between stars, the pipeline now first narrows the search to only the stars that could plausibly be close together on the sky, using a quick bounding-box check. On a dense field like M 81 (150,000 detected sources across 46 frames), this eliminated 99.9% of star pairs that were never going to be close enough to match anyway, cutting the total run time for this step from over 20 minutes down to a few seconds — a 50 to 100 times speedup — while producing exactly the same matches every time as the slower, exhaustive approach.
6. **A Real Bug in Cross-Session Photometry:** As the M 81 target's data grew to 8 observing sessions, testing found that its brightness-tracking step was, incorrectly, tracking stars across the *entire* observation history in one pass, using a single reference frame from whichever session happened to come first. Since a star's exact pixel position is only stable within one observing session, this corrupted almost all of the affected stars' measurements: 85–94% of frames read exactly zero brightness for a given star, across every brightness range. The resulting noise measurements disproportionately mislabeled the stars in the brightest fifth of the field (a quintile) as variable — 75% of that group flagged, versus only 3–10% in the other four-fifths — which is backwards from what should happen, since noise normally affects faint stars the most. A single-session comparison target (NGC 2903) showed none of this problem. The fix was to scope each brightness-tracking run to one observing session at a time (Section 5.2). Re-tested against the same real M 81 data, incorrect "brightest star" flagging dropped from 75% down to 14%, and the remaining variability correctly shifted to the faintest fifth of stars (46%, closely matching NGC 2903's own baseline of 47%) — the expected pattern once the bug was fixed. The number of distinct stars found also rose from 1,353 (one shared reference frame for all 8 sessions) to 11,392 (each of the 8 sessions using its own reference frame, counted separately rather than merged — see Finding 7).
7. **Matching the Same Star Across Sessions:** Fixing Finding 6 made each session's measurements correct on their own, but left every session's stars as unrelated entries with no way to combine them into one continuous light curve per star. Implementing the cross-session star-matching described in Section 5.2 (concept 3), and testing it against the same real 8-session M 81 data, combined the previous 11,392 separate entries down to 8,422 (2,970 successful merges of the same star seen in different sessions). Running the exact same match twice in a row produced identical results both times, confirming it's fully repeatable. Testing at this real-world scale (thousands of stars per session) surfaced two problems that smaller test data hadn't: first, identifying stars by catalog name was accidentally also triggering a live lookup to an online star catalog for every single star on every successful run, adding a lot of unnecessary network delay for a step that only actually needed the already-solved sky position; that lookup was removed once it was no longer needed. Second, an early version of the cross-session matching compared every star to every other star one pair at a time, which doesn't scale — on the real M 81 data, this took over 45 minutes without finishing, and had to be replaced with a much faster, position-indexed search that solved an equivalent test case in 0.17 seconds. Separately, one of M 81's 8 sessions turned out to reference a different target's stacked image altogether, due to a pre-existing data-labeling mistake in the library, unrelated to the matching logic itself — and this pipeline correctly excluded just that one session from the results, confirming that one bad session doesn't spoil the other seven (Section 5.3, item 4).

### 8.4 Future Recommended Target Additions

While the current 8-target validation set covers all five pipelines, the following additional target types are recommended for future testing:

1. **Eclipsing Binary or Exoplanet Transit Target (Photometry):** Observations of a known short-period eclipsing star pair or exoplanet host (e.g., *Algol / $\beta$ Persei*, *RR Lyrae*, or *WASP-12b*) to test the pipeline's ability to detect a periodic dimming pattern and measure its depth (about $\Delta m \approx 0.015\text{ mag}$).
2. **Emission-Line Nebula Target (Spectroscopy):** Grism observations of a planetary nebula (e.g., *Ring Nebula / M 57* or *Dumbbell Nebula / M 27*) to test wavelength calibration against bright emission lines — $[\mathrm{OIII}]$ ($500.7\text{ nm}$) and $\mathrm{H}\alpha$ ($656.3\text{ nm}$) — alongside Vega's absorption-line spectrum.
3. **Asteroid Field Near the Ecliptic (Moving Object Detection):** A field along the ecliptic plane (the sky path the planets and most asteroids follow) containing known, cataloged asteroids, to test matching against an official minor-planet catalog alongside SkyBoT.

## 9. Conclusion

This document presented a single, unified design for amateur astronomy image processing. By organizing every pipeline around one shared Observation Target, the design connects stacking, astrometry, photometry, spectroscopy, and moving object detection into one consistent system.

---

## Acknowledgments

This design builds on several open-source tools and public services rather than reimplementing their work: Siril for frame stacking and registration; Astropy [6] and its affiliated packages photutils and specutils for FITS handling, source detection, aperture photometry, and spectral data structures; Astrometry.net [7] for blind plate solving; the SIMBAD astronomical database [8] for star identification; and SkyBoT [9] for solar-system ephemeris cross-matching.

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
