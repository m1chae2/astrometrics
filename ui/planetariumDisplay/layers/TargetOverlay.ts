/**
 * @module TargetOverlay
 * @fileoverview Renders targeted frames and reticle boundaries on the Planetarium canvas viewport.
 */

import { PlanetariumOverlay, ProjectionContext } from './overlayTypes';
import { parseTargetFovDegrees } from './overlayShared';
import { pixelsPerDegree } from '../utils/projectionMath';

/** Minimum reticle radius for target objects, in pixels. */
export const TARGET_RETICLE_MIN_PX = 16.0;

/**
 * TargetOverlay Class
 *
 * Renders targeted frames and reticle boundaries.
 */
export class TargetOverlay implements PlanetariumOverlay {
  id = 'targets';
  name = 'Library Targets';

  /**
   * Draws dashed reticle bounds for target objects, scaling to keep them visible at any zoom level.
   *
   * @param {CanvasRenderingContext2D} context - The 2D rendering context.
   * @param {ProjectionContext} projectionContext - Projection context.
   * @returns {void}
   */
  draw(context: CanvasRenderingContext2D, projectionContext: ProjectionContext): void {
    const validTargets = projectionContext.targets.filter(
      target => !(target.ra === 0 && target.dec === 0)
    );
    const filteredTargets = projectionContext.showCatalog ? validTargets : [];

    filteredTargets.forEach(target => {
      const entry = projectionContext.loadedFits[target.id] || null;
      const centerRA = entry?.crval1 !== undefined ? entry.crval1 : target.ra;
      const centerDec = entry?.crval2 !== undefined ? entry.crval2 : target.dec;

      const point = projectionContext.projectCoords(centerRA, centerDec);
      if (!point.visible) return;

      const fovDeg = parseTargetFovDegrees(target);

      const scale = pixelsPerDegree(projectionContext.width, projectionContext.height, projectionContext.fov);
      const sizePx = fovDeg * scale;

      const reticleSize = Math.max(TARGET_RETICLE_MIN_PX, sizePx);
      const isSelected = !!projectionContext.selectedTargetId && target.id === projectionContext.selectedTargetId;
      context.strokeStyle = isSelected ? 'rgba(255, 120, 50, 0.95)' : 'rgba(255, 120, 50, 0.4)';
      context.lineWidth = isSelected ? 2 : 1;
      context.setLineDash([4, 4]);
      context.beginPath();
      context.arc(point.x, point.y, reticleSize / 2, 0, 2 * Math.PI);
      context.stroke();
      context.setLineDash([]); // Reset
    });
  }
}
