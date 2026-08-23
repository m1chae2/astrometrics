---
orphan: true
---

# Astrometrics User Interface: Architecture and Design

*Version 2.1 · 2026-08-09 · Status: current*

## Abstract

This document specifies the functional architecture, display capabilities, and real-time domain mapping of the Astrometrics User Interface (`ui/`). The user interface acts as a visual client for the scientific processing capabilities of `astrometricslib` and the observatory planning and hardware control engine of `wayfindinglib`. This document details how each display mode maps directly to domain library registries, how real-time telemetry and state are synchronized across display views, and how automated sequence execution and AI agent interactions interface with the underlying domain architecture.

---

## Introduction

The Astrometrics UI operates as a user interface that delegates all underlying domain logic, image math, hardware communication, and catalog indexing to the core Python libraries:

1. **Astrometrics Data Library**: Handles data ingestion, target cataloging, FITS array stretching, Siril frame calibration/stacking, astrometric plate-solving, aperture/PSF photometry, light curve extraction, and slitless spectroscopy tracing.
2. **Wayfinding Observatory Library**: Handles INDI hardware device communication, telescope mount tracking, Alt/Az target visibility planning, sky catalog spatial indexing, mosaic grid layout authoring, session sequence execution, and automated safety/fault recovery.

---

## Display Architecture & Domain Mapping

The user interface exposes distinct, task-oriented **Displays**, each encapsulating a primary scientific or operational workflow while remaining completely backed by `astrometricslib` and `wayfindinglib`. Users can switch between displays within a single window or open separate displays in secondary application windows for multi-monitor observatory layouts.

### UI Component & Library Registry Matrix

Table 1 details the exact mapping between frontend display components and the underlying domain systems they interact with.

**Table 1.** UI Display Component to Library Subsystem Matrix.

| UI Display Component | Primary Purpose | Implemented Data Subsystems | Implemented Observatory Subsystems | Specific Implemented Capabilities |
| :--- | :--- | :--- | :--- | :--- |
| **Status Header** | Real-time system telemetry bar | — | Observatory Control | Mount connection status, tracking state, Alt/Az, RA/Dec coordinates, ambient temperature & humidity telemetry stream |
| **Image Viewer** | FITS inspection & fast preview | Target Catalog | — | Target FITS file list fetching, base64 PNG rendering, ZScale/stretch parameter updates, FITS header inspection |
| **Image Processing** | Batch processing & calibration | Target Catalog<br/>Processing Pipelines | — | Target creation/scanning, Siril master dark/flat calibration, frame stacking, astrometric plate-solving, slitless spectroscopy tracing |
| **Astronomy Manager** | Scientific photometry & analysis | Stellar Catalog<br/>Processing Pipelines | — | Point source detection, aperture & PSF photometry, light curve extraction, Lomb-Scargle periodogram analysis |
| **Observatory Manager** | Direct hardware operation | — | Observatory Control | INDI server connection management, direct telescope mount slewing, filter wheel selection, focuser offset adjustments, camera exposure capture |
| **Observation Manager** | Session sequence authoring | — | Observation Planning<br/>Observation Execution | Mosaic grid calculation, target visibility filtering, observation package export, sequence plan execution, meridian flip automation, fault recovery |
| **Planetarium** | WebGL interactive 3D sky map | — | Observation Planning | Altitude/Azimuth coordinate transformation, real-time target visibility calculation, online catalog source queries ($m_v \le 12$), camera sensor FOV projection |
