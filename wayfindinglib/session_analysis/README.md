# Session-quality analysis

This folder judges how an observing night went, using the records the incumbent control software (Ekos) left behind. It answers three questions in order: Is the data good? What does it show? What should change?

Each analysis follows the same three stages the science library's pipelines use. The stage names below match the folder names inside each analysis.

1. **Pre-processing** asks whether the data is good. It reports problems in how the data was gathered, before anyone interprets the numbers.
2. **Processing** measures what the data shows.
3. **Post-processing** turns the measurements into recommendations. Each recommendation names the evidence behind it and the limit it was compared with.

## Where the limits come from

Every limit comes from the equipment in use, not from a number typed into the code. `wayfindinglib/analytics/performance_envelope.py` works the limits out from the telescope, cameras, guide scope, the star width the equipment's own frames show, and the equipment's earlier nights. When the equipment changes, the limits change with it, and the new equipment starts with no history. A limit without enough data behind it reports no value, and the check that needs it gives no verdict.

A night is judged only against the equipment's **earlier** nights. A history that contained the night itself, or later nights, would let a bad night raise the limit it is compared with.

## What is stored

Nothing the analysis produces is stored. A stored recommendation would go stale as soon as the equipment, and so every limit, changed. The analysis reads the stored guiding samples, guiding runs, session records and the science library's frame records, and recomputes the result each time.

## Files

- `pipeline_base.py` — the shared four-step interface (`process_input`, `run`, `validate_output`, `to_result_dict`) and `run_session_pipeline`, which calls the steps in order. It mirrors the science library's `AnalysisPipeline` without importing it, because that interface is built around an astronomical target and a night of telescope records has none.
- `guiding/` — the guiding analysis. See `guiding/README.md`.
- `sky/` — the sky-position analysis across every night: is some part of the sky measurably worse, and which parts has no night reached. See `sky/README.md`.
- `recurring_issues.py` — lists the findings that repeat across nights, from the guiding and capture analyses.
- `capture/` — the capture analysis: completeness, clipping, star quality. It reuses the science library's frame measurements and saturation verdicts. See `capture/README.md`.

Gathering one night's data from storage is not done here. `wayfindinglib/tasks/control_tasks/session_analysis_tasks.py` (guiding) and `capture_analysis_tasks.py` (capture) do it. `ObservatoryControl.analyze_guiding_session`, `summarize_guiding_sessions`, `analyze_capture_session` and `summarize_capture_sessions` are the entry points. This keeps every stage a pure function of its input.

For exact behavior, read the code. The code is the source of truth.
