# Getting Started

*Version 1.1 · 2026-08-16 · Status: current*

## Abstract

This guide introduces our two Python libraries with a quick demo. You will learn how to register an observation target, process its images, and plan when to observe it. Make sure you finish the setup in `Installation.md` first. We recommend reading this guide before looking at the API reference or the architecture papers.

## Introduction

This software uses two main libraries that do different jobs. The `astrometricslib` library handles your data, like observation targets, images, stars, and measurements. The `wayfindinglib` library controls the observatory, helping you operate hardware, plan sessions, and run tasks automatically. It builds on the data organized by `astrometricslib`.


## Hardware Setup & Spectroscopy

Astrometrics is designed to be hardware-agnostic, meaning you can use almost any camera and mount combination via INDI. However, if you are specifically interested in capturing stellar spectra, you will need a diffraction grating or spectrograph in your optical train.

To give you an idea of a proven, working setup, here is a reference hardware configuration used for spectroscopy with Astrometrics:

* **Main Camera:** ZWO ASI 533MM Pro (Monochrome is excellent for spectroscopy, but color works too)
* **Telescope:** Apertura 75Q
* **Mount:** Sky-Watcher Star Adventurer GTi (A lightweight, portable tracking mount)
* **Diffraction Grating:** Star Analyser 200 (The critical component for splitting starlight into a spectrum)
* **Guiding:** ZWO ASI 120MC-S on an Apertura 32mm Guide Scope

**What do you *actually* need?**
You do **not** need the exact gear listed above. To get started with spectroscopy and Astrometrics, the minimum viable setup is:
1. **A Tracking Mount:** Any form of motorized tracking mount (even a basic star tracker) to keep the star and its spectrum stationary during the exposure.
2. **A Camera:** A dedicated astronomy camera or a DSLR.
3. **A Diffraction Grating:** A filter-threaded grating like the **Star Analyser 100 or 200**. This is what actually creates the spectrum you will analyze in the software.

## The two entry points

Each library gives you a main object to work with. Everything else under these objects works behind the scenes and might change in future updates.

```python
from astrometricslib import Astrometrics
from wayfindinglib import Wayfinder

astrometrics = Astrometrics()
wayfinder = Wayfinder()
```

When you start `Astrometrics`, it opens your target library. `Wayfinder` groups together the three observatory tools shown in Section 4. It won't try to connect to any telescope hardware until you actually run a command, so it is perfectly safe to use on a normal computer.

## Working with observation targets

An observation target represents a specific area of the sky. It links together everything about that area: raw photos, calibration images, final stacked pictures, and the stars found in them.

Here is how you register a new target and load it:

```python
target = astrometrics.targets.create("M 81")
print(target.id)

same_target = astrometrics.targets.get("M 81")
```

Calling `list` on `astrometrics.targets` returns the whole library, which is the usual starting point for a survey of what has been collected:

```python
for target in astrometrics.targets.list():
    print(target.id, len(target.frames))
```

For a given target object, explicit processing routines in the `processing` namespace are used to execute specific analysis tasks:

```python
astrometrics.processing.run_photometry(
    target,
    filter_type="V",
    use_astrometry_seed=True,
)
```

The stellar objects detected for a target become available once astrometry or photometry has run:

```python
for stellar_object in astrometrics.stars.list_objects():
    if "M 81" in stellar_object.target_ids:
        print(stellar_object.id, stellar_object.magnitude)
```

To interactively inspect the results, high-level multi-panel visualization tools are provided:

```python
astrometrics.visualization.plot_target_dashboard(target)
```

## Planning an observation

The observatory library divides into three functions, summarized in Table 1.

**Table 1.** Observatory functions and their responsibilities.

| Function | Accessor | Responsibility |
| :--- | :--- | :--- |
| Observatory Control | `wayfinder.control` | Direct hardware operation and device state |
| Observation Planning | `wayfinder.planning` | Target visibility, mosaics, and session authoring |
| Observation Execution | `wayfinder.execution` | Running a planned session |

Planning never contacts a device, so a full night may be planned with nothing connected. Collect the sources in a region of sky and rank them by how high they currently sit:

```python
stars = wayfinder.planning.get_sources(0.0, 0.0, 180.0, include_catalog=False)
visibility = wayfinder.planning.get_visibility(stars)
visibility.sort(key=lambda entry: entry["altitude"], reverse=True)
```

The search accepts a centre in degrees and a radius, so the call above covers the whole sky. Each returned entry carries the altitude and azimuth of one star at the requested time, which defaults to now. Passing `include_catalog=True` widens the search from the targets already in the local library to the online catalogs configured for the observatory.

To slew the telescope to a target already registered in the local library, the observatory control function is used:

```python
slewed = wayfinder.control.slew_to_target("M 81")
```

Coordinates in degrees can be used instead when the target is not yet in the library:

```python
slewed = wayfinder.control.slew_to_coordinates(ra=148.888, dec=69.065)
```


## Next steps

The tutorials under `notebooks/index` work through calibration and stacking, aperture and point-spread-function photometry, slitless spectroscopy calibration, and target planning as complete sequences. The architecture papers, `Astrometrics_Library_Architecture.md` and `Wayfinding_Library_Architecture.md`, explain the data models and the observatory design. The API reference documents every public class in both libraries.
