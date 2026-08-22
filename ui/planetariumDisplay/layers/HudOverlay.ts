/**
 * @module HudOverlay
 * @fileoverview Renders the active tracking status indicator on the Planetarium canvas viewport.
 */

import { PlanetariumOverlay, ProjectionContext } from './overlayTypes';

/** Width of the HUD tracking indicator rectangle, in pixels. */
const HUD_RECT_WIDTH_PX = 150;

/** Height of the HUD tracking indicator rectangle, in pixels. */
const HUD_RECT_HEIGHT_PX = 28;

/**
 * HudOverlay Class
 *
 * Renders the active tracking status indicator.
 */
export class HudOverlay implements PlanetariumOverlay {
  id = 'tracking_hud';
  name = 'Tracking Status HUD';

  /**
   * Draws a visual panel indicating if the viewport is locked to equatorial tracking.
   *
   * @param {CanvasRenderingContext2D} context - The 2D rendering context.
   * @param {ProjectionContext} projectionContext - Projection context.
   * @returns {void}
   */
  draw(context: CanvasRenderingContext2D, projectionContext: ProjectionContext): void {
    if (!projectionContext.trackingMode) return;

    context.save();
    context.fillStyle = 'rgba(0, 0, 0, 0.6)';
    context.strokeStyle = 'rgba(0, 200, 200, 0.8)';
    context.lineWidth = 1.5;
    context.beginPath();

    const rectX = projectionContext.width - HUD_RECT_WIDTH_PX - 16;
    const rectY = projectionContext.height - HUD_RECT_HEIGHT_PX - 16;

    context.roundRect(rectX, rectY, HUD_RECT_WIDTH_PX, HUD_RECT_HEIGHT_PX, 4);
    context.fill();
    context.stroke();

    context.fillStyle = '#00f3f3';
    context.font = '11px monospace';
    context.fillText('TRACKING EQUATORIAL', rectX + 10, rectY + 17);
    context.restore();
  }
}
