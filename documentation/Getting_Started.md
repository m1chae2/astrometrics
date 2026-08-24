# Getting Started

*Version 1.1 · 2026-08-22 · Status: current*

## 1. Introduction

After completing the setup in `Installation.md`, you are ready to start using Astrometrics to process images and control your telescope.

Astrometrics is a desktop application designed to streamline astrophotography, photometry, and spectroscopy.

:::{note}
**Observatory Control:** The application currently handles image processing and basic telescope control. Planning and running automated observing nights is recommended to be managed by a dedicated INDI-compatible client like [KStars/Ekos](https://docs.kde.org/trunk5/en/kstars/kstars/ekos.html).
:::

## 2. Hardware Requirements

Astrometrics works with a wide variety of cameras and mounts using INDI. To capture images, you will need:
1. **A Tracking Mount:** A motorized mount to keep the stars stationary during exposures.
2. **A Camera:** A dedicated astronomy camera or DSLR.
3. **Optional (for Spectroscopy):** A diffraction grating like the Star Analyser 100 or 200 to split starlight into a spectrum.

## 3. Starting the Application

To open the desktop application, run the following command in your terminal:

```bash
./run_astrometrics.sh
```

## 4. Core Concepts

Before using the analysis tools in the desktop application, it is helpful to understand the primary concepts and processes Astrometrics uses:

### Image Types
- **Light Frames:** The actual pictures of your target (stars, galaxies, nebulas).
- **Dark Frames:** Pictures taken with the camera covered. These capture thermal noise (heat) from the camera sensor so it can be subtracted from your light frames.
- **Bias Frames:** Extremely short exposures taken with the camera covered. These capture the baseline electrical noise of the sensor.
- **Flat Frames:** Pictures of an evenly illuminated surface (like a glowing panel or the twilight sky). These are used to correct vignetting (dark corners) and dust spots on your camera sensor.

### Image Processing
- **Calibration:** The process of mathematically subtracting dark and bias frames, and dividing by flat frames, to clean the noise and artifacts from your light frames.
- **Stacking:** The process of aligning and combining dozens or hundreds of light frames into a single master image. This dramatically reduces random noise and reveals faint details that are invisible in a single frame.

### Scientific Analysis
- **Astrometry (Plate Solving):** The process of matching the stars in an image to a known star catalog. This determines the exact celestial coordinates the telescope is pointing at.
- **Photometry:** The measurement of a star's brightness over time. This is used to create light curves to detect variable stars or exoplanet transits.
- **Spectroscopy:** The process of splitting light into a spectrum using a diffraction grating. This allows for the identification of a star's chemical composition and temperature.

:::{note}
For a deep dive into the algorithms behind astrometry, photometry, and spectroscopy, see the [Image Processing Architecture](library_design/Astrometrics_Library_Architecture.md) document.
:::

## 5. Next Steps

Once the application is running, refer to the guides in the [Desktop Application section](user_interface/index.rst) to learn how to:
- Connect to your hardware and align your telescope mount.
- Process and stack your captured images.
- Analyze your data using the photometry and spectroscopy tools.

## 6. Scripting & Data Analysis

The desktop application provides a visual interface for operations powered by two underlying Python libraries:

1. **[`astrometricslib`](api/astrometricslib.rst)**: Handles image processing, stacking, star detection, and spectroscopy.
2. **[`wayfindinglib`](api/wayfindinglib.rst)**: Communicates with hardware to position the telescope and execute observation sessions.

Users can import these libraries directly to perform customized data processing, script automated telescope sequences, or run analysis pipelines.

A collection of [Jupyter Notebook tutorials](notebooks/index.md) demonstrates what complete data analysis workflows look like in practice. These notebooks, along with the complete [Python API reference](api/index.rst), are available on the main documentation page.

:::{note}
The API and scripting documentation assumes familiarity with programming and astronomical concepts.
:::
