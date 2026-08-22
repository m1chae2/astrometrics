/**
 * @module GridOverlay
 * @fileoverview Renders RA/Dec coordinate grid meridians on the Planetarium canvas viewport.
 */

import { PlanetariumOverlay, ProjectionContext } from './overlayTypes';

/**
 * GridOverlay Class
 *
 * Renders RA/Dec coordinate grid meridians.
 */
export class GridOverlay implements PlanetariumOverlay {
  id = 'grid';
  name = 'Coordinate Grid';

  /**
   * Draws coordinate grid circles for declination levels and spokes for RA meridians.
   *
   * @param {CanvasRenderingContext2D} context - The 2D rendering context.
   * @param {ProjectionContext} projectionContext - Projection context.
   * @returns {void}
   */
  draw(context: CanvasRenderingContext2D, projectionContext: ProjectionContext): void {
    if (!projectionContext.showGrid) return;

    context.lineWidth = 1;
    context.strokeStyle = 'rgba(100, 160, 255, 0.22)';

    // Declination levels
    [-30, 0, 30, 60].forEach(declination => {
      context.beginPath();
      let first = true;
      for (let rightAscension = 0; rightAscension <= 360; rightAscension += 3) {
        const point = projectionContext.projectCoords(rightAscension, declination);
        if (point.visible) {
          if (first) {
            context.moveTo(point.x, point.y);
            first = false;
          } else {
            context.lineTo(point.x, point.y);
          }
        } else {
          first = true;
        }
      }
      context.stroke();
    });

    // RA meridians
    for (let rightAscension = 0; rightAscension < 360; rightAscension += 30) {
      context.beginPath();
      let first = true;
      for (let declination = -90; declination <= 90; declination += 3) {
        const point = projectionContext.projectCoords(rightAscension, declination);
        if (point.visible) {
          if (first) {
            context.moveTo(point.x, point.y);
            first = false;
          } else {
            context.lineTo(point.x, point.y);
          }
        } else {
          first = true;
        }
      }
      context.stroke();
    }
  }
}
