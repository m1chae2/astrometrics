/**
 * @module StarSelectionOverlay
 * @fileoverview Renders only the selected-star reticle on the 2D canvas.
 *
 * Used in place of StarOverlay when the WebGL StarFieldRenderer is active:
 * StarFieldRenderer renders the (potentially thousands of) star dots on its
 * own canvas, but the O(1)-per-frame selection reticle stays on the 2D
 * canvas, since moving a single ring+tick-marks decoration to GLSL would add
 * real shader complexity for no measurable performance benefit.
 */

import { PlanetariumOverlay, ProjectionContext } from './overlayTypes';
import { isDisplayableStar, drawStarSelectionReticle, computeLimitingMagnitude } from './StarOverlay';

/**
 * StarSelectionOverlay Class
 *
 * Renders just the selection reticle around the currently-selected star, if any.
 */
export class StarSelectionOverlay implements PlanetariumOverlay {
  id = 'star-selection';
  name = 'Selected Star Reticle';

  /**
   * Draws the selection reticle around the selected star, if it is currently visible.
   *
   * @param {CanvasRenderingContext2D} context - The 2D rendering context.
   * @param {ProjectionContext} projectionContext - Projection context.
   * @returns {void}
   */
  draw(context: CanvasRenderingContext2D, projectionContext: ProjectionContext): void {
    if (!projectionContext.selectedTargetId) return;

    const limitingMagnitude = computeLimitingMagnitude(projectionContext.fov);
    const selected = projectionContext.sources.find(
      source =>
        source.id === projectionContext.selectedTargetId &&
        isDisplayableStar(source, projectionContext.showStars, projectionContext.showCatalog, limitingMagnitude),
    );
    if (!selected) return;

    const point = projectionContext.projectCoords(selected.ra, selected.dec);
    if (!point.visible) return;

    drawStarSelectionReticle(context, point.x, point.y);
  }
}
