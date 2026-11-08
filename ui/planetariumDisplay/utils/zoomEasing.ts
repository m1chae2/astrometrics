/**
 * @fileoverview Zoom easing and parent-notification rules for the Planetarium sky map.
 *
 * A mouse-wheel tick or Ctrl+arrow key press only moves a zoom target. The render
 * loop then eases the drawn field of view (FOV) toward that target a little each
 * frame, the same way the pan eases. The parent React component is told about the
 * FOV only when the zoom settles, or at a slow rhythm during a long gesture, so
 * state updates and catalog requests do not run in the middle of the animation.
 */

/** FOV zoom bounds, matching the render loop's own clamp. */
export const MIN_FOV_DEG = 0.05;
export const MAX_FOV_DEG = 135.0;

/** FOV multiplier for one wheel tick or key press when zooming in. */
export const ZOOM_IN_FACTOR = 0.85;

/** FOV multiplier for one wheel tick or key press when zooming out. */
export const ZOOM_OUT_FACTOR = 1.15;

/**
 * Share of the remaining zoom (in log scale) covered each frame. 0.25 closes half the
 * gap in about two to three frames, a little snappier than the 0.15 used for panning.
 */
const ZOOM_EASE_FRACTION = 0.25;

/** The drawn FOV snaps to its target once it is within this ratio of it (0.1 percent). */
const ZOOM_SETTLE_RATIO = 0.001;

/**
 * The shortest time, in milliseconds, between two FOV notifications to the parent
 * during one long zoom gesture. Chosen by judgement. It is shorter than
 * LOCAL_CATALOG_QUERY_DEBOUNCE_MS, so each notification restarts the request wait
 * and no catalog request starts in the middle of a gesture.
 */
export const FOV_NOTIFY_INTERVAL_MS = 200;

/**
 * Clamps a FOV to the allowed zoom range.
 *
 * @param {number} fovDegrees - Proposed FOV in degrees.
 * @returns {number} The FOV limited to MIN_FOV_DEG..MAX_FOV_DEG.
 */
export function clampFov(fovDegrees: number): number {
  return Math.max(MIN_FOV_DEG, Math.min(MAX_FOV_DEG, fovDegrees));
}

/**
 * Applies one zoom step to a target FOV. Several steps before the render loop
 * runs accumulate, because each one starts from the previous target.
 *
 * @param {number} targetFov - The current zoom target in degrees.
 * @param {boolean} zoomIn - True to zoom in (smaller FOV), false to zoom out.
 * @returns {number} The new zoom target in degrees, clamped to the zoom range.
 */
export function nextZoomTarget(targetFov: number, zoomIn: boolean): number {
  return clampFov(targetFov * (zoomIn ? ZOOM_IN_FACTOR : ZOOM_OUT_FACTOR));
}

/**
 * Moves the drawn FOV one frame toward its target, in log scale so zooming in
 * and out feel the same.
 *
 * @param {number} currentFov - The FOV drawn last frame, in degrees.
 * @param {number} targetFov - The zoom target in degrees.
 * @returns {{ fov: number; settled: boolean }} The FOV for this frame and whether it has reached the target.
 */
export function easeFovTowardTarget(currentFov: number, targetFov: number): { fov: number; settled: boolean } {
  const ratio = targetFov / currentFov;
  if (Math.abs(ratio - 1) <= ZOOM_SETTLE_RATIO) {
    return { fov: targetFov, settled: true };
  }
  return { fov: currentFov * Math.pow(ratio, ZOOM_EASE_FRACTION), settled: false };
}

/**
 * Decides when the parent should hear about the FOV: once when the zoom settles,
 * and at most once per FOV_NOTIFY_INTERVAL_MS while it is still moving.
 */
export class FovNotifyGate {
  private lastNotifiedFov: number | null;
  private lastNotifiedAtMs = Number.NEGATIVE_INFINITY;

  /**
   * @param {number | null} initialFov - The FOV the parent already knows, if any.
   */
  constructor(initialFov: number | null = null) {
    this.lastNotifiedFov = initialFov;
  }

  /**
   * Reports the FOV drawn this frame and says whether to tell the parent.
   *
   * @param {number} fov - The FOV drawn this frame, in degrees.
   * @param {boolean} settled - Whether the FOV has reached its target.
   * @param {number} nowMs - The current time in milliseconds.
   * @returns {number | null} The FOV to send to the parent, or null to stay quiet.
   */
  update(fov: number, settled: boolean, nowMs: number): number | null {
    if (fov === this.lastNotifiedFov) return null;
    if (settled) {
      this.lastNotifiedFov = fov;
      // The next gesture starts its own interval clock.
      this.lastNotifiedAtMs = Number.NEGATIVE_INFINITY;
      return fov;
    }
    if (this.lastNotifiedAtMs === Number.NEGATIVE_INFINITY) {
      // First moving frame of a gesture: start the clock, say nothing yet.
      this.lastNotifiedAtMs = nowMs;
      return null;
    }
    if (nowMs - this.lastNotifiedAtMs >= FOV_NOTIFY_INTERVAL_MS) {
      this.lastNotifiedFov = fov;
      this.lastNotifiedAtMs = nowMs;
      return fov;
    }
    return null;
  }

  /**
   * The FOV most recently sent to the parent, so an echo of it from the parent's
   * props can be told apart from a real outside change.
   *
   * @returns {number | null} The last notified FOV, or null if none was sent.
   */
  get lastNotified(): number | null {
    return this.lastNotifiedFov;
  }

  /**
   * Records a FOV that came from outside (the parent set it), so it is not echoed back.
   *
   * @param {number} fov - The FOV the parent chose, in degrees.
   * @returns {void}
   */
  adoptExternal(fov: number): void {
    this.lastNotifiedFov = fov;
  }
}
