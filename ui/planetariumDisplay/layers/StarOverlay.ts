/**
 * @module StarOverlay
 * @fileoverview Renders database and online-catalog star points on the Planetarium canvas viewport.
 */

import { PlanetariumOverlay, ProjectionContext } from './overlayTypes';
import { refreshStarColors, starColorRgb } from '../utils/starColor';

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

/**
 * Radius, in pixels, of a star drawn in its class color in the "By spectral class" mode.
 * A 2 px dot is too small for its color to read at a wide view, so a colored dot is a little
 * bigger. Chosen by eye, not measured.
 */
export const CLASS_STAR_MARKER_RADIUS_PX = 3.0;

/**
 * Lowest opacity of a star drawn in its class color. A faint star at the normal opacity floor
 * looks gray whatever its color, so a colored dot never goes below this. Chosen by eye.
 */
export const CLASS_STAR_MIN_BRIGHTNESS = 0.8;

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
 * 12), so reaching this depth requires the downloaded Gaia DR3 catalog (see
 * DeepStarCatalogDriver) -- Hipparcos alone can't supply anything fainter than
 * ~12. That catalog itself stops at DEEP_STAR_MAX_MAGNITUDE, so at the
 * deepest zooms only the user's own library stars go fainter than that.
 */
export const MAX_ZOOM_LIMITING_MAGNITUDE = 19.0;

/** FOV, in degrees, at which the naked-eye limiting magnitude applies. Matches the app's documented default FOV. */
export const NAKED_EYE_REFERENCE_FOV_DEG = 90.0;

/**
 * FOV, in degrees, at which the full catalog-depth limiting magnitude applies. Set so the
 * limit grows slowly with zoom: about 9.4 at 15 degrees, 13.3 at 2 degrees and 15.9 at 0.5
 * degrees, so a wide view stays sparse and fainter stars come in as the view narrows. The
 * user asked for fewer stars at once (2026-11-25); chosen by eye, not measured.
 */
export const MAX_ZOOM_REFERENCE_FOV_DEG = 0.1;

/**
 * Most star dots drawn per million screen pixels, brightest first: about 1,500 on a
 * 1920 x 1000 sky view. It keeps a dense part of the sky (the Milky Way, a target's own
 * detections) from turning into a solid mass; the selected star and a small chosen class
 * always count in. Chosen by eye, not measured.
 */
export const MAX_STARS_PER_MEGAPIXEL = 800;

/** One star that passed the drawing rule, with its place on screen. */
export interface DrawableStar {
  source: ProjectionContext['sources'][number];
  x: number;
  y: number;
}

/**
 * Keeps at most MAX_STARS_PER_MEGAPIXEL stars per million screen pixels, brightest first.
 *
 * One rule for every star layer (the bright and deep catalogs and the user's own stars),
 * applied after the magnitude limit, so the number of dots grows with zoom but never crowds
 * the view. The selected star and stars marked `alwaysShow` are always kept.
 *
 * @param {DrawableStar[]} stars The stars that passed the drawing rule and are on screen.
 * @param {number} width The canvas width in pixels.
 * @param {number} height The canvas height in pixels.
 * @param {string} [selectedId] The selected object's id.
 * @returns {DrawableStar[]} The stars to draw.
 */
export function limitStarDensity(stars: DrawableStar[], width: number, height: number, selectedId?: string): DrawableStar[] {
  const cap = Math.max(1, Math.round((width * height) / 1e6 * MAX_STARS_PER_MEGAPIXEL));
  if (stars.length <= cap) return stars;
  const isKept = (star: DrawableStar) => !!star.source.alwaysShow || (!!selectedId && star.source.id === selectedId);
  const kept = stars.filter(isKept);
  const ranked = stars.filter((star) => !isKept(star));
  // Brightest first; a star with no catalog magnitude counts as faint.
  const rankOf = (star: DrawableStar) => catalogMagnitudeOf(star.source)
    ?? (star.source.catalogSource && typeof star.source.magnitude === 'number' ? star.source.magnitude : 99);
  ranked.sort((first, second) => rankOf(first) - rankOf(second));
  return [...ranked.slice(0, Math.max(0, cap - kept.length)), ...kept];
}

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
 * Faintest magnitude the deep-star (Gaia) layer asks for, however far the view is zoomed in,
 * when the catalog has not reported its own depth (`magnitude_limit` from
 * `planetarium:get_deep_catalog_status`) -- before that status arrives, or with no catalog
 * installed. Once it has, the catalog's own value is used instead, so this only has to agree
 * with the builder's default, not track a catalog built to a different depth.
 *
 * Derivation: for a 30 s exposure with the 75 mm Apertura 75Q and the ASI533MM Pro, a
 * back-of-envelope signal-to-noise estimate puts 5-sigma detection at about G = 17.5 and
 * photometry good to about 5 percent at about G = 15.7 (assuming roughly 21 mag/arcsec^2 sky,
 * so uncertain by about half a magnitude). 16 keeps the stars whose photometry can be trusted
 * and is about a third of the data of G = 18. See the fuller derivation and validation on
 * `DEFAULT_MAGNITUDE_LIMIT` in astrometricslib/pipelines/astrometry/deep_catalog_builder.py, whose default this mirrors.
 */
export const DEEP_STAR_MAX_MAGNITUDE = 16;

/**
 * Widest FOV, in degrees, at which the user's own stars that have no catalog
 * magnitude are shown. Most of a local library's stars are detections around
 * imaged targets with an empty or instrumental magnitude, so no magnitude cut
 * can thin them out; at a wide FOV they are just dense dot clusters (a library
 * can hold hundreds of thousands), so they only appear once the view is
 * narrow enough for a target's field to be worth looking at.
 */
export const UNCATALOGED_STAR_MAX_FOV_DEG = 10.0;

/**
 * FOV, in degrees, at which the user's own stars with no catalog magnitude
 * reach their full (dim) brightness. Between UNCATALOGED_STAR_MAX_FOV_DEG and
 * this value they fade in from invisible instead of appearing all at once, so
 * zooming in never makes a whole imaged field pop into view. Half the gate FOV
 * is one halving of the view, chosen by judgement, not measured.
 */
export const UNCATALOGED_STAR_FULL_BRIGHTNESS_FOV_DEG = UNCATALOGED_STAR_MAX_FOV_DEG / 2;

/** The parts of a source that say how bright it is. */
export interface SourceMagnitude {
  /** The source's magnitude field as received from the backend. */
  magnitude?: number | null;
  /** The library's verdict that `magnitude` is a real catalog magnitude (SkySource `hasCatalogMagnitude`). */
  hasCatalogMagnitude?: boolean;
}

/**
 * The source's catalog magnitude, when the library says it has one.
 *
 * @param {SourceMagnitude} source - The source.
 * @returns {number | null} The magnitude, or null when it is missing, 0 or instrumental.
 */
export function catalogMagnitudeOf(source: SourceMagnitude): number | null {
  return source.hasCatalogMagnitude === true && typeof source.magnitude === 'number' ? source.magnitude : null;
}

/**
 * Formats a star's magnitude for display, or `'--'` when the library says it
 * is not a real catalog magnitude (missing, the 0 saved when a catalog gave
 * none, or an instrumental value from photometry).
 *
 * @param {SourceMagnitude} source - The source.
 * @param {number} decimals - Number of decimal places. Defaults to 2.
 * @returns {string} The formatted magnitude, or `'--'` if it is unknown.
 */
export function formatCatalogMagnitude(source: SourceMagnitude, decimals: number = 2): string {
  const magnitude = catalogMagnitudeOf(source);
  return magnitude === null ? '--' : magnitude.toFixed(decimals);
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
 * The user's own stars without a catalog magnitude can't be judged against
 * the limiting magnitude, so they show only at or below
 * UNCATALOGED_STAR_MAX_FOV_DEG instead.
 *
 * @param {ProjectionContext['sources'][number]} source - The candidate source.
 * @param {boolean} showStars - Whether the generic Hipparcos background star field should display.
 * @param {boolean} showCatalog - Whether the user's own cataloged (non-Hipparcos) sources should display.
 * @param {number} limitingMagnitude - Faintest magnitude to display at the current FOV (see computeLimitingMagnitude).
 * @param {number} fovDegrees - Current viewport field of view, in degrees.
 * @param {string} [selectedId] - The selected object's id; that star always shows.
 * @returns {boolean} True if this source should be rendered as a star point.
 */
export function isDisplayableStar(
  source: { id?: string; ra: number; dec: number; type?: string; catalogSource?: string; colorClass?: string; alwaysShow?: boolean } & SourceMagnitude,
  showStars: boolean,
  showCatalog: boolean,
  limitingMagnitude: number,
  fovDegrees: number,
  selectedId?: string,
): boolean {
  if (source.ra === 0 && source.dec === 0) return false;
  if (source.type !== 'star') return false;
  // The selected star, and a star of a small chosen spectral class (see useClassStarSources),
  // always show, whatever the zoom.
  if (selectedId !== undefined && selectedId !== '' && source.id === selectedId) return true;
  if (source.alwaysShow) return true;

  if (source.catalogSource === 'hipparcos' || source.catalogSource === 'deep_stars') {
    const magnitude = typeof source.magnitude === 'number' ? source.magnitude : 5.0;
    if (magnitude > limitingMagnitude) return false;
    return showStars;
  }

  // The user's own stars follow the same zoom rule as the catalog layers: a star shows once the
  // view is narrow enough for its magnitude; one with no catalog magnitude counts as faint.
  const catalogMagnitude = catalogMagnitudeOf(source);
  if (catalogMagnitude !== null) {
    if (catalogMagnitude > limitingMagnitude) return false;
  } else if (fovDegrees > UNCATALOGED_STAR_MAX_FOV_DEG) {
    return false;
  }
  // A star in its class color is one the spectral class list shows, so the Deep Catalog
  // switch does not hide it.
  return source.colorClass ? true : showCatalog;
}


/**
 * Computes how bright a source is drawn, given its magnitude and the current FOV.
 *
 * A star with a real catalog magnitude is shaded by that magnitude (see
 * computeStarBrightness). Any other star -- the user's own detections, whose
 * magnitude is missing or instrumental (the library's hasCatalogMagnitude) -- says nothing
 * about how bright it looks, so it is drawn dim at the lowest brightness and
 * fades in as the FOV narrows from UNCATALOGED_STAR_MAX_FOV_DEG to
 * UNCATALOGED_STAR_FULL_BRIGHTNESS_FOV_DEG. Sharing this between StarOverlay and
 * StarFieldRenderer keeps both drawing paths identical.
 *
 * @param {SourceMagnitude} source - The source's magnitude and the library's verdict on it.
 * @param {number} fovDegrees - Current viewport field of view, in degrees.
 * @returns {number} Opacity in [0, STAR_MAX_BRIGHTNESS].
 */
export function computeSourceBrightness(source: SourceMagnitude & { colorClass?: string }, fovDegrees: number): number {
  if (source.colorClass) {
    const shade = catalogMagnitudeOf(source);
    return Math.max(CLASS_STAR_MIN_BRIGHTNESS, shade === null ? 0 : computeStarBrightness(shade));
  }
  const magnitude = catalogMagnitudeOf(source);
  if (magnitude !== null) return computeStarBrightness(magnitude);

  const fadeProgress =
    (UNCATALOGED_STAR_MAX_FOV_DEG - fovDegrees) /
    (UNCATALOGED_STAR_MAX_FOV_DEG - UNCATALOGED_STAR_FULL_BRIGHTNESS_FOV_DEG);
  return STAR_MIN_BRIGHTNESS * Math.max(0, Math.min(1, fadeProgress));
}

/**
 * The radius a star's dot is drawn at: bigger for a star in its class color, so the color reads.
 *
 * @param {{ colorClass?: string }} source - The star.
 * @returns {number} The radius in pixels.
 */
export function starMarkerRadius(source: { colorClass?: string }): number {
  return source.colorClass ? CLASS_STAR_MARKER_RADIUS_PX : STAR_MARKER_RADIUS_PX;
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
 * @param {string} [colorClass] - The star's color class in the spectral class mode; the reticle then
 *   takes the star's class color. Without one it is light blue.
 * @returns {void}
 */
export function drawStarSelectionReticle(
  context: CanvasRenderingContext2D,
  pointX: number,
  pointY: number,
  colorClass?: string,
): void {
  if (colorClass) {
    const [red, green, blue] = starColorRgb(colorClass);
    context.strokeStyle = `rgba(${red}, ${green}, ${blue}, 0.9)`;
  } else {
    context.strokeStyle = 'rgba(173, 216, 230, 0.85)';
  }
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
   * A star with a color class (the spectral class mode) is drawn in its class color; others are white.
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
    refreshStarColors();
    const limitingMagnitude = computeLimitingMagnitude(projectionContext.fov);
    const stars = projectionContext.sources.filter(source =>
      isDisplayableStar(
        source,
        projectionContext.showStars,
        projectionContext.showCatalog,
        limitingMagnitude,
        projectionContext.fov,
        projectionContext.selectedTargetId,
      ),
    );

    const onScreen: DrawableStar[] = [];
    for (const source of stars) {
      const point = projectionContext.projectCoords(source.ra, source.dec);
      if (point.visible) onScreen.push({ source, x: point.x, y: point.y });
    }
    const drawn = limitStarDensity(onScreen, projectionContext.width, projectionContext.height, projectionContext.selectedTargetId);

    drawn.forEach(({ source, x, y }) => {
      const point = { x, y };

      const brightness = computeSourceBrightness(source, projectionContext.fov);

      const [red, green, blue] = starColorRgb(source.colorClass);
      context.fillStyle = `rgba(${red}, ${green}, ${blue}, ${brightness})`;
      context.beginPath();
      context.arc(point.x, point.y, starMarkerRadius(source), 0, 2 * Math.PI);
      context.fill();

      // Selection crosshair
      if (projectionContext.selectedTargetId && source.id === projectionContext.selectedTargetId) {
        drawStarSelectionReticle(context, point.x, point.y, source.colorClass);
      }
    });
  }
}
