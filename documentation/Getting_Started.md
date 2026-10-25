# Getting started

*Version 2.0 · 2026-10-15 · Status: current*

This tutorial walks you through a first session with the Astrometrics desktop app. You will start the app, learn the main views, open a target, and stack its frames. It assumes you finished [Installation](Installation.md). It needs no telescope if you already have frames on disk.

A *frame* is one camera exposure saved as a file, usually in FITS format (the standard file type for astronomy images). A *target* is one object in the sky, such as the Orion Nebula, together with its frames. A *stack* is one image made by combining many frames, which lowers the noise.

## Start the app

1. Open a terminal in the `astrometrics` folder.
2. Run the start command.

   ```bash
   ./astrometrics.sh start
   ```

3. Wait for the splash screen to close. The splash screen stays up while the backend loads its star catalog. The backend is the Python program that does the real work.

The main window opens on the view you used last. On a first run, that is the Image Viewer. To close everything later, run `./astrometrics.sh stop`.

## Find your way around

The top bar stays on screen in every view. From left to right, it holds these items.

| Item | What it does |
| --- | --- |
| Menu icon (three lines) | Opens the settings panel. Hover over it to see "Open settings". |
| View name | Shows the current view. Click it to open the list of views. |
| Status dot and state | Shows the telescope mount. The dot is green when a mount is connected and red when it is not. The text beside it reads, for example, `Tracking` or `Parked`. |
| RA, DEC, ALT and AZ | Show where the telescope points. RA and DEC are sky coordinates. ALT and AZ are the height above the horizon and the compass direction. |
| Temperature and humidity | Show the air temperature in °C and the relative humidity in %. |

On a narrow window, some of these readings are hidden.

The view list holds seven views. Next to each name is a small button that opens that view in a new window, which helps if you have several monitors. Settings can turn individual views off.

| View | Use it to |
| --- | --- |
| Image Viewer | Look at the latest image of a target and read its details. |
| Image Processing | Check a target's frames, stack them, and analyze the result. |
| Command Console | Run Python commands and scripts inside the app. |
| Astronomy Manager | Measure star brightness over time and work with star spectra. |
| Planetarium | Explore an interactive sky map. |
| Observatory Manager | Set up the equipment and watch the mount, guiding and calibration frames. |
| Observation Manager | Keep observation packages for your targets and plan tonight's session. |

## Open a target

1. Open the view list and choose Image Viewer.
2. Look at the Targets list on the left. Type in the filter box above it to narrow the list.
3. Click a target. Its latest image appears in the center, and the Information panel on the right shows its details.

If the Targets list says "No targets in the catalog", your library has no frames yet. Do the next two sections to add some.

### Add a target

1. Choose Observation Manager from the view list.
2. Click Add Target in the Observation Packages panel.
3. In the "Add New Target" window, type a Target Name, such as `M42`. The RA and DEC boxes are optional.
4. Click Add Target.

The Observatory Manager has an Add Target button too.

### Bring in frames

The app can download frames from the telescope's computer into your library.

1. Choose Image Processing and click your target in the Targets list.
2. Click Download Frames. The "Ingest From Telescope" window opens.
3. Check the Target Name and Telescope boxes, then pick the files you want.
4. Click Start Ingestion and wait for it to finish.

## Run one processing step

Stacking is the first step most people run. It aligns your frames and combines them into one image.

1. In Image Processing, click a target that has frames.
2. Look at the center of the window. The FITS Viewer shows the selected frame, and the File Browser lists the target's frames.
3. Find the Image Controls panel on the right. Click Stack Frames.
4. Watch the panel above it. Open its Analysis tab to see the log. It reads "Processing Active" while the job runs and "Processing Successful" when it ends. Click Cancel to stop the job.
5. Open the Data tab to see the stacked image. The FITS Viewer can show it.

The Stack Frames button does not appear when the target has no frames. It reads "Stacking requires matching cameras" when the frames come from more than one camera. Stacking runs Siril, so the Siril settings in your configuration must be correct. See [Installation](Installation.md#configure-the-app).

The Analyze Target button in the same panel runs the analysis step on your stack. The tutorial linked below covers it.

## Next steps

- Learn every view in the [user manual](user_interface/user_guides/User_Manual.md).
- Follow the full processing workflow in the [image processing tutorial](user_interface/user_guides/Image_Processing_and_Analysis_Tutorial.md).
- Control a telescope in the [observatory control guide](user_interface/user_guides/Observatory_Control_and_Automation_Guide.md).
- Script the same steps in Python with the [library quick start](how_to/library_quick_start.md).
