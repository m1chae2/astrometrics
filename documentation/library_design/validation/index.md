# Validation register

*Version 1.2 · 2026-10-15 · Status: current*

## Abstract

This register records how the tuned values in `astrometricslib` were checked. Each entry gives the library calls that were made, the kind of data needed, what was measured, the result on our data, and which setting to change to tune the tool for another telescope or camera. The method documents, meaning the READMEs, the [pipeline review guides](../pipelines/index.md) and `Astrometrics_Library_Architecture.md`, describe what each step does and why. They cite an entry here by its ID instead of repeating its numbers. Terms are defined in the [glossary](../../Glossary.md).

## 1. Introduction

**Statement of need.** A threshold is only as good as the data it was chosen on. Our frames come from one telescope, one camera and one sky, and they are not in the repository. A reviewer needs two things. They need to see how each number was reached, so they can judge it. They also need to repeat the check on their own frames, so they can keep the number or change it. This register gives both, in one place, and keeps the method documents free of run results.

## 2. How to read an entry

Each pipeline has its own file, and each entry has an ID made of the pipeline's prefix and a number.

**Table 1.** Register files and their entry prefixes.

| File | Prefix | Pipeline |
| --- | --- | --- |
| [stacking.md](stacking.md) | `STK` | Image stacking |
| [astrometry.md](astrometry.md) | `AST` | Astrometry (plate solving and star identification) |
| [photometry.md](photometry.md) | `PHO` | Photometry and variable stars |
| [spectroscopy.md](spectroscopy.md) | `SPE` | Slitless spectroscopy |
| [asteroid_detection.md](asteroid_detection.md) | `MOV` | Moving-object (asteroid) detection |
| [wayfinding.md](wayfinding.md) | `WAY` | Quality statements, efficiency and forecasts in `wayfindinglib` |

Every entry has the same fields, in this order:

1. **Claim.** What the evidence shows.
2. **Settings it supports.** The settings the evidence bears on, and where each one lives. A config key can be changed in the configuration file. A code constant can only be changed by editing the code. Each setting ends with `Relation:` and one of four relations to the entry. *Derived from* means the value was set from this evidence. *Confirmed by* means the value was chosen earlier or on other grounds and this evidence agrees with it. *Sanity check only* means the evidence shows the value is not absurd but does not fix it. *Challenged by* means the evidence argues against the value. Only a derived or confirmed setting counts as supported. A setting listed in a design-estimate table has no measurement behind it, so an entry cannot derive or confirm it.
3. **Status.** One of the three labels in Section 3, then a sentence saying why. The form is `**Status:** Partly measured. <why>.` A retired entry reads `**Status:** Retired. Superseded by [ID](link).`
4. **Data you need.** The kind of frames or records the check needs. This describes the data, not our files.
5. **Procedure.** The steps, with the library calls that were made. The calls use the public API (the names exported by `astrometricslib`) where one exists. A step that needs an internal function says so, because internal functions can change between versions.
6. **Acceptance criterion.** The limit or outcome the result is judged against. When the entry states none, it reads "Not recorded (set after the result)." because a criterion chosen after seeing the result is not a test.
7. **What to look at.** The measured quantity, its units, and what a good or bad value means.
8. **Our result.** The data set, the date and the numbers we measured. A bound that a unit test enforces is not a measured value, so it goes in **Guarded by tests**. When only that bound is known, this field reads "Measured value not recorded."
9. **Verdict.** Pass, Fail, Inconclusive or Not judged, against the acceptance criterion. Not judged means the entry states no limit to compare the result with.
10. **Limits.** What the check does not show.
11. **To tune for your setup.** Which setting to change, in which direction, and what to run again.
12. **Regenerate with.** The script in `astrometricslib/scripts/`, or the command, that repeats the measurement. When there is no script, the field says so and how the measurement was made.
13. **Guarded by tests.** The unit tests that keep the code behaving as the entry describes. A test checks that the code still behaves as described. It does not repeat the measurement on real data. The field reads "None." when there are none.
14. **Raw record.** The saved record in `documentation/evidence/`, or its location, when one exists. The field reads "None kept." when there is none.

An entry that has been run more than once also has a **Runs** table between **Verdict** and **Limits** (Section 2.1).

New and updated entries also record four facts a reader needs to judge a result. They go in **Our result**, or as their own fields:

- **Code version.** The git commit (or release) the result was produced with. A result made before a later change to the method says so.
- **Sample size.** How many frames, stars, sessions or synthetic trials the number rests on.
- **Uncertainty.** The spread or confidence interval of the result, with how it was estimated (for example, the binomial interval on a recovery rate, or the scatter across sessions). When none was estimated, the entry says so.
- **Data split.** Whether the result was measured on the same data the value was tuned on (in-sample) or on data kept aside for the check (held out). An in-sample result shows the value fits the data it came from, not that it carries over to new data.

Entries written before these fields were added hold them only where the original record had them. They are not filled in after the fact. A missing field is written as "not recorded", never guessed.

Each file ends with a table of design estimates. These are values that have not been measured. Each row has a stable ID such as `STK-D01`, the value, where it lives, the risk if the value is wrong, and a note on how it could be measured. A row ID is never reused.

### 2.1 Re-running, superseding and retiring entries

- An entry ID is never reused, so a link to an entry keeps pointing at the same thing.
- A retired entry keeps its heading and its other fields as the historical record. Its status line reads `**Status:** Retired. Superseded by [ID](link).`, with a link to the entry that replaces it.
- A re-run of an entry's procedure adds a row to the entry's **Runs** table. The columns are Date, Code version, Verdict and Record. The other fields describe the current run, and **Our result** shows it. The table holds the history.
- Only an entry that has been run more than once has a **Runs** table. So far these are MOV-01 and MOV-03.
- The raw record of a re-run is a new file in `documentation/evidence/`. A saved record is never edited (see the [evidence README](https://github.com/m1chae2/astrometrics/blob/main/documentation/evidence/README.md)).

## 3. Status labels

The same three labels are used here, in the review guides and in the READMEs.

**Table 2.** Status labels.

| Label | Meaning |
| --- | --- |
| Measured | Chosen or confirmed from a measurement on real data, recorded in an entry. |
| Partly measured | Backed by a measurement that is incomplete: synthetic data only, one target, a small sample, or a model of an outside tool rather than the tool itself. The entry says which. |
| Design estimate | Chosen by reasoning and not yet measured. It is listed in the design-estimate table of its file. |

## 4. Keeping the register honest

A test, `astrometricslib/test/test_validation_register.py`, checks the register on every run of the test suite. It fails when:

- an entry is missing one of the required fields, or has them out of order;
- a status, verdict or setting relation does not use the allowed words;
- a setting is both supported by an entry and listed in a design-estimate table;
- a design-estimate row has no ID;
- an entry names a data set ID that Section 7 does not define;
- a link from an entry to the code names a function, class or constant that no longer exists;
- a Python example in a procedure is not valid Python;
- an entry names a script or evidence file that is not in the repository;
- a method document cites an entry ID that does not exist;
- an entry is not cited by any method document.

A second test, `astrometricslib/test/test_documentation_rules.py`, keeps evidence in the register: it fails when a README or a pipeline review guide carries a dated result. The same checks run before each commit (the `documentation-rules` pre-commit hook) and in continuous integration whenever the documentation changes.

Neither test can check that a number is still true. When the code behind an entry changes, run the procedure again and update **Our result** and the date.

## 5. Data availability

The frames and catalogs behind the results are not in the repository: they are large, and some are observing data from one private observatory. Each entry names its data set (target, camera and date) so the owner can identify it. A data set from the first validation campaign also carries its ID from Section 7. Raw records small enough to keep are in `documentation/evidence/`. A reviewer who needs a data set should ask the repository owner. The procedures are written so that a reviewer can repeat each check on their own data instead.

## 6. Validation sessions

The architecture paper (`Astrometrics_Library_Architecture.md`, Section 8) describes how the library is validated and why. This section records the data the evidence came from.

### 6.1 Data and coverage

Three kinds of evidence are used: synthetic data with a known answer, real sessions from the observatory's library, and library-wide surveys that repeat one measurement over every stored stack or light curve. The real sessions were taken mostly with a ZWO ASI 533MM Pro monochrome camera ($3.76\,\mu\text{m}$ pixels). Some stacking checks also use a Nikon D5300 color camera.

Table 3 lists what each register file covers.

**Table 3.** Validation register files and what they cover.

| Pipeline | Register file | What was checked |
| --- | --- | --- |
| Stacking | [stacking.md](stacking.md) | Frame screening and quarantine, calibration masters, pixel rejection, exposure groups and their brightness scale, the after-stacking checks, color calibration, the preview stretch and the noise of a stack |
| Astrometry | [astrometry.md](astrometry.md) | Solver residuals against an independent Gaia check, the Gaia refit, the solution checks, star detection, catalog matching radii and the Gaia service limits |
| Photometry | [photometry.md](photometry.md) | The comparison ensemble, the error model and camera noise, frame alignment, the variability indices and cutoffs, the period search and cross-session matching |
| Spectroscopy | [spectroscopy.md](spectroscopy.md) | Sky subtraction, the reading box, resolution and errors, wavelength calibration, the instrument response, classification limits and the Gaia XP comparison |
| Moving objects | [asteroid_detection.md](asteroid_detection.md) | Recovery of injected movers, false tracks, real fields far from the ecliptic, the pointing correction and the chaining step |
| Quality statements | [wayfinding.md](wayfinding.md) | The statement and confidence vocabulary, and the design estimates behind the efficiency reference |

### 6.2 The first validation campaign

The first campaign ran eight real observing sessions through the pipelines named for each, and ran each result through its pipeline's own after-the-fact quality check. Section 7 lists the sessions as data sets. A session here means one target's data set, which for M 81 later grew to several nights. One further target, M 45 (the Pleiades), was plate-solved as an extra astrometry check; its frame count is not recorded.

:::{warning}
**The first campaign predates the current algorithms.** Its results were measured with earlier versions of the stacking rejection cutoffs and sharpness check, the plate-solve gates, the comparison ensemble and variability flag, the spectrum extraction, and the track tests. The plate solves in particular predate the matched-star minimum and the residual check described in `Astrometrics_Library_Architecture.md`, Section 4.4.2. Each register entry says whether its result has been repeated with the current algorithms. Treat a result that has not been repeated as evidence about the data and the shape of the method, not as current performance.
:::

### 6.3 Recommended future targets

The following target types are recommended for future testing:

1. **Eclipsing Binary or Exoplanet Transit Target (Photometry):** Observations of a star that dims on a known period, to test the pipeline's ability to detect a periodic dimming pattern and measure its depth on real data. The period search is so far checked only on synthetic light curves ([PHO-12](photometry.md#pho-12)). An eclipsing binary (two stars orbiting each other, each passing in front of the other) such as *Algol / $\beta$ Persei* dims by more than a magnitude in each eclipse and tests the period search on a large, sharp dip. A star with a transiting planet such as *WASP-12* (host of the planet WASP-12b) dims by only about $\Delta m \approx 0.015\text{ mag}$ (about 1.4%) and tests the faint end.
2. **Emission-Line Nebula Target (Spectroscopy):** A planetary nebula (e.g., *Ring Nebula / M 57* or *Dumbbell Nebula / M 27*) to test wavelength calibration against bright emission lines: $[\mathrm{OIII}]$ ($500.7\text{ nm}$; doubly ionized oxygen, where the brackets mark a "forbidden" line seen only in very thin gas) and $\mathrm{H}\alpha$ ($656.3\text{ nm}$). These lines are tested alongside Vega's absorption-line spectrum. The library already holds grating stacks of both nebulae, but the wavelength calibration uses only absorption lines and has not been tested on their emission lines.
3. **Asteroid Field Near the Ecliptic (Moving Object Detection):** A field along the ecliptic (the sky path the planets and most asteroids follow) containing known, cataloged asteroids, to test matching against an official minor-planet catalog alongside SkyBoT. The real fields checked so far lie far from the ecliptic, where almost no known asteroid is expected ([MOV-04](asteroid_detection.md#mov-04)).

### 6.4 History fill of the quality store

The script `astrometricslib/scripts/backfill_quality_measurements.py` copies the latest saved quality summary of each pipeline on each target into the quality store, marked as backfilled. It measures nothing, so it has no register entry of its own. It was run on 2026-10-23 on a copy of the database: 56 targets and 4,847 rows (stacking 1,480, spectral stacking 679, astrometry 802, photometry 768, spectroscopy 1,118). The read-back check, which compares the stored rows with the plan for each target and pipeline, passed, and a second apply wrote 0 rows. The script's own tests are in `astrometricslib/scripts/test/test_backfill_quality_measurements.py`. The rows hold only what the saved summaries held, so numbers that were never saved (stack noise, effective frame counts, frame weights) have no history.

## 7. Data sets

The frames behind the results of the first validation campaign (Section 6.2) are listed here. Each data set has a stable ID that is never reused. An entry that uses one names it with its ID, for example "M 81 (DS-M81)". A data set here means one target's frames, which for M 81 later grew to several nights.

**Table 4.** Data sets of the first validation campaign.

| ID | Target | Field | Light frames; total exposure | Pipelines | Register entries |
| --- | --- | --- | --- | --- | --- |
| DS-VEGA | Vega | Bright standard star, grating | 160; 270 s | Spectroscopy | [SPE-12](spectroscopy.md#spe-12) |
| DS-M13 | M 13 | Dense star cluster, grating | 86; 2,520 s | Spectroscopy, stacking | [SPE-29](spectroscopy.md#spe-29) |
| DS-ALCOR | Alcor | Double star, grating | 138; 1,080 s | Spectroscopy | — |
| DS-NGC2244 | NGC 2244 | Open star cluster, time series | 29; 10,800 s | Photometry | [PHO-13](photometry.md#pho-13) |
| DS-M81 | M 81 | Spiral galaxy | 46; 7,230 s at first, later 258 frames in 8 sessions (2023-05 to 2026-05) | Photometry, stacking, moving objects | [STK-24](stacking.md#stk-24), [STK-25](stacking.md#stk-25), [PHO-14](photometry.md#pho-14), [MOV-07](asteroid_detection.md#mov-07) |
| DS-NGC2903 | NGC 2903 | Galaxy, time series | 36; 14,400 s | Photometry | [PHO-14](photometry.md#pho-14) (single-session baseline) |
| DS-NGC2403 | NGC 2403 | Wide galaxy field | 70; 13,740 s | Stacking, astrometry, moving objects | [STK-24](stacking.md#stk-24), [STK-25](stacking.md#stk-25), [MOV-04](asteroid_detection.md#mov-04), [MOV-05](asteroid_detection.md#mov-05) |
| DS-NGC1893 | NGC 1893 | Open cluster | 49; 7,800 s | Stacking, astrometry | [AST-14](astrometry.md#ast-14) |
| DS-M45 | M 45 (the Pleiades) | Open star cluster, extra astrometry check | Not recorded | Astrometry | [AST-14](astrometry.md#ast-14) |

```{toctree}
:maxdepth: 1
:hidden:

stacking
astrometry
photometry
spectroscopy
asteroid_detection
wayfinding
```
