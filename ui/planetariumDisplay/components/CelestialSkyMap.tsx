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
import { safeParse } from '../utils/coordinateUtils';
import { findNearestSource } from '../utils/hitTesting';
import { FitsLoaderItem, LoadedFitsEntry } from './FitsLoaderItem';
import { useTelescopeStatus } from '../../common/hooks/useTelescopeStatus';
import type { ProjectionContext } from '../layers/overlayTypes';
import { BackgroundOverlay } from '../layers/BackgroundOverlay';
import { StarOverlay } from '../layers/StarOverlay';
import { StarSelectionOverlay } from '../layers/StarSelectionOverlay';
import { TargetOverlay } from '../layers/TargetOverlay';
import { ImageOverlay } from '../layers/ImageOverlay';
import { EnvironmentOverlay } from '../layers/EnvironmentOverlay';
import { GridOverlay } from '../layers/GridOverlay';
import { FovOverlay } from '../layers/FovOverlay';
import { TelescopeOverlay } from '../layers/TelescopeOverlay';
import { CompassOverlay } from '../layers/CompassOverlay';
import { HudOverlay } from '../layers/HudOverlay';
import { ConstellationOverlay } from '../layers/ConstellationOverlay';
import { StarFieldRenderer } from '../webgl/StarFieldRenderer';

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
  /** Show FITS image overlays. */
  showFITS: boolean;
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
  /** Callback invoked (throttled) when the viewport center changes during pan. */
  onCenterChange: (ra: number, dec: number) => void;
  /** Sensor FOV width in degrees from the active equipment configuration. */
  sensorFovWidthDeg?: number;
  /** Sensor FOV height in degrees from the active equipment configuration. */
  sensorFovHeightDeg?: number;
}


/**
 * Calculates the Local Sidereal Time (LST) for a given time offset and longitude.
 *
 * The base time is always the current system clock, with an offset applied for
 * simulation mode. The GMST formula follows the IAU 1982 standard.
 *
 * @param {number} offsetMinutes - Time offset from system clock in minutes (0 = live time).
 * @param {number} lon - Observer longitude in degrees (negative = west).
 * @returns {number} Local Sidereal Time in degrees (0–360).
 */
// How much Local Sidereal Time must drift since the last drawn frame before
// that drift alone is worth a redraw, at a wide FOV. 0.001 degrees corresponds
// to roughly a quarter of a real second at 1x live-time speed — imperceptible
// to the eye, so idle "live time" viewing redraws only a few times a second
// instead of every rAF tick. During fast time-scrubbing (up to 1000x) LST
// advances much faster per frame, so this same threshold is crossed on nearly
// every tick, restoring full-rate smoothness automatically with no special-casing.
const LST_REDRAW_EPSILON_DEGREES = 0.001;

// How many screen pixels a source may drift from sidereal motion alone before
// that drift is worth a redraw, independent of zoom. A fixed *degrees*
// threshold alone is calibrated against wall-clock time only: at a wide FOV
// 0.001° is sub-pixel and invisible, but at a tight zoom (small FOV, large
// pixels-per-degree) that same angular drift is many pixels — enough to make
// an idle, untracked view visibly jump/flicker at only ~4 redraws/second
// instead of updating smoothly. The actual redraw threshold below takes
// whichever of the two (degrees or pixel-equivalent) is smaller, so wide-FOV
// idle viewing keeps its original ~4Hz cadence while tight zooms redraw every
// frame once sidereal drift exceeds this many pixels.
const LST_REDRAW_EPSILON_PIXELS = 1.5;

const calculateLST = (offsetMinutes: number, lon: number): number => {
  const baseDate = new Date();
  const date = new Date(baseDate.getTime() + offsetMinutes * 60 * 1000);

  const jd2000 = 2451545.0;
  const currentJd = (date.getTime() / 86400000.0) + 2440587.5;
  const d = currentJd - jd2000;
  let gmst = 18.697374558 + 24.06570982441908 * d;
  gmst = (gmst % 24.0 + 24.0) % 24.0;

  return (gmst * 15.0 + lon + 360.0) % 360.0;
};

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
  showFITS,
  showEnvironment,
  showGrid,
  showCatalog,
  showConstellations,
  constellationLines,
  showTelescope,
  fov,
  onFOVChange,
  onSelectSource,
  onRightClickSource,
  selectedTargetId,
  slewRequestId,
  onCenterChange,
  sensorFovWidthDeg,
  sensorFovHeightDeg,
}) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const starCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

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
    new ImageOverlay(),
    new GridOverlay(),
    new ConstellationOverlay(),
    new CompassOverlay(),
    new TargetOverlay(),
    starRenderMode === 'webgl' ? new StarSelectionOverlay() : new StarOverlay(),
    new FovOverlay(),
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

  // Drag interaction
  const [isDragging, setIsDragging] = useState(false);
  const isDraggingRef = useRef(false);
  const dragStart = useRef({ x: 0, y: 0 });
  const dragLast = useRef({ x: 0, y: 0 });
  const dragCenterStart = useRef({ az: 0, alt: 0 });

  // Time & LST state
  const [timeOffsetMinutes, setTimeOffsetMinutes] = useState<number>(0);
  const timeOffsetMinutesRef = useRef<number>(0);
  useEffect(() => {
    timeOffsetMinutesRef.current = timeOffsetMinutes;
  }, [timeOffsetMinutes]);
  const [isTimePlaying, setIsTimePlaying] = useState<boolean>(false);
  const [timeSpeed, setTimeSpeed] = useState<number>(1);
  const [trackingMode, setTrackingMode] = useState<boolean>(false);
  const trackedCoords = useRef<{ ra: number; dec: number } | null>(null);

  // Sync tracked coordinates when center RA/Dec is set by parent
  useEffect(() => {
    if (center) {
      trackedCoords.current = { ra: safeParse(center.ra), dec: safeParse(center.dec) };
    }
  }, [center]);

  const observerLat = safeParse(location?.latitude ?? 39.7392);
  const observerLon = safeParse(location?.longitude ?? -104.9903);

  const currentLST = calculateLST(timeOffsetMinutes, observerLon);

  // Kept in sync so the 't' keydown handler (subscribed once) can read the
  // latest selection without recreating the window listener on every change.
  const selectedTargetIdRef = useRef<string>(selectedTargetId);
  useEffect(() => {
    selectedTargetIdRef.current = selectedTargetId;
  }, [selectedTargetId]);

  // Keyboard listener to toggle tracking mode on 't' keypress.
  // Engaging tracking requires a star or target to already be selected —
  // selecting one only recenters the view, it never auto-engages tracking.
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

  // Dynamic Sidereal Time play interval loop
  useEffect(() => {
    if (!isTimePlaying) return;
    const interval = setInterval(() => {
      setTimeOffsetMinutes(prev => prev + (timeSpeed * 0.1));
    }, 100);
    return () => clearInterval(interval);
  }, [isTimePlaying, timeSpeed]);



  // Keep LST ref in sync
  useEffect(() => {
    currentLSTRef.current = currentLST;
  }, [currentLST]);


  // Initial camera orientation. Slews to the pre-selected object (if any) so a
  // restored session opens looking at it, but never auto-engages continuous
  // tracking — tracking is always a deliberate, separate choice made with the
  // 't' key, whether the selection came from a restored session or a fresh one.
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
  // once the user opts into tracking with the 't' key. This only sets the
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
    }
    previousSlewRequestIdRef.current = slewRequestId;
  }, [slewRequestId, center, observerLat]);

  // Throttled notification handler for coordinate changes
  const parentCenterNotifyTimeout = useRef<number | null>(null);
  const throttledNotifyParentCenter = useCallback((alt: number, az: number) => {
    if (parentCenterNotifyTimeout.current !== null) {
      window.clearTimeout(parentCenterNotifyTimeout.current);
    }
    // See notifyParentFOV above — same debounce-resets-on-every-tick shape,
    // shortened for the same reason (catalogSourceCache made settling cheap).
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

  const [loadedFits, setLoadedFits] = useState<Record<string, LoadedFitsEntry>>({});

  // Targets (with a stackedImage) currently on screen -- only these get a
  // FitsLoaderItem mounted. Recomputed inside the render loop below (see
  // "Determine which stacked-image targets are on screen"), gated on the
  // camera/overlay-inputs redraw check so it doesn't run every frame, and
  // only committed to state when the set actually changes so it doesn't
  // trigger a React re-render on every redrawn frame either. Without this,
  // showFITS mounted a FitsLoaderItem for every target the (up to
  // near-whole-sky) source query returned -- confirmed to spawn dozens of
  // concurrent Web Worker FITS parses all racing to draw onto
  // FitsLoaderItem's single shared offscreen MTF-stretch canvas, corrupting
  // each other's output.
  const [visibleFitsTargetIds, setVisibleFitsTargetIds] = useState<Set<string>>(new Set());
  const visibleFitsTargetIdsRef = useRef<Set<string>>(new Set());

  const handleFitsLoaded = useCallback((id: string, entry: LoadedFitsEntry) => {
    setLoadedFits(prev => ({ ...prev, [id]: entry }));
  }, []);

  /**
   * Begins a canvas drag operation, capturing the pointer and disabling tracking mode.
   *
   * @param {React.PointerEvent} e - The pointer down event.
   * @returns {void}
   */
  const onPointerDown = (e: React.PointerEvent) => {
    isDraggingRef.current = true;
    setIsDragging(true);
    setTrackingMode(false);
    // Cancel any residual recenter-on-selection easing so the drag starts from
    // where the camera actually is, not from the still-in-flight animation
    // target — otherwise the pan fights the leftover lerp and feels locked
    // back toward the selected object.
    targetAzRef.current = centerAzRef.current;
    targetAltRef.current = centerAltRef.current;
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch (err) {
      // Ignore setPointerCapture failures
    }
    dragStart.current = { x: e.clientX, y: e.clientY };
    dragLast.current = { x: e.clientX, y: e.clientY };
    dragCenterStart.current = { az: centerAzRef.current, alt: centerAltRef.current };
  };

  /**
   * Pans the viewport by converting pointer delta to Alt/Az coordinate offsets.
   *
   * @param {React.PointerEvent} e - The pointer move event.
   * @returns {void}
   */
  const onPointerMove = (e: React.PointerEvent) => {
    if (!isDraggingRef.current) return;
    const dx = e.clientX - dragLast.current.x;
    const dy = e.clientY - dragLast.current.y;
    dragLast.current = { x: e.clientX, y: e.clientY };

    const canvas = canvasRef.current;
    if (!canvas) return;

    const scale = pixelsPerDegree(canvas.width, canvas.height, localFOVRef.current);

    const dAlt = dy / scale;
    const dAz = -(dx / scale);

    const nextAlt = Math.max(-89.9, Math.min(89.9, targetAltRef.current + dAlt));
    const nextAz = (targetAzRef.current + dAz + 360.0) % 360.0;

    targetAzRef.current = nextAz;
    targetAltRef.current = nextAlt;
  };

  /**
   * Ends a canvas drag operation and releases pointer capture.
   *
   * @param {React.PointerEvent} e - The pointer up event.
   * @returns {void}
   */
  const onPointerUp = (e: React.PointerEvent) => {
    isDraggingRef.current = false;
    setIsDragging(false);
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch (err) {
      // Ignore releasePointerCapture failures
    }
  };

  // Sync local FOV and sensor FOV refs
  useEffect(() => {
    localFOVRef.current = fov;
  }, [fov]);

  useEffect(() => {
    sensorFovWidthDegRef.current = sensorFovWidthDeg;
  }, [sensorFovWidthDeg]);

  useEffect(() => {
    sensorFovHeightDegRef.current = sensorFovHeightDeg;
  }, [sensorFovHeightDeg]);

  const parentNotifyTimeout = useRef<number | null>(null);
  const notifyParentFOV = useCallback((nextFOV: number) => {
    if (parentNotifyTimeout.current !== null) {
      window.clearTimeout(parentNotifyTimeout.current);
    }
    // Resets on every wheel/key tick, so the source-fetching hooks
    // (queryRadius -> useOnlineCatalogSources/usePlanetariumSources) never see
    // the new FOV until ticks stop arriving faster than this delay. 80ms was
    // tuned back when every settle meant a real backend fetch; now that
    // catalogSourceCache usually resolves settles with no network call, one
    // rAF frame's worth of coalescing is enough to avoid over-firing.
    parentNotifyTimeout.current = window.setTimeout(() => {
      onFOVChange(nextFOV);
      parentNotifyTimeout.current = null;
    }, 16);
  }, [onFOVChange]);

  useEffect(() => {
    return () => {
      if (parentNotifyTimeout.current !== null) {
        window.clearTimeout(parentNotifyTimeout.current);
      }
    };
  }, []);

  // Keyboard listener for arrow key pan (plain arrows) and zoom (Ctrl+Up/Down)
  useEffect(() => {
    const handleArrowKey = (e: KeyboardEvent) => {
      const active = document.activeElement;
      if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA')) return;

      if (e.ctrlKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
        e.preventDefault();
        const delta = e.key === 'ArrowUp' ? 0.85 : 1.15;
        const nextFOV = Math.max(0.05, Math.min(135.0, localFOVRef.current * delta));
        localFOVRef.current = nextFOV;
        notifyParentFOV(nextFOV);
        return;
      }

      // Pan step is 10% of current FOV so it feels proportional at any zoom level
      const panStep = localFOVRef.current * 0.1;

      switch (e.key) {
        case 'ArrowUp':
          e.preventDefault();
          setTrackingMode(false);
          targetAltRef.current = Math.max(-89.9, Math.min(89.9, targetAltRef.current + panStep));
          break;
        case 'ArrowDown':
          e.preventDefault();
          setTrackingMode(false);
          targetAltRef.current = Math.max(-89.9, Math.min(89.9, targetAltRef.current - panStep));
          break;
        case 'ArrowLeft':
          e.preventDefault();
          setTrackingMode(false);
          targetAzRef.current = (targetAzRef.current - panStep + 360) % 360;
          break;
        case 'ArrowRight':
          e.preventDefault();
          setTrackingMode(false);
          targetAzRef.current = (targetAzRef.current + panStep + 360) % 360;
          break;
      }
    };

    window.addEventListener('keydown', handleArrowKey);
    return () => window.removeEventListener('keydown', handleArrowKey);
  }, [notifyParentFOV]);

  /**
   * Adjusts field of view via mouse wheel, clamped to 0.05°–135°.
   *
   * @param {React.WheelEvent} e - The wheel event.
   * @returns {void}
   */
  const onWheel = (e: React.WheelEvent) => {
    const delta = e.deltaY < 0 ? 0.85 : 1.15;
    const nextFOV = Math.max(0.05, Math.min(135.0, localFOVRef.current * delta));

    localFOVRef.current = nextFOV;
    notifyParentFOV(nextFOV);
  };

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
      // 1. Advance tracking target position for current sidereal time
      const currentFrameLST = calculateLST(timeOffsetMinutesRef.current, observerLon);

      if (trackingMode && trackedCoords.current) {
        const targetAltAz = getAltAz(trackedCoords.current.ra, trackedCoords.current.dec, currentFrameLST, observerLat);
        targetAzRef.current = targetAltAz.az;
        targetAltRef.current = targetAltAz.alt;
      }

      // 2. Apply linear damping to smooth viewport pan transitions
      // 0.15 damping factor gives ~5 frames to cover 50% of remaining distance at 60fps
      let deltaAltitude = targetAltRef.current - centerAltRef.current;
      let deltaAzimuth = targetAzRef.current - centerAzRef.current;
      if (deltaAzimuth > 180) deltaAzimuth -= 360;
      if (deltaAzimuth < -180) deltaAzimuth += 360;

      const deltaThreshold = 0.001;
      const needsMovement = Math.abs(deltaAltitude) > deltaThreshold || Math.abs(deltaAzimuth) > deltaThreshold;

      if (needsMovement) {
        centerAltRef.current += deltaAltitude * 0.15;
        centerAzRef.current = (centerAzRef.current + deltaAzimuth * 0.15 + 360) % 360;

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
      // sidereal time (beyond whichever of LST_REDRAW_EPSILON_DEGREES or the
      // current zoom's LST_REDRAW_EPSILON_PIXELS is smaller), canvas size, and
      // every overlay-relevant prop are all unchanged.
      const lastDrawn = lastDrawnFrameStateRef.current;
      let lstDeltaDegrees = lastDrawn ? Math.abs(currentFrameLST - lastDrawn.lst) : Infinity;
      if (lstDeltaDegrees > 180) lstDeltaDegrees = 360 - lstDeltaDegrees;

      const lstEpsilonPixelsAsDegrees =
        LST_REDRAW_EPSILON_PIXELS / pixelsPerDegree(canvas.width, canvas.height, localFOVRef.current);
      const lstEpsilonDegrees = Math.min(LST_REDRAW_EPSILON_DEGREES, lstEpsilonPixelsAsDegrees);

      const cameraChanged =
        !lastDrawn ||
        centerAzRef.current !== lastDrawn.centerAz ||
        centerAltRef.current !== lastDrawn.centerAlt ||
        localFOVRef.current !== lastDrawn.fov ||
        lstDeltaDegrees > lstEpsilonDegrees;

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
        loadedFits,
        sources,
        targets,
        showStars,
        showFOV,
        showFITS,
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

      // Determine which stacked-image targets are on screen, so only those
      // get a FitsLoaderItem mounted below (see visibleFitsTargetIds above).
      // Only recomputed on a real redraw (this point is unreached otherwise,
      // per the cameraChanged/overlayInputsChanged early-return above), and
      // only committed to state when the set of IDs actually changed.
      if (showFITS) {
        const nextVisibleFitsTargetIds = new Set<string>();
        targets.forEach(target => {
          if (!target.stackedImage) return;
          if (target.ra === 0 && target.dec === 0) return;
          if (projectionContext.projectCoords(target.ra, target.dec).visible) {
            nextVisibleFitsTargetIds.add(target.id);
          }
        });
        const previousIds = visibleFitsTargetIdsRef.current;
        const idsChanged =
          nextVisibleFitsTargetIds.size !== previousIds.size ||
          [...nextVisibleFitsTargetIds].some(id => !previousIds.has(id));
        if (idsChanged) {
          visibleFitsTargetIdsRef.current = nextVisibleFitsTargetIds;
          setVisibleFitsTargetIds(nextVisibleFitsTargetIds);
        }
      } else if (visibleFitsTargetIdsRef.current.size > 0) {
        visibleFitsTargetIdsRef.current = new Set();
        setVisibleFitsTargetIds(new Set());
      }

      // 5. Execute overlay draw stack sequentially
      overlays.forEach(overlay => {
        try {
          overlay.draw(context, projectionContext);
        } catch (error) {
          console.error(`Failed to draw overlay '${overlay.id}':`, error);
        }
      });

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
    sources, targets, showStars, showFOV, showFITS,
    showEnvironment, showGrid, showCatalog, showTelescope, selectedTargetId,
    showConstellations, constellationLines,
    loadedFits, trackingMode, observerLat, observerLon, overlays
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
      canvasWidth: rect.width, canvasHeight: rect.height,
      fov: localFOVRef.current,
      centerAlt: centerAltRef.current,
      centerAz: centerAzRef.current,
      lst: currentLSTRef.current,
      observerLat,
    });
    onSelectSource(nearest);
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
        <button
          className={`celestial-time-btn ${isTimePlaying ? 'active' : ''}`}
          onClick={() => setIsTimePlaying(!isTimePlaying)}
          title="Play/Pause Sky Sidereal Rotation"
        >
          {isTimePlaying ? '⏸' : '▶'}
        </button>

        {isTimePlaying && (
          <select
            className="celestial-time-select"
            value={timeSpeed}
            onChange={(e) => setTimeSpeed(Number(e.target.value))}
            title="Accelerated Time Speed"
          >
            <option value="1">1x</option>
            <option value="5">5x</option>
            <option value="25">25x</option>
            <option value="100">100x</option>
            <option value="1000">1000x</option>
          </select>
        )}

        <input
          type="range"
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

      {showFITS && targets.map(target => (
        target.stackedImage && visibleFitsTargetIds.has(target.id) ? (
          <FitsLoaderItem
            key={target.id}
            target={target}
            onLoaded={handleFitsLoaded}
          />
        ) : null
      ))}
    </div>
  );
};
