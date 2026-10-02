/**
 * @module useSkyMapInput
 * @fileoverview Pointer-drag panning, mouse-wheel zoom, and arrow-key pan/zoom
 * input handling for CelestialSkyMap's canvas viewport.
 *
 * Extracted from CelestialSkyMap to separate raw input handling from the
 * render loop and camera/time-tracking orchestration. Operates directly on the
 * camera refs (rather than React state) so drags and key repeats don't trigger
 * a re-render on every event -- the render loop reads these refs each frame.
 */

import React, { useState, useRef, useEffect, useCallback, RefObject, MutableRefObject } from 'react';
import { pixelsPerDegree } from '../utils/projectionMath';

/** FOV zoom bounds, matching the render loop's own clamp. */
const MIN_FOV_DEG = 0.05;
const MAX_FOV_DEG = 135.0;

/**
 * Wraps an altitude/azimuth pair over the zenith or nadir instead of clamping
 * at it, so panning "up" past the top of the sky continues over the pole and
 * down the other side (azimuth rotates 180°), matching how a real observer's
 * gaze would continue rather than getting stuck facing straight up.
 *
 * @param {number} alt - Proposed altitude in degrees, may be outside [-90, 90].
 * @param {number} az - Azimuth in degrees to rotate 180° for each pole crossed.
 * @returns {{ alt: number; az: number }} Altitude reflected back into
 * [-90, 90] only if it actually crossed a pole (left untouched otherwise, so
 * approaching 90 from below doesn't get stopped short of it), and the azimuth
 * adjusted to match.
 */
function wrapOverPole(alt: number, az: number): { alt: number; az: number } {
  let wrappedAlt = alt;
  let wrappedAz = az;
  while (wrappedAlt > 90 || wrappedAlt < -90) {
    if (wrappedAlt > 90) {
      wrappedAlt = 180 - wrappedAlt;
    } else {
      wrappedAlt = -180 - wrappedAlt;
    }
    wrappedAz += 180;
  }
  // Only the exact pole itself is a projection singularity; nudge off of it
  // without otherwise capping how close a pan can approach it.
  if (wrappedAlt === 90) wrappedAlt = 89.9999;
  if (wrappedAlt === -90) wrappedAlt = -89.9999;
  wrappedAz = ((wrappedAz % 360) + 360) % 360;
  return { alt: wrappedAlt, az: wrappedAz };
}

/** Dependencies useSkyMapInput needs from CelestialSkyMap's camera state. */
export interface SkyMapInputRefs {
  canvasRef: RefObject<HTMLCanvasElement | null>;
  centerAzRef: MutableRefObject<number>;
  centerAltRef: MutableRefObject<number>;
  targetAzRef: MutableRefObject<number>;
  targetAltRef: MutableRefObject<number>;
  localFOVRef: MutableRefObject<number>;
  /** Disables continuous tracking on any manual pan/zoom input. */
  setTrackingMode: (value: boolean) => void;
  /** Debounced notification back to the parent when FOV changes via wheel/keys. */
  notifyParentFOV: (nextFOV: number) => void;
}

/** Return shape of useSkyMapInput: pointer/wheel event handlers plus drag-visual state. */
export interface SkyMapInput {
  isDragging: boolean;
  onPointerDown: (e: React.PointerEvent) => void;
  onPointerMove: (e: React.PointerEvent) => void;
  onPointerUp: (e: React.PointerEvent) => void;
  onWheel: (e: React.WheelEvent) => void;
}

/**
 * Wires up drag-to-pan, wheel-to-zoom, and arrow-key pan/zoom for the sky map canvas.
 *
 * @func useSkyMapInput
 * @param {SkyMapInputRefs} refs - Camera refs and callbacks this hook reads/writes.
 * @returns {SkyMapInput} Pointer/wheel handlers and drag-visual state for the canvas element.
 */
export const useSkyMapInput = ({
  canvasRef, centerAzRef, centerAltRef, targetAzRef, targetAltRef, localFOVRef,
  setTrackingMode, notifyParentFOV,
}: SkyMapInputRefs): SkyMapInput => {
  const [isDragging, setIsDragging] = useState(false);
  const isDraggingRef = useRef(false);
  const dragStart = useRef({ x: 0, y: 0 });
  const dragLast = useRef({ x: 0, y: 0 });
  const dragCenterStart = useRef({ az: 0, alt: 0 });

  /**
   * Begins a canvas drag operation, capturing the pointer and disabling tracking mode.
   *
   * @param {React.PointerEvent} e - The pointer down event.
   * @returns {void}
   */
  const onPointerDown = useCallback((e: React.PointerEvent) => {
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
  }, [centerAzRef, centerAltRef, targetAzRef, targetAltRef, setTrackingMode]);

  /**
   * Pans the viewport by converting pointer delta to Alt/Az coordinate offsets.
   *
   * @param {React.PointerEvent} e - The pointer move event.
   * @returns {void}
   */
  const onPointerMove = useCallback((e: React.PointerEvent) => {
    if (!isDraggingRef.current) return;
    const dx = e.clientX - dragLast.current.x;
    const dy = e.clientY - dragLast.current.y;
    dragLast.current = { x: e.clientX, y: e.clientY };

    const canvas = canvasRef.current;
    if (!canvas) return;

    const scale = pixelsPerDegree(canvas.width, canvas.height, localFOVRef.current);

    const dAlt = dy / scale;
    const dAz = -(dx / scale);

    const wrapped = wrapOverPole(targetAltRef.current + dAlt, targetAzRef.current + dAz);

    targetAzRef.current = wrapped.az;
    targetAltRef.current = wrapped.alt;
  }, [canvasRef, localFOVRef, targetAzRef, targetAltRef]);

  /**
   * Ends a canvas drag operation and releases pointer capture.
   *
   * @param {React.PointerEvent} e - The pointer up event.
   * @returns {void}
   */
  const onPointerUp = useCallback((e: React.PointerEvent) => {
    isDraggingRef.current = false;
    setIsDragging(false);
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch (err) {
      // Ignore releasePointerCapture failures
    }
  }, []);

  /**
   * Adjusts field of view via mouse wheel, clamped to MIN_FOV_DEG-MAX_FOV_DEG.
   *
   * @param {React.WheelEvent} e - The wheel event.
   * @returns {void}
   */
  const onWheel = useCallback((e: React.WheelEvent) => {
    const delta = e.deltaY < 0 ? 0.85 : 1.15;
    const nextFOV = Math.max(MIN_FOV_DEG, Math.min(MAX_FOV_DEG, localFOVRef.current * delta));

    localFOVRef.current = nextFOV;
    notifyParentFOV(nextFOV);
  }, [localFOVRef, notifyParentFOV]);

  // Keyboard listener for arrow key pan (plain arrows) and zoom (Ctrl+Up/Down)
  useEffect(() => {
    const handleArrowKey = (e: KeyboardEvent) => {
      const active = document.activeElement;
      if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA')) return;

      if (e.ctrlKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
        e.preventDefault();
        const delta = e.key === 'ArrowUp' ? 0.85 : 1.15;
        const nextFOV = Math.max(MIN_FOV_DEG, Math.min(MAX_FOV_DEG, localFOVRef.current * delta));
        localFOVRef.current = nextFOV;
        notifyParentFOV(nextFOV);
        return;
      }

      // Pan step is 10% of current FOV so it feels proportional at any zoom level
      const panStep = localFOVRef.current * 0.1;

      switch (e.key) {
        case 'ArrowUp': {
          e.preventDefault();
          setTrackingMode(false);
          const wrapped = wrapOverPole(targetAltRef.current + panStep, targetAzRef.current);
          targetAltRef.current = wrapped.alt;
          targetAzRef.current = wrapped.az;
          break;
        }
        case 'ArrowDown': {
          e.preventDefault();
          setTrackingMode(false);
          const wrapped = wrapOverPole(targetAltRef.current - panStep, targetAzRef.current);
          targetAltRef.current = wrapped.alt;
          targetAzRef.current = wrapped.az;
          break;
        }
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
  }, [notifyParentFOV, localFOVRef, targetAzRef, targetAltRef, setTrackingMode]);

  return { isDragging, onPointerDown, onPointerMove, onPointerUp, onWheel };
};
