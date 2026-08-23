# Astrometrics: Hardware Control and Automation Guide

*Version 2.2 · 2026-08-21 · Status: current*

## Overview

This guide provides comprehensive operational procedures for configuring INDI hardware drivers, calibrating autoguiders, running autofocus V-curves, executing plate solving alignments, and setting up automated weather safety interlocks within the Astrometrics software.

---

## 1. Introduction

This guide demonstrates how to connect to hardware, align the telescope mount, set up autofocus, and start autoguiding. It also covers the safety features that protect equipment.


---

## 2. INDI Driver Connection & Setup

![Observatory Manager Interface](./assets/observatory_manager.png)

Before initiating any automated sequence, the hardware must be successfully connected and initialized.

### 2.1 Connection Workflow
1. Launch the application and open **Observatory Manager** mode (`Ctrl + 5`).
2. Open the **Settings Drawer** (gear icon) $\rightarrow$ **System Form**:
   - Set **INDI Host** (default `127.0.0.1` for local, or a remote IP e.g., `192.168.1.100`).
   - Set **INDI Port** (default `7624`).
3. Inspect the **Devices** panel on the right sidebar. Confirm green status dots for:
   - Mount Driver (e.g., `EQMod Mount` or `Telescope Simulator`)
   - Camera Driver (e.g., `ZWO ASI` or `CCD Simulator`)
   - Focuser Driver (e.g., `EAF` or `Focuser Simulator`)
   - Filter Wheel (e.g., `EFW` or `Filter Simulator`)

> [!WARNING]
> If a device shows a yellow warning dot or fails to connect, verify that the device is powered on, USB cables are securely connected, and no other software (like an instance of Ekos or PHD2) is exclusively locking the COM port.

---

## 3. Plate Solving & Mount Alignment

Plate solving syncs the telescope mount's reported coordinates $(\text{RA}_{\text{mount}}, \text{Dec}_{\text{mount}})$ with true sky position $(\text{RA}_{\text{true}}, \text{Dec}_{\text{true}})$ by analyzing the star patterns in a captured image.

### 3.1 Alignment Workflow
1. Slew the telescope to a bright catalog target near the zenith (avoiding the horizon where atmospheric refraction degrades plate solving accuracy).
2. In the **Telescope Control** card, ensure the camera cooling is active and click **Start Alignment**.
3. The engine captures a brief (e.g., 3-second) exposure and runs the ASTAP plate solving algorithm.
4. Upon successful solve, the pointing error $(\Delta \text{RA}, \Delta \text{Dec})$ is computed, and the mount is automatically synchronized.
5. Monitor real-time alignment history in the **Alignment Status** panel. The panel lists each alignment attempt's symbol status (`solving` ⟳, `aligned` ✓, `warning` ⚠, `failed` ✗) alongside precise coordinate deltas.

### 3.2 Troubleshooting Plate Solving Failures
If the solver fails to find a match:
- **Check Exposure:** Ensure stars are visible and not trailed. Increase exposure time to 5 seconds if the field is sparse.
- **Check Focal Length/Pixel Size:** The solver relies on the image scale. Ensure the focal length and pixel size configured in the camera profile accurately reflect the optical train (including reducers/flatteners).

---

## 4. Autofocus V-Curve & Temperature Compensation

Achieving critical focus is essential for resolving fine details and maximizing the Signal-to-Noise Ratio (SNR) of faint targets.

### 4.1 Running an Autofocus V-Curve
1. **Setting Step Size**: In **Focuser Control**, set the initial step size (default `100` steps). This should be large enough that a single step produces a measurable change in star size, but small enough to capture the bottom of the focus curve.
2. **V-Curve Run**:
   - Click **Start Autofocus**.
   - The focuser steps inward and outward through 9 points, capturing an image and measuring the size of the stars at each point.
   - The engine automatically calculates the curve to find the exact point where stars are smallest and sharpest.
   - The focuser automatically moves to this optimal position.



> [!TIP]
> If the V-curve is flat or erratic, the step size may be too small (failing to leave the critical focus zone) or atmospheric seeing is exceptionally poor. Increase the step size and try again.

### 4.2 Temperature Compensation
As ambient temperature drops throughout the night, optical tubes contract, shifting the focal plane.
1. Enable the **Temp Comp** toggle in the Focuser Control panel.
2. Enter the thermal coefficient (e.g., -35 steps per degree Celsius). The system will automatically adjust the focus position in the background as the temperature drops, preventing the need to frequently interrupt the imaging session to refocus.

---

## 5. Autoguider Calibration & Dither Tuning

Autoguiding continuously measures and corrects small mechanical tracking errors in the mount.

### 5.1 Calibration and Guiding
1. **PHD2 Connection**: Confirm an active connection to the internal guiding engine or external PHD2 instance in the **Guiding Trends** card.
2. **Calibration Run**: Slew near the celestial equator and meridian. Click **Calibrate Guider**. The mount steps in all four directions to learn how the mount responds to corrections.
3. **Error Monitoring**: Monitor the total guiding error on the real-time graph. Target total error should remain below the imaging scale (typically under 0.8 arcseconds) for round stars.



### 5.2 Dither Configuration
Dithering shifts target coordinates slightly between exposures to eliminate fixed-pattern sensor noise and hot pixels during the stacking process.
1. In the session settings, set the Dither Scale (default `2` pixels) and frequency (e.g., every `1` image).
2. During sequence execution, the mount will dither and wait for guiding to settle before starting the next exposure.

---

## 6. Automated Weather Safety & Emergency Interlocks

Unattended operation relies on automated safety systems to protect equipment from environmental hazards.

### 6.1 Safety Monitoring
1. **Weather Sensors**: Monitor ambient temperature, humidity, rain status, and cloud sensor telemetry in the **Environmental Status** panel.
2. **Automatic Roof Interlock**: If rain is detected or cloud cover exceeds the configured safety threshold:
   - The active exposure sequence is automatically suspended.
   - The telescope mount executes an immediate **PARK** command.
   - The Roof/Dome shutter sends a **CLOSE** command.
   - The camera cooler is turned off to save power.

> [!CAUTION]
> Ensure the mount is configured with strict software slew limits and cord-wrap limits. If the safety system triggers a park, the mount must be able to return to its home position without colliding with the pier or snagging cables.

### 6.2 Manual Emergency Stop
Click the red **EMERGENCY STOP** button at any time to immediately interrupt all hardware operations, halt mount slewing, and abort any active camera exposures.
