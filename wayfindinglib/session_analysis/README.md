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
- `live_status.py` — judges the session in progress from the live Ekos files. See "Live status" below.
- `recurring_issues.py` — lists the findings that repeat across nights, from the guiding and capture analyses.
- `capture/` — the capture analysis: completeness, clipping, star quality. It reuses the science library's frame measurements and saturation verdicts. See `capture/README.md`.

## Live status

`live_status.py` answers the questions an observer has during a session: Is guiding steady? Did the last dither work? Which frames are already ruined? It reads the newest Ekos analyze log and KStars text log, stores nothing, and recomputes on every call. `control.history.get_live_session_status` downloads the latest logs first and returns the result.

It reports:

- **Recent guiding.** The root-mean-square guide error along RA, along Dec, and combined, in arcseconds, over a window (ten minutes by default). A larger value means looser tracking.
- **Guiding excursions.** Stretches where the guide error ran above 8 times the session median (never below 5 arcseconds). Each is a *jump* if the error arrived at full size in one step, or a *drift* if it built up. For a drift in RA it gives the rate in arcseconds per second, and says whether that rate matches a mount whose RA axis has stopped (15.04 arcseconds per second times the cosine of the declination).
- **Recent exposures.** For each: the star count, the star size and roundness Ekos measured, the guide error in the first 30 seconds against the rest, and whether a dither came just before. A frame is flagged when it holds under half the usual star count or when an excursion overlapped it.
- **Dithers.** Each dither, whether it worked, and its settle time.
- **Session flags.** One sentence per problem, such as a failed dither, a stale file, or a guider that is not guiding.

Every limit comes from the session's own data and is checked against the 2026-10-02 session, as the comments beside each constant record.

Gathering one night's data from storage is not done here. `wayfindinglib/tasks/control_tasks/session_analysis_tasks.py` (guiding) and `capture_analysis_tasks.py` (capture) do it. `wayfindinglib/tasks/control_tasks/night_analysis.py` runs the analyses, and `control.history.query(kind="guiding")` and `query(kind="capture")` are the entry points. This keeps every stage a pure function of its input.

For exact behavior, read the code. The code is the source of truth.
