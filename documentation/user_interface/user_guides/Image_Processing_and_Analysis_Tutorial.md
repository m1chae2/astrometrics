# Image processing tutorial

*Version 2.0 · 2026-10-15 · Status: current*

In this tutorial you take the frames of one target from raw files to a measured light curve. A light curve is a plot of a star's brightness over time. You will check your frames, stack them, plate solve them, and read the brightness of a star. Each step tells you what you should see before you move on.

![Image Processing](./assets/image_processing.png)

## What you need

- Astrometrics is running. See [Getting started](../../Getting_Started.md).
- One target with at least 10 light frames in your image library. A light frame is one camera exposure of the sky. To copy frames from the telescope, use the recipe [Download captured frames](Observatory_Control_and_Automation_Guide.md#download-captured-frames).
- Matching dark frames in the library. A dark frame is an exposure with the camera covered. It records the heat noise of the sensor.
- Plate solving set up. Plate solving matches the stars in an image to a star catalog to learn where the image points. In Settings > Processing, enter an astrometry.net API Key, or install a local solver.
- Siril installed. Siril is the outside program that stacks the frames. Settings > Processing > Siril Executable names it.

## Step 1: Open your target

1. Click the mode button in the status header and choose Image Processing.
2. In the Targets list on the left, click your target. Use the search box or the Filter Targets tabs if the list is long.

**What you should see.** The FITS Viewer in the middle shows one of your frames. FITS is the standard file format for astronomy images. The File Browser under it lists every frame of the target, one row each, with Date, Camera, Filter and Exp (s). The viewer picks the frame with the longest exposure.

## Step 2: Check your frames and calibration

Calibration cleans each frame before stacking. The app subtracts the dark frames and the other calibration frames it finds in your library. You do not run a separate calibration step. You only check that the calibration frames are there.

1. In the Frame Analysis panel on the right, click the Data tab.
2. Find the Session Frames table. Each row is one exposure length.
3. Read the Session column. It is the number of light frames.
4. Read the Library Match column. A check mark with a number means that many matching dark frames are in the library.
5. Click a row in the File Browser to look at a single frame. Click the Header Info button to read its FITS header, the notes the camera saved with the image.

**What you should see.** Every row of Session Frames shows a check mark in Library Match. If a row says Missing, the library has no dark frame for that exposure length. Add dark frames with the Ingest From Telescope dialog before you go on.

## Step 3: Stack the frames

Stacking lines up your frames so each star falls on the same pixel, and then combines them. Real stars add up. Random noise averages out. Bad pixels from satellites and cosmic rays are thrown out.

1. Leave every box in the File Browser unticked to stack all frames. Tick boxes only to use a chosen set.
2. In the Image Controls panel, click Stack Frames.
3. In the Frame Analysis panel, click the Analysis tab.
4. Watch the status box until it reads Processing Successful.

**What you should see.** The status box reads Processing Active with a spinner, and the log fills with lines from the job. When the job ends, the box reads Processing Successful and the log ends with a success line. If the button reads Stacking requires matching cameras, your frames come from more than one camera. Tick the frames of one camera and try again.

Now look at the result.

1. Click the Data tab.
2. Find the Final Stack row. Click View.
3. If the image looks dark, look at the button above the viewer. If it reads Linear, click it to switch to the stretched view. A stretch brightens faint detail.

**What you should see.** The Final Stack row names the stack file, and its button now reads Viewing. A Total Exposure line shows the exposure time of the stack. The FITS Viewer shows the stack. It has less noise than a single frame, and faint stars stand out. Scroll to zoom in and drag to move around.

## Step 4: Plate solve and measure the stars

The next job plate solves your frames and then measures the brightness of every star in each frame.

1. In the File Browser, click the check box in the table header. This ticks every frame.
2. In the Image Controls panel, click Analyze Target.
3. Click the Analysis tab and watch the log until the job ends.
4. Click the Data tab.

**What you should see.** The log shows the job running, and the button changes to Cancel until the job ends. The Data tab then shows three counts: Total Images, Stars Found and Frames Processed. Stars Found is above zero.

Now check the plate solve.

1. Click the Astrometry button above the viewer.

**What you should see.** Small labels appear on the image. Each label sits on a star and carries the star's catalog name. The labels exist because the plate solve worked. If a message says no stars were identified, the solve did not work. Check your plate solver in Settings > Processing and run Analyze Target again.

## Step 5: Read a light curve

1. In the viewer, click the label of a bright star.
2. A message says that the star is opening in the Astronomy Manager. The view changes to the Astronomy Manager.
3. Look at the star list on the left. Rows with a P badge have photometry. Photometry means brightness measurements. If you see the note No photometry has been recorded for this star, click another star with a P badge.
4. Look at the Photometry panel in the middle.
5. Look at the Stellar Analysis panel on the right. Under Light Curve, read Measurements and Time span.
6. Drag the two Timeline sliders to look at part of the time range.
7. If a Run period analysis button shows under Period Search, click it. The search looks for brightness that rises and falls on a regular schedule.

**What you should see.** The Photometry panel plots brightness against time, with one point for each frame. For most stars the points scatter around a flat line. That is normal, because most stars do not change. After a period search, a Repeating Patterns section appears. It gives Detected period or Possible period, and Chance of noise. A low Chance of noise is more convincing.

## What you did

You checked that your frames had matching calibration frames, stacked them into one clean image, plate solved the frames, and read a star's brightness over time. You used four views of the app: Image Processing, the FITS Viewer, the Astrometry labels and the Astronomy Manager.

## Where to go next

The [user manual](User_Manual.md) describes every control you used here. The [Astronomy Manager section](User_Manual.md#astronomy-manager) also covers spectra, for frames taken through a diffraction grating.

## See also

- [User manual](User_Manual.md)
- [Observatory control guide](Observatory_Control_and_Automation_Guide.md)
- [Getting started](../../Getting_Started.md)
- [Pipeline review guides](../../library_design/pipelines/index.md)
