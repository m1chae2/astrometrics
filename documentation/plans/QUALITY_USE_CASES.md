# Quality Use Cases

This document describes how the user is expected to use the quality metrics, the target dataset
report and the new displays, step by step. It is the agreement the other plans build toward. If a
use case here is wrong, the plans are wrong too.

It is the user-facing companion of three plans:

- `QUALITY_METRICS_PLAN.md`: the measurements, efficiency, and the target dataset report (its
  section 6);
- `QUALITY_SCIENCE_PLAN.md`: the new science metrics;
- `UI_DISPLAY_PLAN.md`: the displays where the user sees them.

## Status

Status on 2026-10-17: draft for the user's agreement. Section 6 lists the points that still need
it.

## 1. The user and the setting

- **One observer** with a small refractor, ZWO cameras (an ASI533MM Pro for imaging, an
  ASI120MC-S for guiding), a filter wheel, a slitless grating, and a Nikon D5300. The observatory
  computer is a StellarMate running Ekos.
- **Ekos captures today.** The app reviews the data, recommends what to do next, and watches the
  live session. It never commands a device while Ekos captures. Direct control from the app comes
  later.
- **Two monitors** at the desk.
- **Frames arrive in the library** by the sync from the StellarMate. Each new light frame is
  measured once when it arrives.

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
5. **Limits come from the user's equipment** and its earlier nights, and change when the equipment
   changes.

## 3. Reviewing observations

### UC1. Review last night

- **When:** the morning after a session.
- **Where:** Observation Execution, with last night chosen in its night selector (section 6,
  point 1).
- **The user sees:**
  - each target imaged, with frames captured, frames set aside and the reason, and the effective
    time: "M 81: 4.0 h captured, 2.6 h effective. Clouds 0.8 h, Moon 0.4 h, poor seeing 0.2 h";
  - guiding over the night, with the stretches where it failed;
  - focus against temperature;
  - warnings that repeat across nights ("guide star faint on 3 of the last 4 nights").
- **Decisions it supports:** whether last night's problems need fixing before tonight (focus
  routine, guider calibration, dew heater), and which targets got usable data.
- **Done right when:** the loss account adds up to the captured time, and every lost hour has a
  cause or is marked "cause unknown".

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
    - "No darks for gain 100, 60 s, −10 °C."
- **Decisions it supports:** which capture settings to keep using, which frames to leave out of the
  stack, and what calibration is missing.
- **Done right when:** every warning names its capture setting, its evidence and its confidence.

### UC3. Check whether more data helped

- **When:** after new frames of a target are stacked.
- **Where:** Observation Planner, Data Quality panel, forecast check.
- **The user sees:** the forecast from the previous recommendation and what the new stack achieved:
  - "Forecast: noise down 29%. Measured: down 27%. As expected."
  - "Forecast: noise down 29%. Measured: down 18%. The new frames had efficiency 0.55 (Moon), which
    explains 9 points."
  - "Forecast: noise down 29%. Measured: down 6%, cause unknown. More frames may not help; see the
    warning about a noise floor."
- **Decisions it supports:** whether to keep adding frames, or fix something first.
- **Done right when:** the forecast is rebuilt from the stored history, not from a saved
  recommendation, and the new recommendation reflects the new stack.

### UC4. Look into a warning

- **When:** a warning is surprising, or the user wants to see the frames.
- **Where:** the warning's evidence, then the Image Viewer, often on the second monitor.
- **The user sees:** the measurements behind the warning, frame by frame, the limit, where the limit
  came from (equipment, own earlier nights, or a validated data set), and a link that opens the
  frames in the Image Viewer.
- **Decisions it supports:** whether to trust the warning.
- **Done right when:** the user can trace any warning to the frames and numbers that caused it.

### UC5. Trust a science result

- **When:** reviewing a spectrum's classification, a variable star's period, or an asteroid
  candidate.
- **Where:** Astronomy Manager, with the quality shown beside the result.
- **The user sees:** the raw statistic and its uncertainty (for example the fit's relative RMS and
  the gap to the next class), the judgment of whether it is good enough for its purpose, and that
  judgment's confidence. A probability appears only where it was validated on held-out data.
- **Decisions it supports:** whether to use the result, note a caveat, or observe again.
- **Done right when:** no result shows a bare yes or no without the number behind it.

## 4. Planning observations

### UC6. Decide what to image tonight

- **When:** in the afternoon or evening, or at night when plans change.
- **Where:** Observation Planner, Tonight panel; the Planetarium on the second monitor.
- **The user sees:**
  - the current target's altitude over the night, with the Moon, twilight and altitude limits;
  - tonight's candidates ranked by how much an hour helps each one: "An hour on NGC 7331 cuts its
    noise by 18%; an hour on M 57 by 4%";
  - the Planetarium centered on a candidate with "Show on sky".
- **Decisions it supports:** which targets to image tonight, and in what order.
- **Done right when:** the ranking uses the user's own measured data and tonight's sky, and a
  target with no data yet is marked as new rather than ranked.

### UC7. Plan the next session for a target

- **When:** after UC2, UC3 or UC6.
- **Where:** Observation Planner, recommendations and night plan.
- **The user sees:** recommendations with numbers, for example:
  - "Take 60 more frames at 30 s, filter L, dither every 3 frames by about 10 px (about 30 min).
    Expected: noise down about 29%."
  - "Image above 35° altitude; frames lower down lost 35%."
  - "Refocus every 1.5 °C."
- **What the user does:** chooses "Add to plan" on a capture recommendation, which puts its exact
  values in the night plan. Other recommendations join the night plan as a checklist. The night
  plan is then shown as a sequence summary to enter in Ekos.
- **Done right when:** the sequence summary matches the night plan exactly, and every suggested
  value is one the user's equipment allows.

### UC8. Frame a target

- **When:** before imaging a new target, or when a target needs a mosaic.
- **Where:** the Planetarium, framing overlay.
- **What the user does:** places the sensor's field, sets the rotation and the mosaic panels on the
  sky, and chooses "Use this framing", which sends it to the night plan.
- **Done right when:** the framing in the night plan matches the overlay, panel for panel.

### UC9. Plan calibration frames

- **When:** a warning says calibration is missing or does not match, or a capture setting is new.
- **Where:** the warning in Observation Planner; the calibration library in Observatory Manager.
- **The user sees:** which darks, flats or bias frames are missing for which capture setting, and
  what the library holds today.
- **Decisions it supports:** which calibration frames to take, and when.

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
- **Where:** Observation Execution in monitor mode, on the main monitor.
- **The user sees:**
  - whether guiding is steady, whether the last dither worked, and which frames are already ruined;
  - each frame's quality as it arrives in the library;
  - the quality strip: the target's top warning, top recommendation and the night's loss so far.
- **What the user does:** acts in Ekos (refocus, recalibrate the guider, move to another target).
  The app sends no commands.
- **Done right when:** a problem shows within a few frames of when it started.

### UC12. Change plans mid-night

- **When:** clouds arrive, a target sets early, or the first frames show a problem.
- **Where:** Observation Planner on the second monitor, at the current time.
- **The user sees:** the candidates re-ranked for the rest of the night.
- **Decisions it supports:** what to switch to, entered in Ekos.

### UC13. Measure a new camera setting

- **When:** the user starts using a gain, offset or binning not measured before.
- **Where:** Observatory Manager, camera profiles.
- **What the user does:** takes flat pairs and bias pairs at that setting and runs the measurement.
- **The user sees:** the measured gain and read noise, saved with their source. Until then, the
  target report marks that setting's efficiency as relative to the user's own data only.

### UC14. Change equipment

- **When:** the user changes the camera, telescope, focal length or guide optics.
- **What happens:** limits that depend on the user's earlier nights start fresh for the new setup,
  and say "no baseline yet" until enough nights exist. Limits from physics and the camera profile
  apply at once.

### UC15. Ask the AI companion

- **When:** the user prefers to ask in words, for example "what should I do next on M 81?".
- **Where:** the read-only AI companion, through the app's MCP tools.
- **What happens:** the companion reads the same target dataset report and measurements through
  the public quality calls and explains them. It cannot change settings or command devices. When
  its tools cannot answer, it reports the gap.

## 6. Points that need agreement

1. **Where the night review lives.** This document places UC1 in Observation Execution, with a
   night selector that defaults to tonight and can show any earlier night from the stored data. The
   UI plan does not have this yet.
2. **The checklist is plan state.** Non-capture recommendations join the night plan as a checklist.
   The plan and its ticked items are saved as the user's plan; the recommendations themselves are
   still worked out fresh. A ticked item that the next measurements show did not work comes back as
   a recommendation.
3. **The "new target" case.** For a target with no frames yet, the report has no data to judge. It
   offers a starting capture setting from the user's other targets with similar brightness and the
   same equipment, marked as a starting point, not a recommendation from data.
4. **How often the report refreshes at night.** This document assumes the quality strip and frame
   list update as frames arrive from each sync, not continuously.
5. **The Nikon D5300.** The use cases assume the ZWO cameras. The Nikon gets measurements, but its
   gain and read noise are not yet measured, so its recommendations stay relative until UC13 is
   done for it.

## 7. Not use cases

- Recommending new or different hardware.
- The app commanding any device while Ekos captures.
- Planning a whole season automatically, or moving targets between nights without the user.
- Saving recommendations. Only measurements and the user's plan are saved.
- A phone companion. It is planned separately.
