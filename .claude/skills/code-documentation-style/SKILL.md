---
name: code-documentation-style
description: House style for prose documentation everywhere in this repo except under documentation/ (which has its own documentation-style skill). Covers README.md files and other Markdown write-ups that explain what a module, pipeline, or tool does. Use when writing, editing, restructuring, or reviewing any such file — including updating one incidentally while touching its code.
---

# Repo Documentation Style

Conventions for prose documentation outside `documentation/` — mainly `README.md` files under
`astrometricslib/`, `wayfindinglib/`, `backend/`, `ui/`, and similar code-adjacent write-ups that
explain what a module or pipeline does. Update the relevant README whenever you touch the code it
describes, even if that wasn't the primary task.

**Out of scope**: anything under `documentation/` follows `documentation-style` instead — it
targets a hobbyist reading level with its own citation/numbering/admonition rules, which stay as
they are. Also out of scope, structurally: docstrings and inline code comments follow the
project's existing per-language conventions (LSST-style Python docstrings, TypeScript JSDoc) and
CLAUDE.md's docstring requirement — this skill doesn't change their format. Their target reading
level, however, is the same UI-tier split as this skill's prose docs (see "Reading level" below
and CLAUDE.md): high school for `ui/`/`backend/`/`electron/` comments, first-year college level
elsewhere.

## Tone

- Strictly neutral and direct. No marketing language, no hedging, no rhetorical questions used as
  scene-setting.
- No anecdotes. Don't narrate development history ("an earlier version did X", "this used to be a
  toss-up", "on 2026-09-24 we found..."). If a past state is relevant context, state the current
  behavior and its rationale — not the story of how it changed. Provenance facts (a source URL, a
  download date, a file count) are fine as plain facts; frame them as facts, not as a story.
- Don't editorialize about how good, elegant, or clever a design is. Describe what it does.

## Active voice

Write with the module, pipeline, or script as the subject doing the action, not as the passive
recipient of one.

- Prefer: "The pipeline drops frames that fail this check."
- Avoid: "Frames that fail this check are dropped."

Passive voice is acceptable only when the actor is genuinely unknown or irrelevant to the point
being made (rare in this codebase, since almost everything here has a clear actor: a pipeline
stage, a script, a class).

## Reading level

The target reader depends on which side of the repo the document lives in:

- **UI-tier docs — `ui/`, `backend/`, `electron/`** (one user interface over the two domain
  libraries, per the project's own layering; `backend/` is not a separate audience from `ui/`):
  write for a high school reading level. Short, plain sentences; no assumed math beyond
  arithmetic; define even everyday technical words (API, endpoint, component) the first time they
  appear.
- **Everything else this skill covers — `astrometricslib/`, `wayfindinglib/`, and any other
  non-UI-tier code** (this is the default when a document doesn't clearly belong to the UI tier):
  write for a first-year college engineering or science student — someone who can follow algebra,
  basic statistics, and plain descriptions of physical quantities, but does not already know this
  codebase or its domain jargon.

Either way, define a domain term in a short parenthetical or clause the first time it appears in a
document ("airmass, a measure of how much atmosphere the light passed through"), rather than
assuming the reader already knows it or footnoting it elsewhere — the UI-tier level just requires
defining more terms, and more plainly.

Keep sentences doing one job each. Prefer several short sentences over one sentence with several
subordinate clauses. This matters more, not less, at the UI-tier's high school level.

## Structure

- Say what the thing does before saying how, in one or two sentences, before any list or detail.
- When documenting a multi-step process (a pipeline, a stage, a script), lay out the steps in
  order, numbered, each as a short imperative or declarative statement of what happens — not a
  narrative of how it was built.
- When a step produces a quality metric, confidence score, or flag, document it explicitly: what
  it measures, its units, what a high vs. low value means, and what later step or reader consumes
  it. Treat this as required content for any README describing a pipeline stage, not optional
  detail.
- Close a README describing runtime behavior with a pointer back to the code as the source of
  truth for exact thresholds and edge cases, rather than trying to keep the prose in lockstep with
  every implementation detail: "For exact behavior, read the code."
- For a directory of files rather than a single flow (e.g. `utilities/`, a data directory), state
  what each file or file group is and why it lives there, rather than exact procedural steps.

## What this looks like in practice

Before (anecdotal, passive, narrates history):

> This is denser than an early version of this set that only sampled every ~5 subtypes: a star
> whose true type falls between two coarse rungs used to be a near-toss-up between two templates,
> showing up as an `is_classification_ambiguous` flag. With every rung present, that same star
> usually has one clearly-closer template to land on.

After (neutral, active, states current rationale):

> Including every rung avoids the case where a star's true type falls between two widely-spaced
> reference templates: with only every fifth subtype sampled, such a star would sit roughly
> equidistant from its two nearest templates, and the classifier would report the match as
> ambiguous (the `is_classification_ambiguous` flag) more often than necessary.

## Verification checklist before committing a doc change

- No sentence narrates "before/after" development history as a story; current-state rationale
  replaces it where the history mattered.
- Every passive-voice sentence in the main prose has been checked for a natural active rewrite;
  only genuinely actor-less statements stay passive.
- The target reader for this file's tier (high school for `ui/`/`backend/`/`electron/`,
  first-year engineering for everything else) could read it without stopping on an undefined term.
- Every quality metric, flag, or score the described code produces is named, defined, and tied to
  who or what reads it next.
- If the file documents runtime behavior in detail, it points to the code as the source of truth
  rather than promising to stay exhaustively in sync with it.
