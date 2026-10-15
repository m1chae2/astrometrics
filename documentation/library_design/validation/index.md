# Astrometrics Validation and Tuning

*Version 1.1 · 2026-10-15 · Status: current*

## Abstract

This register records how the tuned values in `astrometricslib` were checked. Each entry gives the library calls that were made, the kind of data needed, what was measured, the result on our data, and which setting to change to tune the tool for another telescope or camera. The method documents, meaning the READMEs, the [pipeline review guides](../pipelines/index.md) and `Astrometrics_Library_Architecture.md`, describe what each step does and why. They cite an entry here by its ID instead of repeating its numbers. Terms are defined in the [glossary](../Astrometrics_Library_Architecture.md#appendix-a-glossary).

## 1. Introduction

**Statement of need.** A threshold is only as good as the data it was chosen on. Our frames come from one telescope, one camera and one sky, and they are not in the repository. A reviewer needs two things. They need to see how each number was reached, so they can judge it. They also need to repeat the check on their own frames, so they can keep the number or change it. This register gives both, in one place, and keeps the method documents free of run results.

## 2. How to read an entry

Each pipeline has its own file, and each entry has an ID made of the pipeline's prefix and a number.

**Table 1.** Register files and their entry prefixes.

| File | Prefix | Pipeline |
| :--- | :--- | :--- |
| [stacking.md](stacking.md) | `STK` | Image stacking |
| [astrometry.md](astrometry.md) | `AST` | Astrometry (plate solving and star identification) |
| [photometry.md](photometry.md) | `PHO` | Photometry and variable stars |
| [spectroscopy.md](spectroscopy.md) | `SPE` | Slitless spectroscopy |
| [asteroid_detection.md](asteroid_detection.md) | `MOV` | Moving-object (asteroid) detection |

Every entry has the same fields, in this order:

1. **Claim.** What the evidence shows.
2. **Settings it supports.** The values the evidence backs, and where each one lives. A config key can be changed in the configuration file. A code constant can only be changed by editing the code.
3. **Status.** One of the three labels in Section 3.
4. **Data you need.** The kind of frames or records the check needs. This describes the data, not our files.
5. **Procedure.** The steps, with the library calls that were made. The calls use the public API (the names exported by `astrometricslib`) where one exists. A step that needs an internal function says so, because internal functions can change between versions.
6. **What to look at.** The measured quantity, its units, and what a good or bad value means.
7. **Our result.** The data set, the date and the numbers we got.
8. **Limits.** What the check does not show.
9. **To tune for your setup.** Which setting to change, in which direction, and what to run again.
10. **Automated by.** The script in `astrometricslib/scripts/` or the test that runs the procedure, and the raw record in `documentation/evidence/` when one exists.

Each file ends with a table of design estimates. These are values that have not been measured, with a note on how they could be.

## 3. Status labels

The same three labels are used here, in the review guides and in the READMEs.

**Table 2.** Status labels.

| Label | Meaning |
| :--- | :--- |
| Measured | Chosen or confirmed from a measurement on real data, recorded in an entry. |
| Partly measured | Backed by a measurement that is incomplete: synthetic data only, one target, a small sample, or a model of an outside tool rather than the tool itself. The entry says which. |
| Design estimate | Chosen by reasoning and not yet measured. It is listed in the design-estimate table of its file. |

## 4. Keeping the register honest

A test, `astrometricslib/test/test_validation_register.py`, checks the register on every run of the test suite. It fails when:

- an entry is missing one of the required fields;
- a link from an entry to the code names a function, class or constant that no longer exists;
- a Python example in a procedure is not valid Python;
- an entry names a script or evidence file that is not in the repository;
- a method document cites an entry ID that does not exist;
- an entry is not cited by any method document.

The test cannot check that a number is still true. When the code behind an entry changes, run the procedure again and update **Our result** and the date.

## 5. Validation sessions

The architecture paper (`Astrometrics_Library_Architecture.md`, Section 8) describes how the library is validated and why. This section records the data the evidence came from.

### 5.1 Data and coverage

Three kinds of evidence are used: synthetic data with a known answer, real sessions from the observatory's library, and library-wide surveys that repeat one measurement over every stored stack or light curve. The real sessions were taken mostly with a ZWO ASI 533MM Pro monochrome camera ($3.76\,\mu\text{m}$ pixels). Some stacking checks also use a Nikon D5300 colour camera.

Table 3 lists what each register file covers.

**Table 3.** Validation register files and what they cover.

| Pipeline | Register file | What was checked |
|---|---|---|
| Stacking | [stacking.md](stacking.md) | Frame screening and quarantine, calibration masters, pixel rejection, exposure groups and their brightness scale, the after-stacking checks, colour calibration and the preview stretch |
| Astrometry | [astrometry.md](astrometry.md) | Solver residuals against an independent Gaia check, the Gaia refit, the solution checks, star detection, catalog matching radii and the Gaia service limits |
| Photometry | [photometry.md](photometry.md) | The comparison ensemble, the error model and camera noise, frame alignment, the variability indices and cutoffs, the period search and cross-session matching |
| Spectroscopy | [spectroscopy.md](spectroscopy.md) | Sky subtraction, the reading box, resolution and errors, wavelength calibration, the instrument response, classification limits and the Gaia XP comparison |
| Moving objects | [asteroid_detection.md](asteroid_detection.md) | Recovery of injected movers, false tracks, real fields far from the ecliptic, the pointing correction and the chaining step |

### 5.2 The first validation campaign

The first campaign ran eight real observing sessions through the pipelines named for each, and ran each result through its pipeline's own after-the-fact quality check (Table 4). A session here means one target's data set, which for M 81 later grew to several nights. One further target, M 45 (the Pleiades), was plate-solved as an extra astrometry check; its frame count is not recorded.

**Table 4.** Sessions of the first validation campaign.

| Session | Field | Light frames; total exposure | Pipelines | Register entries |
|---|---|---|---|---|
| Vega | Bright standard star, grating | 160; 270 s | Spectroscopy | [SPE-12](spectroscopy.md#spe-12) |
| M 13 | Dense star cluster, grating | 86; 2,520 s | Spectroscopy, stacking | [SPE-29](spectroscopy.md#spe-29) |
| Alcor | Double star, grating | 138; 1,080 s | Spectroscopy | — |
| NGC 2244 | Open star cluster, time series | 29; 10,800 s | Photometry | [PHO-13](photometry.md#pho-13) |
| M 81 | Spiral galaxy | 46; 7,230 s at first, later 258 frames in 8 sessions (2023-05 to 2026-05) | Photometry, stacking, moving objects | [STK-24](stacking.md#stk-24), [STK-25](stacking.md#stk-25), [PHO-14](photometry.md#pho-14), [MOV-07](asteroid_detection.md#mov-07) |
| NGC 2903 | Galaxy, time series | 36; 14,400 s | Photometry | [PHO-14](photometry.md#pho-14) (single-session baseline) |
| NGC 2403 | Wide galaxy field | 70; 13,740 s | Stacking, astrometry, moving objects | [STK-24](stacking.md#stk-24), [STK-25](stacking.md#stk-25), [MOV-04](asteroid_detection.md#mov-04), [MOV-05](asteroid_detection.md#mov-05) |
| NGC 1893 | Open cluster | 49; 7,800 s | Stacking, astrometry | [AST-14](astrometry.md#ast-14) |

:::{warning}
**The first campaign predates the current algorithms.** Its results were measured with earlier versions of the stacking rejection cutoffs and sharpness check, the plate-solve gates, the comparison ensemble and variability flag, the spectrum extraction, and the track tests. The plate solves in particular predate the matched-star minimum and the residual check described in `Astrometrics_Library_Architecture.md`, Section 4.4.2. Each register entry says whether its result has been repeated with the current algorithms. Treat a result that has not been repeated as evidence about the data and the shape of the method, not as current performance.
:::

### 5.3 Recommended future targets

The following target types are recommended for future testing:

1. **Eclipsing Binary or Exoplanet Transit Target (Photometry):** Observations of a star that dims on a known period, to test the pipeline's ability to detect a periodic dimming pattern and measure its depth on real data. The period search is so far checked only on synthetic light curves ([PHO-12](photometry.md#pho-12)). An eclipsing binary (two stars orbiting each other, each passing in front of the other) such as *Algol / $\beta$ Persei* dims by more than a magnitude in each eclipse and tests the period search on a large, sharp dip. A star with a transiting planet such as *WASP-12* (host of the planet WASP-12b) dims by only about $\Delta m \approx 0.015\text{ mag}$ (about 1.4%) and tests the faint end.
2. **Emission-Line Nebula Target (Spectroscopy):** A planetary nebula (e.g., *Ring Nebula / M 57* or *Dumbbell Nebula / M 27*) to test wavelength calibration against bright emission lines — $[\mathrm{OIII}]$ ($500.7\text{ nm}$; doubly ionized oxygen, where the brackets mark a "forbidden" line seen only in very thin gas) and $\mathrm{H}\alpha$ ($656.3\text{ nm}$) — alongside Vega's absorption-line spectrum. The library already holds grating stacks of both nebulae, but the wavelength calibration uses only absorption lines and has not been tested on their emission lines.
3. **Asteroid Field Near the Ecliptic (Moving Object Detection):** A field along the ecliptic (the sky path the planets and most asteroids follow) containing known, cataloged asteroids, to test matching against an official minor-planet catalog alongside SkyBoT. The real fields checked so far lie far from the ecliptic, where almost no known asteroid is expected ([MOV-04](asteroid_detection.md#mov-04)).

```{toctree}
:maxdepth: 1
:hidden:

stacking
astrometry
photometry
spectroscopy
asteroid_detection
```
