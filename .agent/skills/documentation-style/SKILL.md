---
name: documentation-style
description: House documentation conventions for the Astrometrics documentation. Use when writing, editing, restructuring, or reviewing any file under documentation/ (papers, guides) to apply the repo's structure, citation, numbering, admonition, and cross-reference rules.
---

# Astrometrics Documentation Style

> **Source of truth.** The full conventions (where content lives, status labels, document layouts, US spelling, terms, units, Markdown mechanics, and which test or linter enforces each rule) are in `documentation/Documentation_Conventions.md`. This skill summarizes them; when the two differ, the conventions page wins and this skill should be updated to match.

Conventions for everything under `documentation/`. Modeled on the Siril, Astropy, photutils,
and specutils documentation ecosystems, adapted to a markdown-in-repo setting.

## Document tiers

Match the document to its tier before writing; each tier has different rules.

1. **Explanation tier (papers)** — scientific-paper-style documents such as
   `library_design/Astrometrics_Library_Architecture.md`. Describe *what the system does today and
   why* — never implementation history ("prior to this work…" is banned). Architecture-tier
   papers are implementation-agnostic: no file paths, class names, function names, config keys,
   or tuned numeric defaults (naming external tools/libraries as design-level attributions is
   fine — "delegated to Siril" is architecture, `siril_interface.py` is not). Implementation-tier
   papers (e.g. `library_design/Astrometrics_Library_Implementation.md`) carry the concrete
   identifiers and the tuned values, in its "Tuned Parameters and Validation Status" tables.
   Evidence (data sets, dates, results) lives in the validation register, not in either paper —
   see "Where content lives" below.
2. **User-guide tier** — task-oriented pages: the tutorials (`notebooks/`), the how-to guides
   (`how_to/`) and the desktop-app guides (`user_interface/`). Pair prose with runnable examples
   and their output, in the style of photutils/specutils topic pages; when creating one, follow
   the template below.

There is no specification/requirements tier — the project dropped requirements traceability, and
`documentation/requirements/` does not exist. Do not recreate it or a numbered-requirement
convention without the user asking.

## File conventions

- Naming: `<Topic>.md` (underscored title case).
- Companion documents cross-reference each other by plain bracketed filename in prose —
  `` `Astrometrics_Library_Implementation.md` `` — never as a numbered reference entry.
- Every paper carries a one-line metadata header directly under the H1 title:
  `*Version X.Y · YYYY-MM-DD · Status: current*`. Bump the version on substantive revisions.

## Papers: structure and mechanics

- Section order: Title, metadata line, Abstract, numbered body sections, Acknowledgments
  (unnumbered), References (unnumbered), Appendices.
- The Introduction opens with a **Statement of need** bolded lead-in (JOSS convention).
- **References are IEEE style, per document**: numbered `[n]` in order of first citation,
  initials-first authors, quoted article titles, abbreviated journal names. Each document owns
  its own independently numbered list — when content moves between documents, renumber. Prefer
  citable papers (JOSS/journal) over bare software citations when one exists.
- **Equations, tables, and figures are manually numbered per document**, each starting at 1.
  Equations use `$$ ... \tag{n} $$`. When inserting one mid-document, renumber everything after
  it and grep for stale in-text references.
- **Numeric parameters**: physically meaningful constants (Balmer wavelengths, grating equations)
  and derivation results belong in prose. Implementation-tuning values (aperture radii,
  thresholds, rank cutoffs) never appear in an architecture paper: describe the idea ("a floor on
  the cutoff for small stacks") and put the value in the implementation paper's parameter tables,
  with its status label and register link. Never present an unvalidated design estimate as a
  validated result — say which it is.
- **Cite what you rely on**: every published method, catalogue, data set or outside tool the
  paper names or depends on gets a numbered reference (for example Benjamini–Hochberg,
  Lomb–Scargle, box least squares, the Pickles library, the Cardelli extinction law, Siril).
- **Acknowledgments section** (Astropy convention): credit the open-source projects the system
  builds on, with citation cross-references.
- Diagrams are Mermaid code blocks; math is `$...$`/`$$...$$` LaTeX — both render on GitHub.
  Wide content must live in tables, not overflow prose.

## Where content lives

Three homes, each enforced by a test in `astrometricslib/test/`:

| Content | Home | Enforced by |
| :--- | :--- | :--- |
| Theory: what each step does and why, equations, the glossary | `library_design/Astrometrics_Library_Architecture.md` | `test_documentation_rules.py` (no code names, no tuned values) and `test_architecture_doc_claims.py` (hidden `<!-- impl: -->` links resolve) |
| Code names, tuned values and their status | `library_design/Astrometrics_Library_Implementation.md` | `test_architecture_doc_claims.py` (its impl links resolve) |
| Evidence: data sets, dates, results, procedures | `library_design/validation/` (the validation register) | `test_validation_register.py`; `test_documentation_rules.py` keeps dated results out of the method documents |

- An architecture-paper claim that has code behind it carries a hidden `<!-- impl: dotted.path -->`
  comment. It does not render, and it keeps the claim tied to code.
- Generated files are never edited by hand: the pipeline review guides in
  `library_design/pipelines/` come from `build/documentation/generate_pipeline_review_docs.py`, and
  `library_design/Pipeline_Audit_Report.md` from `build/documentation/audit_pipelines.py`. Edit the
  generator and rerun it.
- The status labels are Measured, Partly measured and Design estimate, defined in
  `library_design/validation/index.md`. Use them and no others.

## The validation register

Each tuned value that has evidence gets an entry in its pipeline's register file
(`validation/stacking.md` and so on; IDs `STK-nn`, `AST-nn`, `PHO-nn`, `SPE-nn`, `MOV-nn`). The
entry template is in `validation/index.md`. Rules:

- An entry gives the library calls a reviewer would make to repeat the check on their own data,
  not just the result on ours.
- Report like a paper: give the code version (git commit) the result came from, the sample size,
  an uncertainty (with how it was estimated), and whether the result is in-sample (measured on the
  data the value was tuned on) or held out. Write "not recorded" for anything unknown; never guess.
- Changing a tuned value or the method behind it means updating its entry: rerun the procedure,
  or mark the old result as made with an earlier version.
- Every entry is cited by at least one method document, and every citation resolves (the test
  checks both).

## Admonitions (Siril convention)

Use MyST "colon fence" syntax for markdown documents and Sphinx reStructuredText syntax (`.. warning::`) for Python docstrings. Use them sparingly (a handful per document, not decoration):

- `:::{note}` with a `**Theory.**` lead-in — optional mathematical background a reader may skip.
- `:::{warning}` — a genuine trap (e.g., "never compare FWHM values from two different
  estimators").
- Do not use alerts for emphasis; if everything is noteworthy, nothing is.

## Writing style

- **Tone**: Strictly neutral, objective, and direct. Avoid conversational filler, marketing speak, and overly academic phrasing.
- **Reading Level**: First-year college engineering or science for the library documents; high school for the desktop-app guides (`user_interface/`). Plain words, short sentences, every term defined on first use.
- **Clarity**: Sentences do one job. Use shorter, punchy sentences and avoid long, multi-clause paragraphs.
- **Terminology**: Define domain-specific terms at first use (ADU, zero order) and keep the glossary (`library_design/Astrometrics_Library_Architecture.md`, Appendix A) current when introducing new ones.
- **Structure**: Rationale first, mechanism second. Explain *why* before *how*. Keep implementation pointers as terse trailing parentheticals or appendix rows, not the spine of the prose.
- **Formatting**: No bold-for-emphasis mid-sentence (italics at most). No adverb-ly hyphenation ("locally installed", not "locally-installed").

## Template: new topic document

```markdown
# Astrometrics <Topic>

*Version 1.0 · YYYY-MM-DD · Status: draft*

## Abstract
One paragraph: what this document covers and for whom. Companion-doc pointer if applicable.

## 1. Introduction
**Statement of need.** ...

## 2. <Body sections>
Rationale-first prose; equations tagged; admonitions where earned; runnable examples for
user-guide-tier docs.

## Acknowledgments

## References
[1] ...

## Appendices (parameters, schemas, glossary pointers)
```

## Verification checklist before committing a doc change

- The documentation tests pass:
  `.venv/bin/pytest astrometricslib/test/test_documentation_rules.py astrometricslib/test/test_documentation_style.py astrometricslib/test/test_architecture_doc_claims.py astrometricslib/test/test_validation_register.py astrometricslib/pipelines/test/test_pipeline_doc_parity.py -q`
- Every in-text `[n]` resolves to exactly one reference entry; no orphaned numbers.
- Every published method or tool the paper names has a reference.
- Equation/table/figure numbers are sequential with no gaps or duplicates.
- Companion filename references point at files that exist.
- Mermaid blocks and `\tag{}` math render (GitHub/VS Code preview).
- No implementation identifiers in architecture-tier documents.
