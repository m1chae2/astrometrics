/**
 * @module AlignmentOverlay
 * @fileoverview Renders telescope plate-solve pointing error vectors and
 * KStars/Ekos Polar Alignment Assistant (PAA) projections on the Planetarium canvas.
 */

import { PlanetariumOverlay, ProjectionContext } from './overlayTypes';
import { AlignmentAttempt, PolarAlignmentStatus } from '../../common/types/backendTypes';
import { clusterAlignmentAttempts, formatDuration } from '../utils/alignmentClustering';
import { pixelsPerDegree } from '../utils/projectionMath';

/** Radius of the sync point reticle in pixels. */
const RETICLE_RADIUS_PX = 8;

/** Arrowhead tip size in pixels. */
const ARROW_HEAD_PX = 6;

/**
 * Normalizes RA from hours (0..24) or degrees to decimal degrees (0..360).
 *
 * @param {number | null | undefined} ra - RA in hours or degrees.
 * @returns {number} RA in decimal degrees.
 */
function normalizeRaDeg(ra: number | null | undefined): number {
  if (ra === null || ra === undefined || isNaN(ra)) return 0;
  // If <= 24.0, assume hours and convert to degrees
  return ra <= 24.0 ? (ra * 15.0) % 360.0 : ra % 360.0;
}

/**
 * Returns error-magnitude color coding.
 *
 * @param {number} errorArcsec - Total error in arcseconds.
 * @param {string} status - Attempt status string.
 * @returns {string} CSS rgba color string.
 */
function getErrorColor(errorArcsec: number, status: string): string {
  if (status === 'failed') return 'rgba(239, 68, 68, 0.95)'; // Red
  if (errorArcsec <= 30.0 || status === 'aligned') return 'rgba(16, 185, 129, 0.95)'; // Emerald
  if (errorArcsec <= 180.0 || status === 'warning') return 'rgba(245, 158, 11, 0.95)'; // Amber
  return 'rgba(239, 68, 68, 0.95)'; // Red
}

/**
 * Formats error arcseconds into human-readable string (e.g. 14" or 2.4').
 *
 * @param {number | null | undefined} errorArcsec - Error in arcseconds.
 * @returns {string} Formatted string.
 */
function formatError(errorArcsec: number | null | undefined): string {
  if (errorArcsec === null || errorArcsec === undefined || isNaN(errorArcsec)) return '—';
  if (Math.abs(errorArcsec) >= 60.0) {
    return `${(errorArcsec / 60.0).toFixed(1)}'`;
  }
  if (Math.abs(errorArcsec) < 1.0 && Math.abs(errorArcsec) > 0.0) {
    return `${errorArcsec.toFixed(2)}"`;
  }
  return `${errorArcsec.toFixed(0)}"`;
}

/**
 * AlignmentOverlay Class
 *
 * Implements PlanetariumOverlay to project pointing error vectors, sequential
 * alignment paths, and polar alignment assistant measurements onto the celestial sphere.
 */
export class AlignmentOverlay implements PlanetariumOverlay {
  id = 'alignment_overlay';
  name = 'Alignment & Polar Vectors';

  /**
   * Main draw method called by CelestialSkyMap.
   *
   * @param {CanvasRenderingContext2D} context - Canvas 2D context.
   * @param {ProjectionContext} projectionContext - Projection viewport context.
   * @returns {void}
   */
  draw(context: CanvasRenderingContext2D, projectionContext: ProjectionContext): void {
    if (!projectionContext.showAlignment) return;

    context.save();

    // 1. Draw Pointing Error Vectors & Historical Sync Points
    this.drawPointingSyncs(context, projectionContext);

    // 2. Draw Polar Alignment Assistant (PAA) Vectors & Pole Reticle
    this.drawPolarAlignment(context, projectionContext);

    context.restore();
  }

  /**
   * Draws pointing sync reticles, error arrows, and connecting trails.
   */
  private drawPointingSyncs(
    context: CanvasRenderingContext2D,
    projectionContext: ProjectionContext
  ): void {
    const rawAttempts: AlignmentAttempt[] = projectionContext.alignmentAttempts || [];
    const clusters = clusterAlignmentAttempts(rawAttempts);
    if (clusters.length === 0) return;

    // Track projected screen points for sequential path connection between session centroids
    const projectedPath: { x: number; y: number }[] = [];

    // Scale for converting FOV degrees to canvas pixels
    const scale = pixelsPerDegree(projectionContext.width, projectionContext.height, projectionContext.fov);
    const defaultFovDeg = 1.0;
    const fovWidthDeg = projectionContext.sensorFovWidthDeg ?? defaultFovDeg;
    const fovHeightDeg = projectionContext.sensorFovHeightDeg ?? defaultFovDeg;
    const fovWidthPx = Math.max(24, fovWidthDeg * scale);
    const fovHeightPx = Math.max(24, fovHeightDeg * scale);

    clusters.forEach((cluster, index) => {
      const screenPt = projectionContext.projectCoords(cluster.centroidRa, cluster.centroidDec);
      if (!screenPt.visible) return;

      projectedPath.push({ x: screenPt.x, y: screenPt.y });

      const isMultiFrame = cluster.totalFrames > 1;
      const displayErr = isMultiFrame ? cluster.rmsTotal : cluster.initialErrorArcsec;
      const color = getErrorColor(displayErr, isMultiFrame ? 'aligned' : 'warning');

      if (isMultiFrame) {
        // --- Draw Camera Sensor FOV Footprint Box ---
        context.strokeStyle = color;
        context.fillStyle = 'rgba(16, 185, 129, 0.08)';
        context.lineWidth = 1.5;
        context.setLineDash([4, 2]);

        const boxX = screenPt.x - fovWidthPx / 2;
        const boxY = screenPt.y - fovHeightPx / 2;

        context.beginPath();
        context.rect(boxX, boxY, fovWidthPx, fovHeightPx);
        context.fill();
        context.stroke();
        context.setLineDash([]);

        // Center crosshair / optical center
        context.beginPath();
        context.arc(screenPt.x, screenPt.y, 3, 0, 2 * Math.PI);
        context.fillStyle = color;
        context.fill();

        // Corner tick marks
        const tickLen = 6;
        context.beginPath();
        // Top-left
        context.moveTo(boxX, boxY + tickLen); context.lineTo(boxX, boxY); context.lineTo(boxX + tickLen, boxY);
        // Top-right
        context.moveTo(boxX + fovWidthPx - tickLen, boxY); context.lineTo(boxX + fovWidthPx, boxY); context.lineTo(boxX + fovWidthPx, boxY + tickLen);
        // Bottom-left
        context.moveTo(boxX, boxY + fovHeightPx - tickLen); context.lineTo(boxX, boxY + fovHeightPx); context.lineTo(boxX + tickLen, boxY + fovHeightPx);
        // Bottom-right
        context.moveTo(boxX + fovWidthPx - tickLen, boxY + fovHeightPx); context.lineTo(boxX + fovWidthPx, boxY + fovHeightPx); context.lineTo(boxX + fovWidthPx, boxY + fovHeightPx - tickLen);
        context.stroke();
      } else {
        // --- Standalone Single Sync Reticle (Diamond + Center Dot) ---
        context.strokeStyle = color;
        context.fillStyle = color;
        context.lineWidth = 1.5;

        context.beginPath();
        context.moveTo(screenPt.x, screenPt.y - RETICLE_RADIUS_PX);
        context.lineTo(screenPt.x + RETICLE_RADIUS_PX, screenPt.y);
        context.lineTo(screenPt.x, screenPt.y + RETICLE_RADIUS_PX);
        context.lineTo(screenPt.x - RETICLE_RADIUS_PX, screenPt.y);
        context.closePath();
        context.stroke();

        context.beginPath();
        context.arc(screenPt.x, screenPt.y, 2, 0, 2 * Math.PI);
        context.fill();
      }

      // --- Draw Initial Slew Error Vector Arrow if significant (> 30") ---
      if (cluster.initialErrorArcsec > 30.0) {
        const first = cluster.rawAttempts[0];
        const dRa = first.deltaRaArcsec || 0;
        const dDec = first.deltaDecArcsec || 0;
        const lengthPx = Math.min(55, Math.max(16, Math.sqrt(cluster.initialErrorArcsec) * 2.8));
        const angle = Math.atan2(-dDec, dRa);

        const tipX = screenPt.x + Math.cos(angle) * lengthPx;
        const tipY = screenPt.y + Math.sin(angle) * lengthPx;

        context.strokeStyle = 'rgba(239, 68, 68, 0.85)';
        context.fillStyle = 'rgba(239, 68, 68, 0.85)';
        context.lineWidth = 1.5;

        context.beginPath();
        context.moveTo(screenPt.x, screenPt.y);
        context.lineTo(tipX, tipY);
        context.stroke();

        context.beginPath();
        context.moveTo(tipX, tipY);
        context.lineTo(
          tipX - ARROW_HEAD_PX * Math.cos(angle - Math.PI / 6),
          tipY - ARROW_HEAD_PX * Math.sin(angle - Math.PI / 6)
        );
        context.lineTo(
          tipX - ARROW_HEAD_PX * Math.cos(angle + Math.PI / 6),
          tipY - ARROW_HEAD_PX * Math.sin(angle + Math.PI / 6)
        );
        context.closePath();
        context.fill();
      }

      // --- Draw Single Consolidated Session Badge ---
      const labelText = isMultiFrame
        ? `${cluster.targetName} (${cluster.totalFrames} frames · ${formatDuration(cluster.elapsedSeconds)} · ${formatError(cluster.rmsTotal)} RMS)`
        : `${cluster.targetName} (${formatError(cluster.initialErrorArcsec)})`;

      context.font = '10px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
      const textWidth = context.measureText(labelText).width;

      context.fillStyle = 'rgba(15, 23, 42, 0.85)';
      context.strokeStyle = 'rgba(51, 65, 85, 0.8)';
      context.lineWidth = 1;

      const tagX = isMultiFrame ? screenPt.x - textWidth / 2 : screenPt.x + RETICLE_RADIUS_PX + 4;
      const tagY = isMultiFrame ? screenPt.y - fovHeightPx / 2 - 8 : screenPt.y - 8;

      context.beginPath();
      context.roundRect(tagX - 4, tagY - 10, textWidth + 8, 14, 3);
      context.fill();
      context.stroke();

      context.fillStyle = color;
      context.fillText(labelText, tagX, tagY);
    });

    // --- Connect Sequential Path Trail Between Sessions ---
    if (projectedPath.length > 1) {
      context.strokeStyle = 'rgba(0, 243, 255, 0.4)';
      context.lineWidth = 1;
      context.setLineDash([4, 4]);

      context.beginPath();
      context.moveTo(projectedPath[0].x, projectedPath[0].y);
      for (let i = 1; i < projectedPath.length; i++) {
        context.lineTo(projectedPath[i].x, projectedPath[i].y);
      }
      context.stroke();
      context.setLineDash([]);
    }
  }

  /**
   * Draws True Pole reticle, Mount RA Axis pole, and PAA error vector.
   */
  private drawPolarAlignment(
    context: CanvasRenderingContext2D,
    projectionContext: ProjectionContext
  ): void {
    const isNorth = projectionContext.observerLat >= 0;
    const truePoleDec = isNorth ? 90.0 : -90.0;
    const poleName = isNorth ? 'NCP' : 'SCP';

    const truePolePt = projectionContext.projectCoords(0.0, truePoleDec);
    const polarStatus: PolarAlignmentStatus | null = projectionContext.polarAlignment ?? null;

    if (truePolePt.visible) {
      // True Celestial Pole Reticle (Gold)
      context.strokeStyle = 'rgba(251, 191, 36, 0.9)';
      context.fillStyle = 'rgba(251, 191, 36, 0.9)';
      context.lineWidth = 1.5;

      context.beginPath();
      context.arc(truePolePt.x, truePolePt.y, 14, 0, 2 * Math.PI);
      context.stroke();

      context.beginPath();
      context.arc(truePolePt.x, truePolePt.y, 3, 0, 2 * Math.PI);
      context.fill();

      // Cross ticks
      context.beginPath();
      context.moveTo(truePolePt.x - 20, truePolePt.y);
      context.lineTo(truePolePt.x - 14, truePolePt.y);
      context.moveTo(truePolePt.x + 14, truePolePt.y);
      context.lineTo(truePolePt.x + 20, truePolePt.y);
      context.moveTo(truePolePt.x, truePolePt.y - 20);
      context.lineTo(truePolePt.x, truePolePt.y - 14);
      context.moveTo(truePolePt.x, truePolePt.y + 14);
      context.lineTo(truePolePt.x, truePolePt.y + 20);
      context.stroke();

      context.font = 'bold 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
      context.fillText(`True ${poleName}`, truePolePt.x + 18, truePolePt.y - 6);
    }

    // If Polar Alignment Assistant telemetry is available
    if (polarStatus && polarStatus.totalErrorArcsec !== null && polarStatus.totalErrorArcsec !== undefined) {
      const poleRa = polarStatus.poleRa !== null && polarStatus.poleRa !== undefined
        ? normalizeRaDeg(polarStatus.poleRa)
        : 0.0;
      const poleDec = polarStatus.poleDec !== null && polarStatus.poleDec !== undefined
        ? polarStatus.poleDec
        : truePoleDec;

      const mountPolePt = projectionContext.projectCoords(poleRa, poleDec);

      if (mountPolePt.visible) {
        // Mount Axis Reticle (Cyan)
        context.strokeStyle = 'rgba(6, 182, 212, 0.95)';
        context.fillStyle = 'rgba(6, 182, 212, 0.95)';
        context.lineWidth = 1.5;

        context.beginPath();
        context.arc(mountPolePt.x, mountPolePt.y, 10, 0, 2 * Math.PI);
        context.stroke();

        context.beginPath();
        context.arc(mountPolePt.x, mountPolePt.y, 2, 0, 2 * Math.PI);
        context.fill();

        context.font = '10px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        context.fillText('Mount RA Axis', mountPolePt.x + 14, mountPolePt.y + 12);

        // Vector from Mount Pole to True Pole
        if (truePolePt.visible) {
          context.strokeStyle = 'rgba(239, 68, 68, 0.9)';
          context.lineWidth = 2;
          context.setLineDash([3, 3]);

          context.beginPath();
          context.moveTo(mountPolePt.x, mountPolePt.y);
          context.lineTo(truePolePt.x, truePolePt.y);
          context.stroke();
          context.setLineDash([]);
        }
      }

      // Draw PAA measurement points along rotation arc
      if (polarStatus.paaPoints && polarStatus.paaPoints.length > 0) {
        context.strokeStyle = 'rgba(168, 85, 247, 0.7)';
        context.fillStyle = 'rgba(168, 85, 247, 0.9)';
        context.lineWidth = 1;

        polarStatus.paaPoints.forEach((pt, idx) => {
          const ptScreen = projectionContext.projectCoords(normalizeRaDeg(pt.ra), pt.dec);
          if (ptScreen.visible) {
            context.beginPath();
            context.arc(ptScreen.x, ptScreen.y, 5, 0, 2 * Math.PI);
            context.stroke();
            context.fill();
            context.font = '9px monospace';
            context.fillText(`PAA #${idx + 1}`, ptScreen.x + 7, ptScreen.y - 4);
          }
        });
      }
    }
  }
}
