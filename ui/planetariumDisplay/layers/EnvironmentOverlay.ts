/**
 * @module EnvironmentOverlay
 * @fileoverview Renders local ground shading and the horizon boundary on the Planetarium canvas viewport.
 */

import { PlanetariumOverlay, ProjectionContext } from './overlayTypes';
import { viewportDipsBelowHorizon } from './overlayShared';

/**
 * EnvironmentOverlay Class
 *
 * Renders local ground shading and the horizon boundary.
 */
export class EnvironmentOverlay implements PlanetariumOverlay {
  id = 'environment';
  name = 'Local Horizon & Ground';

  /**
   * Renders horizon outline and shades the ground mask matching altitude levels.
   *
   * @param {CanvasRenderingContext2D} context - The 2D rendering context.
   * @param {ProjectionContext} projectionContext - Projection context.
   * @returns {void}
   */
  draw(context: CanvasRenderingContext2D, projectionContext: ProjectionContext): void {
    if (!projectionContext.showEnvironment) return;

    // When the bottom-center of the viewport is at or above the horizon the entire
    // projected sky area is above ground.  The horizon circle would clip only the
    // left/right screen edges in stereographic space, producing vertical bar
    // artifacts rather than a sensible horizon line.
    if (!viewportDipsBelowHorizon(projectionContext.centerAlt, projectionContext.fov)) return;

    const horizonPoints: { x: number; y: number }[] = [];
    const step = 1;

    for (let azimuth = 0; azimuth <= 360; azimuth += step) {
      const raDec = projectionContext.getRaDec(0, azimuth);
      const point = projectionContext.projectCoords(raDec.ra, raDec.dec);
      const isOnScreen =
        point.x >= -100 && point.x <= projectionContext.width + 100
        && point.y >= -100 && point.y <= projectionContext.height + 100;
      if (isOnScreen) {
        horizonPoints.push({ x: point.x, y: point.y });
      }
    }

    if (horizonPoints.length > 0) {
      horizonPoints.sort((pointA, pointB) => pointA.x - pointB.x);

      context.fillStyle = 'rgba(18, 66, 34, 1.0)'; // Opaque dark forest green ground
      context.strokeStyle = '#28a050';
      context.lineWidth = 3;

      context.beginPath();
      context.moveTo(horizonPoints[0].x, horizonPoints[0].y);
      for (let i = 1; i < horizonPoints.length; i++) {
        context.lineTo(horizonPoints[i].x, horizonPoints[i].y);
      }

      // Check if bottom of the screen faces the ground
      const bottomCenterAlt = projectionContext.centerAlt - projectionContext.fov / 2;

      if (bottomCenterAlt < projectionContext.centerAlt) {
        context.lineTo(projectionContext.width + 100, horizonPoints[horizonPoints.length - 1].y);
        context.lineTo(projectionContext.width + 100, projectionContext.height + 100);
        context.lineTo(-100, projectionContext.height + 100);
        context.lineTo(-100, horizonPoints[0].y);
      } else {
        context.lineTo(projectionContext.width + 100, horizonPoints[horizonPoints.length - 1].y);
        context.lineTo(projectionContext.width + 100, -100);
        context.lineTo(-100, -100);
        context.lineTo(-100, horizonPoints[0].y);
      }
      context.closePath();
      context.fill();

      // Horizon line
      context.beginPath();
      context.moveTo(horizonPoints[0].x, horizonPoints[0].y);
      for (let i = 1; i < horizonPoints.length; i++) {
        context.lineTo(horizonPoints[i].x, horizonPoints[i].y);
      }
      context.stroke();
    } else {
      if (projectionContext.centerAlt < 0) {
        context.fillStyle = 'rgba(18, 66, 34, 1.0)';
        context.fillRect(0, 0, projectionContext.width, projectionContext.height);
      }
    }
  }
}
