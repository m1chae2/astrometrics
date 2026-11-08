/**
 * @module CelestialSkyMap
 * @fileoverview Celestial viewport map rendering on HTML5 Canvas.
 *
 * Renders stars, coordinates, and overlays using requestAnimationFrame at a fixed rate,
 * decoupling UI math and high-frequency pointer interactions from React re-renders.
 */

import React, { useRef, useEffect, useState, useMemo, useCallback } from 'react';
import { PlanetariumSource, PlanetariumTarget, ObserverLocation, ConstellationLineSegment } from '../../common/types/planetariumTypes';
import { getAltAz, getRaDec, projectAltAz, pixelsPerDegree } from '../utils/projectionMath';
import { PopoverSelect } from '../../common/components/PopoverSelect';
import { safeParse } from '../utils/coordinateUtils';
import { calculateLST } from '../utils/siderealTime';
import { findNearestSource } from '../utils/hitTesting';
import { useTelescopeStatus } from '../../common/hooks/useTelescopeStatus';
import { emitToast } from '../../common/utils/emitToast';
import type { ProjectionContext } from '../layers/overlayTypes';
import { BackgroundOverlay } from '../layers/BackgroundOverlay';
import { StarOverlay } from '../layers/StarOverlay';
import { StarSelectionOverlay } from '../layers/StarSelectionOverlay';
import { TargetOverlay } from '../layers/TargetOverlay';
import { EnvironmentOverlay } from '../layers/EnvironmentOverlay';
import { GridOverlay } from '../layers/GridOverlay';
import { FovOverlay } from '../layers/FovOverlay';
import { TelescopeOverlay } from '../layers/TelescopeOverlay';
import { CompassOverlay } from '../layers/CompassOverlay';
import { HudOverlay } from '../layers/HudOverlay';
import { ConstellationOverlay } from '../layers/ConstellationOverlay';
import { AlignmentOverlay } from '../layers/AlignmentOverlay';
import { TrackingRiskOverlay } from '../layers/TrackingRiskOverlay';
import { FramingOverlay, FramingPanelCorners } from '../layers/FramingOverlay';
import { StarFieldRenderer } from '../webgl/StarFieldRenderer';
import { perfEnd, perfStart } from '../utils/perfMarks';
import { useTimeController } from '../hooks/useTimeController';
import { useSkyMapInput } from '../hooks/useSkyMapInput';
import { skyMovedEnoughToDraw } from '../utils/skyMotion';
import { easePanStep } from '../utils/panEasing';
import { trackingCameraStep, TrackingState } from '../utils/trackingView';
import { FovNotifyGate, easeFovTowardTarget } from '../utils/zoomEasing';
import { Button } from '../../common/components/Button';
import { Slider } from '../../common/components/Slider';

/**
 * Props for CelestialSkyMap.
 */
interface Props {
  /** Initial viewport center; null defaults to Az=0°, Alt=0° (North horizon). */
  center: { ra: number; dec: number } | null;
  /** Star sources to render in the StarOverlay. */
  sources: PlanetariumSource[];
  /** Target objects to render reticle bounds and FITS images for. */
  targets: PlanetariumTarget[];
  /** Observer geographic position for coordinate transforms. */
  location: ObserverLocation | null;
  /** Show star catalog overlay. */
  showStars: boolean;
  /** Show sensor FOV outline overlay. */
  showFOV: boolean;
  /** Show horizon and ground environment overlay. */
  showEnvironment: boolean;
  /** Show RA/Dec coordinate grid overlay. */
  showGrid: boolean;
  /** Show library (cataloged) objects. */
  showCatalog: boolean;
  /** Show bundled constellation stick-figure lines. */
  showConstellations: boolean;
  /** Full bundled set of constellation stick-figure line segments, pre-resolved to RA/Dec. */
  constellationLines: ConstellationLineSegment[];
  /** Show telescope pointing crosshair overlay. */
  showTelescope: boolean;
  /** Show telescope alignment pointing vectors and polar alignment overlay. */
  showAlignment?: boolean;
  /** Show mount tracking mechanical risk heatmap overlay. */
  showTrackingRisk?: boolean;
  /** Plate solves grouped by target (from the library) to project onto the celestial sphere. */
  alignmentTargets?: import('../../common/types/backendTypes').AlignmentTargetSession[];
  /** The backend's tracking-risk grid over hour angle and declination. */
  trackingRisk?: import('../../common/types/backendTypes').TrackingRiskMap | null;
  /** Polar Alignment Assistant (PAA) status and coordinates. */
  polarAlignment?: import('../../common/types/backendTypes').PolarAlignmentStatus | null;
  /** Selected historical session identifier being reviewed. */
  selectedSessionId?: string | null;
  /** Active simulation date (from Date & Time modal or simulation clock). */
  simulationDate?: Date;
  /** Current field of view in degrees. */
  fov: number;
  /** Callback invoked when the user scrolls to change FOV. */
  onFOVChange: (fov: number) => void;
  /** Callback invoked when a source is clicked or deselected. */
  onSelectSource: (source: PlanetariumSource | null) => void;
  /** Callback invoked on right-click over a source, with canvas-local position. */
  onRightClickSource: (source: PlanetariumSource, pos: { x: number; y: number }) => void;
  /** ID of the currently selected target for crosshair rendering. */
  selectedTargetId: string;
  /**
   * Counter bumped only when the user explicitly picks a target from the
   * target list (never for a canvas click). Selecting a source directly on
   * the sky map should select it without moving the camera; only a change to
   * this counter triggers the one-time slew to the newly selected object.
   */
  slewRequestId: number;
  /**
   * Whether the slew that a change to `slewRequestId` starts should also turn
   * on tracking, so the map keeps the picked object centered as the sky turns.
   * Read only when `slewRequestId` changes.
   */
  trackOnSlew?: boolean;
  /** Callback invoked (throttled) when the viewport center changes during pan. */
  onCenterChange: (ra: number, dec: number) => void;
  /** Sensor FOV width in degrees from the active equipment configuration. */
  sensorFovWidthDeg?: number;
  /** Sensor FOV height in degrees from the active equipment configuration. */
  sensorFovHeightDeg?: number;
  /** Framing panels to outline, as corners in RA and Dec degrees. */
  framingPanels?: FramingPanelCorners[];
}

/** The speeds the time controller offers for the accelerated sky rotation. */
const TIME_SPEED_OPTIONS = [1, 5, 25, 100, 1000].map((speed) => ({ id: String(speed), label: `${speed}x` }));

// The sky may turn SKY_MOTION_REDRAW_PIXELS on screen before the map draws again (utils/skyMotion.ts).
// A wide view idles; a tight view draws often enough for smooth motion. Time playback draws every frame.

/**
 * High-performance celestial sky map rendered on HTML5 Canvas.
 *
 * Uses a requestAnimationFrame loop with linear damping for smooth coordinate panning,
 * decoupling overlay rendering from React's reconciler. Refs hold truth for all
 * high-frequency state (viewport center, LST, telescope coords) to avoid re-renders.
 *
 * @func CelestialSkyMap
 * @param {Props} props - Component props.
 * @returns {React.ReactElement} The canvas container with time controller.
 *
 * REQ: PLN-2.1, REQ: PLN-2.2, REQ: PLN-2.3, REQ: PLN-2.4, REQ: PLN-2.5
 */
export const CelestialSkyMap: React.FC<Props> = ({
  center,
  sources,
  targets,
  location,
  showStars,
  showFOV,
  showEnvironment,
  showGrid,
  showCatalog,
  showConstellations,
  constellationLines,
  showTelescope,
  showAlignment = true,
  showTrackingRisk = false,
  alignmentTargets,
  trackingRisk,
  polarAlignment,
  selectedSessionId,
  simulationDate,
  fov,
  onFOVChange,
  onSelectSource,
  onRightClickSource,
  selectedTargetId,
  slewRequestId,
  trackOnSlew = false,
  onCenterChange,
  sensorFovWidthDeg,
  sensorFovHeightDeg,
  framingPanels,
}) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const starCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  // Keep a ref to the active simulation date. It is undefined in live mode,
  // where the render loop reads the system clock itself each frame, so no
  // React state has to tick once a second just to move the sky.
  const simulationDateRef = useRef<Date | undefined>(simulationDate);
  useEffect(() => {
    simulationDateRef.current = simulationDate;
  }, [simulationDate]);

  // WebGL star-field renderer: draws the (potentially thousands of) star
  // points on its own canvas via instanced point sprites, instead of
  // StarOverlay's per-star context.arc()+fill() calls. Falls back to the
  // original 2D StarOverlay if WebGL2 is unavailable (see the mount effect
  // below). 'pending' briefly precedes both outcomes on first mount.
  const starFieldRendererRef = useRef<StarFieldRenderer | null>(null);
  const [starRenderMode, setStarRenderMode] = useState<'pending' | 'webgl' | 'fallback'>('pending');

  useEffect(() => {
    const starCanvas = starCanvasRef.current;
    if (!starCanvas) return;
    try {
      starFieldRendererRef.current = new StarFieldRenderer(starCanvas);
      setStarRenderMode('webgl');
    } catch (error) {
      console.warn('WebGL2 star field renderer unavailable; falling back to 2D star rendering.', error);
      // A one-time, mount-only check (empty deps below) — safe to surface to
      // the user, unlike the per-frame render errors further down this file,
      // which would spam a toast on every animation frame if reported the same way.
      emitToast(
        'WebGL2 unavailable — using slower 2D star rendering.',
        'warning',
        'planetarium-renderer'
      );
      starFieldRendererRef.current = null;
      setStarRenderMode('fallback');
    }
    return () => {
      starFieldRendererRef.current?.dispose();
      starFieldRendererRef.current = null;
    };
  }, []);

  // Instantiated overlays. The star layer is StarOverlay (draws dots + selection
  // reticle on the 2D canvas) until we know whether WebGL2 is available, then
  // becomes either StarSelectionOverlay (reticle only; StarFieldRenderer owns the
  // dots on its own canvas) or stays StarOverlay as the WebGL2-unavailable fallback.
  const overlays = useMemo(() => [
    new BackgroundOverlay(),
    new TrackingRiskOverlay(),
    new GridOverlay(),
    new ConstellationOverlay(),
    new CompassOverlay(),
    new TargetOverlay(),
    new AlignmentOverlay(),
    starRenderMode === 'webgl' ? new StarSelectionOverlay() : new StarOverlay(),
    new FovOverlay(),
    new FramingOverlay(),
    new TelescopeOverlay(),
    new EnvironmentOverlay(),
    new HudOverlay()
  ], [starRenderMode]);

  // Projection state refs to bypass React state re-renders during high-frequency cycles
  const centerAzRef = useRef<number>(0.0);
  const centerAltRef = useRef<number>(0.0);
  const targetAzRef = useRef<number>(0.0);
  const targetAltRef = useRef<number>(0.0);

  const localFOVRef = useRef<number>(fov);
  // Where the wheel and keys want the FOV to end up; the render loop eases localFOVRef toward it.
  const targetFovRef = useRef<number>(fov);
  const fovNotifyGateRef = useRef<FovNotifyGate>(new FovNotifyGate(fov));
  const currentLSTRef = useRef<number>(0.0);
  const sensorFovWidthDegRef = useRef<number | undefined>(sensorFovWidthDeg);
  const sensorFovHeightDegRef = useRef<number | undefined>(sensorFovHeightDeg);

  // Tracks whether the render loop needs to redraw, so an idle, unchanged
  // view doesn't repaint every star/overlay from scratch on every rAF tick.
  // overlayInputsChangedRef is set whenever any overlay-relevant prop
  // changes (see the render-loop effect's dependency array); the camera
  // state (az/alt/fov/LST/canvas size) is compared directly against what
  // was last actually drawn.
  const overlayInputsChangedRef = useRef<boolean>(true);
  const lastDrawnFrameStateRef = useRef<{
    centerAz: number;
    centerAlt: number;
    fov: number;
    lst: number;
    canvasWidth: number;
    canvasHeight: number;
  } | null>(null);

  // Hook for live telescope status
  const { telemetry, telescopeConnection } = useTelescopeStatus();
  const telescopeRaRef = useRef<number | null>(null);
  const telescopeDecRef = useRef<number | null>(null);
  const telescopeConnectedRef = useRef<boolean>(false);

  useEffect(() => {
    telescopeConnectedRef.current = !!telescopeConnection;
    if (telescopeConnection && telemetry.ra !== '-' && telemetry.dec !== '-') {
      telescopeRaRef.current = safeParse(telemetry.ra, true);
      telescopeDecRef.current = safeParse(telemetry.dec, false);
    } else {
      telescopeRaRef.current = null;
      telescopeDecRef.current = null;
    }
  }, [telemetry.ra, telemetry.dec, telescopeConnection]);

  // Time & LST state
  const {
    timeOffsetMinutes, setTimeOffsetMinutes, timeOffsetMinutesRef,
    isTimePlaying, setIsTimePlaying, timeSpeed, setTimeSpeed, getOffsetMinutesAt,
  } = useTimeController();
  const [trackingMode, setTrackingMode] = useState<boolean>(false);
  const trackedCoords = useRef<{ ra: number; dec: number } | null>(null);
  // Remembers whether the camera is locked on the tracked point (utils/trackingView.ts).
  const trackingStateRef = useRef<TrackingState>({ lockedAnchor: null });

  // Sync tracked coordinates when center RA/Dec is set by parent
  useEffect(() => {
    if (center) {
      trackedCoords.current = { ra: safeParse(center.ra), dec: safeParse(center.dec) };
    }
  }, [center]);

  const observerLat = safeParse(location?.latitude ?? 39.7392);
  const observerLon = safeParse(location?.longitude ?? -104.9903);

  const currentLST = calculateLST(simulationDate || new Date(), timeOffsetMinutes, observerLon);

  // Kept in sync so the 't' keydown handler (subscribed once) can read the
  // latest selection without recreating the window listener on every change.
  const selectedTargetIdRef = useRef<string>(selectedTargetId);
  useEffect(() => {
    selectedTargetIdRef.current = selectedTargetId;
  }, [selectedTargetId]);

  // Keyboard listener to toggle tracking mode on 't' keypress.
  // Engaging tracking requires a star or target to already be selected.
  // A canvas click only selects; a list pick turns tracking on only when the
  // parent asks for it (`trackOnSlew`).
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() === 't') {
        const active = document.activeElement;
        if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA')) {
          return;
        }
        setTrackingMode(prev => {
          if (prev) return false;
          return !!selectedTargetIdRef.current && !!trackedCoords.current;
        });
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  // Keep LST ref in sync
  useEffect(() => {
    currentLSTRef.current = currentLST;
  }, [currentLST]);


  // Initial camera orientation. Slews to the pre-selected object (if any) so a
  // restored session opens looking at it, but never auto-engages continuous
  // tracking: a restored session leaves it off until the 't' key or a list pick.
  const hasInitializedZenith = useRef<boolean>(false);
  useEffect(() => {
    if (!hasInitializedZenith.current && currentLST !== undefined) {
      if (center && selectedTargetId) {
        const targetRA = safeParse(center.ra);
        const targetDec = safeParse(center.dec);
        const targetAltAz = getAltAz(targetRA, targetDec, currentLST, observerLat);
        centerAzRef.current = targetAltAz.az;
        centerAltRef.current = targetAltAz.alt;
        targetAzRef.current = targetAltAz.az;
        targetAltRef.current = targetAltAz.alt;
        trackedCoords.current = { ra: targetRA, dec: targetDec };
      } else {
        centerAzRef.current = 0.0;
        centerAltRef.current = 0.0;
        targetAzRef.current = 0.0;
        targetAltRef.current = 0.0;
      }
      hasInitializedZenith.current = true;
    }
  }, [observerLat, currentLST, center, selectedTargetId]);

  // Keeps trackedCoords in sync with whatever is currently selected — a list
  // pick or a canvas click alike — purely so the 't' key has coordinates to
  // engage tracking on. This never moves the camera by itself.
  useEffect(() => {
    if (selectedTargetId && center) {
      trackedCoords.current = { ra: safeParse(center.ra), dec: safeParse(center.dec) };
    } else {
      trackedCoords.current = null;
    }
  }, [selectedTargetId, center]);

  // Slews the viewport only when the caller bumps slewRequestId — i.e. an
  // explicit pick from the target list. A canvas click selects a source
  // without moving the camera; the camera only moves here, or continuously
  // once tracking is on (the 't' key, or a pick that asks for `trackOnSlew`). This only sets the
  // animation loop's target az/alt (targetAzRef/targetAltRef); the render
  // loop's existing lerp (see the trackingMode block below) eases the camera
  // there smoothly. Guarded on hasInitializedZenith so it never fires before
  // or in place of the one-time initial-orientation effect above, which
  // already covers the selection present at mount.
  const previousSlewRequestIdRef = useRef<number>(slewRequestId);
  useEffect(() => {
    const isNewSlewRequest = slewRequestId !== previousSlewRequestIdRef.current;
    if (hasInitializedZenith.current && isNewSlewRequest && center) {
      const targetRA = safeParse(center.ra);
      const targetDec = safeParse(center.dec);
      const targetAltAz = getAltAz(targetRA, targetDec, currentLSTRef.current, observerLat);
      targetAzRef.current = targetAltAz.az;
      targetAltRef.current = targetAltAz.alt;
      if (trackOnSlew) {
        setTrackingMode(true);
      }
    }
    previousSlewRequestIdRef.current = slewRequestId;
    // trackOnSlew is read only when slewRequestId changes; the parent sets both
    // in the same update, so the value seen here belongs to this request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slewRequestId, center, observerLat]);

  // Throttled notification handler for coordinate changes
  const parentCenterNotifyTimeout = useRef<number | null>(null);
  const throttledNotifyParentCenter = useCallback((alt: number, az: number) => {
    if (parentCenterNotifyTimeout.current !== null) {
      window.clearTimeout(parentCenterNotifyTimeout.current);
    }
    // Debounce that restarts on every pan step, kept short because catalogSourceCache
    // makes settling cheap.
    parentCenterNotifyTimeout.current = window.setTimeout(() => {
      const raDec = getRaDec(alt, az, currentLSTRef.current, observerLat);
      onCenterChange(raDec.ra, raDec.dec);
      parentCenterNotifyTimeout.current = null;
    }, 16);
  }, [observerLat, onCenterChange]);

  useEffect(() => {
    return () => {
      if (parentCenterNotifyTimeout.current !== null) {
        window.clearTimeout(parentCenterNotifyTimeout.current);
      }
    };
  }, []);

  // Sync local FOV and sensor FOV refs
  // The FOV prop echoes what this component reported when a zoom settled; ignore that echo
  // so it cannot pull a zoom in progress back. Any other value was set from outside (for
  // example a star pick), so the view jumps to it as before.
  useEffect(() => {
    if (fov === fovNotifyGateRef.current.lastNotified) return;
    fovNotifyGateRef.current.adoptExternal(fov);
    localFOVRef.current = fov;
    targetFovRef.current = fov;
  }, [fov]);

  const onFOVChangeRef = useRef(onFOVChange);
  useEffect(() => {
    onFOVChangeRef.current = onFOVChange;
  }, [onFOVChange]);

  useEffect(() => {
    sensorFovWidthDegRef.current = sensorFovWidthDeg;
  }, [sensorFovWidthDeg]);

  useEffect(() => {
    sensorFovHeightDegRef.current = sensorFovHeightDeg;
  }, [sensorFovHeightDeg]);

  // Pointer-drag panning, wheel zoom, and arrow-key pan/zoom
  const { isDragging, onPointerDown, onPointerMove, onPointerUp, onWheel } = useSkyMapInput({
    canvasRef, centerAzRef, centerAltRef, targetAzRef, targetAltRef, localFOVRef, targetFovRef,
    setTrackingMode,
  });

  // Main Fixed-Rate requestAnimationFrame render loop
  useEffect(() => {
    // A change to any of this effect's dependencies (overlay data, toggles,
    // tracking mode, etc.) forces the next frame to redraw even if the
    // camera itself hasn't moved since the last drawn frame.
    overlayInputsChangedRef.current = true;

    let animFrameId: number;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext('2d');
    if (!context) return;

    const renderLoop = () => {
      // 0. Power efficiency: Pause render work if document is hidden or canvas is not visible
      if (document.hidden || canvas.offsetParent === null) {
        animFrameId = requestAnimationFrame(renderLoop);
        return;
      }

      // 1. Advance tracking target position for current sidereal time
      // The clock is read on every frame, so the sky turns smoothly. Playback offsets are carried
      // forward between steps (useTimeController). Drawing is gated below by how far the sky moved.
      const currentFrameLST = calculateLST(
        simulationDateRef.current ?? new Date(),
        getOffsetMinutesAt(performance.now()),
        observerLon,
      );
      // Keeps click and key handlers on the current time when nothing re-renders.
      currentLSTRef.current = currentFrameLST;

      // Tracking: the camera is anchored on an RA/Dec. Once it has arrived, it is set to that
      // point's alt/az for this frame's own clock reading, so the stars do not move at all. Easing
      // only carries it to a new point (see utils/trackingView.ts).
      if (trackingMode && trackedCoords.current) {
        const trackedCamera = trackingCameraStep(
          trackedCoords.current,
          currentFrameLST,
          observerLat,
          { altitude: centerAltRef.current, azimuth: centerAzRef.current },
          pixelsPerDegree(canvas.width, canvas.height, localFOVRef.current),
          trackingStateRef.current,
        );
        const cameraMoved =
          trackedCamera.altitude !== centerAltRef.current || trackedCamera.azimuth !== centerAzRef.current;
        centerAltRef.current = trackedCamera.altitude;
        centerAzRef.current = trackedCamera.azimuth;
        if (cameraMoved) throttledNotifyParentCenter(trackedCamera.altitude, trackedCamera.azimuth);
        targetAltRef.current = trackedCamera.altitude;
        targetAzRef.current = trackedCamera.azimuth;
      } else {
        trackingStateRef.current.lockedAnchor = null;
      }

      // 1b. Ease the zoom toward its target. The parent hears about the FOV only when the
      // zoom settles, or every FOV_NOTIFY_INTERVAL_MS during a long gesture, so React state
      // and catalog requests do not run on every wheel tick.
      if (localFOVRef.current !== targetFovRef.current) {
        const zoomStep = easeFovTowardTarget(localFOVRef.current, targetFovRef.current);
        localFOVRef.current = zoomStep.fov;
        const fovToReport = fovNotifyGateRef.current.update(zoomStep.fov, zoomStep.settled, performance.now());
        if (fovToReport !== null) onFOVChangeRef.current(fovToReport);
      }

      // 2. Apply linear damping to smooth viewport pan transitions
      // 0.15 damping factor gives ~5 frames to cover 50% of remaining distance at 60fps
      let deltaAltitude = targetAltRef.current - centerAltRef.current;
      let deltaAzimuth = targetAzRef.current - centerAzRef.current;
      if (deltaAzimuth > 180) deltaAzimuth -= 360;
      if (deltaAzimuth < -180) deltaAzimuth += 360;

      // Closes the gap in steps, then snaps onto the target (inside a fraction of a
      // pixel) so the easing ends and the map can stop redrawing.
      if (deltaAltitude !== 0 || deltaAzimuth !== 0) {
        const panStep = easePanStep(
          deltaAltitude,
          deltaAzimuth,
          pixelsPerDegree(canvas.width, canvas.height, localFOVRef.current),
        );
        centerAltRef.current += panStep.altitudeStep;
        centerAzRef.current = (centerAzRef.current + panStep.azimuthStep + 360) % 360;

        // Throttled notification back to parent React component coordinates state
        throttledNotifyParentCenter(centerAltRef.current, centerAzRef.current);
      }

      // 3. Resize canvas if container dimensions changed
      const rect = canvas.getBoundingClientRect();
      const canvasSizeChanged = canvas.width !== rect.width || canvas.height !== rect.height;
      if (canvasSizeChanged) {
        canvas.width = rect.width;
        canvas.height = rect.height;
      }

      // 3b. Skip the redraw entirely if nothing that affects the picture has
      // changed since the last frame we actually drew: the camera (az/alt/fov),
      // sidereal time (beyond LST_REDRAW_EPSILON_PIXELS at the current zoom), canvas size, and
      // every overlay-relevant prop are all unchanged.
      const lastDrawn = lastDrawnFrameStateRef.current;
      const framePixelsPerDegree = pixelsPerDegree(canvas.width, canvas.height, localFOVRef.current);
      const skyMoved = skyMovedEnoughToDraw(currentFrameLST, lastDrawn ? lastDrawn.lst : null, framePixelsPerDegree);

      // While the camera is locked on a tracked point it turns with the sky, so it needs the same
      // pixel tolerance as the sky itself. Otherwise it would force a draw on every frame.
      const cameraTolerance = trackingMode && trackingStateRef.current.lockedAnchor ? 0.25 / framePixelsPerDegree : 0;
      const cameraChanged =
        !lastDrawn ||
        Math.abs(centerAzRef.current - lastDrawn.centerAz) > cameraTolerance ||
        Math.abs(centerAltRef.current - lastDrawn.centerAlt) > cameraTolerance ||
        localFOVRef.current !== lastDrawn.fov ||
        skyMoved;

      if (!overlayInputsChangedRef.current && !cameraChanged && !canvasSizeChanged) {
        animFrameId = requestAnimationFrame(renderLoop);
        return;
      }

      context.clearRect(0, 0, canvas.width, canvas.height);

      // 4. Build ProjectionContext for overlay pipeline
      const projectionContext: ProjectionContext = {
        width: canvas.width,
        height: canvas.height,
        fov: localFOVRef.current,
        centerAz: centerAzRef.current,
        centerAlt: centerAltRef.current,
        lst: currentFrameLST,
        observerLat,
        observerLon,
        selectedTargetId,
        sources,
        targets,
        showStars,
        showFOV,
        showEnvironment,
        showGrid,
        showCatalog,
        showConstellations,
        constellationLines,
        trackingMode,
        showTelescope,
        telescopeConnected: telescopeConnectedRef.current,
        telescopeRa: telescopeRaRef.current,
        telescopeDec: telescopeDecRef.current,
        showAlignment,
        showTrackingRisk,
        alignmentTargets,
        trackingRisk,
        polarAlignment,
        selectedSessionId,
        framingPanels,
        sensorFovWidthDeg: sensorFovWidthDegRef.current,
        sensorFovHeightDeg: sensorFovHeightDegRef.current,
        projectCoords: (ra: number, dec: number) => {
          const { alt, az } = getAltAz(ra, dec, currentFrameLST, observerLat);
          const projection = projectAltAz(alt, az, centerAltRef.current, centerAzRef.current, localFOVRef.current, canvas.width, canvas.height);
          return { ...projection, alt };
        },
        getAltAz: (ra: number, dec: number) => getAltAz(ra, dec, currentFrameLST, observerLat),
        getRaDec: (alt: number, az: number) => getRaDec(alt, az, currentFrameLST, observerLat)
      };

      // 5. Execute overlay draw stack sequentially
      const overlayDrawStartedAt = perfStart();
      overlays.forEach(overlay => {
        try {
          overlay.draw(context, projectionContext);
        } catch (error) {
          console.error(`Failed to draw overlay '${overlay.id}':`, error);
        }
      });
      perfEnd('planetarium:overlay_draw', overlayDrawStartedAt);

      // 5b. Draw the WebGL star field on its own canvas, if available.
      if (starFieldRendererRef.current) {
        try {
          starFieldRendererRef.current.render(projectionContext);
        } catch (error) {
          console.error('Failed to render WebGL star field:', error);
        }
      }

      lastDrawnFrameStateRef.current = {
        centerAz: centerAzRef.current,
        centerAlt: centerAltRef.current,
        fov: localFOVRef.current,
        lst: currentFrameLST,
        canvasWidth: canvas.width,
        canvasHeight: canvas.height,
      };
      overlayInputsChangedRef.current = false;

      animFrameId = requestAnimationFrame(renderLoop);
    };

    animFrameId = requestAnimationFrame(renderLoop);
    return () => cancelAnimationFrame(animFrameId);
  }, [
    sources, targets, showStars, showFOV,
    showEnvironment, showGrid, showCatalog, showTelescope, selectedTargetId,
    showConstellations, constellationLines,
    showAlignment, showTrackingRisk, alignmentTargets, trackingRisk, polarAlignment, selectedSessionId,
    framingPanels,
    simulationDate,
    trackingMode, observerLat, observerLon, overlays, getOffsetMinutesAt
  ]);

  // Click handler to select sources. REQ: PLN-2.5
  const handleCanvasClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const nearest = findNearestSource({
      clickX: e.clientX - rect.left,
      clickY: e.clientY - rect.top,
      sources, targets, showStars, showCatalog, showEnvironment,
      showAlignment, alignmentTargets,
      canvasWidth: rect.width, canvasHeight: rect.height,
      fov: localFOVRef.current,
      centerAlt: centerAltRef.current,
      centerAz: centerAzRef.current,
      lst: currentLSTRef.current,
      observerLat,
    });
    if (nearest) {
      onSelectSource(nearest);
    }
  };

  // Context menu handler. REQ: PLN-2.5
  const handleCanvasContextMenu = (e: React.MouseEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    e.stopPropagation();
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const nearest = findNearestSource({
      clickX: e.clientX - rect.left,
      clickY: e.clientY - rect.top,
      sources, targets, showStars, showCatalog, showEnvironment,
      showAlignment, alignmentTargets,
      canvasWidth: rect.width, canvasHeight: rect.height,
      fov: localFOVRef.current,
      centerAlt: centerAltRef.current,
      centerAz: centerAzRef.current,
      lst: currentLSTRef.current,
      observerLat,
    });
    if (nearest) {
      onRightClickSource(nearest, { x: e.clientX - rect.left, y: e.clientY - rect.top });
    }
  };

  return (
    <div className="celestial-sky-map-container" ref={containerRef}>
      <canvas
        ref={canvasRef}
        tabIndex={0}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={onPointerUp}
        onClick={handleCanvasClick}
        onContextMenu={handleCanvasContextMenu}
        onWheel={onWheel}
        style={{ cursor: isDragging ? 'grabbing' : 'grab' }}
      />
      {/*
        Stacked above the main 2D canvas (not beneath it, despite drawing the
        "Star" layer): BackgroundOverlay paints an opaque full-canvas gradient
        as the first thing the main canvas draws, which would otherwise
        completely hide a star canvas placed underneath it. Pointer events
        pass through to the main canvas below, which still owns all drag/
        click/wheel interaction.
      */}
      <canvas ref={starCanvasRef} style={{ pointerEvents: 'none' }} />

      <div className="celestial-time-controller">
        <Button
          variant="secondary"
          size="small"
          active={isTimePlaying}
          aria-pressed={isTimePlaying}
          onClick={() => setIsTimePlaying(!isTimePlaying)}
          title="Play/Pause Sky Sidereal Rotation"
        >
          {isTimePlaying ? '⏸' : '▶'}
        </Button>

        {isTimePlaying && (
          <PopoverSelect
            label="Accelerated Time Speed"
            options={TIME_SPEED_OPTIONS}
            selectedId={String(timeSpeed)}
            onSelect={(id) => setTimeSpeed(Number(id ?? 1))}
            size="compact"
            placement="above"
          />
        )}

        <Slider
          className="celestial-time-slider"
          min="-720"
          max="720"
          value={timeOffsetMinutes}
          onChange={(e) => setTimeOffsetMinutes(Number(e.target.value))}
          title="Drag to Adjust Time Offset (Hours)"
        />
        <span className="celestial-time-label">
          {timeOffsetMinutes === 0 ? 'Live Time' : `${(timeOffsetMinutes / 60).toFixed(1)}h Offset`}
        </span>
      </div>
    </div>
  );
};
