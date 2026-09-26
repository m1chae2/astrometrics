/**
 * @module TrackingRiskOverlay
 * @fileoverview Visualizes mechanical mount tracking risk across the celestial sphere.
 *
 * For a German Equatorial Mount (such as a Star Adventurer GTi) carrying a heavy
 * payload with an East-heavy counterweight bias:
 * - East of Meridian (HA < 0, 25° < Alt < 75°, -15° < Dec < 65°): Optimal tracking / firm gear preload (Green).
 * - West of Meridian (HA > 0): Gear-float / backlash risk under fixed East-heavy balance (Amber-Red).
 * - Near Pole (Dec > 65°): Cantilever thrust & Dec sleeve bushing stiction risk (Amber).
 * - Low Altitude (Alt < 20°): Atmospheric seeing degradation & high moment arm (Amber-Red).
 */

import { PlanetariumOverlay, ProjectionContext } from './overlayTypes';
import { clusterAlignmentAttempts, ClusteredAlignmentSession } from '../utils/alignmentClustering';

/**
 * Calculates mechanical tracking risk factor prior between 0.0 (optimal) and 1.0 (high risk).
 *
 * @param {number} haDeg - Hour Angle in degrees (-180 to +180, negative is East).
 * @param {number} decDeg - Declination in degrees (-90 to +90).
 * @param {number} altDeg - Altitude in degrees (0 to 90).
 * @returns {number} Prior risk score from 0.0 to 1.0.
 */
function computeMechanicalPriorRisk(haDeg: number, decDeg: number, altDeg: number): number {
  if (altDeg < 0) return 1.0;

  let risk = 0.05; // Base minimal mechanical risk

  // 1. East vs West Meridian Bias:
  // With East-heavy balance, HA > 0 (West of meridian) pulls gear teeth apart (gear float)
  if (haDeg > 0) {
    const westFactor = Math.min(1.0, haDeg / 75.0);
    risk += 0.55 * westFactor;
  }

  // 2. High Declination (Bushing Stiction):
  if (decDeg > 55.0) {
    const polarFactor = Math.min(1.0, (decDeg - 55.0) / 30.0);
    risk += 0.45 * polarFactor;
  }

  // 3. Low Altitude (Seeing & Long Moment Arm):
  if (altDeg < 30.0) {
    const altFactor = (30.0 - altDeg) / 30.0;
    risk += 0.4 * altFactor;
  }

  return Math.min(1.0, Math.max(0.0, risk));
}

/**
 * Calculates angular great-circle separation in degrees between two RA/Dec coordinates.
 *
 * @param {number} ra1 - First RA in degrees.
 * @param {number} dec1 - First Declination in degrees.
 * @param {number} ra2 - Second RA in degrees.
 * @param {number} dec2 - Second Declination in degrees.
 * @returns {number} Angular separation in degrees.
 */
function angularDistanceDeg(ra1: number, dec1: number, ra2: number, dec2: number): number {
  const d2r = Math.PI / 180.0;
  const phi1 = dec1 * d2r;
  const phi2 = dec2 * d2r;
  const deltaPhi = (dec2 - dec1) * d2r;
  const deltaLambda = (ra2 - ra1) * d2r;

  const a =
    Math.sin(deltaPhi / 2.0) ** 2 +
    Math.cos(phi1) * Math.cos(phi2) * Math.sin(deltaLambda / 2.0) ** 2;
  const c = 2.0 * Math.atan2(Math.sqrt(a), Math.sqrt(Math.max(0, 1.0 - a)));
  return (c * 180.0) / Math.PI;
}

/**
 * Maps measured empirical tracking RMS in arcseconds into a normalized risk score (0.0 to 1.0).
 * - <= 0.05" RMS: Ideal tracking (0.05 risk - green)
 * - 0.20" RMS: Acceptable guiding (0.35 risk - yellow-green)
 * - 0.50" RMS: Borderline / degraded (0.65 risk - amber)
 * - >= 1.00" RMS: High jitter / trailing (0.95 risk - red)
 *
 * @param {number} rmsArcsec - Measured total tracking RMS in arcseconds.
 * @returns {number} Risk score from 0.0 to 1.0.
 */
function rmsToRiskScore(rmsArcsec: number): number {
  if (rmsArcsec <= 0.05) return 0.05;
  if (rmsArcsec >= 1.0) return 0.95;
  // Non-linear perceptual scaling
  return Math.min(0.95, Math.max(0.05, 0.05 + 0.9 * Math.sqrt((rmsArcsec - 0.05) / 0.95)));
}

/**
 * Maps a risk score (0..1) to an RGBA fill color.
 *
 * @param {number} risk - Score from 0.0 to 1.0.
 * @returns {string} RGBA color string.
 */
function getRiskColor(risk: number): string {
  if (risk < 0.28) {
    // Optimal: Subtle emerald green
    return `rgba(16, 185, 129, ${0.12 + risk * 0.15})`;
  }
  if (risk < 0.58) {
    // Caution: Amber / yellow
    const t = (risk - 0.28) / 0.3;
    return `rgba(245, 158, 11, ${0.16 + t * 0.16})`;
  }
  // High Risk: Soft warning red/rose
  const t = Math.min(1.0, (risk - 0.58) / 0.42);
  return `rgba(239, 68, 68, ${0.2 + t * 0.2})`;
}

/**
 * TrackingRiskOverlay Class
 *
 * Renders a discrete altitude/azimuth grid mesh tinted by mount mechanical tracking risk.
 */
export class TrackingRiskOverlay implements PlanetariumOverlay {
  id = 'tracking_risk_overlay';
  name = 'Mount Tracking Risk';

  draw(context: CanvasRenderingContext2D, projectionContext: ProjectionContext): void {
    if (!projectionContext.showTrackingRisk) return;

    const { lst, width, height } = projectionContext;

    // Cluster raw plate-solve and tracking logs across cumulative sessions to extract empirical measured RMS
    const rawAttempts =
      projectionContext.cumulativeTrackingAttempts && projectionContext.cumulativeTrackingAttempts.length > 0
        ? projectionContext.cumulativeTrackingAttempts
        : projectionContext.alignmentAttempts || [];
    const clusters: ClusteredAlignmentSession[] = clusterAlignmentAttempts(rawAttempts).filter(
      (c) => c.totalFrames >= 2 && c.rmsTotal > 0
    );
    const hasEmpiricalData = clusters.length > 0;

    context.save();

    // Sample in Horizontal (Alt / Az) space so the heatmap forms a stable,
    // seamless dome over the horizon that never clips or shifts when panning
    const altStep = 10;
    const azStep = 15;

    for (let alt = 5; alt <= 85; alt += altStep) {
      for (let az = 0; az < 360; az += azStep) {
        // Convert the 4 corners of the Alt/Az cell to RA/Dec to evaluate Hour Angle & Dec
        const coord0 = projectionContext.getRaDec(alt, az);
        const coordRight = projectionContext.getRaDec(alt, (az + azStep) % 360);
        const coordTop = projectionContext.getRaDec(Math.min(90.0, alt + altStep), az);
        const coordTopRight = projectionContext.getRaDec(Math.min(90.0, alt + altStep), (az + azStep) % 360);

        // Project the 4 corners to screen coordinates
        const p0 = projectionContext.projectCoords(coord0.ra, coord0.dec);
        const pRight = projectionContext.projectCoords(coordRight.ra, coordRight.dec);
        const pTop = projectionContext.projectCoords(coordTop.ra, coordTop.dec);
        const pTopRight = projectionContext.projectCoords(coordTopRight.ra, coordTopRight.dec);

        // If all 4 corners are off-screen outside margins, skip drawing this quad
        const margin = 80;
        const allOffScreen =
          (p0.x < -margin && pRight.x < -margin && pTop.x < -margin && pTopRight.x < -margin) ||
          (p0.x > width + margin && pRight.x > width + margin && pTop.x > width + margin && pTopRight.x > width + margin) ||
          (p0.y < -margin && pRight.y < -margin && pTop.y < -margin && pTopRight.y < -margin) ||
          (p0.y > height + margin && pRight.y > height + margin && pTop.y > height + margin && pTopRight.y > height + margin);

        if (allOffScreen) continue;

        // Skip quads where points wrap around stereographic pole singularity
        if (!p0.visible && !pRight.visible && !pTop.visible && !pTopRight.visible) continue;

        // Calculate Hour Angle at center of cell: HA = LST - RA (normalized to -180..+180)
        let haDeg = (lst - coord0.ra) % 360;
        if (haDeg > 180) haDeg -= 360;
        if (haDeg < -180) haDeg += 360;

        // 1. Theoretical prior mechanical risk
        const priorRisk = computeMechanicalPriorRisk(haDeg, coord0.dec, alt);

        // 2. Blend with empirical session tracking if nearby sessions exist
        let finalRisk = priorRisk;

        if (hasEmpiricalData) {
          let totalWeight = 0;
          let weightedRiskSum = 0;
          const influenceRadiusDeg = 45.0; // 45° Gaussian influence radius across sky

          for (const session of clusters) {
            const distDeg = angularDistanceDeg(
              coord0.ra,
              coord0.dec,
              session.centroidRa,
              session.centroidDec
            );

            if (distDeg < influenceRadiusDeg * 2.0) {
              // Frame count scaling: more sub-frames = higher statistical confidence
              const confidence = Math.min(1.0, session.totalFrames / 30.0);
              // Gaussian spatial decay: e^(-0.5 * (d / sigma)^2)
              const spatialWeight = Math.exp(-0.5 * ((distDeg / influenceRadiusDeg) ** 2)) * confidence;

              const empiricalScore = rmsToRiskScore(session.rmsTotal);
              weightedRiskSum += empiricalScore * spatialWeight;
              totalWeight += spatialWeight;
            }
          }

          if (totalWeight > 0.001) {
            const empiricalRisk = weightedRiskSum / totalWeight;
            // Adaptive blending factor: up to 85% empirical weight when backed by dense session data
            const blendFactor = Math.min(0.85, totalWeight);
            finalRisk = priorRisk * (1.0 - blendFactor) + empiricalRisk * blendFactor;
          }
        }

        const fillColor = getRiskColor(finalRisk);

        // Fill quad cell
        context.fillStyle = fillColor;
        context.beginPath();
        context.moveTo(p0.x, p0.y);
        context.lineTo(pRight.x, pRight.y);
        context.lineTo(pTopRight.x, pTopRight.y);
        context.lineTo(pTop.x, pTop.y);
        context.closePath();
        context.fill();
      }
    }

    // Render a diagnostic legend badge in bottom-right corner
    context.fillStyle = 'rgba(15, 23, 42, 0.88)';
    context.strokeStyle = 'rgba(255, 255, 255, 0.14)';
    context.lineWidth = 1;
    const isMultiSession = Boolean(
      projectionContext.cumulativeTrackingAttempts && projectionContext.cumulativeTrackingAttempts.length > 0
    );
    const badgeW = isMultiSession ? 260 : 230;
    const badgeH = 54;
    const badgeX = width - badgeW - 16;
    const badgeY = height - badgeH - 16;
    context.beginPath();
    context.roundRect(badgeX, badgeY, badgeW, badgeH, 6);
    context.fill();
    context.stroke();

    context.fillStyle = '#bae6fd';
    context.font = 'bold 11px system-ui, sans-serif';
    const badgeTitle = !hasEmpiricalData
      ? 'Mount Tracking Heatmap (Prior)'
      : isMultiSession
        ? `Tracking Heatmap (${clusters.length} targets · ${rawAttempts.length} solves)`
        : 'Mount Tracking Heatmap (Empirical)';
    context.fillText(badgeTitle, badgeX + 10, badgeY + 17);

    context.font = '10px system-ui, sans-serif';
    // Green sample
    context.fillStyle = 'rgba(16, 185, 129, 0.85)';
    context.fillRect(badgeX + 10, badgeY + 28, 10, 10);
    context.fillStyle = '#94a3b8';
    context.fillText('Sub-0.1" / Firm Preload', badgeX + 24, badgeY + 37);

    // Amber/Red sample
    context.fillStyle = 'rgba(239, 68, 68, 0.85)';
    context.fillRect(badgeX + 140, badgeY + 28, 10, 10);
    context.fillStyle = '#94a3b8';
    context.fillText('High Jitter / Float', badgeX + 154, badgeY + 37);

    context.restore();
  }
}
