/**
 * @module ConstellationOverlay
 * @fileoverview Renders constellation stick-figure lines on the Planetarium canvas viewport.
 */

import { PlanetariumOverlay, ProjectionContext } from './overlayTypes';

/**
 * Line color for constellation stick-figure connections — a dim cool blue-white,
 * distinct from both the grid's blue and the stars' pure white, sitting quietly
 * behind star points without competing for attention.
 */
const CONSTELLATION_LINE_COLOR = 'rgba(150, 180, 220, 0.35)';

/** Line width in pixels — a thin hairline, matching the grid's weight. */
const CONSTELLATION_LINE_WIDTH_PX = 1;

/**
 * ConstellationOverlay Class
 *
 * Renders bundled constellation stick-figure line segments.
 */
export class ConstellationOverlay implements PlanetariumOverlay {
  id = 'constellations';
  name = 'Constellation Lines';

  /**
   * Draws a line between the projected endpoints of every bundled constellation segment.
   *
   * @param {CanvasRenderingContext2D} context - The 2D rendering context.
   * @param {ProjectionContext} projectionContext - Projection context.
   * @returns {void}
   */
  draw(context: CanvasRenderingContext2D, projectionContext: ProjectionContext): void {
    if (!projectionContext.showConstellations) return;

    context.strokeStyle = CONSTELLATION_LINE_COLOR;
    context.lineWidth = CONSTELLATION_LINE_WIDTH_PX;

    projectionContext.constellationLines.forEach(segment => {
      const start = projectionContext.projectCoords(segment.ra1, segment.dec1);
      const end = projectionContext.projectCoords(segment.ra2, segment.dec2);
      if (!start.visible || !end.visible) return;

      context.beginPath();
      context.moveTo(start.x, start.y);
      context.lineTo(end.x, end.y);
      context.stroke();
    });
  }
}
