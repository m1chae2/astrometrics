/**
 * @module TelescopeOverlay
 * @fileoverview Renders the current pointing position of the telescope on the Planetarium canvas viewport.
 */

import { PlanetariumOverlay, ProjectionContext } from './overlayTypes';

/** Inner circle radius for the telescope pointing crosshair, in pixels. */
const TELESCOPE_INNER_RADIUS_PX = 8;

/** Outer circle radius for the telescope pointing crosshair, in pixels. */
const TELESCOPE_OUTER_RADIUS_PX = 18;

/** Half-length of each crosshair arm extending beyond the outer circle, in pixels. */
const TELESCOPE_CROSSHAIR_ARM_PX = 30;

/**
 * TelescopeOverlay Class
 *
 * Renders the current pointing position of the telescope when connected.
 */
export class TelescopeOverlay implements PlanetariumOverlay {
  id = 'telescope_pointer';
  name = 'Telescope Pointer';

  /**
   * Draws a red crosshair target centered at the telescope RA/Dec coordinates.
   *
   * @param {CanvasRenderingContext2D} context - The 2D rendering context.
   * @param {ProjectionContext} projectionContext - Projection context.
   * @returns {void}
   */
  draw(context: CanvasRenderingContext2D, projectionContext: ProjectionContext): void {
    const hasTelescopeCoordinates =
      projectionContext.telescopeRa !== null && projectionContext.telescopeRa !== undefined
      && projectionContext.telescopeDec !== null && projectionContext.telescopeDec !== undefined;
    if (!projectionContext.showTelescope || !projectionContext.telescopeConnected || !hasTelescopeCoordinates) {
      return;
    }

    const point = projectionContext.projectCoords(
      projectionContext.telescopeRa as number,
      projectionContext.telescopeDec as number
    );
    if (!point.visible) return;

    context.save();
    context.strokeStyle = 'rgba(180, 0, 0, 0.8)';
    context.lineWidth = 1.5;

    // Draw inner circle
    context.beginPath();
    context.arc(point.x, point.y, TELESCOPE_INNER_RADIUS_PX, 0, 2 * Math.PI);
    context.stroke();

    // Draw outer circle
    context.beginPath();
    context.arc(point.x, point.y, TELESCOPE_OUTER_RADIUS_PX, 0, 2 * Math.PI);
    context.stroke();

    // Draw crosshair lines, with a gap inside the inner circle
    context.beginPath();
    // Left arm
    context.moveTo(point.x - TELESCOPE_CROSSHAIR_ARM_PX, point.y);
    context.lineTo(point.x - TELESCOPE_INNER_RADIUS_PX, point.y);
    // Right arm
    context.moveTo(point.x + TELESCOPE_INNER_RADIUS_PX, point.y);
    context.lineTo(point.x + TELESCOPE_CROSSHAIR_ARM_PX, point.y);
    // Top arm
    context.moveTo(point.x, point.y - TELESCOPE_CROSSHAIR_ARM_PX);
    context.lineTo(point.x, point.y - TELESCOPE_INNER_RADIUS_PX);
    // Bottom arm
    context.moveTo(point.x, point.y + TELESCOPE_INNER_RADIUS_PX);
    context.lineTo(point.x, point.y + TELESCOPE_CROSSHAIR_ARM_PX);
    context.stroke();
    context.restore();
  }
}
