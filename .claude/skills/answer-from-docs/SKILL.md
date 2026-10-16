---
name: answer-from-docs
description: Answer questions about how, why or where the Astrometrics software does something by reading the documentation first and the code second. Use for any question about a pipeline, method, tuned value, design choice, app view or observatory behavior, and before explaining or changing code you have not read yet.
---

# Answer from the docs first

The documents say what the software is meant to do and why. The code says what it does now. Read the documents to orient, read the code to confirm, and report any place where they disagree.

## Steps

1. **Find the topic.** Open `llms.txt` at the repository root. It maps each topic to its paper section, review guide, README, evidence and code. For a document not listed there, use the table in `documentation/About_These_Docs.md`, Section 3. Look up unfamiliar terms in `documentation/Glossary.md`.
2. **Read the right kind of document.**
   - *Why or how in principle* (methods, equations, design reasons): the architecture paper section.
   - *Which module, class or tuned value*: the implementation paper, then the pipeline review guide.
   - *How a stage runs, step by step*: the pipeline README next to the code.
   - *Whether a value was measured, and on what data*: the validation register entry and its status label (Measured, Partly measured, Design estimate).
   - *How to use the app*: the user manual or the observatory control guide.
3. **Confirm in the code.** Open the module the document names and check the claim you plan to repeat: the function exists, the value matches, the steps run in that order.
4. **Answer with sources.** Cite the document section (for example, "Astrometrics architecture, Section 4.2") and the code path (`file.py:line`). Say which parts you confirmed in the code.
5. **Report disagreements.** When a document and the code differ, the code is the current behavior. Say so plainly, name both places, and offer to fix the document (or the code, if the document states the intended design). Do not quietly answer from one and ignore the other.

## When the docs say nothing

Say that the documents do not cover the topic, answer from the code, and suggest where the missing text belongs under the rules in `documentation/Documentation_Conventions.md`, Section 1: theory in the architecture paper, code names and tuned values in the implementation paper, evidence in the validation register.
