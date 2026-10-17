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
- the order of work.

It is the user-interface companion of `QUALITY_METRICS_PLAN.md`. That plan builds the target
dataset report (its section 6): the warnings and recommendations for a target. This plan decides
where the user sees them.

The findings come from a read-only survey of `ui/` and `electron/` on 2026-10-17. The code is the
source of truth. Line numbers drift, so this plan names files and functions instead.

## Status

Status on 2026-10-17: draft, with the user's decisions folded in (decisions 6 to 15). Not yet
reviewed by the change control board, nothing built.

## 1. Findings

### 1.1 How displays work today

- **Registry.** Each display is listed twice and the two lists must agree:
  `ui/common/constants/displayFlags.ts` (`DISPLAY_DEFINITIONS`: the order in the menu and the
  `[Frontend] enable_*` flag) and `ui/App.tsx` (`MODE_PANELS`: the component for each mode). The
  display menu is the mode selector in `ui/statusHeader/StatusHeader.tsx`.
- **Windows.** Electron can already open a second full app window for any display.
  `createDisplayWindow` in `electron/main.js` places it on a monitor other than the primary one,
  and the mode selector has a pop-out button for each display (`openDisplayWindow`). Two gaps:
  - only the main window's position is saved and restored (`electron/window_state.js`); popped-out
    windows are not reopened at start;
  - each window is a separate React tree with its own `TargetContext`. The selected target is
    saved to local storage but only read when a window starts, so a second window does not follow
    a target chosen in the first.
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

Agreed with the user on 2026-10-17:

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

## 3. The four displays

The plan's three displays work beside the existing Planetarium, which keeps its own display.

### 3.1 Observation Planner

Replaces Observation Manager. Used by day, and at night when plans change.

```text
┌───────────────┬─────────────────────────────────────┬──────────────────────┐
│ Targets       │ Data Quality (current target)        │ Tonight              │
│  filters      │  dataset summary per capture setting │  altitude over the   │
│  search       │  loss account                        │  night: Moon,        │
│  list         │  warnings                            │  twilight, limits    │
│               │  recommendations  [Add to plan]      │  candidates ranked   │
│               │  forecast check                      │  by gain per hour    │
│               │                                      ├──────────────────────┤
│               │                                      │ Night plan           │
│               │                                      │  frames, filter,     │
│               │                                      │  exposure, dither,   │
│               │                                      │  calibration frames, │
│               │                                      │  framing [Show on    │
│               │                                      │  sky]                │
└───────────────┴─────────────────────────────────────┴──────────────────────┘
```

- **Data Quality** is the full target dataset report from `QUALITY_METRICS_PLAN.md` section 6.
  Each warning and recommendation opens to its evidence and its confidence.
- **Tonight** replaces Visible Targets and Target Status with one panel: the altitude curve of the
  current target over the night, and tonight's candidates ranked by gain per hour
  (`QUALITY_METRICS_PLAN.md` section 6.4).
- **Add to plan.** A capture-settings recommendation ("take 60 more frames at 30 s, filter L,
  dither every 3") adds its typed action to the night plan in one click. Other recommendations
  (calibration, scheduling, adjustment) become a checklist in the night plan.
- **Night plan** is the Image Planner and Image List, with dither settings folded in, and the
  framing chosen in the Planetarium. While Ekos captures, the plan is shown as a sequence summary to
  enter in Ekos (section 4).

### 3.2 Observation Execution

New. Takes the live panels from Observatory Manager. Used at night.

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ Night: [tonight ▾] · current target · quality strip: warning · advice · loss│
├────────────────────────────┬─────────────────────────────────────────────┤
│ Live session (monitor)     │ Guiding trends                              │
│  guiding steady?           │                                             │
│  last dither worked?       ├─────────────────────────────────────────────┤
│  ruined frames             │ Guiding target                              │
├────────────────────────────┴─────────────────────────────────────────────┤
│ Frames tonight: per frame star width, sky, set-aside reason              │
├──────────────────────────────────────────────────────────────────────────┤
│ Control (direct control only): telescope, manual guide, focuser,         │
│ filter wheel, execution queue, live frame                                │
└──────────────────────────────────────────────────────────────────────────┘
```

- **Night selector.** It defaults to tonight. Choosing an earlier night shows the same panels from
  the stored data, which makes this display the place to review last night
  (`QUALITY_USE_CASES.md`, UC1).
- **Monitor mode** (now): the live session status from the Ekos files, guiding trends, and the
  quality of each frame as it arrives in the library.
- **When it refreshes.** The quality strip and the frame list update when new frames of the target
  are ingested into the library, not on a timer. The live session status updates when the Ekos
  logs are synced.
- **Control mode** (later): the control panels and the execution queue become active, and the live
  frame returns.

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
  settings they affect.

### 3.4 Planetarium

Stays its own display. It already draws the sensor's field of view from the active equipment,
overlays the library's targets, follows the shared selected target and shows alignment points.

- **Linked to the planner.** It shares the current target and the planning time with the
  Observation Planner. Clicking an object sets the current target. "Show on sky" in the planner
  centers the Planetarium on the current target at the planning time.
- **Framing.** The Mosaic Planner becomes a framing overlay: the sensor's field, its rotation and
  the mosaic panels drawn on the sky. "Use this framing" sends the result to the night plan.
- **Alignment points stay here.** They show where the mount's model has measured the sky, which
  the map shows best.
- **A survey-image background is a possible later feature.** Other framing tools draw the field
  over a survey image of the real sky, which matters when framing a faint nebula. The Planetarium
  renders catalog stars today.

## 4. Ekos now, direct control later

The app has one setting for who captures: Ekos (monitor mode, the default) or this app (control
mode). Control mode stays off until direct control is built.

- **In monitor mode** the control panels are visible but unavailable, with one line saying why
  ("Ekos is controlling the equipment"). Two programs commanding the same devices would conflict,
  so no panel sends a command, with no exceptions (decision 9). No panel is deleted. A test checks
  that every command call in a control panel is blocked in monitor mode.
- **Recommendations are typed actions.** The target report's `proposed_action` holds exposure,
  frame count, filter and dither as values. In monitor mode they are shown as a sequence summary
  to enter in Ekos. In control mode the same values fill the night plan and the execution queue.
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
- **Windows come back.** Popped-out windows, with their display and monitor, are saved and reopened
  at start, the way the main window already is.
- **The current target follows across windows.** Choosing a target publishes it as an app-wide
  event, and every window sets it as its current target. The plan uses a backend event on the
  existing `/ws/events` connection rather than an Electron-only broadcast: it reaches every window
  and also a future phone companion. It needs a declaration in `backend/public_interface.py` and
  regenerated types. A window that is hidden keeps its deferred update (`DeferWhileHidden`).
- **One planning time.** The Observation Planner and the Planetarium share one planning date and
  time, carried the same way as the current target. "Now" is the default; choosing a time in either
  display moves both. The Planetarium's own date and time picker sets the shared time.

## 6. Order of work

Each phase ends with `npm run type-check`, `npm test` and ESLint passing, the affected Python tests
passing if the backend changes, the user guides updated, and new screenshots from
`build/documentation/generate_screenshots.spec.ts`.

0. **Data Quality panel on today's Observation Manager.** Delivered with phase 4 of
   `QUALITY_METRICS_PLAN.md`. It takes the place of Last Image, so the recommendations reach the
   user before the restructure. The Calibration Library stays where it is until phase 3 moves it.
1. **Shared current target, planning time and restored windows.** The app-wide target and
   planning-time events, and saving and reopening popped-out windows. Done when choosing a target
   or a time in one window changes it in the other, and a restart brings both windows back on
   their monitors.
2. **Observation Execution.** The new display, its flag and its mode entry. It takes the live
   panels from Observatory Manager and adds the capture-source setting, monitor mode with the live
   session status (adding a backend RPC method for it if there is none), the frame list and the
   quality strip refreshed on ingest, the night selector, and the control panels gated by mode. Execution Queue moves here. Done when a
   real Ekos night shows live status and arriving frames, last night can be reviewed with the night
   selector, and every control panel reads as unavailable and sends nothing in monitor mode.
3. **Observatory Manager.** What is left becomes the equipment display: devices, settings, limits,
   alignment, camera profiles, the calibration library moved from Observation Manager and the
   equipment settings moved from the Planetarium. Done when every setup panel works there and no
   live panel remains.
4. **Observation Planner.** Observation Manager becomes Observation Planner: the Tonight panel with
   the altitude chart and the ranked candidates, the night plan with dither folded in, "Add to
   plan", and the checklist saved with the plan (decision 15). Today plan items live in the
   display's memory (`PlanningContext`) until `ObservationPlanning.create_plan` writes the plan; the
   checklist is saved with that written plan. Done when a recommendation goes into the night plan in one click and the sequence summary
   matches the plan.
5. **Planetarium links and framing.** "Show on sky", "set as current target" from the map, and the
   framing overlay that replaces the Mosaic Planner and feeds the night plan. Done when a framing
   chosen on the sky appears in the night plan, and the Mosaic Planner is gone from the planner.
6. **Documentation.** `User_Manual.md`, `Observatory_Control_and_Automation_Guide.md` and
   `User_Interface_Architecture.md` describe the displays, the two modes, the shared target and
   time, and the two-monitor layout.

Phase 1 can run beside phase 0. Phases 2, 3 and 4 follow in order, because each moves panels out
of the display the next one rebuilds. Phase 5 follows phase 4.

## 7. Open questions

- **Display names (settled).** See decision 6.
- **Equipment settings in the Planetarium (settled).** See decision 13.
- **Old mode names.** Saved layouts and local storage hold the mode name "Observation Manager",
  which phase 4 maps to Observation Planner so a saved layout still opens. "Observatory Manager"
  keeps its name.
- **Survey-image framing.** Whether to add a survey-image background to the Planetarium for
  framing, and from which survey. Not part of this plan.
- **A phone companion.** A future Android companion app is planned. This plan does not build for it,
  except that the current-target and planning-time events are backend events any client can
  receive.
