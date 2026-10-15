# Astrometrics Validation and Tuning

*Version 1.0 · 2026-10-15 · Status: current*

## Abstract

This register records how the tuned values in `astrometricslib` were checked. Each entry gives the library calls that were made, the kind of data needed, what was measured, the result on our data, and which setting to change to tune the tool for another telescope or camera. The method documents, meaning the READMEs, the [pipeline review guides](../pipelines/index.md) and `Astrometrics_Library_Architecture.md`, describe what each step does and why. They cite an entry here by its ID instead of repeating its numbers. Terms are defined in the [glossary](../Astrometrics_Library_Architecture.md#appendix-b-glossary).

## 1. Introduction

**Statement of need.** A threshold is only as good as the data it was chosen on. Our frames come from one telescope, one camera and one sky, and they are not in the repository. A reviewer needs two things. They need to see how each number was reached, so they can judge it. They also need to repeat the check on their own frames, so they can keep the number or change it. This register gives both, in one place, and keeps the method documents free of run results.

## 2. How to read an entry

Each pipeline has its own file, and each entry has an ID made of the pipeline's prefix and a number.

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

```{toctree}
:maxdepth: 1
:hidden:

stacking
astrometry
photometry
spectroscopy
asteroid_detection
```
