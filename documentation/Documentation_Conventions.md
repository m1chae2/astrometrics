# Documentation Conventions

*Version 1.0 · 2026-10-15 · Status: current*

## Abstract

This page is the single reference for how the Astrometrics documentation is organized and written. It covers where each kind of content lives, the layout of each document type, and the spelling, terms, units and Markdown rules. The two style skills used by AI assistants (`.claude/skills/documentation-style/` and `.claude/skills/code-documentation-style/`) summarize this page and point back to it. Tests and linters enforce most of the rules; Section 9 lists which.

## 1. Where content lives

Each kind of content has one home, so a fact is written once.

| Content | Home | Reader |
| --- | --- | --- |
| Theory: what each step does and why, equations, the glossary | `library_design/Astrometrics_Library_Architecture.md` | Anyone learning the methods |
| Code names, tuned values and their status | `library_design/Astrometrics_Library_Implementation.md` | Developers and reviewers |
| Evidence: data sets, dates, results, and the library calls to repeat each check | `library_design/validation/` (the validation register) | Reviewers and anyone tuning the tool |
| Per-pipeline review summaries | `library_design/pipelines/` (generated) | Reviewers |
| How a module or pipeline stage works, step by step | `README.md` next to the code | Developers |
| Step-by-step learning | `notebooks/` (tutorials) | New users |
| Task recipes | `how_to/` (how-to guides) | Users with a goal |
| Function and class reference | `api/` (generated from docstrings) | Programmers |

Rules:

- The architecture paper names no code and states no tuned values. It explains the idea behind each setting.
- A README or review guide gives a tuned value with its status label and a link to its register entry. It does not repeat the evidence.
- Generated files are never edited by hand. The review guides come from `build/documentation/generate_pipeline_review_docs.py` and `library_design/Pipeline_Audit_Report.md` from `build/documentation/audit_pipelines.py`. Edit the generator and run it again.

## 2. Status labels

Every tuned value carries exactly one of three labels, defined in `library_design/validation/index.md`:

- **Measured.** Chosen or confirmed by a measurement on real data, recorded in a register entry.
- **Partly measured.** Backed by an incomplete measurement (synthetic data only, one target, a small sample, or a model of an outside tool).
- **Design estimate.** Chosen by reasoning and not yet measured.

No other labels are used (for example `designed`, `design value`, `empirical` or `provisional`). Plain qualifiers after a label, such as "not validated on real fields", are fine.

Forms:

- In a README: `the floor is 2.5σ (partly measured; see [STK-08](…/validation/stacking.md#stk-08))`. The label is lowercase inside a sentence. A design estimate links to its file's table: `(design estimate; see [stacking design estimates](…/validation/stacking.md#design-estimates))`. Data-set names do not go in the label; they belong in the register entry.
- In a review guide table: `Partly measured ([STK-08](../validation/stacking.md#stk-08))`, in a column headed "Status".
- In a register entry: `**Status:** Partly measured. <one sentence saying why "partly">`.

## 3. Document layouts

### 3.1 Pipeline README (`astrometricslib/pipelines/<name>/README.md`)

1. H1 `<Pipeline> pipeline`, then one or two sentences on what it does.
2. The glossary line (Section 6.6).
3. `## Terms used here`.
4. `## What the pipeline does, in order`: a numbered list. Long sub-topics follow as their own H2 sections.
5. `## Settings`.
6. `## Quality outputs`: the gate table, then the metrics table.
7. `## Where each piece lives`: the stage READMEs and a link to the pipeline's review guide.
8. `## Limits`.
9. `## Scope`.
10. Final line: `For exact behavior, read the code.`

### 3.2 Stage README (`.../pre_processing/`, `processing/`, `post_processing/`)

1. H1 `<Pipeline> pre-processing` (or processing, post-processing), then a purpose sentence.
2. The glossary line.
3. `## What this stage does, in order`.
4. `## Settings`.
5. `## Quality metrics` (one table, Section 6.3).
6. `## Files`.
7. Final line: `For exact behavior, read the code.`

### 3.3 Folder README (a directory of modules, such as `utilities/`)

H1, a purpose sentence, the glossary line, then one bullet per file or file group saying what it is and why it lives there. Final line as above.

### 3.4 Papers and register files

Papers follow `.claude/skills/documentation-style/SKILL.md`: title, metadata line, Abstract, numbered sections, Acknowledgments, References, Appendices. Register files follow the entry template in `library_design/validation/index.md`.

## 4. Spelling

US spelling throughout: catalog, color, center, neighbor, behavior, gray, normalize, analyze, artifact, modeling, labeled. Code identifiers keep their own spelling and appear in code format.

## 5. Terms

| Use | For | Not |
| --- | --- | --- |
| frame | one camera exposure file | picture, sub-exposure, sub |
| light frame | a frame of the sky, when contrasting it with calibration frames | |
| image | a processed product such as a stack | |
| stack | the combined image of many frames | |
| spectrum trail | the star's spread-out spectrum in a slitless frame | streak (for spectra) |
| streak | a line drawn by a satellite, meteor or fast mover | trail (for these) |
| gate | a run-level pass/fail entry in a quality record | check (as a noun) |
| checkpoint | a per-spectrum quality record in spectroscopy | gate (for these) |
| flag | a soft warning string | |
| session | frames from one continuous run with the same setup; each document states its pipeline's exact rule on first use | night, run (as synonyms) |
| night | calendar time only, such as aliasing arguments | |
| pixel scale | arcseconds per pixel | plate scale (as a synonym) |
| FWHM | full width at half maximum, defined once per document | star width (after the definition) |
| grating | the transmission grating (Star Analyser) | grism |
| asteroid detection | the fifth pipeline's name | moving-object detection, asteroid recovery |
| moving object | the thing that pipeline finds | |

Measures of a detector or classifier:

| Term | Definition |
| --- | --- |
| completeness (recall) | true positives ÷ all real positives, for injected or known signals |
| purity (precision) | true positives ÷ everything flagged |
| false-positive rate | false positives ÷ all real negatives |
| false alarms per field | a count of false detections in one field or session |
| false-alarm probability (FAP) | the p-value of one test: the chance that noise alone scores this high |
| false discovery rate (FDR) | the expected share of false positives among the flagged, as controlled by Benjamini–Hochberg |
| AUC | area under the ROC curve; 0.5 is chance and 1 is perfect. Say "AUC against catalog labels" when the truth is a catalog. |

## 6. Mechanics

### 6.1 Units and numbers

| Quantity | Write | Not |
| --- | --- | --- |
| Wavelength | `4861 Å` (define ångström once) | `4861 A`, `angstrom` after a number |
| Percent | `5%` | `5 %`, `5 percent` |
| Angle on the sky | `3 arcsec`, `2 arcmin`, `20°` | `3"`, `3″`, `20 deg` |
| Pixel scale | `1.92 arcsec/pixel` | |
| Pixels | `pixels` in prose; `px` in tables and register results | |
| Standard deviations | `5σ` after a number; "sigma clipping" as a noun | `5 sigma`, `5-sigma`, `5 σ` |
| Time | `30 s`, `4 h`, `2 min` | `30s` |
| Thousands | `50,000` for 10,000 and above, except inside code | |
| Ranges | `5 to 13` | `5–13` |

### 6.2 Headings and paragraphs

- Sentence case in headings, with `&` written as "and".
- One line per paragraph; no hard wrapping.
- A lead-in is `**Label.**` followed by the text. No `**Label**:` and no plain "Settings:".
- Italics mark a term where it is defined. Bold is for lead-ins only, never for emphasis mid-sentence.

### 6.3 Tables

- Separator row `| --- |`.
- A quality-metric table has the columns: `Field` (code format) | `Unit` | `Limit (status)` | `High or low means` | `Read by`.
- A gate table in a README has the columns: `Gate` | `Fails when` | `Not checked when`. A review guide's gate table has `Gate` | `Role` | `Defined in` | `What it checks`, because it is generated from the gate constants and is read by a reviewer comparing pipelines.

### 6.4 Symbols

The architecture paper's Appendix B is the one list of symbols. A review guide or README that writes an equation uses the paper's symbols and indices, and names the paper's equation it restates.

### 6.5 Links

- Relative links with anchors to other documents and sections, not quoted section names or bare paths.
- Each pipeline README links its review guide, and each review guide links back.

### 6.6 The glossary line

Near the top of every README, review guide and register file:

> Terms are defined in the [glossary](<relative path>/documentation/library_design/Astrometrics_Library_Architecture.md#appendix-a-glossary). This document also defines each term the first time it appears.

## 7. Voice and reading level

- Reading level: first-year college engineering or science for the libraries, high school for `ui/`, `backend/` and `electron/`.
- Sentences do one job. Aim for 25 words; rewrite any sentence over 40.
- Active voice, with the module or pipeline as the subject.
- No first person in READMEs and papers. "We" is allowed only in a register entry's **Our result**.
- No history narration ("used to", "previously", "now uses", "before this"). State the current behavior and its reason.
- No marketing or editorializing.

## 8. File names

- Papers and guides: `Title_Case.md`.
- Collections (review guides, register files, how-to guides): `snake_case.md`.
- Tutorials: `NN_snake_case.ipynb`.

## 9. What enforces these rules

| Rule | Enforced by |
| --- | --- |
| Theory-only architecture paper; evidence only in the register | `astrometricslib/test/test_documentation_rules.py` |
| Status labels next to register links, README layouts, glossary line and code pointer, review-guide links, register values equal to the code, section references, relative links and anchors, matching skill copies | `astrometricslib/test/test_documentation_style.py` |
| Register entries complete, cited and resolvable | `astrometricslib/test/test_validation_register.py` |
| Code links in the papers resolve | `astrometricslib/test/test_architecture_doc_claims.py` |
| Generated review guides up to date | `astrometricslib/pipelines/test/test_pipeline_doc_parity.py` |
| Spelling, terms, units, labels, voice | Vale (`.vale.ini`, `build/documentation/vale/`) |
| Markdown mechanics | markdownlint (`.markdownlint-cli2.jsonc`) |
| Docs site builds with no warnings | `build/linux/build_docs.sh` (`sphinx-build -W`) |

The pre-commit hooks `documentation-rules` and `documentation-lint`, and the `astrometricslib` and `docs` CI workflows, run these checks; `build/documentation/lint_docs.sh` runs both linters locally. A weekly workflow (`docs-linkcheck.yml`) reports dead external links without blocking pull requests.
