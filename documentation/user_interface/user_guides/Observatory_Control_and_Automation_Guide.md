# Observatory control guide

*Version 2.0 · 2026-10-15 · Status: current*

This guide has short recipes for running your telescope from the Astrometrics desktop app. Each recipe has one goal, the things you need first, and numbered steps. Pick the recipe you need. You do not have to read them in order.

The app handles basic telescope control and simple capture queues. Full automated nights are best run in a dedicated INDI client such as [KStars/Ekos](https://docs.kde.org/trunk5/en/kstars/kstars/ekos.html). INDI is the open standard that lets software talk to astronomy hardware. For a description of every control, see the [user manual](User_Manual.md).

![Observatory Manager](./assets/observatory_manager.png)

## Connect to your INDI devices

**Goal.** Point the app at the INDI server that runs your mount, camera, focuser and filter wheel.

**Before you start.**

- The INDI server is running with the drivers for your hardware.
- You know the computer name or address of the INDI server, and its port.

**Steps.**

1. Click the Open settings button (three lines) at the left of the status header.
2. Open the Observatory tab.
3. Under Telescope Connection, type the computer name or address in Mount Hostname.
4. Type the port in INDI Port. INDI servers often use 7624.
5. Click Save. The app uses a changed host name the next time it connects.
6. Click the mode button in the status header and choose Observatory Manager.
7. Look at the Devices panel on the right. It shows one button for each connected device.
8. Click a device button to read its properties in the Device Properties panel.

**Result.** The dot in the status header shows the mount connection, and the Devices panel lists your hardware. If it reads No INDI devices connected., check the host name, the port, and that the server is running.

## Let the app command the telescope

**Goal.** Turn off Safe Mode, so the app may move the mount and run the camera.

**Before you start.** Your mount and camera are connected. See [Connect to your INDI devices](#connect-to-your-indi-devices).

**Steps.**

1. Open settings and click the Observatory tab.
2. Under Safety, tick Allow Telescope Commands (Disable Safe Mode).
3. Read the message that appears. It reads Controller mode enabled when it worked. If it says some parts are not yet eligible, it lists them.

**Result.** The setting acts at once, so you do not need to click Save. Clear the box to put the app back in monitoring mode, where it only watches. Without this box ticked, a slew can fail with a message that mentions Safe Mode.

## Add a target

**Goal.** Put a target in the list so you can slew to it or plan exposures for it.

**Before you start.** Know the target's name. Know its right ascension (RA) and declination (Dec), which are its sky coordinates.

**Steps.**

1. Open the Observatory Manager or the Observation Manager.
2. Under the Target List, click Add Target.
3. Type the Target Name. For example, M42.
4. Fill in RA and DEC, for example 05h 35m 17s and -05d 23m 28s. Both are optional in the dialog. A slew needs coordinates.
5. Click the add button in the dialog.

**Result.** The target appears in the Target List. Use the search box to find it.

## Slew to a target and track it

**Goal.** Move the mount to a target and make it follow the stars. A slew is a fast move of the mount to a new place in the sky.

**Before you start.**

- The mount is connected and Safe Mode is off. See [Let the app command the telescope](#let-the-app-command-the-telescope).
- The target is in the list and has coordinates.
- Nothing can block the mount's path.

**Steps.**

1. Open the Observatory Manager.
2. If the status header reads Parked, click UNPARK in the Telescope Control panel.
3. Select the target in the Target List.
4. Click Slew to Target. A message tells you if the target has no coordinates.
5. Watch RA and DEC in the status header move toward the target.
6. When the mount stops, click Tracking ON in the Telescope Control panel.

**Result.** The status header reads Tracking, and RA and DEC stay steady.

You can also slew from the Planetarium. Right-click an object on the sky map and choose Slew Telescope Here. This choice appears only when a mount is connected.

## Center a target by plate solving

**Goal.** Make the mount point exactly at a target. The app takes an image, matches its stars to a catalog (plate solving), and corrects the pointing.

**Before you start.**

- Safe Mode is off and the camera is connected.
- Plate solving is set up. In Settings > Processing, enter an astrometry.net API Key, or install a local solver.
- You selected a target with coordinates in the Target List.

**Steps.**

1. Open the Observatory Manager and select the target.
2. Click Start Alignment in the Telescope Control panel.
3. Watch the Alignment Status table. Each row is one round. The symbol shows the state: ⟳ solving, ✓ aligned, ⚠ warning, ✗ failed. dRA and dDEC show the pointing error in arcseconds.
4. Wait for ✓. A run makes at most 10 rounds.
5. To stop early, click Stop Alignment.

**Result.** The last row shows ✓ and small dRA and dDEC values. The Alignment and Guiding fields in the Tracking Settings panel do not change this run.

## Move the mount by hand

**Goal.** Nudge the mount in small steps, for example to put a star in the middle of the frame.

**Before you start.** The mount is connected, Safe Mode is off, and the mount is unparked.

**Steps.**

1. Open the Observatory Manager and find the Manual Guide panel.
2. Drag the Slew Speed slider. The four steps, from Min to Max, are Guide, Centering, Find and Max. Choose a slow step for small moves.
3. Press and hold an arrow button. The mount moves while you hold it.
4. Let go of the button. The mount stops.
5. Click STOP if the mount keeps moving.

**Result.** The mount moves only while you hold a button.

## Park the mount or stop it in an emergency

**Goal.** Put the mount in its safe parked position, or stop all motion at once.

**Before you start.** For a normal park, the mount is connected and Safe Mode is off.

**Steps.**

1. To park, open the Observatory Manager and click PARK in the Telescope Control panel.
2. Wait until the status header reads Parked.
3. To unpark later, click UNPARK.
4. To halt a move at once, click STOP in the Manual Guide panel.
5. If the main window is not in front, click the Astrometrics icon in the system tray and click Park in the panel that opens.

**Result.** The mount stops or parks. If the tray Park fails, an error message tells you to park the mount by hand. Do that at once.

## Change the filter

**Goal.** Move the filter wheel to a different filter.

**Before you start.**

- The filter wheel is connected and Safe Mode is off.
- Settings > Observatory > Available Filters lists the filters that the wheel holds.

**Steps.**

1. Open the Observatory Manager and find the Filter Wheel panel.
2. Click the button for the filter you want: L, R, G, B, Ha, OIII, SII or Spect.
3. Wait while the button shows that the change is in progress.

**Result.** The button for the active filter is highlighted.

## Focus by hand

**Goal.** Move the focuser until stars look as small and sharp as possible. The app has no autofocus. You focus by taking test frames and moving the focuser between them.

**Before you start.**

- The focuser and camera are connected and Safe Mode is off.
- The mount points at a star field and is tracking.
- Tip: open the Observation Manager in a second window. In the mode menu, click the pop-out button next to Observation Manager. Then you can see the focuser and the test frame at the same time.

**Steps.**

1. In the Observation Manager, take a [test frame](#take-a-test-frame).
2. Look at the Last Image panel. Judge how big the stars are.
3. In the Observatory Manager, find the Focuser panel. Note the Position.
4. Under Steps, choose a step size. Use 500 for a big move and 10 for a fine move.
5. Click Focus IN (-) or Focus OUT (+).
6. Take another test frame and compare the stars.
7. Repeat steps 4 to 6. If the stars get bigger, go the other way. Stop when the stars are smallest.

**Result.** Stars are small and round in the test frame.

## Check guiding

**Goal.** See how well the mount holds a star in place during an exposure. Guiding is a loop that measures small tracking errors and corrects them.

**Before you start.**

- Guiding is already running. The Observatory Manager has no button that starts guiding. Start it in the program that guides, such as PHD2 or KStars/Ekos.
- Samples are arriving from the guiding program.

**Steps.**

1. Open the Observatory Manager and find the Guiding Trends panel.
2. Tick or clear dRA, dDEC, SNR, RMS and Pulses to choose the lines. dRA and dDEC are the errors in right ascension and declination.
3. Drag the History slider to show 10 to 100 points.
4. Look at the Guiding Target panel. It plots dRA against dDEC.

**Result.** Points that stay near the center of Guiding Target, and flat lines in Guiding Trends, mean good guiding. Long drifts away from the center mean the mount is not holding the star.

## Take a test frame

**Goal.** Take one frame with chosen settings, to check focus, framing or exposure.

**Before you start.** The camera is connected, Safe Mode is off, and the right filter is in place.

**Steps.**

1. Open the Observation Manager and select your target in the Target List.
2. In the Image Planner, set Type to Light.
3. Set Filter and Exposure. Exposure is in seconds.
4. Click Test Image.
5. Wait for the message Test frame capture started.
6. Look at the Last Image panel. Use its button to switch between the linear and the stretched view.

**Result.** The Last Image panel shows the new frame when the exposure ends.

## Plan and run a capture sequence

**Goal.** Queue the exposures for a target and let the app take them.

**Before you start.**

- The mount and camera are connected, and Safe Mode is off.
- The mount is unparked. The queue checks safety, slews to the target and takes the frames.
- You know what you need: the frame type, filter, exposure time and number of frames.

**Steps.**

1. Open the Observation Manager and select your target in the Target List.
2. In the Image Planner, set Type, Count, Filter, Exposure and Delay. Delay is the pause between frames, in seconds.
3. Click Add to List. The row appears in the Image List.
4. Repeat steps 2 and 3 for each filter or exposure length you need.
5. Optional: to shift the pointing between frames, use Dither Settings. The settings apply to mosaic panels. To plan a grid of panels, set Grid Size and Overlap in the Mosaic Planner and click Create Mosaic. Each panel adds a row to the Image List.
6. Check the Image List. Use the pencil on a row to edit it, or × to remove it.
7. Click Add to Sequence. A card for the target appears in the Execution Queue.
8. To change the order of cards, drag a card. To change a plan, click the pencil on its card, edit the rows, and click Modify.
9. Click Start Imaging Session.
10. Watch the Last Image panel. It updates after each frame.

**Result.** The queue runs card by card. The Observation Manager has no pause or stop button. For a night that needs one, use KStars/Ekos.

## Download captured frames

**Goal.** Copy the frames from the telescope's storage into your image library, so you can process them.

**Before you start.**

- The telescope computer is reachable. Settings > Observatory > `Remote Pictures Path` points at the folder that holds the frames.
- A target row is highlighted in the Image Processing target list when the telescope has a folder with its name.

**Steps.**

1. Open Image Processing and select the target.
2. In the Image Controls panel, click Download Frames.
3. In the Ingest From Telescope dialog, check Target Name. Check that Files Found shows a number.
4. In the File Selection list, keep the files you want ticked. Click Refresh if you expect more files.
5. Click Start Ingestion.
6. Watch Ingest Status. When it reads completed, the dialog closes by itself.

**Result.** The frames appear in the File Browser for the target. For dark, bias and flat frames, use the name Calibration and the buttons Dark Only, Bias Only and Flat Only.

## See also

- [User manual](User_Manual.md)
- [Image processing tutorial](Image_Processing_and_Analysis_Tutorial.md)
- [Getting started](../../Getting_Started.md)
- [Pipeline review guides](../../library_design/pipelines/index.md)
