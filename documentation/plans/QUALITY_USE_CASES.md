# Quality Use Cases

This document describes how the user is expected to use the quality metrics, the target dataset
report and the new displays, step by step. It is the agreement the other plans build toward. If a
use case here is wrong, the plans are wrong too. Each "Done right when" line becomes an acceptance
test.

It is the user-facing companion of three plans:

- `QUALITY_METRICS_PLAN.md`: the measurements, efficiency, and the target dataset report (its
  section 6);
- `QUALITY_SCIENCE_PLAN.md`: the new science metrics;
- `UI_DISPLAY_PLAN.md`: the displays where the user sees them.

## Status

Status on 2026-10-17: agreed with the user. Section 6 records the points settled while writing it.
The change control board reviewed it a second time on 2026-10-17, and its changes are folded in
(appendix A).

## 1. The user and the setting

- **One observer** with a small refractor, ZWO cameras (an ASI533MM Pro for imaging, an
  ASI120MC-S for guiding), a filter wheel with an L filter, a slitless grating with its
  spectroscopy filter, and a Nikon D5300. The observatory computer is a StellarMate running Ekos.
- **Ekos captures today.** The app reviews the data, recommends what to do next, and watches the
  live session. It never commands a device while Ekos captures. Direct control from the app comes
  later.
- **Two monitors** at the desk.
- **Frames arrive in the library** when the user syncs from the StellarMate. Each new light frame
  is measured once when it arrives.

## 2. Rules every use case follows

1. **No hardware advice.** The app never suggests buying or replacing equipment. It may suggest
   capture settings, scheduling, calibration, which frames to use, session procedure, and
   adjustments to the equipment the user has (polar alignment, tilt, camera rotation, dew heater).
   When only new hardware would help, it says so as a plain limit, without advice.
2. **Every number shows where it came from.** Each warning and recommendation opens to the
   measurements behind it, the limit it was compared with, and how confident the app is. A rule
   that is not yet validated on the user's data is shown as tentative.
3. **"Not enough data" is an answer.** The app says when it cannot judge something, and why. It
   never fills the gap with a default.
4. **Recommendations are worked out fresh** each time they are shown, from the stored
   measurements. Only the measurements and the user's own plan are saved.
5. **Limits come from the user's equipment** and its earlier nights. This happens on its own, with
   nothing for the user to do: after an equipment change, limits that depend on earlier nights
   start fresh and say "no baseline yet", while limits from physics and the camera profile apply at
   once.
6. **The screens only show what the libraries return.** Every list, ranking, grouping, sentence
   and unit the user sees comes ready-made from `wayfindinglib` or `astrometricslib`. The screens
   do no sorting, judging, wording or unit conversion of their own. So the AI companion and the
   screens always say the same thing.
7. **Observing advice comes from `wayfindinglib`.** `astrometricslib` only measures: star widths,
   noise, how close the brightest star comes to clipping. Every piece of advice about what to do at
   the telescope (exposure, schedule, refocus, more flats) is worked out in `wayfindinglib` from
   those measurements.
8. **Every statement has a type and a confidence.** Each item the app shows is one of four types,
   told apart by an icon and a word, never by color alone:
   - **Warning:** a problem in the data already taken.
   - **Recommendation:** something to do, with numbers and an action button where one fits.
   - **Limit:** a plain statement of what the equipment or the sky allows. It has no action button.
   - **Not enough data:** shown greyed, with the reason it cannot be judged yet.

   Each item also carries a confidence chip in words: "Measured", "Partly measured" or
   "Tentative" (a rule that is still a design estimate). Items arrive already in order of
   importance. A screen shows the top three, with "Show all" for the rest.

## 3. Reviewing observations

### UC1. Review last night

- **When:** the morning after a session.
- **Where:** Observation Execution, with last night chosen in its night selector (section 6).
- **The user sees:**
  - each target imaged, with its loss account in two parts, against a stated goal and reference.
    Frames set aside are real lost time. Kept frames lose part of their value when they are less
    efficient than the reference. For example:
    "M 81 (depth, against your best L nights on M 81): 4.0 h captured.
    Set aside 0.8 h: clouds 0.6 h, trailed 0.2 h.
    Kept 3.2 h, worth 2.6 h effective: Moon 0.4 h (model-derived), cause unknown 0.2 h.
    Transparency: not included yet."
    Seeing is not counted as lost time for an extended object such as a galaxy, since a softer
    night still adds depth. Transparency shows as "not included" until the zero point from the
    science plan exists. The Moon's share comes from a sky model and is labeled model-derived;
  - a "needs attention" badge on each target that has warnings, with the count and how serious the
    top one is;
  - "Open in Planner" on each target row, which makes it the current target and opens its Data
    Quality panel (UC2);
  - guiding over the night, with the stretches where it failed;
  - focus against temperature;
  - warnings that repeat across nights ("guide star faint on 3 of the last 4 nights").
- **Decisions it supports:** whether last night's problems need fixing before tonight (focus
  routine, guider calibration, dew heater), and which targets got usable data.
- **Done right when:** the loss account adds up to the captured time, every lost hour has a cause
  or is marked "cause unknown", and the causes are checked on nights where the real cause is known
  (a night with logged clouds, a night with a bright Moon). Adding up alone cannot fail, because
  the remainder is always "cause unknown".

### UC2. Review a target's data

- **When:** before deciding what to do next with a target, often after UC1.
- **Where:** Observation Planner, Data Quality panel, after selecting the target.
- **The user sees:**
  - one row per capture setting (camera, filter, exposure, gain, offset, binning): frames, nights,
    frames set aside, captured and effective time;
  - warnings about the data already taken, for example:
    - "Frames at 120 s clip the target core in 38 of 40. Use 45 s or less."
    - "30 s frames are limited by read noise. 90 s reaches the same depth in 25% less time."
    - "12 of 60 frames set aside; clouds from 22:40 to 23:10."
    - "No darks for gain 100, offset 10, bin 1, 60 s, −10 °C."
- **Decisions it supports:** which capture settings to keep using, which frames to leave out of the
  stack, and what calibration is missing.
- **Done right when:** every warning names its capture setting, its evidence and its confidence,
  and no recommendation elsewhere (UC7) contradicts a warning here.

### UC3. Check whether more data helped

- **When:** after new frames of a target are stacked.
- **Where:** Observation Planner, Data Quality panel, forecast check.
- **The user sees:** three numbers, each with its tolerance: the forecast when the frames were
  planned, the forecast given the frames actually taken, and what the new stack measured. For
  example:
  - "Planned: noise down 29% ± 5. Given the frames taken: down 29% ± 5. Measured: down 27% ± 5. As
    expected."
  - "Planned: noise down 29% ± 5. Given the frames taken: down 19% ± 5. Measured: down 18% ± 5. The
    new frames had efficiency 0.55 (Moon). The stacker gave them equal weight, because their
    exposure length matched the older frames."
  - "Planned: noise down 29% ± 5. Given the frames taken: down 28% ± 5. Measured: down 6% ± 5,
    cause unknown." After one shortfall the app only reports it. It names a noise floor ("more
    frames may not help") only when two or more steps of data show the noise leveling off.
- **Decisions it supports:** whether to keep adding frames, or fix something first.
- **Done right when:** the forecast is rebuilt from the stored history, not from a saved
  recommendation; it uses the weights the stacker actually gave each frame; and the new
  recommendation reflects the new stack.

### UC4. Look into a warning

- **When:** a warning is surprising, or the user wants to see the frames.
- **Where:** the warning's evidence, then the Image Viewer, often on the second monitor.
- **The user sees:** the measurements behind the warning, frame by frame, the limit, where the limit
  came from (equipment, own earlier nights, or a validated data set), and a link that opens the
  Image Viewer showing only the cited frames. If an Image Viewer window is already open, the link
  uses it.
- **Decisions it supports:** whether to trust the warning.
- **Done right when:** the user can trace any warning to the frames and numbers that caused it.

### UC5. Trust a science result

- **When:** reviewing a spectrum's classification, a variable star's period, or an asteroid
  candidate.
- **Where:** Astronomy Manager, with a quality chip beside the result. The chip opens the same
  statement view used everywhere else (rule 8).
- **The user sees:** the raw statistic and its uncertainty (for example the fit's relative RMS and
  the gap to the next class), the judgment of whether it is good enough for its purpose, and that
  judgment's confidence. A probability appears only where it was validated on held-out data. For a
  spectrum, the answer can also be "not reachable at this resolution": more time will not separate
  the two best classes with this grating.
- **Decisions it supports:** whether to use the result, note a caveat, or observe again.
- **Done right when:** no result shows a bare yes or no without the number behind it.

## 4. Planning observations

### UC6. Decide what to image tonight

- **When:** in the afternoon or evening, or at night when plans change.
- **Where:** Observation Planner, Tonight panel; the Planetarium on the second monitor.
- **The user sees:**
  - the current target's altitude over the night, with the Moon, twilight and altitude limits;
  - the planning time, as "Now" or "fixed at 22:00", the same in both windows;
  - tonight's candidates ranked by how much an hour of real clock time helps each one, counting
    downloads, dither settling and autofocus: "An hour on NGC 7331 cuts its noise by 18%; an hour
    on M 57 by 4%";
  - separate rankings for imaging, photometry and spectroscopy, because their gains are not
    comparable (asteroid searches are not ranked);
  - targets past their stop signal ranked at zero, with the reason;
  - a note when a target ranks high only because it has little data so far;
  - tonight's expected sky (brightness from the Moon and altitude, and extinction) labeled as a
    model, with its confidence;
  - "Show on sky" to center the Planetarium on a candidate, and "Send to plan" to add it.
- **Decisions it supports:** which targets to image tonight, and in what order.
- **Done right when:** the ranking comes from `wayfindinglib` (`get_advisory(kind="tonight")`),
  uses the user's own measured data and tonight's sky model, and a target with no data yet is
  marked as new rather than ranked.

### UC7. Plan the next session for a target

- **When:** after UC2, UC3 or UC6.
- **Where:** Observation Planner, recommendations and night plan.
- **The user sees:** recommendations with numbers, for example:
  - "Take 20 more frames at 90 s, filter L, dither every 3 frames (about 35 min with downloads and
    dithers). Expected: noise down about 29%." The count is in effective time, so it already
    accounts for 90 s frames gaining more per minute than the 30 s frames in UC2.
  - "Image above 35° altitude; frames lower down lost 35%."
  - "Refocus every 1.4 °C. Stars widen 0.7 px per °C, and the allowed widening is 1 px."
  - Advice on dither size comes later, once the science plan measures it.
- **What the user does:** chooses "Add to plan" on a capture recommendation, which puts its exact
  values in the night plan. If the plan already holds the same or a clashing setting, the library
  merges the two or refuses with the reason. Other recommendations join the night plan as a
  checklist.
- **The sequence summary for Ekos:** the night plan is shown as rows in the same order and with the
  same fields as Ekos's Capture module (filter, exposure, count, binning, gain, offset). Each row
  says which Ekos screen the value goes on; for example, dither size is set in Ekos's Guide options
  and dither frequency on each capture job. Each row has a copy button and a "done in Ekos" tick.
  A value the camera cannot take is refused by the library, and the reason shows beside the
  button.
- **Done right when:** the sequence summary matches the night plan exactly, and every suggested
  value is one the user's equipment allows.

### UC8. Frame a target

- **When:** before imaging a new target, or when a target needs a mosaic.
- **Where:** the Planetarium, framing overlay.
- **What the user does:** places the sensor's field, sets the rotation and the mosaic panels on the
  sky, and chooses "Use this framing", which sends it to the night plan. The panels are worked out
  by `wayfindinglib`. The first version may show the field, the panels and the rotation as numbers
  without dragging.
- **Done right when:** the framing in the night plan matches the overlay, panel for panel.

### UC9. Plan calibration frames

- **When:** a warning says calibration is missing or does not match, or a capture setting is new.
- **Where:** the warning in Observation Planner; the calibration library in Observatory Manager.
- **The user sees:** which darks, flats or bias frames are missing for which capture setting, and
  what the library holds today. A frame only counts as a match when exposure, filter, gain, offset,
  binning and temperature all match. Each calibration warning links to the exact row in the
  calibration library it refers to.
- **Decisions it supports:** which calibration frames to take, and when.
- **Done right when:** calibration advice comes from one set of rules, and the same warning shows in
  the target report and the calibration library.

### UC10. Adjust the equipment

- **When:** a recommendation of the adjustment class appears.
- **Where:** Observation Planner, as a checklist item in the night plan.
- **The user sees:** "Field rotation shows a polar alignment error; redo the polar alignment" or
  "Stars in one corner are 30% wider; check the camera tilt", with the evidence.
- **Done right when:** after the adjustment, the next night's measurements show whether it worked
  (UC1 and UC3).

## 5. During the night and around the equipment

### UC11. Watch a running Ekos session

- **When:** during a session.
- **Where:** Observation Execution in monitor mode, on the main monitor. Monitor mode means Ekos is
  in charge of every device, so the app's control panels are greyed out.
- **The user sees, from top to bottom:**
  - the quality strip: the target's top warning, top recommendation and the night's loss so far;
  - the live session as one verdict with its reason, for example "Guiding steady; last dither
    settled";
  - the frame list, with each frame's quality once it is in the library, and the frames the live
    status marks as ruined. Reasons for setting frames aside appear only after stacking, so at
    night the list shows the ruined frames instead;
  - guiding trends on a second panel or tab;
  - alerts as one sentence plus a count, with details on click;
  - a staleness line: "last sync 23:40, 25 min ago".
- **What the user does:** chooses "Sync now" to fetch the Ekos logs and the new frames. Syncs are
  manual. After a sync, the app reads target changes from the Ekos analyze log (the mount slewing
  and then tracking at a new position, matched to the nearest library target). The user acts in
  Ekos (refocus, recalibrate the guider, move to another target). The app sends no commands.
- **Refreshes:** the quality strip and the frame list update when the frames from a sync have been
  ingested into the library, not on a timer.
- **Done right when:** a problem shows in the first sync after it started.

### UC12. Change plans mid-night

- **When:** clouds arrive, a target sets early, or the first frames show a problem.
- **Where:** Observation Planner on the second monitor, at the current time.
- **The user sees:** the candidates re-ranked for the rest of the night, by the same rules as UC6.
  Each candidate has "Switch to", which makes it the current target and shows its sequence summary.
- **Decisions it supports:** what to switch to, entered in Ekos.

### UC13. Measure a new camera setting

- **When:** the user starts using a gain, offset or binning not measured before.
- **Where:** Observatory Manager, camera profiles.
- **What the user does:** takes flat pairs and bias pairs at that setting and runs the measurement.
- **The user sees:** the measured gain and read noise, saved with their source. Until then, the
  target report marks that setting's efficiency as relative to the user's own data only.

### UC14. Ask the AI companion

- **When:** the user prefers to ask in words, for example "what should I do next on M 81?".
- **Where:** the read-only AI companion, through the app's MCP tools.
- **What happens:** the companion reads the same target dataset report and measurements through
  the public quality calls and explains them. It cannot change settings, run the camera
  measurement of UC13, or command devices. When its tools cannot answer, it reports the gap.

## 6. Points agreed

Agreed with the user on 2026-10-17:

1. **The night review lives in Observation Execution.** It has a night selector that defaults to
   tonight and can show any earlier night from the stored data (UC1).
2. **A new target gets a starting point.** For a target with no frames yet, the report has no data
   to judge. It offers a starting capture setting worked out from the sky brightness expected for
   the filter and the Moon, the camera's read noise, and the brightest stars in the field from the
   catalog. The total time depends on how bright the target is per area of sky, not its total
   brightness. It is marked as a starting point, not a recommendation from data.
3. **The report refreshes when frames are ingested.** The quality strip, the frame list and a
   target's report update only when new frames of that target are ingested into the library, not
   on a timer.
4. **The Nikon D5300 stays relative for now.** The use cases assume the ZWO cameras. The Nikon
   gets measurements, but its gain and read noise are not yet measured, so its recommendations
   compare against its own earlier data only until UC13 is done for it.
5. **The checklist is part of the night plan.** Non-capture recommendations join the night plan as
   a checklist. The plan and its ticked items are saved as the user's plan; the recommendations
   themselves are still worked out fresh. A ticked item comes back as a recommendation if the next
   measurements show it did not work, and it says why ("ticked Oct 12; stars still 28% wider Oct
   15").
6. **Syncs stay manual** (second board review, 2026-10-17). "Sync now" on Observation Execution
   fetches the Ekos logs and the frames. Target changes are read from the Ekos analyze log after
   each sync. A staleness line shows the time of the last sync. Automatic syncing may come later
   and is not designed now (UC11).
7. **No special night theme** (second board review). Observation Execution uses the same theme as
   the rest of the app. The basics of access still apply: how serious an item is shows by icon and
   words, not color alone; every screen works from the keyboard; charts have a text alternative;
   and summary text can be selected and copied.
8. **Popped-out windows are not restored** (second board review). The user sets up the second
   monitor once per session. The app does not save or reopen popped-out windows.

## 7. Not use cases

- Recommending new or different hardware.
- The app commanding any device while Ekos captures.
- Planning a whole season automatically, or moving targets between nights without the user.
- Saving recommendations. Only measurements and the user's plan are saved.
- A phone companion. It is planned separately.
- Sharing time between filters. The user owns only an L filter and a spectroscopy filter.
- Asteroid search completeness and the nightly false-alarm check, for now. They wait until
  asteroid searches are a regular part of the user's nights.
- A special night or red theme (section 6).
- Scheduling spectral standard stars at a matching altitude, and checking that asteroid reports
  are ready for the Minor Planet Center (including clock timing). These are later items in
  `QUALITY_SCIENCE_PLAN.md`.

## Appendix A. Review board changes

The board met a second time on 2026-10-17. Four reviewers took part: an astronomer, a software
engineer, a technical architect and a user-experience reviewer. Each finding below touched this
document. No findings were rejected.

| Finding | Raised by | Where it landed |
| :--- | :--- | :--- |
| The screens must only show what the libraries return | Architect, UX | Section 2, rule 6 |
| Observing advice belongs in `wayfindinglib`; `astrometricslib` only measures | Architect, astronomer | Section 2, rule 7 |
| Statement types and confidence labels, told apart by icon and word | UX, architect | Section 2, rule 8 |
| Loss account: set-aside frames apart from kept-frame efficiency; goal and reference; seeing not lost time for extended objects; transparency not included yet; Moon model-derived | Astronomer | UC1 |
| Loss causes must be checked on nights with a known cause | Astronomer, engineer | UC1 |
| "Open in Planner" and a needs-attention badge in the night review | UX, architect | UC1 |
| UC2 and UC7 examples contradicted each other; counts in effective time with overheads | Astronomer | UC2, UC7 |
| Forecast check: three numbers with tolerances, the stacker's real weights, noise floor only from two or more steps | Astronomer, engineer | UC3 |
| Evidence opens the Image Viewer on the cited frames, reusing an open window | UX | UC4 |
| Quality chip in the Astronomy Manager; "not reachable at this resolution" | UX, architect, astronomer | UC5 |
| Ranking from `wayfindinglib`, in clock time with overheads, per use, stop signal at zero, thin-dataset note, labeled sky model | Astronomer, architect | UC6, UC12 |
| Planning time shown as "Now" or a fixed time in both windows; "Show on sky" and "Send to plan" | UX, architect | UC6 |
| Refocus advice states its tolerance; dither size advice comes later | Astronomer | UC7 |
| Sequence summary rows mirror Ekos fields and screens; copy and "done in Ekos"; values and clashes checked by the library | Engineer, UX, architect | UC7 |
| Framing worked out by `wayfindinglib`; first version may be read-only | Architect | UC8 |
| Calibration match includes gain, offset, binning and temperature; link to the exact library row; one rule set | Engineer, architect | UC2, UC9 |
| Monitor mode is the existing delegation policy | Architect, engineer | UC11 |
| Manual "Sync now", target changes from the analyze log, staleness line (decision D1) | Engineer, UX | UC11, section 6 |
| At night, show ruined frames from the live status, not set-aside reasons | Engineer | UC11 |
| Screen order: quality strip, one live verdict, frame list | UX | UC11 |
| "Switch to" on re-ranked candidates | UX | UC12 |
| The camera measurement is a write, kept from the read-only companion | Engineer | UC14 |
| "Is tonight photometric?" for L photometry (decision D2) | Astronomer | UC15 |
| Starting point for a new target from sky, read noise and bright stars | Astronomer | Section 6, point 2 |
| A returning checklist item says why it came back | Architect, UX | Section 6, point 5 |
| No special night theme; access basics kept (decision D3) | UX | Sections 6 and 7 |
| Popped-out windows are not restored (decision D5) | UX, engineer | Section 6 |
| No filter-sharing use case; spectral standards and Minor Planet Center readiness are later science items (decision D2) | Astronomer | Section 7 |
| Asteroid completeness and null search deferred (decision D4) | Astronomer | Section 7 |
| "Done right when" lines become acceptance tests | UX | Introduction |
