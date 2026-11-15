# Parameter inventory

This folder keeps the lists behind two rules about the numbers in `astrometricslib/` and
`wayfindinglib/`.

Every tuned value is a `TunedParameter` declaration (public module `astrometricslib.parameters`).
The declaration holds its role, source, equipment, kind, pending status and register entry. A plain
numeric constant or config default is what is left, and each one is on exactly one of two lists.
`astrometricslib/test/test_parameter_inventory.py` fails when a plain numeric value is on neither
list, when a row names a value that is gone, and when a value is on both lists or twice.

## The lists

- `not_tuning.csv` (`file,name,reason`): values that are not tuning, such as units, physical
  constants, timeouts, caps and plumbing. Each row has a reason.
- `tuning_not_registered.csv` (the backlog): tuning values that are not declared yet because a
  `TunedParameter` default cannot hold them (dict or nested-tuple values). The `draft_*` columns hold
  a first-pass role, source and equipment in the declaration's words; `related_entry` names the
  register entry that may cover the value; `note` says why it is not declared. The backlog only
  shrinks.

A row is keyed by `file` and `name`. A class constant is `Class.NAME`; a config key is
`[Section] key`.

A third list holds the rule that automatic values are measured
(`.claude/skills/documentation-style/CONVENTIONS.md`, Section 2):

- `automatic_not_measured.csv` (`parameter,register_entry,role,status`): declared parameters whose
  role is automatic (anything but `recommendation`) and whose register entry is not Measured. A
  design-estimate row is never Measured. The rows come from the two registries and the entry
  status in `documentation/library_design/validation/`.
  `astrometricslib/test/test_measured_rule.py` fails on a new automatic parameter that is not
  Measured, and on a row whose parameter is now Measured, advisory or gone. This list only shrinks.

## Adding a constant

1. If it is tuning, declare it as a `TunedParameter` with the ID of its register entry. The test
   checks that the entry exists.
2. If it is not tuning, add a row to `not_tuning.csv` with the reason.
3. If it is tuning but cannot be declared yet, add a row to `tuning_not_registered.csv` with a note.
   Delete the row when you declare the value.

## Running the report

```bash
.venv/bin/python build/documentation/parameter_inventory.py --report
```

The report prints the counts and any unlisted name, and exits with 1 when one is unlisted.

## Reading the parameters

```bash
.venv/bin/python build/documentation/generate_parameter_table.py --out <folder>
```

writes `parameter_table.csv`, one page per register file and `parameter_table_by_equipment.md`
from the two registries and the backlog.
