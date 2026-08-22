/**
 * @module FovOverlay
 * @fileoverview Renders the sensor field-of-view outline overlay on the Planetarium canvas viewport.
 *
 * The overlay is only drawn when both sensorFovWidthDeg and sensorFovHeightDeg are present
 * in the ProjectionContext, meaning an equipment configuration has been loaded from the backend.
 * If no configuration is available, the overlay is suppressed entirely.
 */

import { PlanetariumOverlay, ProjectionContext } from './overlayTypes';
import { pixelsPerDegree } from '../utils/projectionMath';

/**
 * FovOverlay renders a dashed rectangle representing the imaging sensor's angular
 * field of view, centered on the viewport. Dimensions are driven entirely by the
 * active equipment configuration — there are no hardcoded fallback sizes.
 */
export class FovOverlay implements PlanetariumOverlay {
  id = 'fov_outline';
  name = 'Sensor FOV Outline';

  /**
   * Draws a dashed sensor bounds indicator in the centre of the viewport.
   * Silently returns if the FOV toggle is off or no equipment config is loaded.
   *
   * @param {CanvasRenderingContext2D} context - The 2D rendering context.
   * @param {ProjectionContext} projectionContext - Projection context.
   * @returns {void}
   */
  draw(context: CanvasRenderingContext2D, projectionContext: ProjectionContext): void {
    if (!projectionContext.showFOV) return;
    if (projectionContext.sensorFovWidthDeg == null || projectionContext.sensorFovHeightDeg == null) return;

    const scale = pixelsPerDegree(projectionContext.width, projectionContext.height, projectionContext.fov);
    const fovWidthPx = projectionContext.sensorFovWidthDeg * scale;
    const fovHeightPx = projectionContext.sensorFovHeightDeg * scale;

    context.strokeStyle = 'rgba(255, 255, 255, 0.45)';
    context.lineWidth = 1.5;
    context.setLineDash([6, 3]);
    context.strokeRect(
      (projectionContext.width - fovWidthPx) / 2,
      (projectionContext.height - fovHeightPx) / 2,
      fovWidthPx,
      fovHeightPx
    );
    context.setLineDash([]);
  }
}
