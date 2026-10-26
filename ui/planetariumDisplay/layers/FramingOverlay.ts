/**
 * @module FramingOverlay
 * @fileoverview Draws the camera framing panels on the Planetarium sky map.
 *
 * The overlay does no framing math. It draws only the corners it is given
 * (right ascension and declination in degrees), projected with the map's own
 * projection. A panel is skipped when any of its corners is out of view.
 */

import { getVar } from '../../common/utils/plotTools';
import { PlanetariumOverlay, ProjectionContext } from './overlayTypes';

/** One corner of a framing panel on the sky, in degrees. */
export interface FramingCorner {
  /** Right ascension in degrees. */
  raDeg: number;
  /** Declination in degrees. */
  decDeg: number;
}

/** One framing panel: a name and its corners, in the order given. */
export interface FramingPanelCorners {
  /** The panel's name, drawn at the middle of the outline. */
  name: string;
  /** The corners, in drawing order. */
  corners: FramingCorner[];
}

/**
 * Draws each framing panel as a closed outline with its name.
 */
export class FramingOverlay implements PlanetariumOverlay {
  id = 'framing_panels';
  name = 'Framing Panels';

  /**
   * Draws the panels in `projectionContext.framingPanels`, if any.
   *
   * @param {CanvasRenderingContext2D} context - The 2D rendering context.
   * @param {ProjectionContext} projectionContext - Projection and visibility configuration.
   * @returns {void}
   */
  draw(context: CanvasRenderingContext2D, projectionContext: ProjectionContext): void {
    const panels = projectionContext.framingPanels;
    if (!panels || panels.length === 0) return;

    context.save();
    // The colors and the label font are theme tokens (see theme.css); a canvas ignores an empty value.
    const outlineColor = getVar('--framing-outline', 'currentColor');
    const fillColor = getVar('--framing-fill', 'transparent');
    const labelColor = getVar('--framing-label', 'currentColor');
    context.strokeStyle = outlineColor;
    context.fillStyle = fillColor;
    context.lineWidth = 2;
    context.font = getVar('--framing-label-font', '');
    context.textAlign = 'center';

    for (const panel of panels) {
      const points = panel.corners.map((corner) => projectionContext.projectCoords(corner.raDeg, corner.decDeg));
      if (points.length < 3 || points.some((point) => !point.visible)) continue;

      context.beginPath();
      points.forEach((point, index) => {
        if (index === 0) context.moveTo(point.x, point.y);
        else context.lineTo(point.x, point.y);
      });
      context.closePath();
      context.fill();
      context.stroke();

      const middleX = points.reduce((sum, point) => sum + point.x, 0) / points.length;
      const middleY = points.reduce((sum, point) => sum + point.y, 0) / points.length;
      context.fillStyle = labelColor;
      context.fillText(panel.name, middleX, middleY);
      context.fillStyle = fillColor;
    }
    context.restore();
  }
}
