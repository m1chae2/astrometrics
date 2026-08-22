/**
 * @module CompassOverlay
 * @fileoverview Renders cardinal direction letters (N, E, S, W) and 10-degree tick increments
 * along the Alt = 0 horizon line on the Planetarium canvas viewport.
 */

import { PlanetariumOverlay, ProjectionContext } from './overlayTypes';

/**
 * CompassOverlay Class
 *
 * Renders cardinal direction letters (N, E, S, W) and 10-degree tick increments
 * along the Alt = 0 horizon line.
 */
export class CompassOverlay implements PlanetariumOverlay {
  id = 'compass';
  name = 'Compass Directions';

  /**
   * Draws cardinal directions and ticks along the horizon.
   *
   * @param {CanvasRenderingContext2D} context - The 2D rendering context.
   * @param {ProjectionContext} projectionContext - Projection context.
   * @returns {void}
   */
  draw(context: CanvasRenderingContext2D, projectionContext: ProjectionContext): void {
    context.save();
    context.strokeStyle = 'rgba(0, 255, 100, 0.5)'; // Bright green for horizon-line visibility
    context.fillStyle = 'rgba(0, 255, 100, 0.8)';
    context.font = 'bold 11px monospace';
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    context.lineWidth = 1;

    for (let azimuth = 0; azimuth < 360; azimuth += 10) {
      // Convert Alt/Az to RA/Dec
      const { ra: raCenter, dec: decCenter } = projectionContext.getRaDec(0, azimuth);
      const pointCenter = projectionContext.projectCoords(raCenter, decCenter);

      if (!pointCenter.visible) continue;

      const isCardinal = azimuth % 90 === 0;
      const label =
        azimuth === 0 ? 'N' : azimuth === 90 ? 'E' : azimuth === 180 ? 'S' : azimuth === 270 ? 'W' : '';

      // Ticks along the vertical altitude axis
      const tickSize = isCardinal ? 1.2 : 0.6;
      const { ra: raBottom, dec: decBottom } = projectionContext.getRaDec(-tickSize, azimuth);
      const { ra: raTop, dec: decTop } = projectionContext.getRaDec(tickSize, azimuth);

      const pointBottom = projectionContext.projectCoords(raBottom, decBottom);
      const pointTop = projectionContext.projectCoords(raTop, decTop);

      if (pointBottom.visible && pointTop.visible) {
        context.beginPath();
        context.moveTo(pointBottom.x, pointBottom.y);
        context.lineTo(pointTop.x, pointTop.y);
        context.stroke();
      }

      if (isCardinal) {
        // Render direction letter slightly above the top of the tick
        const { ra: raLabel, dec: decLabel } = projectionContext.getRaDec(2.8, azimuth);
        const pointLabel = projectionContext.projectCoords(raLabel, decLabel);
        if (pointLabel.visible) {
          context.fillText(label, pointLabel.x, pointLabel.y);
        }
      } else {
        // Draw small numeric degree label on non-cardinal ticks when zoomed in
        const { ra: raLabel, dec: decLabel } = projectionContext.getRaDec(2.0, azimuth);
        const pointLabel = projectionContext.projectCoords(raLabel, decLabel);
        if (pointLabel.visible && projectionContext.fov <= 60) {
          context.font = '8px monospace';
          context.fillStyle = 'rgba(0, 255, 100, 0.5)';
          context.fillText(`${azimuth}°`, pointLabel.x, pointLabel.y);
        }
      }
    }

    context.restore();
  }
}
