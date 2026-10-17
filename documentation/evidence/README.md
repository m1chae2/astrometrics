# Evidence

This folder holds measurement records. Code comments and the [validation register](../library_design/validation/index.md) cite them as the source of a number, for example a threshold or a default value.

Terms are defined in the [glossary](../Glossary.md). This document also defines each term the first time it appears.

## Rules for records

- A record is append-only. A saved record is never edited, not even to tidy it or to fix a typo. A re-run is a new file.
- A new file is named `<ENTRY-ID>_<slug>_<YYYYMMDD>.json`. The ID is the register entry the record was made for, the slug says what was measured, and the date is the day of the run. An example is `SPE-01_sky_band_sweep_vega_20260919.json`.
- A record is made for one entry. Other entries and code comments cite it by its path, `documentation/evidence/<file name>`.
- If a saved record turns out to be wrong, the register entry says so in its **Limits** or its **Runs** table, and the corrected run is saved as a new file.
- Sphinx does not build this folder (see `exclude_patterns` in `documentation/conf.py`).
- Run logs are not kept here. The app writes them to `${XDG_STATE_HOME:-~/.local/state}/astrometrics/logs`.

## Minimal schema

A new record is a JSON object with the fields in Table 1. A field that is unknown is written as `null` with a reason, never guessed.

**Table 1.** Fields of a new record.

| Field | Content |
| --- | --- |
| `schema_version` | The integer version of this schema. It is 1 for the first version. |
| `entry_id` | The register entry the record was made for, such as `STK-08`. |
| `date` | The date of the run, as `YYYY-MM-DD`. |
| `operator` | The person or agent who ran it. |
| `code_commit` | The git commit of the code the run used, and whether the working tree had uncommitted changes. |
| `script` | The script path and its arguments, or the library calls, that made the record. |
| `environment` | The Python version, the versions of the main packages and the operating system. |
| `config_diff` | The settings that differed from the defaults. |
| `seeds` | The random seeds, or `null` when the run used none. |
| `inputs` | The inputs, each with its data set ID from the register index (such as `DS-M81`) or the name of its synthetic generator, and a count. |
| `data_split` | `in-sample` when the value was tuned on these inputs, or `held-out` when the inputs were kept aside. |
| `acceptance` | The acceptance criterion, copied from the entry, or `null` when none was set before the run. |
| `metrics` | A list of measured quantities. Each has a name, a value, a unit, an uncertainty with how it was estimated, and the sample size `n`. |
| `verdict` | `Pass`, `Fail`, `Inconclusive` or `Not judged`, as defined in the register index. |

A minimal record looks like this:

```json
{
  "schema_version": 1,
  "entry_id": "STK-08",
  "date": "2026-10-15",
  "operator": "name",
  "code_commit": "0123abc (clean)",
  "script": "python -m astrometricslib.scripts.rejection_small_n_check --seed 0",
  "environment": {"python": "3.12", "numpy": "2.0", "os": "Linux"},
  "config_diff": {},
  "seeds": [0],
  "inputs": [{"dataset": "synthetic Gaussian noise", "n": 100000}],
  "data_split": "in-sample",
  "acceptance": null,
  "metrics": [
    {"name": "good samples rejected", "value": 3.0, "unit": "%", "uncertainty": null, "n": 100000}
  ],
  "verdict": "Not judged"
}
```

## Records that predate the schema

The six records below were saved before these rules and do not follow the schema or the naming pattern. They stay as they are. They are cited by their current names, so renaming them would break those citations.

- `atmospheric_mask_validation_20260919.json`
- `k_star_sky_investigation_20260919.json`
- `sky_band_sweep_vega_20260919.json`
- `spectral_resolution_estimate_vega_20260919.json`
- `spectral_score_calibration_20260919.json`
- `stack_exposure_groups_20260920.json`

### Known problem in `stack_exposure_groups_20260920.json`

The name and the `date` field of this record say 2026-09-20. Seven of its sections have names that end in `_20260921`, and its `instrument_response_check` section also mentions 2026-09-21. The record was therefore extended after its first save, which the append-only rule does not allow for new records. It is left unedited because register entries [STK-11](../library_design/validation/stacking.md#stk-11) and [STK-12](../library_design/validation/stacking.md#stk-12) cite it. Later measurements go in new files.
