# UI Display Plan

This plan reorganizes the desktop app's two busiest displays, Observation Manager and Observatory
Manager, into three displays, each built around one job: planning (Observation Planner), running
the night (Observation Execution), and setting up the equipment (Observatory Manager). It also makes the app work well across two monitors.

The plan covers:

- what the two displays hold today, and what each panel is worth,
- the decisions agreed on 2026-10-17,
- what other imaging tools do,
- the three new displays, how the Planetarium links to them, and what goes on each,
- how the app behaves while Ekos does the capture, without blocking direct control later,
- two monitors, the shared current target and the shared planning time,
- what the UI calls for each panel,
- the order of work.

It is the user-interface companion of `QUALITY_METRICS_PLAN.md`. That plan builds the target
dataset report (its section 6): the warnings and recommendations for a target. This plan decides
where the user sees them.

The UI is a thin wrapper over `wayfindinglib` and `astrometricslib`. Every panel shows what a
library call returns. The UI does no ranking, grouping, classification, text composition, unit
conversion or joins (decision 16, section 6).

The findings come from a read-only survey of `ui/` and `electron/` on 2026-10-17. The code is the
source of truth. Line numbers drift, so this plan names files and functions instead.

## Status

Status on 2026-10-17: draft, reviewed by the change control board on 2026-10-17. The board's
changes and the user's decisions from that review are folded in (decisions 16 to 22, appendix A).
Nothing built.

## 1. Findings

### 1.1 How displays work today

- **Registry.** Each display is listed twice and the two lists must agree:
  `ui/common/constants/displayFlags.ts` (`DISPLAY_DEFINITIONS`: the order in the menu and the
  `[Frontend] enable_*` flag) and `ui/App.tsx` (`MODE_PANELS`: the component for each mode). The
  display menu is the mode selector in `ui/statusHeader/StatusHeader.tsx`.
- **Windows.** Electron can already open a second full app window for any display.
  `createDisplayWindow` in `electron/main.js` places it on a monitor other than the primary one,
  and the mode selector has a pop-out button for each display (`openDisplayWindow`). Only the main
  window's position is saved and restored (`electron/window_state.js`). That stays: popped-out
  windows are set up once per session (decision 22).
- **Shared current target.** Each window is a separate React tree with its own `TargetContext`.
  A shared current target already exists through the backend handoff state:
  `HandoffService` (`backend/services/infrastructure/handoff_service.py`) stores
  `selected_target` and broadcasts a `handoff` event when `handoff:update_state` changes it.
  `ui/App.tsx` turns that event into `astrometrics:targetSelected`, which `TargetContext`
  (`ui/common/context/TargetContext.tsx`) follows. The missing piece is publishing:
  `setSelectedTarget` saves the target to local storage but never calls `handoff:update_state`,
  so a second window does not follow a target chosen in the first.
- **Layout.** Displays use `GenericDisplayLayout` (two or three columns) and `SectionPanel`. Some
  panels fetch their own data and can be placed anywhere (`LastImageViewer`, `TargetListStatus`,
  `MosaicPlanner`). Others take everything from their display's hook through props (the
  Observatory Manager panels in `ui/observatoryManager/panels/`, fed by `useObservatoryControl`).
- **Gating.** The only switches are the per-display `enable_*` flags. No panel changes when no
  INDI device is connected; the screens show "No INDI devices connected" and "Unknown" instead.
- **Live session.** No display shows the live Ekos session status that
  `wayfindinglib/session_analysis/live_status.py` already computes (guiding steadiness, dither
  results, ruined frames). `GuidingStatusPanel` shows guiding RMS and a bullseye only.

### 1.2 What each panel is worth

Ekos does the capture today, which lowers the value of every capture-control panel for now.

| Panel | Display today | Value today | Goes to |
| :--- | :--- | :--- | :--- |
| Filter Targets and Target List | Both | High, but repeated on both, about a sixth of each screen | Observation Planner. Other displays show the current target only. |
| Visible Targets (`TargetListStatus`) | Observation | High for planning | Observation Planner's Tonight panel: altitude over the night and candidates ranked by gain per hour |
| Target Status (RA, Dec, altitude, rise, set) | Observation | Medium; overlaps Visible Targets | Merged into the Tonight panel |
| Image Planner, Image List, Dither Settings | Observation | Medium: the plan for a night | Observation Planner as "Night plan", dither folded into it |
| Execution Queue | Observation | Only with direct control | Observation Execution, control mode |
| Last Image (`images:last`) | Observation | Low: it shows a stacked file, not the last frame | Removed (agreed). A live frame returns in Observation Execution's control mode. |
| Mosaic Planner | Observation | Occasional | The Planetarium, as a framing overlay that feeds the night plan |
| Calibration Library | Observation | Belongs with the equipment, not the night plan | Observatory Manager, beside the camera profiles (decision 7). Missing calibration also appears as a warning in the target report. |
| Telescope Control, Manual Guide, Focuser, Filter Wheel | Observatory | Only with direct control | Observation Execution, control mode |
| Guiding Trends and Guiding Target | Observatory | High at night | Observation Execution |
| Tracking Settings, Safety Limits, Alignment Status | Observatory | Set once, changed rarely | Observatory Manager |
| Devices and Device Properties | Observatory | Setup and troubleshooting | Observatory Manager |

### 1.3 What other tools do

Amateur imaging software and professional observatories split the same jobs the same way:

- **Planning is list-centered.** N.I.N.A.'s Sky Atlas and Telescopius give a filtered target list
  with an altitude-over-the-night chart for each target. That chart is the visual that does the
  planning work.
- **Framing is its own step, on the real sky.** N.I.N.A.'s Framing Assistant, Sequence Generator
  Pro's framing wizard and Telescopius draw the sensor's field and mosaic panels over a survey
  image, and the result goes into the plan or sequence.
- **The planetarium is for orientation and discovery,** often a separate program (N.I.N.A. and
  Sequence Generator Pro link to Stellarium, Cartes du Ciel or TheSkyX). KStars and Ekos are the
  exception: the planetarium is the hub.
- **Professional observatories grade each observation against its requested conditions** (for
  example ESO's A to D grades for seeing, transparency and Moon). The target dataset report's
  warnings, such as "frames with the Moon above 60% lost 45%", are the same idea for one observer.

This survey is from general knowledge of these tools, not a check of their current versions.

## 2. Decisions

Agreed with the user on 2026-10-17. Decisions 16 to 22 were added after the change control board
review on the same day.

1. **Three displays, one job each.** Observation Planner (by day), Observation Execution (at
   night) and Observatory Manager (rarely). Observation Planner replaces Observation Manager.
   Observatory Manager keeps its name but holds only the equipment setup; its live panels move to
   Observation Execution.
2. **Ekos captures now; direct control comes later.** For now the app gives observation
   recommendations and monitors the Ekos session. The capture-control panels stay in the code,
   shown as unavailable, and nothing in this plan may block replacing Ekos with direct control
   later (section 4).
3. **The night screen shows a compact quality strip** for the current target: its top warning,
   its top recommendation and the night's loss so far. It is the same component as the full Data
   Quality panel, in a smaller form.
4. **Two monitors.** The user has two monitors, so the night screen does not need a device status
   strip. Observatory Manager, the Planetarium or the Observation Planner opens in a second window
   on the other monitor (section 5).
5. **One current target everywhere.** Choosing a target in one display or window makes it the
   current target in all of them, through a backend event (section 5).
6. **Display names.** Observation Planner, Observation Execution and Observatory Manager.
7. **The calibration library belongs to Observatory Manager,** with the camera profiles: what
   calibration frames exist for each camera, gain, offset, binning and temperature.
8. **Last Image is removed.** A live frame returns only in control mode.
9. **The app never commands a device while Ekos captures.** In monitor mode no control panel sends
   a command, with no exceptions (section 4).
10. **The Planetarium stays its own display, linked to the Observation Planner.** They share the
    current target and one planning time. A click on the sky sets the current target, and "Show on
    sky" in the planner centers the Planetarium on it (section 3.4).
11. **The Observation Planner is list-centered.** Its visibility panel is an altitude-over-the-night
    chart for the current target, with the Moon, twilight and the altitude limits marked, beside
    tonight's candidates ranked by gain per hour from the target dataset report.
12. **Framing moves to the Planetarium.** The Mosaic Planner becomes a framing overlay of the
    sensor's field and the mosaic panels on the sky, and its result feeds the night plan.
13. **The Planetarium's equipment settings move to Observatory Manager.** `EquipmentConfigPanel` is
    equipment setup, not sky viewing.
14. **Night review and refresh.** Observation Execution has a night selector for reviewing earlier
    nights. Its quality strip and frame list refresh when frames are ingested, not on a timer
    (`QUALITY_USE_CASES.md`, section 6).
15. **The night plan keeps its checklist.** Non-capture recommendations join the night plan as a
    checklist, and ticked items are saved with the plan. A ticked item comes back as a
    recommendation if the next measurements show it did not work.

16. **The UI is a thin wrapper with named owners.** Every panel and behavior has a named
    `wayfindinglib` or `astrometricslib` call, RPC method or event that supplies it (section 6).
    The UI does no ranking, grouping, classification, text composition, unit conversion or joins.
    The same calls serve the MCP tools.
17. **Monitor mode is `wayfindinglib`'s delegation policy.** There is no separate UI setting for
    who captures. Monitor mode means every capability is delegated to Ekos. The library refuses
    commands, and the UI reads the policy and greys its control panels (section 4).
18. **The night plan is stored in `wayfindinglib`.** It is a stored model, not the memory of one
    window. It holds the capture settings (including gain, offset and binning), the checklist and
    the framing. It is written from the first edit, has a revision check so two windows cannot
    overwrite each other, and raises a plan-changed event (section 5).
19. **The current target and planning time reuse the handoff state.** The backend handoff state
    already carries the selected target to every window. The UI publishes to it on selection, and
    the state gains a planning time. Both windows show "Now" or "fixed at 22:00" (section 5).
20. **Syncs stay manual.** Observation Execution has a "Sync now" action that fetches the Ekos logs
    and the frames, and a staleness line ("last sync 23:40, 25 min ago"). Target changes are read
    from the Ekos analyze log after a sync. Automatic syncing is a later option, not designed now
    (section 3.2).
21. **No night theme.** Observation Execution uses the same theme as the rest of the app. It
    meets the accessibility basics in section 3.5.
22. **Popped-out windows are not saved or restored.** The user sets them up once per session.

## 3. The four displays

The plan's three displays work beside the existing Planetarium, which keeps its own display.

### 3.1 Observation Planner

Replaces Observation Manager. Used by day, and at night when plans change.

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ Current target · Planning time: [Now ▾] (or "fixed at 22:00")            │
├───────────────┬──────────────────────────────┬───────────────────────────┤
│ Targets       │ Data Quality (current target)│ Tonight                   │
│  filters      │  dataset summary per capture │  altitude over the night: │
│  search       │  setting                     │  Moon, twilight, limits   │
│  list         │  loss account                │  candidates ranked by     │
│               │  top 3 statements, each with │  gain per hour, new       │
│               │  type and confidence         │  targets marked "new"     │
│               │  [Show all N]                │  [Show on sky]            │
│               │  [Send to plan] [Evidence]   ├───────────────────────────┤
│               │  forecast check              │ Night plan (stored)       │
│               │                              │  frames, filter, exposure,│
│               │                              │  gain, offset, binning,   │
│               │                              │  dither, checklist,       │
│               │                              │  framing [Show on sky]    │
│               │                              │ Ekos summary, per row:    │
│               │                              │  [Copy] [done in Ekos]    │
└───────────────┴──────────────────────────────┴───────────────────────────┘
```

- **Data Quality** is the full target dataset report from `QUALITY_METRICS_PLAN.md` section 6.
  The report returns its statements already ordered by priority. The panel shows the top 3 with
  "Show all N", using the statement types in section 3.5. Each statement opens to its evidence
  and its confidence.
- **Evidence** opens the Image Viewer filtered to the cited frames. If an Image Viewer window is
  open, it reuses that window.
- **Display-agnostic panel.** The Data Quality panel takes the current target and the night as
  inputs and fetches its own data, so it can sit on any display. Phase 0 places it on today's
  Observation Manager; later phases only move it. The quality strip on Observation Execution and
  the quality chip in the Astronomy Manager use the same statement component.
- **Tonight** replaces Visible Targets and Target Status with one panel: the altitude curve of the
  current target over the night, and tonight's candidates as ranked by the library at the planning
  time (`QUALITY_METRICS_PLAN.md` section 6.4). New targets are marked "new", not ranked. The UI
  draws the order it is given.
- **Send to plan.** A capture-settings recommendation ("take 20 more at 90 s, filter L") puts its
  typed action into the stored night plan in one click. Other recommendations (calibration,
  scheduling, adjustment) become checklist items in the night plan. Values the camera cannot take
  are rejected by the library, and the reason shows beside the button. A conflicting or repeated
  addition is merged or rejected by the library, not by the UI.
- **Night plan** is the Image Planner and Image List, with dither settings folded in, the
  checklist, and the framing chosen in the Planetarium. It is the stored `wayfindinglib` night plan
  (decision 18). When another window changes it, the plan-changed event refreshes this panel.
  A ticked checklist item that comes back says why ("ticked Oct 12; stars still 28% wider
  Oct 15"); the library decides that, not the UI.
- **Ekos summary.** While Ekos captures, the plan is shown as a sequence summary to enter in Ekos
  (section 4). Its rows follow the fields and order of Ekos's Capture module (filter, exposure,
  count, binning, gain, offset) and say which Ekos screen each value goes on. Each row has a copy
  button and a "done in Ekos" tick.
- **Starting point for a new target.** For a target with no data, the report gives a starting
  exposure and total time, marked as a starting point. The UI shows it as given.

### 3.2 Observation Execution

New. Takes the live panels from Observatory Manager. Used at night.

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ Night: [tonight ▾] · current target · Planning time: Now                 │
│ Last sync 23:40, 25 min ago   [Sync now]                                 │
├──────────────────────────────────────────────────────────────────────────┤
│ Quality strip: top warning · top recommendation · loss so far            │
├──────────────────────────────────────────────────────────────────────────┤
│ Live session: one verdict with its reason                                │
│  "Guiding unsteady since 23:10: RMS 1.4 arcsec"                          │
│ Alerts: "2 frames ruined by a slew" (3)   [Details]                      │
│ Candidates re-ranked: top 3 [Switch to]   [Show all]                     │
├──────────────────────────────────────────────────────────────────────────┤
│ Frames tonight: per frame star width, sky, ruined or set-aside reason    │
├──────────────────────────────────────────────────────────────────────────┤
│ [Guiding] [Control]                                                      │
│  Guiding: guiding trends, guiding target                                 │
│  Control (direct control only): telescope, manual guide, focuser,        │
│  filter wheel, execution queue, live frame                               │
└──────────────────────────────────────────────────────────────────────────┘
```

- **Order on the main monitor.** The quality strip comes first, then the live session as a single
  verdict with its reason, then the frame list. Guiding trends and the guiding target are a second
  panel or tab. Alerts are one sentence plus a count; details open on click.
- **Night selector.** It defaults to tonight and lists the nights that have data, from a library
  call. Choosing an earlier night shows the same panels from the stored data, which makes this
  display the place to review last night (`QUALITY_USE_CASES.md`, UC1).
- **Night review rows.** When reviewing a night, each target observed that night is a row with a
  needs-attention badge (top warning count and severity, a library field) and "Open in Planner".
  "Open in Planner" sets the current target and focuses the Data Quality panel of the Observation
  Planner.
- **Sync now** (decision 20). It fetches the Ekos logs and the frames from the StellarMate. The
  staleness line shows when the last sync ran and how long ago. Nothing syncs on its own.
- **Target changes.** After a sync, `wayfindinglib` reads the target changes from the Ekos analyze
  log: a `MountState` change from slewing to tracking, with `MountCoords` at a new position,
  matched to the nearest library target. The parser
  (`wayfindinglib/drivers/ekos/analyze_log_parser.py`) already reads `MountState`, `MountCoords`
  and `CaptureComplete`.
- **When it refreshes.** The live session status is read again after a sync. The quality strip and
  the frame list update when the sync's frames have been measured: the frame-measurement job
  raises a `quality:measured` event with the target and the night. Nothing refreshes on a timer.
- **Frames at night.** Set-aside reasons exist only after stacking moves frames aside. Until then
  the frame list shows the live status's ruined frames.
- **Re-ranked candidates** (`QUALITY_USE_CASES.md`, UC12). When plans change mid-night, the
  library's re-ranked candidates show as the top 3 with "Show all". "Switch to" sets the current
  target and shows its sequence summary.
- **Monitor mode** (now): the live session status from the Ekos files, guiding trends, and the
  quality of each frame as it arrives in the library. The control tab is greyed (section 4).
- **Control mode** (later): the control panels and the execution queue become active, and the live
  frame returns.
- **Theme.** The same theme as the rest of the app (decision 21), with the accessibility basics in
  section 3.5.

### 3.3 Observatory Manager

Keeps its name and becomes the equipment display. Used when equipment changes, during
commissioning, and for troubleshooting, usually on the second monitor.

- Devices and device properties (the INDI inspector, `IndiStatusPanel`).
- Safety limits, tracking and alignment settings, guiding settings, alignment status.
- Equipment settings (`EquipmentConfigPanel`), moved from the Planetarium.
- Camera profiles: the measured gain and read noise for each camera setting, and the call that
  measures a new setting from flat and bias pairs (`QUALITY_METRICS_PLAN.md` decision 7).
- The calibration library: darks, flats and bias frames held for each camera, gain, offset,
  binning and temperature, moved from today's Observation Manager. Gaps show beside the camera
  settings they affect. A calibration warning in the target report links to the exact row here.

### 3.4 Planetarium

Stays its own display. It already draws the sensor's field of view from the active equipment,
overlays the library's targets, follows the shared selected target and shows alignment points.

- **Linked to the planner.** It shares the current target and the planning time with the
  Observation Planner, and shows the same "Now" or "fixed at 22:00" line. Clicking an object sets
  the current target. "Show on sky" in the planner centers the Planetarium on the current target at
  the planning time.
- **Framing.** The Mosaic Planner becomes a framing overlay: the sensor's field, its rotation and
  the mosaic panels drawn on the sky. The panels come from `wayfindinglib`'s `calculate_panels`;
  the UI does no framing math. The first version is read-only: it shows the field and the panels,
  with rotation as a number. Mosaics have no rotation today, so the library adds it.
- **Send to plan.** It puts the framing into the stored night plan. Because the plan is stored and
  raises a plan-changed event, this works when the Planetarium and the Observation Planner are in
  different windows.
- **Alignment points stay here.** They show where the mount's model has measured the sky, which
  the map shows best.
- **A survey-image background is a possible later feature.** Other framing tools draw the field
  over a survey image of the real sky, which matters when framing a faint nebula. The Planetarium
  renders catalog stars today.

### 3.5 Shared rules for every display

**Statement types.** Every report statement shows its type by an icon plus a text label, never by
color alone:

| Type | How it looks | Action |
| :--- | :--- | :--- |
| Warning | Warning icon and the word "Warning" | Evidence |
| Recommendation | Recommendation icon and the word "Recommendation" | Send to plan, evidence |
| Limit | Plain text with the word "Limit" | None |
| Not enough data | Greyed, with the word "Not enough data" and the reason | None |

Confidence is a text chip with the validation register's labels: Measured, Partly measured, and
Tentative (for a design estimate). The type, the order and the confidence come from the library.

**The same verbs everywhere.** "Show on sky" centers the Planetarium on a target. "Send to plan"
puts a recommendation or a framing into the night plan. "Open in Planner" and "Switch to" set the
current target. "Use this framing" and "Add to plan" are not used.

**Accessibility basics** (decision 21):

- Severity shows by icon and words, not color alone.
- Every action works from the keyboard, with a visible focus outline.
- Every chart has a text alternative that states what it shows.
- Summary text, such as the Ekos summary and the statements, can be selected and copied.

**Quality chip in the Astronomy Manager.** A science result (a star's photometry or spectrum)
shows a quality chip that says whether to trust it (`QUALITY_USE_CASES.md`, UC5). It uses the
same statement component.

## 4. Ekos now, direct control later

Who controls the equipment is `wayfindinglib`'s delegation policy. There is no separate UI
setting. `control.safety.enter_monitoring_mode` delegates every capability to Ekos (monitor mode,
the default); `control.safety.enter_controller_mode` gives them back to this app (control mode).
The backend already declares both as RPC methods, `observatory:enter_monitoring_mode` and
`observatory:enter_controller_mode`, in `backend/public_interface.py`. Control mode stays off until
direct control is built.

- **The library refuses commands.** Each hardware command checks
  `DelegationPolicy.is_authoritative` (`wayfindinglib/models/policy/delegation.py`) in
  `wayfindinglib/tasks/control_tasks/hardware_operations.py`, and a delegated capability is
  refused. The no-command guarantee (decision 9) is tested in `wayfindinglib`: commands from the
  backend and from the MCP servers are refused there. No UI-only test stands in for it.
- **The UI reads the policy and greys its panels.** The control panels are visible but
  unavailable, with one line saying why ("Ekos is controlling the equipment"). The policy is read
  with `control.safety.status(include=["delegation_policy"])`. No RPC method reads it today, so
  phase 2 adds one. No panel is deleted.
- **Recommendations are typed actions.** The target report's `proposed_action` holds exposure,
  frame count, filter, gain, offset, binning and dither as values. In monitor mode they are shown
  as the Ekos sequence summary, built by `wayfindinglib` from the stored night plan. In control
  mode the same values fill the execution queue.
- **Frame quality does not depend on who captured.** Frames are measured when they enter the
  library, whether they came from the StellarMate sync or, later, from the app's own capture.
- **Session telemetry stays behind the Ekos driver** (`wayfindinglib/drivers/ekos/`). The live
  session status reads it through that boundary, so direct-control telemetry can take its place
  without changing the night screen.
- **Allowed values come from the equipment catalog and camera profiles,** not from Ekos settings,
  so they are the same in both modes.

## 5. Two monitors, the current target and the planning time

- **Suggested layout.** By day, the Observation Planner on monitor 1 and the Planetarium on monitor
  2. At night, Observation Execution on monitor 1, and the Planetarium, the Observation Planner,
  Observatory Manager or the Image Viewer on monitor 2 as needed.
- **Windows are set up once per session.** Popped-out windows are not saved or reopened at start
  (decision 22). Only the main window's position is kept, as today.
- **The current target follows across windows.** This reuses the backend handoff state
  (decision 19). `setSelectedTarget` calls `handoff:update_state` with the new target;
  `HandoffService` broadcasts the `handoff` event on `/ws/events`; `ui/App.tsx` turns it into
  `astrometrics:targetSelected`, and every window's `TargetContext` follows it. A window does not
  publish a target it has just received. The event reaches every window and also a future phone
  companion. A window that is hidden keeps its deferred update (`DeferWhileHidden`).
- **One planning time.** The handoff state gains a `planning_time` field (none means "Now"), set
  and broadcast the same way as the current target. The Observation Planner and the Planetarium
  share it; choosing a time in either display moves both. The Planetarium's own date and time
  picker sets the shared time. Both windows show "Now" or "fixed at 22:00", so the user can see
  when the plan is not for the present moment.
- **One night plan.** The night plan is stored in `wayfindinglib` (decision 18). Each write
  carries the revision it was based on, and a write based on an old revision is refused, so two
  windows cannot overwrite each other. Each change raises a plan-changed event, and every window
  showing the plan reloads it.

## 6. What the UI calls

Each panel or behavior and what supplies it. Calls marked "new" are built in
`QUALITY_METRICS_PLAN.md` or in this plan's phases; the rest exist today. The UI only displays
what these return. It does no ranking, grouping, classification, text composition, unit conversion
or joins. Results come as typed models, and the same calls serve the MCP tools.

| Panel or behavior | Supplied by |
| :--- | :--- |
| Data Quality panel, quality strip | Planning `get_advisory(kind="quality")`, returning the `TargetDatasetReport` (new; replaces `TargetQualityAdvisory`) |
| Statement order, type and confidence | The report's statements, ordered by priority, each with a kind (warning, recommendation, limit, not enough data) and a confidence label (new) |
| Needs-attention badge | A per-target field in the night review: top warning count and severity (new) |
| Evidence | `quality.query` with a row-id filter (new), opened in the Image Viewer |
| Starting point for a new target | A field on `TargetDatasetReport`, computed in `wayfindinglib` (new) |
| Tonight panel and re-ranked candidates | Planning `get_advisory(kind="tonight", at=...)` (new), at the shared planning time |
| Altitude over the night | `planetarium:get_visibility` (Planning `get_visibility`) |
| Night plan, checklist, framing | The stored night plan: Planning `get_plan` and a plan write with a revision check (new); a plan-changed event (new) |
| Checklist items that come back | Planning `get_advisory`, reading the stored plan (new) |
| Ekos summary | A `SequenceSummary` model of rows built from the stored night plan (new) |
| Framing overlay | Planning `calculate_panels`, with rotation added (new) |
| Night selector | A library call listing the nights that have data (new) |
| Night review | `control.history.query(kind="night_quality", night_id=...)`, typed, with an RPC method (new) |
| Live session verdict and alerts | `control.history.get_live_session_status`, typed, with an RPC method (new); called with `refresh=False` after a sync |
| Sync now | `control.remote.sync_logs` (RPC `telescope:sync_logs`) and `control.remote.sync_frames` |
| Target changes in the night | Read from the Ekos analyze log by `wayfindinglib` after a sync (new) |
| Refresh after ingest | Backend event `quality:measured` with the target and night (new), declared in `backend/public_interface.py` |
| Calibration warnings | Folded into the target report; the calibration library row it links to comes from `CalibrationCatalog.query` |
| Quality chip in the Astronomy Manager | Planning `get_advisory(kind="result")` for one star's result, with an RPC method (new) |
| Control panels greyed | `control.safety.status(include=["delegation_policy"])`, with an RPC method (new) |
| Monitor or control mode | `observatory:enter_monitoring_mode`, `observatory:enter_controller_mode` |
| Current target, planning time | Handoff state: `handoff:update_state` and the `handoff` event (`planning_time` new) |

## 7. Order of work

Each phase ends with `npm run type-check`, `npm test` and ESLint passing, the affected Python tests
passing if the backend changes, the user guides updated, and new screenshots from
`build/documentation/generate_screenshots.spec.ts`.

0. **Data Quality panel on today's Observation Manager.** Delivered with phase 4 of
   `QUALITY_METRICS_PLAN.md`. It takes the place of Last Image, so the recommendations reach the
   user before the restructure. The panel is display-agnostic, so later phases only move it. The
   Calibration Library stays where it is until phase 3 moves it.
1. **Shared current target and planning time.** `setSelectedTarget` publishes through
   `handoff:update_state`, and the handoff state gains `planning_time`. Both windows show "Now" or
   "fixed at 22:00". Done when choosing a target or a time in one window changes it in the other.
2. **Observation Execution.** The new display, its flag and its mode entry. It takes the live
   panels from Observatory Manager and adds: monitor mode read from the delegation policy, with
   the control panels greyed; an RPC method that reads the policy; typed live session status and
   night review reads with RPC methods; "Sync now" and the staleness line; target changes read
   after a sync; the frame list and the quality strip refreshed on `quality:measured`; the night
   selector; night review rows with "Open in Planner" and the needs-attention badge; and the
   accessibility basics. Execution Queue moves here. Done when a real Ekos night shows live status
   and arriving frames after "Sync now", last night can be reviewed with the night selector, every
   control panel reads as unavailable in monitor mode, and the `wayfindinglib` test shows that
   every command is refused in monitor mode.
3. **Observatory Manager.** What is left becomes the equipment display: devices, settings, limits,
   alignment, camera profiles, the calibration library moved from Observation Manager and the
   equipment settings moved from the Planetarium. Done when every setup panel works there and no
   live panel remains.
4. **Observation Planner.** Observation Manager becomes Observation Planner: the Tonight panel with
   the altitude chart and the ranked candidates, the stored night plan with dither folded in,
   "Send to plan", the checklist (decision 15) and the Ekos summary with copy and "done in Ekos"
   per row. The night plan is the stored `wayfindinglib` model (decision 18), not `PlanningContext`
   memory, with a revision check and a plan-changed event. Done when a recommendation goes into
   the night plan in one click, the Ekos summary matches the plan, and an edit in one window shows
   in the other.
5. **Planetarium links and framing.** "Show on sky", "set as current target" from the map, and the
   read-only framing overlay from `calculate_panels` that replaces the Mosaic Planner, with "Send
   to plan". Done when a framing sent from the Planetarium in one window appears in the night plan
   in another, and the Mosaic Planner is gone from the planner.
6. **Documentation.** `User_Manual.md`, `Observatory_Control_and_Automation_Guide.md` and
   `User_Interface_Architecture.md` describe the displays, the two modes, the shared target and
   time, and the two-monitor layout.

Phase 1 can run beside phase 0. Phases 2, 3 and 4 follow in order, because each moves panels out
of the display the next one rebuilds. Phase 5 follows phase 4.

Order across plans: phase 2 (Observation Execution) needs phases 2 and 4 of
`QUALITY_METRICS_PLAN.md`. Phase 4 (Observation Planner) needs phase 4 of that plan.

## 8. Open questions

- **Display names (settled).** See decision 6.
- **Equipment settings in the Planetarium (settled).** See decision 13.
- **Old mode names.** Saved layouts and local storage hold the mode name "Observation Manager",
  which phase 4 maps to Observation Planner so a saved layout still opens. "Observatory Manager"
  keeps its name.
- **Survey-image framing.** Whether to add a survey-image background to the Planetarium for
  framing, and from which survey. Not part of this plan.
- **Automatic syncing.** A later option (decision 20). Not designed in this plan.
- **A phone companion.** A future Android companion app is planned. This plan does not build for it,
  except that the current-target and planning-time events are backend events any client can
  receive.

## Appendix A. Review board changes

The change control board reviewed this plan with the other quality plans on 2026-10-17. Each row
is a finding that changed this plan.

| Finding | Raised by | Where it landed |
| :--- | :--- | :--- |
| F2: every UI behavior needs a named library owner; the UI does no ranking, grouping, classification, text composition, unit conversion or joins | Technical architect, software engineer | Decision 16, section 6 |
| F3: the "capture source" setting duplicated `wayfindinglib`'s delegation policy; the no-command test belongs in the library | Technical architect, software engineer | Decision 17, section 4, phase 2 |
| F4: the night plan lived only in each window's memory and had no gain, offset, binning, checklist or framing | Software engineer, technical architect | Decision 18, sections 3.1, 3.4 and 5, phase 4 |
| F5: a shared current target already exists through the handoff state; only publishing on selection is missing; the planning time needs a visible indicator | Software engineer, user-experience reviewer | Decision 19, sections 1.1 and 5, phase 1 |
| D1: syncs stay manual, with "Sync now", a staleness line and target changes read from the Ekos analyze log | User decision | Decision 20, section 3.2, phase 2 |
| D3: no night theme; accessibility basics | User decision | Decision 21, section 3.5 |
| D5: popped-out windows are not saved or restored | User decision | Decision 22, sections 1.1 and 5, phase 1 |
| F8: refresh on ingest needs a declared event; set-aside reasons exist only after stacking; order across plans | Software engineer | Sections 3.2, 6 and 7 |
| F9: statement types, Execution order, "Open in Planner", "Switch to", top 3 with "Show all", Ekos summary rows, evidence in the Image Viewer, consistent verbs, read-only framing, display-agnostic panel, quality chip | User-experience reviewer | Sections 3.1, 3.2, 3.4, 3.5 and 7 |

No findings were rejected.
