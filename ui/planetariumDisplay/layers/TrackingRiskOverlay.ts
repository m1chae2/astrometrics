/**
 * @module TrackingRiskOverlay
 * @fileoverview Colors the sky by how risky it is for the mount's tracking.
 *
 * The backend scores the risk (the `trackingRisk` grid of the equipment's
 * performance envelope): a grid over hour angle and declination, from 0
 * (safe) to 1 (stars likely to trail), with the scores where the caution and
 * high-risk colors start. This overlay only looks up each patch of sky in that
 * grid and paints it green, amber or red.
 */

import { PlanetariumOverlay, ProjectionContext } from './overlayTypes';
import { TrackingRiskMap } from '../../common/types/backendTypes';
import {
  TRACKING_RISK_ALT_STEP_DEG,
  TRACKING_RISK_AZ_STEP_DEG,
  TRACKING_RISK_OFFSCREEN_MARGIN_PX,
} from './constants';

/**
 * Reads a score from the grid, blending the four nearest grid points so the
 * colors change smoothly.
 *
 * @param {TrackingRiskMap} riskMap - The grid from the backend.
 * @param {number} haDeg - Hour angle in degrees (-180 to 180, west positive).
 * @param {number} decDeg - Declination in degrees.
 * @returns {number} The score, 0 to 1.
 */
export function scoreAt(riskMap: TrackingRiskMap, haDeg: number, decDeg: number): number {
  const { haDeg: has, decDeg: decs, scores } = riskMap;
  const haStep = has[1] - has[0];
  const decStep = decs[1] - decs[0];
  const x = Math.min(has.length - 1, Math.max(0, (haDeg - has[0]) / haStep));
  const y = Math.min(decs.length - 1, Math.max(0, (decDeg - decs[0]) / decStep));
  const x0 = Math.min(has.length - 2, Math.floor(x));
  const y0 = Math.min(decs.length - 2, Math.floor(y));
  const fx = x - x0;
  const fy = y - y0;
  const bottom = scores[y0][x0] * (1 - fx) + scores[y0][x0 + 1] * fx;
  const top = scores[y0 + 1][x0] * (1 - fx) + scores[y0 + 1][x0 + 1] * fx;
  return bottom * (1 - fy) + top * fy;
}

/**
 * Picks a see-through fill color for a score.
 *
 * @param {number} risk - Score from 0 to 1.
 * @param {TrackingRiskMap} riskMap - Supplies where the caution and high colors start.
 * @returns {string} RGBA color string.
 */
function getRiskColor(risk: number, riskMap: TrackingRiskMap): string {
  const { cautionScore, highScore } = riskMap;
  if (risk < cautionScore) {
    return `rgba(16, 185, 129, ${0.12 + risk * 0.15})`;
  }
  if (risk < highScore) {
    const t = (risk - cautionScore) / (highScore - cautionScore);
    return `rgba(245, 158, 11, ${0.16 + t * 0.16})`;
  }
  const t = Math.min(1.0, (risk - highScore) / (1 - highScore));
  return `rgba(239, 68, 68, ${0.2 + t * 0.2})`;
}

/**
 * TrackingRiskOverlay Class
 *
 * Draws an altitude/azimuth mesh tinted by the backend's tracking-risk grid.
 */
export class TrackingRiskOverlay implements PlanetariumOverlay {
  id = 'tracking_risk_overlay';
  name = 'Mount Tracking Risk';

  /**
   * Paints the risk mesh and a legend badge.
   *
   * @param {CanvasRenderingContext2D} context - Canvas to draw on.
   * @param {ProjectionContext} projectionContext - View, projection and the risk grid.
   */
  draw(context: CanvasRenderingContext2D, projectionContext: ProjectionContext): void {
    if (!projectionContext.showTrackingRisk) return;

    const { lst, width, height, trackingRisk } = projectionContext;

    context.save();

    if (trackingRisk) {
      // Sample in Horizontal (Alt / Az) space so the heatmap forms a stable,
      // seamless dome over the horizon that never clips or shifts when panning
      const altStep = TRACKING_RISK_ALT_STEP_DEG;
      const azStep = TRACKING_RISK_AZ_STEP_DEG;

      for (let alt = 5; alt <= 85; alt += altStep) {
        for (let az = 0; az < 360; az += azStep) {
          const coord0 = projectionContext.getRaDec(alt, az);
          const coordRight = projectionContext.getRaDec(alt, (az + azStep) % 360);
          const coordTop = projectionContext.getRaDec(Math.min(90.0, alt + altStep), az);
          const coordTopRight = projectionContext.getRaDec(Math.min(90.0, alt + altStep), (az + azStep) % 360);

          const p0 = projectionContext.projectCoords(coord0.ra, coord0.dec);
          const pRight = projectionContext.projectCoords(coordRight.ra, coordRight.dec);
          const pTop = projectionContext.projectCoords(coordTop.ra, coordTop.dec);
          const pTopRight = projectionContext.projectCoords(coordTopRight.ra, coordTopRight.dec);

          // If all 4 corners are off-screen outside margins, skip drawing this quad
          const margin = TRACKING_RISK_OFFSCREEN_MARGIN_PX;
          const allOffScreen =
            (p0.x < -margin && pRight.x < -margin && pTop.x < -margin && pTopRight.x < -margin) ||
            (p0.x > width + margin && pRight.x > width + margin && pTop.x > width + margin && pTopRight.x > width + margin) ||
            (p0.y < -margin && pRight.y < -margin && pTop.y < -margin && pTopRight.y < -margin) ||
            (p0.y > height + margin && pRight.y > height + margin && pTop.y > height + margin && pTopRight.y > height + margin);
          if (allOffScreen) continue;

          // Skip quads where points wrap around stereographic pole singularity
          if (!p0.visible && !pRight.visible && !pTop.visible && !pTopRight.visible) continue;

          // Hour angle of the cell: HA = LST - RA, wrapped to -180..+180
          let haDeg = (lst - coord0.ra) % 360;
          if (haDeg > 180) haDeg -= 360;
          if (haDeg < -180) haDeg += 360;

          context.fillStyle = getRiskColor(scoreAt(trackingRisk, haDeg, coord0.dec), trackingRisk);
          context.beginPath();
          context.moveTo(p0.x, p0.y);
          context.lineTo(pRight.x, pRight.y);
          context.lineTo(pTopRight.x, pTopRight.y);
          context.lineTo(pTop.x, pTop.y);
          context.closePath();
          context.fill();
        }
      }
    }

    // Legend badge in the bottom-right corner
    context.fillStyle = 'rgba(15, 23, 42, 0.88)';
    context.strokeStyle = 'rgba(255, 255, 255, 0.14)';
    context.lineWidth = 1;
    const badgeW = 280;
    const badgeH = 54;
    const badgeX = width - badgeW - 16;
    const badgeY = height - badgeH - 16;
    context.beginPath();
    context.roundRect(badgeX, badgeY, badgeW, badgeH, 6);
    context.fill();
    context.stroke();

    context.fillStyle = '#bae6fd';
    context.font = 'bold 11px system-ui, sans-serif';
    const measuredTargets = trackingRisk?.measuredTargetCount ?? 0;
    const badgeTitle = !trackingRisk
      ? 'Tracking Heatmap (no active telescope and camera)'
      : measuredTargets > 0
        ? `Tracking Heatmap (${measuredTargets} targets · ${trackingRisk.solveCount ?? 0} solves)`
        : 'Mount Tracking Heatmap (Prior)';
    context.fillText(badgeTitle, badgeX + 10, badgeY + 17);

    if (trackingRisk) {
      context.font = '10px system-ui, sans-serif';
      context.fillStyle = 'rgba(16, 185, 129, 0.85)';
      context.fillRect(badgeX + 10, badgeY + 28, 10, 10);
      context.fillStyle = '#94a3b8';
      context.fillText(`≤${trackingRisk.idealRmsArcsec.toFixed(1)}" / Round`, badgeX + 24, badgeY + 37);

      context.fillStyle = 'rgba(239, 68, 68, 0.85)';
      context.fillRect(badgeX + 145, badgeY + 28, 10, 10);
      context.fillStyle = '#94a3b8';
      context.fillText(`>${trackingRisk.trailingRmsArcsec.toFixed(1)}" / Trailing`, badgeX + 159, badgeY + 37);
    }

    context.restore();
  }
}
