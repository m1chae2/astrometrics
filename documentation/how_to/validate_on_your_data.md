# Validate a result on your data

*Version 1.0 · 2026-10-15 · Status: current*

## Abstract

Every tuned value in the library has a register entry that gives the library calls to repeat its check. This guide shows how to read an entry and run its procedure on your own frames. The entry format is defined in the [validation register](../library_design/validation/index.md).

Terms are defined in the [glossary](../Glossary.md). This document also defines each term the first time it appears.

## 1. Steps

1. **Find the entry.** The [register index](../library_design/validation/index.md) lists one file per pipeline. A README or review guide cites an entry by its ID, such as [STK-01](../library_design/validation/stacking.md#stk-01).
2. **Check the status.** The entry's **Status** field is Measured, Partly measured or Design estimate. A Design estimate has no measurement yet, and a Partly measured entry says what is missing.
3. **Check your data against Data you need.** The field describes the kind of frames the check needs, not our files. Our frames are not in the repository.
4. **Run the Procedure.** The code blocks use the public API where one exists. A step that uses an internal function says so, and that function can change between versions.
5. **Compare with Acceptance criterion and What to look at.** The criterion says what a good result is. Compare your numbers with **Our result**, but note its **Limits**: it was measured on one telescope and one camera.
6. **Decide.** If your result passes, keep the value. If not, follow the entry's **To tune for your setup** field, and see [Change a tuned value](change_a_tuned_value.md).

## 2. Report what you found

Write down the code version (git commit), the number of frames, stars or sessions, an uncertainty, and whether the result is in-sample or held out. In-sample means measured on the data the value was tuned on. Write "not recorded" for anything you do not know. To add your result to the register, see [Add a register entry](add_a_register_entry.md).
