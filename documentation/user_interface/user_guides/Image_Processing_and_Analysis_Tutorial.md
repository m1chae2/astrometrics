# Astrometrics: Image Processing and Science Analysis Tutorial

*Version 2.1 · 2026-08-21 · Status: current*

## Overview

This tutorial provides end-to-end instructions for calibrating, registering, and stacking astronomical images, as well as conducting differential photometry, exoplanet transit detection, and 1D spectroscopy analysis within the Astrometrics User Interface.

---

## 1. Introduction

This guide details the step-by-step workflow for using Astrometrics to process images, from basic calibration and stacking to photometry and spectroscopy.


---

## 2. Calibration & Pre-Processing

Before frames can be aligned and stacked, they must be calibrated to remove sensor read noise, thermal dark current, and optical vignetting.

![Image Processing Workbench](./assets/image_processing.png)

### 2.1 Calibration Workflow
1. Switch to **Image Processing** mode.
2. Select a target name from the **Observation Targets** list in the left sidebar.
3. Under **Calibration Assets**, select the matching master calibration frames that correspond to the camera's temperature, gain, and optical train:
   - **Master Bias**: Subtracts sensor read noise. Ensure the bias frame matches the camera gain.
   - **Master Dark**: Subtracts thermal dark current (heat noise). Ensure the dark frame matches the exposure time and sensor temperature of the light frames.
   - **Master Flat**: Corrects optical vignetting (dark corners) and dust donuts. Ensure the flat frame was taken with the exact same optical setup.



> [!WARNING]
> Do not mix and match calibration frames from different temperatures or binning modes. Applying an incompatible Master Flat will over-correct or under-correct vignetting, leaving bright or dark rings in the final image.

---

## 3. Quality Filtering & Frame Selection

Even with perfect calibration, passing clouds, wind gusts, or satellite trails can ruin individual sub-exposures. The pipeline must reject these degraded frames before stacking.

### 3.1 Frame Rejection
1. Inspect the **Frame Analysis** metric table, which lists the computed FWHM (Full Width at Half Maximum) and background sky brightness for every frame.
2. Drag the **FWHM Cutoff** slider to reject sub-exposures where star FWHM exceeds the target threshold (e.g., $>3.5''$). This ensures only the sharpest frames contribute to the final stack.
3. Adjust the **Roundness Floor** to reject trailed frames caused by wind gusts or guiding errors ($\text{Roundness} < 0.75$).
4. Check the **Background ADU Ceiling** to exclude sub-exposures contaminated by moonlight, passing clouds, or twilight glow.

---

## 4. Alignment & Star Registration

To stack the images, the engine must precisely align them by mapping the star patterns between each frame.

### 4.1 Registration Workflow
1. Select a **Reference Frame**. Choose a frame from the middle of the session with a high star count, low FWHM, and high roundness.
2. Set the **Alignment Algorithm** to `Triangle Matching` (ideal for deep-sky fields) or `Phase Correlation` (ideal for planetary or sparse fields).
3. Click **Align Frames**. The engine maps the star patterns in every image and rotates/translates them to perfectly match the reference frame.

---

## 5. Sigma-Clipped Frame Stacking

Stacking combines the aligned frames to increase the Signal-to-Noise Ratio (SNR) and mathematically reject transient artifacts like satellite trails and cosmic rays.

### 5.1 Stacking Workflow
1. Under **Stacking Engine Configuration**, select the rejection algorithm.
   - **Winsorized Sigma Clipping**: Recommended for stacks $>15$ frames. Iteratively rejects pixels that deviate significantly from the median, replacing them with the threshold limit before averaging.
   - **Linear Fit Clipping**: Recommended for stacks with changing sky gradients (e.g., imaging through twilight).
2. Set the Rejection Sigmas: typically $\sigma_{\text{low}} = 3.0$ and $\sigma_{\text{high}} = 3.0$. A lower high-sigma (e.g., $2.5$) is more aggressive at removing satellite trails but risks clipping the cores of bright stars.
3. Click **Process Target**.
4. The engine processes the sequence and outputs a calibrated, 32-bit floating-point FITS file (`<Target>_Stacked.fits`).

---

## 6. Differential Photometry & Exoplanet Transit Analysis

Astrometrics can extract precise light curves to detect variable stars or exoplanet transits.

![Astronomy Manager Interface](./assets/astronomy_manager.png)

### 6.1 Photometry Workflow
1. Switch to **Astronomy Manager** mode.
2. Load the stacked sequence and select a target variable star or exoplanet host star.
3. Select 3-5 stable comparison stars in the same field of view. The engine uses these reference stars to cancel out passing clouds and changing atmospheric transparency.
4. Click **Run BLS Transit Search** to automatically search for periodic dips in the light curve:
   - Set the Period Range (e.g., $0.5$ to $10.0$ days).
   - Click **Execute**.
   - Review the best-fit transit light curve, calculated orbital period, transit depth, and the phase-folded plot.



---

## 7. 1D Spectroscopy Calibration & Equivalent Width

For targets captured with a diffraction grating (e.g., Star Analyser 100), Astrometrics extracts and calibrates 1D spectral profiles.

### 7.1 Spectroscopy Workflow
1. Select a target object that contains `SPEC` (spectroscopy) sub-exposures.
2. Click **Extract Profile** to perform optimal aperture extraction along the dispersion axis.
3. Wavelength Calibration: Match known arc lamp emission lines or prominent absorption lines to build a wavelength scale (in Angstroms).
4. Click **Fit Continuum** and drag a selection over an absorption line to automatically calculate the Equivalent Width, a physical measure of the line's strength.

*(For polynomial fitting degrees and Equivalent Width integration math, see the {py:class}`~astrometricslib.api.stars.StellarCatalog` API Reference)*
