# Parameter inventory

This folder keeps the lists behind one rule: every numeric constant and config default in
`astrometricslib/` and `wayfindinglib/` is either named in the validation register, or listed here.
`astrometricslib/test/test_parameter_inventory.py` fails when a new one is in none of the three.

## The two lists

- `not_tuning.csv` (`file,name,reason`): values that are not tuning, such as units, physical
  constants, timeouts and plumbing. Each row has a reason.
- `tuning_not_registered.csv` (the backlog): values that are tuning but have no register entry yet.
  The `draft_*` columns hold a first-pass role, source and equipment; `related_entry` names an
  entry that may already cover the value. The backlog only shrinks.

A row is keyed by `file` and `name`. A class constant is `Class.NAME`; a config key is
`[Section] key`.

## Adding a constant

1. If it is tuning, register it in the matching file in `documentation/library_design/validation/`
   (see Section 2.2 of `index.md` for role, source and equipment).
2. If it is not tuning, add a row to `not_tuning.csv` with the reason.
3. When you register a backlog value, delete its row from `tuning_not_registered.csv`. A value that
   is registered cannot also be listed in either file.

## Running the report

```bash
.venv/bin/python build/documentation/parameter_inventory.py --report
```

The report prints the counts and any unlisted name, and exits with 1 when one is unlisted.
