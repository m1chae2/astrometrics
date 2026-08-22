/**
 * @module StarOverlay
 * @fileoverview Renders database and online-catalog star points on the Planetarium canvas viewport.
 */

import { PlanetariumOverlay, ProjectionContext } from './overlayTypes';

/**
 * Radius of the selection reticle circle drawn around a selected star, in pixels.
 * Exported so StarSelectionOverlay (drawn instead of this overlay's own reticle
 * when the WebGL StarFieldRenderer path is active) stays visually identical.
 */
export const STAR_RETICLE_RADIUS_PX = 10;

/**
 * Rendered radius, in pixels, of every visible star marker. Deliberately a
 * single fixed size, not derived from magnitude or zoom: a real star has no
 * meaningful angular size, so there is no motivating case for a marker to
 * render larger than any other. Magnitude instead controls whether a star
 * is drawn at all -- see computeLimitingMagnitude -- which is the
 * telescope-vs-naked-eye distinction this whole feature is modeling:
 * brighter stars aren't bigger, they're just visible sooner as you zoom in.
 *
 * Floored at 2.0 (gl_PointSize 4px in StarFieldRenderer's shader, the
 * primary rendering path): confirmed visually that a smaller point sprite
 * (tried 1.25, gl_PointSize 2.5px) flickers in apparent position and
 * brightness while panning -- WebGL point-sprite rasterization samples too
 * few pixels per point below roughly this size for sub-pixel screen-position
 * changes to blend smoothly, unlike the 2D canvas fallback's analytic
 * context.arc() antialiasing, which doesn't have this failure mode.
 */
export const STAR_MARKER_RADIUS_PX = 2.0;

/** Opacity floor for the faintest displayed stars -- never fully invisible, since a star at this floor already passed the limiting-magnitude cutoff to be shown at all. */
export const STAR_MIN_BRIGHTNESS = 0.35;

/** Opacity ceiling, applied to magnitude ≤ 0 stars. */
export const STAR_MAX_BRIGHTNESS = 1.0;

/**
 * Opacity decay per magnitude step. A gentle visual gradient tuned for
 * legibility across the visible range, not the true Pogson photometric
 * flux ratio (100^(1/5) ≈ 2.512x per magnitude, decay ≈ 0.4) -- applying
 * the literal physical ratio makes everything past a few magnitudes
 * fainter than the brightest visible star crush to the opacity floor,
 * losing the gradient this feature exists to show.
 */
export const STAR_BRIGHTNESS_DECAY_PER_MAG = 0.15;

/**
 * Computes a star's rendered opacity from its magnitude alone, using the
 * same curve StarOverlay and StarFieldRenderer both need to agree on.
 * Brightness (not size, see STAR_MARKER_RADIUS_PX above) is how relative
 * magnitude is shown among the stars currently visible.
 *
 * @param {number} magnitude - The star's apparent magnitude.
 * @returns {number} Opacity in [STAR_MIN_BRIGHTNESS, STAR_MAX_BRIGHTNESS].
 */
export function computeStarBrightness(magnitude: number): number {
  return Math.max(
    STAR_MIN_BRIGHTNESS,
    Math.min(STAR_MAX_BRIGHTNESS, STAR_MAX_BRIGHTNESS * Math.pow(10, -STAR_BRIGHTNESS_DECAY_PER_MAG * magnitude)),
  );
}

/**
 * Faintest magnitude visible at the app's widest FOV (naked-eye-like
 * overview) -- close to true dark-sky naked-eye limiting magnitude (~6.5),
 * so a wide overview shows essentially everything visible to the unaided
 * eye rather than only the brightest named stars.
 */
export const NAKED_EYE_LIMITING_MAGNITUDE = 6.0;

/**
 * Faintest magnitude visible at the app's narrowest useful zoom. Past the
 * Hipparcos catalog's own effective faint completeness limit (~magnitude
 * 12), so reaching this depth requires the GAIA DR3 online catalog (see
 * GaiaCatalogDriver) -- Hipparcos alone can't supply anything fainter than
 * ~12.
 */
export const MAX_ZOOM_LIMITING_MAGNITUDE = 22.0;

/** FOV, in degrees, at which the naked-eye limiting magnitude applies. Matches the app's documented default FOV. */
export const NAKED_EYE_REFERENCE_FOV_DEG = 90.0;

/** FOV, in degrees, at which the full catalog-depth limiting magnitude applies. */
export const MAX_ZOOM_REFERENCE_FOV_DEG = 0.5;

/**
 * Computes the faintest star magnitude that should be visible at the
 * current FOV -- the telescope-vs-naked-eye analogy: a wide, zoomed-out
 * view shows only the brightest stars, and progressively fainter stars
 * reveal themselves as the view narrows, the same way a telescope reveals
 * stars invisible to the naked eye. Interpolated log-linearly in FOV (not
 * linearly), since the app's FOV range spans nearly three orders of
 * magnitude (0.05-135 deg) and magnitude itself is already a log scale of
 * brightness.
 *
 * @param {number} fovDegrees - Current viewport field of view, in degrees.
 * @returns {number} The faintest magnitude that should be displayed; stars fainter than this are hidden.
 */
export function computeLimitingMagnitude(fovDegrees: number): number {
  const clampedFov = Math.max(MAX_ZOOM_REFERENCE_FOV_DEG, Math.min(NAKED_EYE_REFERENCE_FOV_DEG, fovDegrees));
  const zoomFraction =
    (Math.log10(NAKED_EYE_REFERENCE_FOV_DEG) - Math.log10(clampedFov)) /
    (Math.log10(NAKED_EYE_REFERENCE_FOV_DEG) - Math.log10(MAX_ZOOM_REFERENCE_FOV_DEG));
  return (
    NAKED_EYE_LIMITING_MAGNITUDE + zoomFraction * (MAX_ZOOM_LIMITING_MAGNITUDE - NAKED_EYE_LIMITING_MAGNITUDE)
  );
}

/**
 * Determines whether a source qualifies as a displayable star point, applying
 * the same catalog-routing rule StarOverlay and StarFieldRenderer both need
 * to agree on: Hipparcos and GAIA stars (the generic background sky, at
 * naked-eye and deep-zoom depth respectively) both show, subject to the
 * limiting-magnitude cutoff, when showStars is on; anything else (the user's
 * own cataloged stellar objects) routes through the showCatalog toggle
 * independently of showStars -- unchecking the generic background field
 * should not also hide the user's own tracked catalog.
 *
 * @param {ProjectionContext['sources'][number]} source - The candidate source.
 * @param {boolean} showStars - Whether the generic Hipparcos background star field should display.
 * @param {boolean} showCatalog - Whether the user's own cataloged (non-Hipparcos) sources should display.
 * @param {number} limitingMagnitude - Faintest magnitude to display at the current FOV (see computeLimitingMagnitude).
 * @returns {boolean} True if this source should be rendered as a star point.
 */
export function isDisplayableStar(
  source: { ra: number; dec: number; type?: string; catalogSource?: string; magnitude?: number },
  showStars: boolean,
  showCatalog: boolean,
  limitingMagnitude: number,
): boolean {
  if (source.ra === 0 && source.dec === 0) return false;
  if (source.type !== 'star') return false;

  const magnitude = typeof source.magnitude === 'number' ? source.magnitude : 5.0;
  if (magnitude > limitingMagnitude) return false;

  if (source.catalogSource === 'hipparcos' || source.catalogSource === 'gaia') return showStars;
  return showCatalog;
}


/**
 * Draws the selection reticle (ring + four tick marks) centered on a screen
 * point. Shared by StarOverlay's own 2D-fallback draw path and by
 * StarSelectionOverlay, which draws just this reticle when the WebGL
 * StarFieldRenderer path is rendering the star dots instead.
 *
 * @param {CanvasRenderingContext2D} context - The 2D rendering context.
 * @param {number} pointX - Screen-space X of the selected star's center.
 * @param {number} pointY - Screen-space Y of the selected star's center.
 * @returns {void}
 */
export function drawStarSelectionReticle(context: CanvasRenderingContext2D, pointX: number, pointY: number): void {
  context.strokeStyle = 'rgba(173, 216, 230, 0.85)';
  context.lineWidth = 1.5;

  context.beginPath();
  context.arc(pointX, pointY, STAR_RETICLE_RADIUS_PX, 0, 2 * Math.PI);
  context.stroke();

  context.beginPath();
  // Top tick
  context.moveTo(pointX, pointY - STAR_RETICLE_RADIUS_PX - 3);
  context.lineTo(pointX, pointY - STAR_RETICLE_RADIUS_PX + 3);
  // Bottom tick
  context.moveTo(pointX, pointY + STAR_RETICLE_RADIUS_PX - 3);
  context.lineTo(pointX, pointY + STAR_RETICLE_RADIUS_PX + 3);
  // Left tick
  context.moveTo(pointX - STAR_RETICLE_RADIUS_PX - 3, pointY);
  context.lineTo(pointX - STAR_RETICLE_RADIUS_PX + 3, pointY);
  // Right tick
  context.moveTo(pointX + STAR_RETICLE_RADIUS_PX - 3, pointY);
  context.lineTo(pointX + STAR_RETICLE_RADIUS_PX + 3, pointY);
  context.stroke();
}

/**
 * StarOverlay Class
 *
 * Renders database and online-catalog star points. Kept as the 2D-canvas
 * fallback path used when WebGL2 is unavailable; the primary rendering path
 * is StarFieldRenderer (see ui/planetariumDisplay/webgl/StarFieldRenderer.ts).
 */
export class StarOverlay implements PlanetariumOverlay {
  id = 'stars';
  name = 'Stellar Objects';

  /**
   * Draws visible stars as small fixed-size points shaded by brightness, and outlines the active selection.
   *
   * @param {CanvasRenderingContext2D} context - The 2D rendering context.
   * @param {ProjectionContext} projectionContext - Projection context.
   * @returns {void}
   */
  draw(context: CanvasRenderingContext2D, projectionContext: ProjectionContext): void {
    // showStars gates only the generic Hipparcos background field; the
    // user's own cataloged stars stay independently gated by showCatalog
    // inside isDisplayableStar, so this early-out only fires when neither
    // could possibly show anything.
    if (!projectionContext.showStars && !projectionContext.showCatalog) return;

    // Filter sources to displayable stars, routing by catalogSource for online drivers
    // and hiding stars fainter than the current FOV's limiting magnitude
    const limitingMagnitude = computeLimitingMagnitude(projectionContext.fov);
    const stars = projectionContext.sources.filter(source =>
      isDisplayableStar(source, projectionContext.showStars, projectionContext.showCatalog, limitingMagnitude),
    );

    stars.forEach(source => {
      const point = projectionContext.projectCoords(source.ra, source.dec);
      if (!point.visible) return;

      const magnitude = typeof source.magnitude === 'number' ? source.magnitude : 5.0;
      const brightness = computeStarBrightness(magnitude);

      context.fillStyle = `rgba(255, 255, 255, ${brightness})`;
      context.beginPath();
      context.arc(point.x, point.y, STAR_MARKER_RADIUS_PX, 0, 2 * Math.PI);
      context.fill();

      // Selection crosshair
      if (projectionContext.selectedTargetId && source.id === projectionContext.selectedTargetId) {
        drawStarSelectionReticle(context, point.x, point.y);
      }
    });
  }
}
