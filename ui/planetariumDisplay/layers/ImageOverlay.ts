/**
 * @module ImageOverlay
 * @fileoverview Renders loaded FITS canvas/bitmap projections on the Planetarium canvas viewport.
 */

import { PlanetariumOverlay, ProjectionContext } from './overlayTypes';
import { parseTargetFovDegrees } from './overlayShared';
import { pixelsPerDegree } from '../utils/projectionMath';

/** Compositing opacity applied when drawing FITS image overlays onto the sky map. */
const FITS_IMAGE_OPACITY = 0.88;

/** Degrees north of target center used to compute the celestial north rotation vector. */
const FITS_NORTH_OFFSET_DEG = 0.05;

/**
 * ImageOverlay Class
 *
 * Renders loaded FITS canvas/bitmap projections.
 */
export class ImageOverlay implements PlanetariumOverlay {
  id = 'fits_images';
  name = 'FITS Frames Overlays';

  /**
   * Draws auto-stretched FITS image canvases aligned to target celestial orientation.
   *
   * @param {CanvasRenderingContext2D} context - The 2D rendering context.
   * @param {ProjectionContext} projectionContext - Projection context.
   * @returns {void}
   */
  draw(context: CanvasRenderingContext2D, projectionContext: ProjectionContext): void {
    if (!projectionContext.showFITS) return;

    const validTargets = projectionContext.targets.filter(
      target => !(target.ra === 0 && target.dec === 0) && target.stackedImage
    );
    const filteredTargets = projectionContext.showCatalog ? validTargets : [];

    filteredTargets.forEach(target => {
      const entry = projectionContext.loadedFits[target.id] || null;
      const imageToDraw = entry?.image || null;
      if (!imageToDraw) return;

      const centerRA = entry?.crval1 !== undefined ? entry.crval1 : target.ra;
      const centerDec = entry?.crval2 !== undefined ? entry.crval2 : target.dec;

      const point = projectionContext.projectCoords(centerRA, centerDec);
      if (!point.visible) return;

      const fovDeg = parseTargetFovDegrees(target);

      const scale = pixelsPerDegree(projectionContext.width, projectionContext.height, projectionContext.fov);
      const sizePx = fovDeg * scale;

      let widthPx = sizePx;
      let heightPx = sizePx;
      if (entry?.cdelt1 !== undefined && entry?.width !== undefined) {
        widthPx = entry.width * Math.abs(entry.cdelt1) * scale;
      }
      if (entry?.cdelt2 !== undefined && entry?.height !== undefined) {
        heightPx = entry.height * Math.abs(entry.cdelt2) * scale;
      }

      context.save();
      context.translate(point.x, point.y);

      // Rotate image relative to Celestial North
      const pointNorth = projectionContext.projectCoords(centerRA, centerDec + FITS_NORTH_OFFSET_DEG);
      const deltaX = pointNorth.x - point.x;
      const deltaY = pointNorth.y - point.y;
      const rotationAngle = Math.atan2(deltaY, deltaX) + Math.PI / 2;
      context.rotate(rotationAngle);
      context.scale(-1, -1); // Flip on both axes to correct FITS coordinate orientation

      context.globalAlpha = FITS_IMAGE_OPACITY;
      context.drawImage(imageToDraw, -widthPx / 2, -heightPx / 2, widthPx, heightPx);
      context.restore();
    });
  }
}
