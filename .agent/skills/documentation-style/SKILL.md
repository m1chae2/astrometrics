---
name: documentation-style
description: House documentation conventions for Coniglio Observatory docs. Use when writing, editing, restructuring, or reviewing any file under documentation/ (papers, guides, requirement docs) to apply the repo's structure, citation, numbering, admonition, and cross-reference rules.
---

# Coniglio Observatory Documentation Style

Conventions for everything under `documentation/`. Modeled on the Siril, Astropy, photutils,
and specutils documentation ecosystems, adapted to a markdown-in-repo setting.

## Document tiers

Match the document to its tier before writing; each tier has different rules.

1. **Explanation tier (papers)** — scientific-paper-style documents such as
   `Image_Processing_Architecture.md`. Describe *what the system does today and
   why* — never implementation history ("prior to this work…" is banned). Architecture-tier
   papers are implementation-agnostic: no file paths, class names, function names, config keys,
   or tuned numeric defaults (naming external tools/libraries as design-level attributions is
   fine — "delegated to Siril" is architecture, `siril_interface.py` is not). Implementation-tier
   papers carry the concrete identifiers and empirical results.
2. **Specification tier** — `documentation/requirements/`. Numbered requirements; unchanged by
   this skill.
3. **User-guide tier (aspirational)** — task-oriented topic guides pairing prose with runnable
   examples and their output, in the style of photutils/specutils topic pages. Does not exist
   yet; when creating one, follow the template below.

## File conventions

- Naming: `<Topic>.md` (underscored title case).
- Companion documents cross-reference each other by plain bracketed filename in prose —
  `` `Image_Processing_Implementation.md` `` — never as a numbered reference entry.
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
  belong in prose; implementation-tuning values (aperture radii, thresholds, rank cutoffs) belong
  in a parameter appendix with an "empirically validated?" column. Never present an unvalidated
  design estimate as a validated result — say which it is.
- **Acknowledgments section** (Astropy convention): credit the open-source projects the system
  builds on, with citation cross-references.
- Diagrams are Mermaid code blocks; math is `$...$`/`$$...$$` LaTeX — both render on GitHub.
  Wide content must live in tables, not overflow prose.

## Admonitions (Siril convention)

Use GitHub-flavored markdown alerts, sparingly (a handful per document, not decoration):

- `> [!NOTE]` with a `**Theory.**` lead-in — optional mathematical background a reader may skip.
- `> [!WARNING]` — a genuine trap (e.g., "never compare FWHM values from two different
  estimators").
- Do not use alerts for emphasis; if everything is noteworthy, nothing is.

## Writing style

- Professional scientific register; no bold-for-emphasis mid-sentence (italics at most).
- Sentences do one job; split anything past ~40 words carrying multiple parentheticals.
- Assume an engineering-degree reader with basic image-processing knowledge: define
  domain-specific terms at first use (ADU, zero order) and keep the glossary
  (`Image_Processing_Architecture.md`, Appendix B) current when introducing new ones.
- Rationale first, mechanism second: every design decision states *why* before *how*, and
  implementation pointers are terse trailing parentheticals or appendix rows, not the spine of
  the prose.
- No adverb-ly hyphenation ("locally installed", not "locally-installed").

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

- Every in-text `[n]` resolves to exactly one reference entry; no orphaned numbers.
- Equation/table/figure numbers are sequential with no gaps or duplicates.
- Companion filename references point at files that exist.
- Mermaid blocks and `\tag{}` math render (GitHub/VS Code preview).
- No implementation identifiers in architecture-tier documents.
