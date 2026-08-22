# Astrometrics

**Making Amateur Astronomy and Citizen Science More Accessible**

## Overview

A desktop application and Python library for telescope control, session planning, image processing, and astronomical data analysis for amateur astronomers and citizen scientists.

Rather than juggling separate tools for capturing photos, controlling mounts, calibration, and analysis, this software provides a complete toolset divided into three core components:

1. **User Interface Application (`ui/` & `backend/`)**: An interactive desktop app for controlling equipment, reviewing images, stacking sub-exposures, planning imaging nights, and exploring an interactive sky map.
2. **Astrometrics Scientific Library (`astrometricslib/`)**: A Python science library for image calibration, star registration, frame stacking, star brightness measurement (photometry), spectroscopy, and asteroid tracking.
3. **Wayfinding Navigation Library (`wayfindinglib/`)**: A Python automation library for telescope control, sequence scheduling, autoguider monitoring, weather safety protection, and session logging.

---

## Quick Start

To launch the desktop application:

```bash
./build/linux/run_astrometrics.sh start
```

---

## Desktop Application Overview

The desktop application provides 6 primary display workspaces:

- **Image Viewer**: View captured photos, inspect image metadata, adjust contrast and brightness, and overlay catalog star labels.
- **Image Processing**: Calibrate raw sub-exposures with darks and flats, align stars, filter out degraded frames, and stack images for clean results.
- **Astronomy Manager**: Measure star brightness over time (photometry), search for exoplanet transits, and extract and calibrate 1D star spectra.
- **Planetarium**: Explore an interactive 3D sky map, view camera field-of-view outlines, and command telescope slews to target objects.
- **Observatory Manager**: Monitor and control telescope mounts, focusers, filter wheels, autoguiders, and weather safety sensors.
- **Observation Manager**: Plan exposure sequences, check target visibility across the night, author mosaic grids, and run automated imaging queues.

---

## Software Component Summary

| Component | Path | Core Role |
|---|---|---|
| **User Interface Application** | `ui/`, `backend/` | Interactive desktop application combining the user dashboard (`ui/`) and application server (`backend/`) for equipment control, image processing, and sky map navigation. |
| **Astrometrics Library** | `astrometricslib/` | Scientific Python library for image calibration, frame stacking, photometry, and 1D spectroscopy. |
| **Wayfinding Library** | `wayfindinglib/` | Observatory control Python library for telescope slewing, session planning, autoguider monitoring, and weather safety. |

---

## Python Libraries Overview

The underlying Python libraries provide standalone tools for data processing and observatory control:

### 1. Astrometrics Scientific Library (`astrometricslib`)
Scientific engine for image processing and analysis:
- **Frame Stacking**: Calibrate raw images with darks and flats, align stars, and stack frames to eliminate noise and satellite trails.
- **Astrometry**: Identify stars on images and solve celestial coordinates.
- **Photometry**: Measure star brightness over time, plot light curves, and search for exoplanet transit dips.
- **Stellar Spectroscopy**: Extract 1D star spectra, calibrate wavelengths (Å), fit continuum baselines, and identify element absorption lines.
- **Moving Object Detection**: Detect moving solar-system objects across time-series photos and trace their trajectories.

### 2. Wayfinding Navigation Library (`wayfindinglib`)
Observatory control, sequence planning, and safety execution engine:
- **Equipment Control**: Command telescope mounts, focusers, cameras, and filter wheels, and execute autofocus runs.
- **Session Planning**: Author target exposure sequences, calculate night observing windows, check moon separation, and plan mosaic grids.
- **Automated Execution**: Advance imaging queues, execute automated meridian flips, monitor autoguider drift, and trigger emergency parking if rain or clouds are detected.

---

## Documentation Guide

User manuals, step-by-step processing tutorials, and technical architecture specifications are located in the [`documentation/`](documentation/) directory:

### User Guides & Manuals
* **[Desktop User Manual](documentation/user_interface/user_guides/User_Manual.md)**: Official user handbook covering all display workspaces with screenshots, control descriptions, status indicators, and keyboard shortcuts.
* **[Observatory Control & Automation Guide](documentation/user_interface/user_guides/Observatory_Control_and_Automation_Guide.md)**: Operational guide for telescope setup, plate solving alignment, autofocusing, autoguider calibration, and weather safety interlocks.
* **[Image Processing & Science Tutorial](documentation/user_interface/user_guides/Image_Processing_and_Analysis_Tutorial.md)**: Step-by-step tutorial for image calibration, frame stacking, exoplanet transit photometry, and 1D spectroscopy wavelength calibration.

### Scientific & Automation Specifications
* **[Astrometrics Library Specifications](documentation/library_design/Astrometrics_Library_Architecture.md)**: Overview of observational data models, stacking algorithms, photometry, spectroscopy, and asteroid recovery.
* **[Wayfinding Library Specifications](documentation/library_design/Wayfinding_Library_Architecture.md)**: Overview of observatory control architecture, capability delegation, safety interlocks, and automated session execution.
* **[User Interface Technical Reference](documentation/user_interface/technical_reference/User_Interface_Architecture.md)**: Architecture details for the desktop application, data state management, 3D planetarium rendering, and API automation endpoints.

---

## License

Astrometrics is released under the [MIT License](LICENSE).

Some bundled assets are the work of others and remain under their own terms:

* **Hipparcos bright-star catalog** (`wayfindinglib/drivers/catalog/bright_star_catalog.sqlite`): derived from the ESA Hipparcos mission catalogue.
* **Constellation line data** (`wayfindinglib/drivers/catalog/constellation_lines.json`): derived from [d3-celestial](https://github.com/ofrohn/d3-celestial), Copyright (c) 2015 Olaf Frohn, BSD-3-Clause.
* **Plotly.js** (`public/js/plotly.min.js`): Copyright 2012-2025 Plotly, Inc., MIT.
