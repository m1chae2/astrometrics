/**
 * @module BackgroundOverlay
 * @fileoverview Renders the space background gradient for the Planetarium canvas viewport.
 */

import { PlanetariumOverlay, ProjectionContext } from './overlayTypes';

/**
 * BackgroundOverlay Class
 *
 * Renders the space background gradient.
 */
export class BackgroundOverlay implements PlanetariumOverlay {
  id = 'background';
  name = 'Space Background';

  /**
   * Draws a linear night sky gradient covering the full canvas screen space.
   *
   * @param {CanvasRenderingContext2D} context - The 2D rendering context.
   * @param {ProjectionContext} projectionContext - Projection context.
   * @returns {void}
   */
  draw(context: CanvasRenderingContext2D, projectionContext: ProjectionContext): void {
    const gradient = context.createLinearGradient(0, 0, 0, projectionContext.height);
    gradient.addColorStop(0, '#040710');
    gradient.addColorStop(1, '#010204');
    context.fillStyle = gradient;
    context.fillRect(0, 0, projectionContext.width, projectionContext.height);
  }
}
