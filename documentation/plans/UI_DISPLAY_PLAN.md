# UI Display Plan

This plan reorganizes the desktop app's two busiest displays, Observation Manager and Observatory
Manager, into three displays, each built around one job: planning, running the night, and setting
up the equipment. It also makes the app work well across two monitors.

The plan covers:

- what the two displays hold today, and what each panel is worth,
- the decisions agreed on 2026-10-17,
- the three new displays and what goes on each,
- how the app behaves while Ekos does the capture, without blocking direct control later,
- two monitors and the shared current target,
- the order of work.

It is the user-interface companion of `QUALITY_METRICS_PLAN.md`. That plan builds the target
dataset report (its section 6): the warnings and recommendations for a target. This plan decides
where the user sees them.

The findings come from a read-only survey of `ui/` and `electron/` on 2026-10-17. The code is the
source of truth. Line numbers drift, so this plan names files and functions instead.

## Status

Status on 2026-10-17: draft, agreed in discussion, not yet reviewed by the change control board,
nothing built.

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
| Filter Targets and Target List | Both | High, but repeated on both, about a sixth of each screen | Target Planner. Other displays show the current target only. |
| Visible Targets (`TargetListStatus`) | Observation | High for planning | Target Planner, merged with Target Status |
| Target Status (RA, Dec, altitude, rise, set) | Observation | Medium; overlaps Visible Targets | Merged into one visibility panel on the Target Planner |
| Image Planner, Image List, Dither Settings | Observation | Medium: the plan for a night | Target Planner as "Night plan", dither folded into it |
| Execution Queue | Observation | Only with direct control | Night Control, control mode |
| Last Image (`images:last`) | Observation | Low: it shows a stacked file, not the last frame | Removed. A live frame returns in Night Control's control mode. |
| Mosaic Planner | Observation | Occasional | Target Planner, collapsed by default |
| Calibration Library | Observation | Low here: frame counts by camera | Image Processing, where calibration already lives. Missing calibration appears as a warning in the target report. |
| Telescope Control, Manual Guide, Focuser, Filter Wheel | Observatory | Only with direct control | Night Control, control mode |
| Guiding Trends and Guiding Target | Observatory | High at night | Night Control |
| Tracking Settings, Safety Limits, Alignment Status | Observatory | Set once, changed rarely | Observatory Setup |
| Devices and Device Properties | Observatory | Setup and troubleshooting | Observatory Setup |

## 2. Decisions

Agreed with the user on 2026-10-17:

1. **Three displays, one job each.** Target Planner (by day), Night Control (at night) and
   Observatory Setup (rarely). They replace Observation Manager and Observatory Manager.
2. **Ekos captures now; direct control comes later.** For now the app gives observation
   recommendations and monitors the Ekos session. The capture-control panels stay in the code,
   shown as unavailable, and nothing in this plan may block replacing Ekos with direct control
   later (section 4).
3. **The night screen shows a compact quality strip** for the current target: its top warning,
   its top recommendation and the night's loss so far. It is the same component as the full Data
   Quality panel, in a smaller form.
4. **Two monitors.** The user has two monitors, so the night screen does not need a device status
   strip. Observatory Setup, or the Target Planner, opens in a second window on the other monitor
   (section 5).
5. **One current target everywhere.** Choosing a target in one display or window makes it the
   current target in all of them.

## 3. The three displays

### 3.1 Target Planner

Replaces Observation Manager. Used by day, and at night when plans change.

```text
┌───────────────┬─────────────────────────────────────┬─────────────────────┐
│ Targets       │ Data Quality (current target)        │ Visibility          │
│  filters      │  dataset summary per capture setting │  altitude, rise,    │
│  search       │  loss account                        │  set, Moon, best    │
│  list         │  warnings                            │  window             │
│               │  recommendations  [Add to plan]      ├─────────────────────┤
│               │  forecast check                      │ Night plan          │
│               │                                      │  frames, filter,    │
│               │                                      │  exposure, dither,  │
│               │                                      │  calibration frames │
│               │                                      ├─────────────────────┤
│               │                                      │ Mosaic (collapsed)  │
└───────────────┴─────────────────────────────────────┴─────────────────────┘
```

- **Data Quality** is the full target dataset report from `QUALITY_METRICS_PLAN.md` section 6.
  Each warning and recommendation opens to its evidence and its confidence.
- **Add to plan.** A capture-settings recommendation ("take 60 more frames at 30 s, filter L,
  dither every 3") adds its typed action to the night plan in one click. Other recommendations
  (calibration, scheduling, adjustment) become a checklist in the night plan.
- **Night plan** is the Image Planner and Image List, with dither settings folded in. While Ekos
  captures, the plan is shown as a sequence summary to enter in Ekos (section 4).

### 3.2 Night Control

Replaces the live part of Observatory Manager. Used at night.

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ Current target · quality strip: top warning · top recommendation · loss │
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

- **Monitor mode** (now): the live session status from the Ekos files, guiding trends, and the
  quality of each frame as it arrives in the library.
- **Control mode** (later): the control panels and the execution queue become active, and the live
  frame returns.

### 3.3 Observatory Setup

New. Used when equipment changes, during commissioning, and for troubleshooting, usually on the
second monitor.

- Devices and device properties (the INDI inspector, `IndiStatusPanel`).
- Safety limits, tracking and alignment settings, guiding settings, alignment status.
- Camera profiles: the measured gain and read noise for each camera setting, and the call that
  measures a new setting from flat and bias pairs (`QUALITY_METRICS_PLAN.md` decision 7).

## 4. Ekos now, direct control later

The app has one setting for who captures: Ekos (monitor mode, the default) or this app (control
mode). Control mode stays off until direct control is built.

- **In monitor mode** the control panels are visible but unavailable, with one line saying why
  ("Ekos is controlling the equipment"). Two programs commanding the same devices would conflict,
  so the panels do not send commands. No panel is deleted.
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

## 5. Two monitors and the current target

- **Suggested layout.** Monitor 1 holds Night Control at night and the Target Planner by day.
  Monitor 2 holds Observatory Setup, the Image Viewer, or the Target Planner at night when plans
  change.
- **Windows come back.** Popped-out windows, with their display and monitor, are saved and reopened
  at start, the way the main window already is.
- **The current target follows across windows.** Choosing a target publishes it as an app-wide
  event, and every window sets it as its current target. The plan recommends a backend event on
  the existing `/ws/events` connection over an Electron-only broadcast: it reaches every window
  and also a future phone companion. It needs a declaration in `backend/public_interface.py` and
  regenerated types. A window that is hidden keeps its deferred update (`DeferWhileHidden`).

## 6. Order of work

Each phase ends with `npm run type-check`, `npm test` and ESLint passing, the affected Python tests
passing if the backend changes, the user guides updated, and new screenshots from
`build/documentation/generate_screenshots.spec.ts`.

0. **Data Quality panel on today's Observation Manager.** Delivered with phase 4 of
   `QUALITY_METRICS_PLAN.md`. It takes the place of Last Image and Calibration Library, so the
   recommendations reach the user before the restructure.
1. **Shared current target and restored windows.** The app-wide target event, and saving and
   reopening popped-out windows. Done when choosing a target in one window changes it in the
   other, and a restart brings both windows back on their monitors.
2. **Observatory Setup.** The new display, its flag and its mode entry, holding the setup panels
   moved from Observatory Manager. Done when every setup panel works from the new display.
3. **Night Control.** Observatory Manager becomes Night Control: the capture-source setting, monitor
   mode with the live session status (adding a backend RPC method for it if there is none), the
   frame list and the quality strip, and the control panels gated by mode. Execution Queue moves
   here. Done when a real Ekos night shows live status and arriving frames, and every control
   panel reads as unavailable in monitor mode.
4. **Target Planner.** Observation Manager becomes Target Planner: the merged visibility panel, the
   night plan with dither folded in and "Add to plan", the collapsed mosaic planner. Calibration
   Library moves to Image Processing. Done when a recommendation goes into the night plan in one
   click and the sequence summary matches the plan.
5. **Documentation.** `User_Manual.md`, `Observatory_Control_and_Automation_Guide.md` and
   `User_Interface_Architecture.md` describe the three displays, the two modes and the two-monitor
   layout.

Phase 1 can run beside phase 0. Phases 2, 3 and 4 follow in order, because each moves panels out
of the display the next one rebuilds.

## 7. Open questions

- **Display names.** "Target Planner", "Night Control" and "Observatory Setup" are working names.
- **Equipment settings in the Planetarium.** `EquipmentConfigPanel` lives in the Planetarium today.
  It may belong in Observatory Setup.
- **Old mode names.** Saved layouts and local storage hold the mode names "Observation Manager" and
  "Observatory Manager". Phases 3 and 4 map the old names to the new displays so a saved layout
  still opens.
- **A phone companion.** A future Android companion app is planned. This plan does not build for it,
  except that the current-target event is a backend event any client can receive.
