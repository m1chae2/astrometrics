# Astrometrics User Interface: Desktop User Manual

*Version 2.2 · 2026-08-21 · Status: current*

## Abstract

This manual is the official desktop user handbook for the Astrometrics User Interface (`ui/`). The application provides real-time telescope telemetry, image inspection, science-grade calibration and stacking, photometric and 1D spectroscopic analysis, sequence planning, and planetarium navigation.

---

## Table of Contents

- [1. Introduction](#1-introduction)
- [2. Getting Started & Setup](#2-getting-started--setup)
  - [2.1 Launching the Application](#21-launching-the-application)
  - [2.2 Initial Hardware & Backend Connection](#22-initial-hardware--backend-connection)
- [3. Interface Topology & Global Navigation](#3-interface-topology--global-navigation)
  - [3.1 Top Status Bar](#31-top-status-bar)
  - [3.2 Global Hotkeys & Built-in Terminal](#32-global-hotkeys--built-in-terminal)
- [4. Image Viewer & Target Inspector (`TargetDisplay`)](#4-image-viewer--target-inspector-targetdisplay)
  - [4.1 Step-by-Step Instructions](#41-step-by-step-instructions)
- [5. Image Processing & Stacking (`ImageProcessingDisplay`)](#5-image-processing--stacking-imageprocessingdisplay)
  - [5.1 Step-by-Step Stacking Workflow](#51-step-by-step-stacking-workflow)
- [6. Astronomy Manager (`AstronomyDisplay`)](#6-astronomy-manager-astronomydisplay)
  - [6.1 Photometry & Transit Search](#61-photometry--transit-search)
  - [6.2 1D Spectroscopy Analysis](#62-1d-spectroscopy-analysis)
- [7. Observatory Manager (`ObservatoryDisplay`)](#7-observatory-manager-observatorydisplay)
  - [7.1 Control Panels & Operations](#71-control-panels--operations)
- [8. Observation Manager (`ObservationManager`)](#8-observation-manager-observationmanager)
  - [8.1 Authoring an Imaging Session](#81-authoring-an-imaging-session)
- [9. Planetarium & 3D Sky Map (`PlanetariumDisplay`)](#9-planetarium--3d-sky-map-planetariumdisplay)
  - [9.1 Sky Map Navigation & Controls](#91-sky-map-navigation--controls)
- [Appendices](#appendices)
  - [Appendix A: Keyboard Shortcuts Quick Reference](#appendix-a-keyboard-shortcuts-quick-reference)
  - [Appendix B: Telemetry Status Badges](#appendix-b-telemetry-status-badges)
  - [Appendix C: Display Capability Reference](#appendix-c-display-capability-reference)

---

## 1. Introduction

**Statement of need.** Managing an astronomical observatory and reducing its data often requires a fragmented ecosystem of disparate tools. Astrometrics unifies hardware control, mission planning, and high-fidelity data reduction into a single desktop interface, ensuring tight integration between acquisition telemetry and final scientific analysis.


---

## 2. Getting Started & Setup

The Astrometrics User Interface runs as a cross-platform desktop application built on Electron and Vite.

### 2.1 Launching the Application
1. **Desktop App**: Double-click the `Astrometrics` application icon or run `npm start` / `./build/linux/run_astrometrics.sh start` from the terminal.
2. **Web Mode**: Open your browser and navigate to `http://localhost:5173` (or the configured port).

### 2.2 Initial Hardware & Backend Connection
When launched, the application automatically connects to the local backend engine (`http://localhost:8000`) and the INDI hardware server (`127.0.0.1:7624`).
- **Connection Badges**: Check the top status bar. A green **Connected** badge indicates active communication.
- **Configuring Host/Port**: If your telescope or INDI server runs on a remote IP (e.g., StellarMate or Asiair), click the **Settings Drawer** (gear icon) in the top left, select **System Form**, and enter the IP address under **INDI Host**.

---

## 3. Interface Topology & Global Navigation

The desktop workspace is divided into a persistent top **Status Bar** and a main viewport containing 6 displays.

### 3.1 Top Status Bar

![Top Status Bar](./assets/observatory_manager.png)

The top bar is visible across all modes and provides instant system telemetry and navigation:

- **Settings Drawer (Gear Icon)**: Opens global configuration options (module toggles, backend URIs, logging verbosity).
- **Mode Dropdown Menu**: Click the current mode name (e.g., `Observatory Manager`) to switch between workspaces.
- **Connection Status**:
  - `Connected` (Green): Backend engine online.
  - `Disconnected` (Red): Engine offline; auto-reconnecting.
- **Tracking Status**: Shows live telescope motion (`Tracking`, `Slewing`, `Parked`, `Not Tracking`).
- **Telemetry Readouts**: Real-time Telescope Altitude/Azimuth, Target RA/Dec, Temperature (°C), and Humidity (%).

### 3.2 Global Hotkeys & Built-in Terminal
- **Toggle Command Terminal**: Press `Ctrl + \`` to open the integrated debug console for viewing live system logs.
- **Switch Modes**: Use `Ctrl + 1` through `Ctrl + 6` to instantly switch between display modes.

---

## 4. Image Viewer & Target Inspector (`TargetDisplay`)

The **Image Viewer** is your primary workspace for reviewing captured target packages, inspecting FITS image headers, adjusting visual stretching, and identifying catalog stars.

![Image Viewer Workspace](./assets/image_viewer.png)

### 4.1 Step-by-Step Instructions

#### Opening and Selecting Images
1. Select a target from the **Targets** list in the left sidebar (e.g., `NGC 6992`, `M 16`, `M 42`).
2. Use the filter toggle buttons (`L`, `R`, `G`, `B`, `Ha`, `SPEC`) to filter frames by optical channel.
3. Click any image filename in the file tree to display it in the central canvas viewport.

#### Adjusting Canvas Stretch & Zoom
- **Pan**: Click and drag inside the main image canvas.
- **Zoom**: Scroll your mouse wheel or click the **Zoom Toolbar** buttons.
- **Stretch Algorithms**:
  - `Linear`: Standard raw pixel display.
  - `Asinh`: Preserves star color saturation in bright nebula cores.
  - `Logarithmic`: Enhances faint outer nebulosity.
  - `Histogram Equalization`: Maximizes contrast across dim fields.
- **Black / White Point Sliders**: Drag sliders under the viewport to adjust background clipping and saturation ceilings.

#### Inspecting FITS Headers & Star Profiles
- **FITS Header Inspector**: Type a keyword (e.g., `EXPTIME`, `GAIN`, `CRVAL1`) into the search bar in the right panel to view FITS header values.
- **Star Identification & PSF**: Click **Star ID** to overlay SIMBAD/Gaia catalog labels over detected stars and view measured Full-Width Half-Maximum (FWHM) values.

> [!TIP]
> Use the **Invert** function to flip black and white levels, which dramatically highlights faint satellite trails and cosmic ray strikes that may require rejection prior to stacking.

---

## 5. Image Processing & Stacking (`ImageProcessingDisplay`)

The **Image Processing** workbench provides calibration, alignment, quality selection, and frame stacking.

![Image Processing Workspace](./assets/image_processing.png)

### 5.1 Step-by-Step Stacking Workflow

#### Step 1: Load Target & Calibration Frames
1. Select your science target from the left sidebar.
2. In the right panel under **Session Frames**, verify your light sub-exposures are loaded.
3. Under **Calibration Assets**, select matching Master Dark, Master Flat, and Master Bias files.

#### Step 2: Set Quality Filters
Use the **Frame Analysis** sliders to filter out degraded sub-exposures prior to stacking:
- **FWHM Cutoff**: Set maximum acceptable star width in arcseconds.
- **Roundness Floor**: Set minimum star roundness to reject wind-shaken frames.
- **Background Ceiling**: Exclude sub-exposures affected by satellite passes or cloud glow.

#### Step 3: Run Stacking Engine
Use the following parameters to configure the stack:

```{eval-rst}
.. ui-action:: astrometricslib.api.processing.ProcessingPipelines.run_stacking
```

3. Click **Process Target** to initiate stacking. Progress is shown on the live progress bar.

---

## 6. Astronomy Manager (`AstronomyDisplay`)

The **Astronomy Manager** provides tools for stellar photometry light curves and 1D spectroscopy analysis.

![Astronomy Manager Workspace](./assets/astronomy_manager.png)

### 6.1 Photometry & Transit Search
1. **Load Target Data**: Select an identified variable star or exoplanet target from the sidebar list.
2. **Differential Photometry**: Choose up to 5 comparison stars in the field to normalize target brightness against atmospheric extinction.
3. **Exoplanet Transit Search (BLS)**:
   - Click **Run BLS Search**.
   - Enter min/max period bounds (e.g., 0.5 to 10.0 days).
   - The engine plots the folded phase light curve and displays the detected period, transit depth, and transit epoch.

### 6.2 1D Spectroscopy Analysis
1. Select a target with spectroscopic sub-exposures (`SPEC` filter tag).
2. **Aperture Extraction**: Click **Extract Profile** to trace the 1D spectrum from the 2D frame.
3. **Wavelength Calibration**: Match emission lines against Neon/Argon arc lamp lines to establish wavelength scaling (Å).
4. **Continuum Fitting & Line Identification**:
   - Fit spline/polynomial continuum baseline.
   - Identify Balmer absorption lines (e.g., H-alpha 6563 Å, H-beta 4861 Å).
5. **Equivalent Width**: Highlight an absorption or emission line to automatically calculate its equivalent width (line strength).

---

## 7. Observatory Manager (`ObservatoryDisplay`)

The **Observatory Manager** gives you direct manual control over your telescope mount, focuser, filter wheel, autoguider, and weather safety systems.

![Observatory Manager Workspace](./assets/observatory_manager.png)

### 7.1 Control Panels & Operations

#### Telescope Mount Control
- **Nudge Keypad**: Click `↑`, `↓`, `←`, `→` to manual nudge the mount. Adjust the **Slew Speed** slider from 1x (guide speed) to 600x (max slew).
- **Tracking Mode**: Toggle tracking `ON` or `OFF`. Select rate (`Sidereal`, `Lunar`, `Solar`).
- **Park / Unpark**: Click **PARK** to stow the mount in its home position; click **UNPARK** to enable slewing.

#### Mount Alignment & Iterative Solve History (`AlignmentStatus.tsx`)
- Inspect the **Alignment Status** log table to track iterative plate solving cycles.
- View status indicators (`solving` ⟳, `aligned` ✓, `warning` ⚠, `failed` ✗) along with coordinate deltas in arcseconds.

#### Focuser Step Control & Autofocus
- **Manual Nudge**: Click `Focus IN (-)` or `Focus OUT (+)` with step buttons (`10`, `50`, `100`, `500`, `1000`).
- **Autofocus Run**: Click **Start Autofocus** to measure stellar size across a V-curve and determine exact focus.

#### Guider Monitoring (PHD2)
- Inspect the live **Guiding Trends** chart to view real-time RA/Dec RMS errors in arcseconds.

> [!WARNING]
> **Safety Interlock.** Click the red **EMERGENCY STOP** button to immediately halt all hardware motion, park the mount, and force enclosure closure in case of imminent weather or mechanical failure.

### 7.2 Remote Target Ingestion

The Observatory Manager allows you to discover and download images captured by the remote hardware into your local library.

1. **Check for New Images**: Click the **Discover Remote Captures** button. The system will scan the telescope's onboard storage for new FITS files.
2. **Review Targets**: A list of unassociated remote targets will appear. You can see the target name, filter type, and number of sub-exposures.
3. **Download & Sync**: Select the targets you want and click **Download Selected**. The files will be transferred to your local machine and automatically registered into the Library Sidebar, ready for Image Processing.

*(For technical details on how the remote file protocols and target synchronizations are managed under the hood, see the {py:class}`~wayfindinglib.api.control_registry.ObservatoryControl` API Reference)*

---

## 8. Observation Manager (`ObservationManager`)

The **Observation Manager** is your session planner for building target lists, planning filter sequences, generating mosaic panels, and running execution queues.

![Observation Manager Workspace](./assets/observation_manager.png)

### 8.1 Authoring an Imaging Session

#### Step 1: Target Selection & Altitude Check
1. Select an object from the catalog dropdown (e.g., `NGC 6992`, `M 31`, `M 51`).
2. Review the **Target Status** card to confirm the object rises above your altitude horizon floor (e.g., >30 degrees).

#### Step 2: Build Filter Sequence
1. In the **Image Planner** panel, select exposure type (`Light`, `Dark`, `Flat`, `Bias`).
2. Choose filter (`L`, `R`, `G`, `B`, `Ha`), count (e.g., `20`), and exposure duration (e.g., `300s`).
3. Click **Add to List** to append the exposure sequence to your session plan.

#### Step 3: Mosaic Grid Creator (Optional)
1. In the **Mosaic Planner**, enter grid dimensions (e.g., `2 x 2` panels).
2. Set overlap percentage (e.g., `20%`).
3. Click **Create Mosaic** to automatically generate coordinates for all panel tiles.

#### Step 4: Execution Queue Control
1. Drag and drop targets in the **Execution Queue** to set priority order.
2. Click **Start Imaging Session** to begin unattended sequence execution.

---

## 9. Planetarium & 3D Sky Map (`PlanetariumDisplay`)

The **Planetarium** renders a 3D celestial sphere view of the sky above your observatory.

![Planetarium Workspace](./assets/planetarium.png)

### 9.1 Sky Map Navigation & Controls
- **Rotate & Tilt**: Left-click and drag anywhere on the sky canvas.
- **Zoom**: Scroll the mouse wheel to zoom in or out.
- **Layer Checkboxes**: Toggle visible sky layers (Stars, Grid, Constellations, FOV Outline, Telescope).
- **Right-Click Context Menu**: Right-click any celestial object in the sky to slew the telescope or add it to the Observation Manager queue.
- **Date & Time Controls**: Use the bottom time slider to simulate sky positions for future dates.

---

## Appendices

### Appendix A: Keyboard Shortcuts Quick Reference

| Shortcut | Action |
|---|---|
| `Ctrl + 1` | Switch to Image Viewer (`TargetDisplay`) |
| `Ctrl + 2` | Switch to Image Processing (`ImageProcessingDisplay`) |
| `Ctrl + 3` | Switch to Astronomy Manager (`AstronomyDisplay`) |
| `Ctrl + 4` | Switch to Observation Manager (`ObservationManager`) |
| `Ctrl + 5` | Switch to Observatory Manager (`ObservatoryDisplay`) |
| `Ctrl + 6` | Switch to Planetarium (`PlanetariumDisplay`) |
| `Ctrl + ~` | Toggle built-in terminal console |

### Appendix B: Telemetry Status Badges

| Badge | Color | Meaning |
|---|---|---|
| `Connected` | Green | INDI hardware driver bus active and responsive. |
| `Disconnected` | Red | Server connection lost; auto-retrying. |
| `Tracking` | Green | Mount actively tracking at sidereal rate. |
| `Slewing` | Amber | Mount moving to new target coordinates. |
| `Parked` | Blue | Mount parked in safe home position. |
| `Safe State` | Red / Flashing | Emergency interlock active; motion suspended. |

### Appendix C: Display Capability Reference

| UI Display | Primary Functional Capabilities |
|---|---|
| `PlanetariumDisplay` | 3D WebGL Sky Map, Constellation overlays, FOV rectangle projection, object context slews. |
| `ObservatoryDisplay` | INDI device manager, mount slew/track/park keypad, alignment status attempt tracking (`AlignmentStatus.tsx`), focuser steps, autofocus V-curves, PHD2 RMS guider plots, weather interlocks. |
| `ObservationManager` | Session queue creation, filter sequences, target altitude/moon visibility curves, 2x2 mosaic planner. |
| `ImageProcessingDisplay` & `TargetDisplay` | FITS image inspection, header search, star FWHM/HFR measurement, Master Dark/Flat/Bias calibration, star alignment, Winsorized Sigma Clipping stacking. |
| `AstronomyDisplay` | 1D stellar profile extraction, neon/argon arc lamp wavelength calibration, continuum baseline fitting, Balmer line identification, Equivalent Width, and BLS exoplanet transit light curve fitting. |
