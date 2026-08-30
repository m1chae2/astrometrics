# Learn Astrometrics: Interactive Notebook Tutorials

Welcome to **Learn Astrometrics**, an automatically discovered collection of
executable Jupyter Notebook tutorials (`.ipynb`) covering scientific image
processing, target catalog queries, photometry, spectroscopy, moving object
recovery, observation planning, and telescope control using `astrometricslib`
and `wayfindinglib`.

Each tutorial is an executable Jupyter Notebook stored in the
`documentation/notebooks/` directory. You can open and run them interactively
in VS Code or JupyterLab, or click any card below to view the rendered
notebook in the browser.

---

## Astrometrics Library Tutorials

::::{grid} 1 2 2 2
:gutter: 3

:::{grid-item-card} 1. Working with Targets
:link: /notebooks/astrometrics/user_guide/01_working_with_targets
:link-type: doc

In this tutorial, you will learn how to initialize the `Astrometrics` high-level interface, fetch targets from the catalog, and inspect the `Target` domain object.
:::

:::{grid-item-card} 2. Inspecting FITS Metadata
:link: /notebooks/astrometrics/user_guide/02_inspecting_fits_metadata
:link-type: doc

In this tutorial, you will learn how to use the `astrometrics.targets` API to extract FITS header information and check calibration frame statistics.
:::

:::{grid-item-card} 3. Image Stacking & Calibration
:link: /notebooks/astrometrics/user_guide/03_stacking_and_calibration
:link-type: doc

In this tutorial, you will learn how to run the Stacking Pipeline. This pipeline calibrates raw light frames with darks and flats, registers (aligns) them, and stacks them into a high signal-to-noise master frame.
:::

:::{grid-item-card} 4. Astrometry & Plate Solving
:link: /notebooks/astrometrics/user_guide/04_astrometry_and_plate_solving
:link-type: doc

In this tutorial, you will learn how to run the Astrometry Pipeline. This pipeline detects stars in a master frame, matches them against an online catalog (like Gaia or Tycho), and updates the WCS (World Coordinate System) metadata.
:::

:::{grid-item-card} 5. Photometry & Lightcurves
:link: /notebooks/astrometrics/user_guide/05_photometry_and_lightcurves
:link-type: doc

In this tutorial, you will learn how to run the Photometry Pipeline to extract time-series light curves for detected stars, which can be used to identify variable stars or exoplanet transits.
:::

:::{grid-item-card} 6. Spectroscopy Extraction
:link: /notebooks/astrometrics/user_guide/06_spectroscopy_extraction
:link-type: doc

In this tutorial, you will learn how to run the Spectroscopy Pipeline to extract 1D spectral data from 2D grating dispersion images.
:::

:::{grid-item-card} 7. Working with Stellar Catalogs
:link: /notebooks/astrometrics/user_guide/07_stellar_catalogs
:link-type: doc

In this tutorial, you will learn how to query the internal database for identified stars and inspect their cross-session observational data.
:::

:::{grid-item-card} 8. Visualization
:link: /notebooks/astrometrics/user_guide/08_visualization
:link-type: doc

In this tutorial, you will learn how to use the `astrometrics.visualization` registry to generate plots and dashboards.
:::

:::{grid-item-card} 9. End-to-End Image Processing: M 13
:link: /notebooks/astrometrics/user_guide/09_end_to_end_pipeline_M13
:link-type: doc

In this User Guide Cookbook, you will learn how to process a single real dataset (M 13) through all the available pipelines in a single workflow.
:::

::::

---

## Wayfinding Library Tutorials

::::{grid} 1 2 2 2
:gutter: 3

:::{grid-item-card} Mosaic Sequence Authoring: Multi-Panel Grid Planning
:link: /notebooks/wayfinding/planning/mosaic_sequence_authoring
:link-type: doc

Large astronomical targets—such as the Andromeda Galaxy (**M 31**), spanning over $3^\circ$ across the sky—exceed the field of view (FOV) of most telescope/camera combinations. To capture the full extent of such targets, astronomers shoot **multi-panel mosaics**.
:::

:::{grid-item-card} Observation Planning: Target Visibility & Site Sky Engine
:link: /notebooks/wayfinding/planning/observation_planning
:link-type: doc

Before slewing a telescope, an astronomer must evaluate whether a target is visible above the horizon, calculate its peak altitude window, and avoid atmospheric extinction.
:::

:::{grid-item-card} Remote Image Ingestion: StellarMate & INDI Synchronization
:link: /notebooks/wayfinding/control/adding_remote_images
:link-type: doc

Modern observatories often operate remotely. A low-power single-board computer (such as a **StellarMate** or Raspberry Pi running INDI/Ekos) resides at the telescope pier, capturing FITS frames and saving them to local flash storage.
:::

:::{grid-item-card} Telescope Control: Slewing, Tracking & Guiding Subsystems
:link: /notebooks/wayfinding/control/telescope_slewing_and_tracking
:link-type: doc

Observatory automation requires precise communication with hardware drivers. `wayfindinglib` interfaces with the **INDI (Instrument-Neutral-Distributed-Interface)** protocol over TCP/IP networks to control equatorial mounts, camera sensors, motorized focusers, and filter wheels using {py:class}`~wayfindinglib.ObservatoryControl`.
:::

:::{grid-item-card} Observation Execution: Session Queue & Fault Recovery
:link: /notebooks/wayfinding/execution/observation_execution
:link-type: doc

Robotic observatories execute observation queues autonomously throughout the night. The execution engine ({py:class}`~wayfindinglib.ObservationExecution`) steps through queued targets, manages hardware transitions, records telemetry, and handles unexpected environmental or mechanical faults.
:::

::::

---

```{toctree}
:maxdepth: 2
:hidden:

/notebooks/astrometrics/user_guide/01_working_with_targets
/notebooks/astrometrics/user_guide/02_inspecting_fits_metadata
/notebooks/astrometrics/user_guide/03_stacking_and_calibration
/notebooks/astrometrics/user_guide/04_astrometry_and_plate_solving
/notebooks/astrometrics/user_guide/05_photometry_and_lightcurves
/notebooks/astrometrics/user_guide/06_spectroscopy_extraction
/notebooks/astrometrics/user_guide/07_stellar_catalogs
/notebooks/astrometrics/user_guide/08_visualization
/notebooks/astrometrics/user_guide/09_end_to_end_pipeline_M13
/notebooks/wayfinding/planning/mosaic_sequence_authoring
/notebooks/wayfinding/planning/observation_planning
/notebooks/wayfinding/control/adding_remote_images
/notebooks/wayfinding/control/telescope_slewing_and_tracking
/notebooks/wayfinding/execution/observation_execution
```
