<a href="https://astrometrics.readthedocs.io/en/latest/?badge=latest"><img align="right" src="https://readthedocs.org/projects/astrometrics/badge/?version=latest" alt="Documentation Status"></a>

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
./astrometrics.sh start
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

The full documentation is published at **[astrometrics.readthedocs.io](https://astrometrics.readthedocs.io/)**. It is rendered, searchable and the recommended way to read it. The source files are in the [`documentation/`](documentation/) directory. [About these docs](documentation/About_These_Docs.md) maps every document. The site has these sections:

* **Get started.** [Installation](documentation/Installation.md), [Getting started](documentation/Getting_Started.md) and the [library quick start](documentation/how_to/library_quick_start.md).
* **Tutorials.** The [desktop application guides](documentation/user_interface/user_guides/User_Manual.md) and the interactive notebook tutorials.
* **How-to guides.** Task recipes in [`documentation/how_to/`](documentation/how_to/tune_for_your_setup.md), such as tuning the tool for your setup.
* **Explanation.** The [Astrometrics library architecture](documentation/library_design/Astrometrics_Library_Architecture.md) and [Wayfinding library architecture](documentation/library_design/Wayfinding_Library_Architecture.md) papers, and the [pipeline review guides](documentation/library_design/pipelines/index.md).
* **Reference.** The Python API reference and the [Astrometrics](documentation/library_design/Astrometrics_Library_Implementation.md) and [Wayfinding](documentation/library_design/Wayfinding_Library_Implementation.md) implementation papers.
* **Evidence.** The [validation register](documentation/library_design/validation/index.md) and the [pipeline audit report](documentation/library_design/Pipeline_Audit_Report.md).

To contribute, read [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

Astrometrics is released under the [MIT License](LICENSE).

Some bundled assets are the work of others and remain under their own terms:

* **Hipparcos bright-star catalog** (`wayfindinglib/drivers/catalog/bright_star_catalog.sqlite`): derived from the ESA Hipparcos mission catalogue.
* **Constellation line data** (`wayfindinglib/drivers/catalog/constellation_lines.json`): derived from [d3-celestial](https://github.com/ofrohn/d3-celestial), Copyright (c) 2015 Olaf Frohn, BSD-3-Clause.
* **Plotly.js** (`public/js/plotly.min.js`, v4.1.2, a copy of the `plotly.js-dist-min` npm package): Copyright 2012-2026 Plotly, Inc., MIT.
