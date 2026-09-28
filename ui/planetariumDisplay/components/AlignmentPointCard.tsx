/**
 * @module AlignmentPointCard
 * @fileoverview Floating details card for a selected alignment plate-solve point or clustered session.
 *
 * Displays:
 * 1. Session summary (total frames, total duration, initial slew error, RMS jitter)
 * 2. 2D Polar Bulls-Eye Target showing frame dispersion and star eccentricity ellipse
 * 3. Time-Series Tracking Chart showing dRA/dDec curves and linear polar drift rates
 * 4. Rigorous mathematical metrics table
 */

import React, { useMemo } from 'react';
import { PlanetariumSource, ObserverLocation } from '../../common/types/planetariumTypes';
import { safeParse, formatRA, formatDec } from '../utils/coordinateUtils';
import { formatDuration } from '../utils/alignmentClustering';
import { computeChartMaxBound, computeRingStep } from '../utils/chartScale';

interface Props {
  /** The selected alignment source object. */
  source: PlanetariumSource;
  /** Observer geographic position for contextual calculations. */
  observerLocation: ObserverLocation | null;
  /** Callback to close the card and deselect. */
  onClose: () => void;
}

/** Error-magnitude threshold classes matching planetarium-info-card__badge semantics. */
function errorClassFor(totalError: number): string {
  return totalError <= 30 ? 'planetarium-alignment-chart__value--good'
    : totalError <= 90 ? 'planetarium-alignment-chart__value--warn'
    : 'planetarium-alignment-chart__value--bad';
}

/**
 * AlignmentPointCard Component
 */
export const AlignmentPointCard: React.FC<Props> = ({
  source,
  onClose,
}) => {
  const session = source.alignmentSession;
  const attempt = source.alignmentAttempt;

  const isMultiFrame = session != null && session.totalFrames > 1;

  const dRa = attempt?.deltaRaArcsec ?? 0;
  const dDec = attempt?.deltaDecArcsec ?? 0;
  const totalError = isMultiFrame
    ? session.rmsTotal
    : (attempt?.pointingErrorArcsec ?? Math.hypot(dRa, dDec));

  const raNum = safeParse(source.ra);
  const decNum = safeParse(source.dec);
  const altNum = source.altitude ?? 0;
  const azNum = source.azimuth ?? 0;

  // Meridian orientation: Azimuth > 180° is West of Meridian; <= 180° is East
  const isWest = azNum > 180.0 && azNum < 360.0;

  const statusBadge =
    totalError > 120.0
      ? 'planetarium-info-card__badge planetarium-info-card__badge--red'
      : totalError > 30.0
      ? 'planetarium-info-card__badge planetarium-info-card__badge--yellow'
      : 'planetarium-info-card__badge planetarium-info-card__badge--green';

  const titleText = source.name.replace(/^Sync:\s*/, '');

  // --- 2D Polar Bulls-Eye Computations ---
  // Dynamically auto-scale to actual jitter amplitude (e.g. 0.06" or 1.5")
  const polarData = useMemo(() => {
    if (!session || session.timeSeries.length === 0) return null;
    const pts = session.timeSeries;

    // For multi-frame tracking sessions, calculate the mean pointing offset
    // so we can display residual tracking jitter/dispersion around (0, 0)
    let meanRa = 0;
    let meanDec = 0;
    if (pts.length > 1) {
      pts.forEach((p) => {
        meanRa += p.deltaRa;
        meanDec += p.deltaDec;
      });
      meanRa /= pts.length;
      meanDec /= pts.length;
    }

    let peakError = 0.01;
    pts.forEach((p) => {
      const relRa = pts.length > 1 ? p.deltaRa - meanRa : p.deltaRa;
      const relDec = pts.length > 1 ? p.deltaDec - meanDec : p.deltaDec;
      peakError = Math.max(peakError, Math.abs(relRa), Math.abs(relDec));
    });

    // Provide 25% padding above peak error, with minimum scale of 0.04"
    const maxRadius = computeChartMaxBound(peakError);

    // SVG coordinate mapping: center is (80, 80)
    const size = 160;
    const center = size / 2;
    const scale = (center - 14) / maxRadius; // pixels per arcsec

    // Generate 2 or 3 clean concentric rings regardless of maxRadius magnitude
    const ringStep = computeRingStep(maxRadius);

    const rings: number[] = [];
    for (let r = ringStep; r <= maxRadius * 0.95; r += ringStep) {
      rings.push(Number(r.toFixed(3)));
    }

    // Calculate 1-sigma ellipse dimensions (rmsRa, rmsDec)
    const ellipseRx = Math.max(4, session.rmsRa * scale);
    const ellipseRy = Math.max(4, session.rmsDec * scale);

    const projectedPoints = pts.map((p, idx) => {
      const relRa = pts.length > 1 ? p.deltaRa - meanRa : p.deltaRa;
      const relDec = pts.length > 1 ? p.deltaDec - meanDec : p.deltaDec;
      const x = center + relRa * scale;
      const y = center - relDec * scale; // Inverted Y for Dec
      const alpha = 0.4 + 0.6 * (idx / Math.max(1, pts.length - 1));
      return { x, y, alpha, dRa: relRa, dDec: relDec };
    });

    return { size, center, scale, maxRadius, rings, ellipseRx, ellipseRy, projectedPoints, meanRa, meanDec };
  }, [session]);

  // --- Time-Series Tracking Chart Computations ---
  const timeData = useMemo(() => {
    if (!session || session.timeSeries.length < 2) return null;
    const pts = session.timeSeries;

    const width = 280;
    const height = 90;
    const padX = 36;
    const padY = 12;

    const maxT = Math.max(1, session.elapsedSeconds);

    let meanRa = 0;
    let meanDec = 0;
    pts.forEach((p) => {
      meanRa += p.deltaRa;
      meanDec += p.deltaDec;
    });
    meanRa /= pts.length;
    meanDec /= pts.length;

    let peakVal = 0.01;
    pts.forEach((p) => {
      const relRa = p.deltaRa - meanRa;
      const relDec = p.deltaDec - meanDec;
      peakVal = Math.max(peakVal, Math.abs(relRa), Math.abs(relDec));
    });

    const maxVal = computeChartMaxBound(peakVal);

    const toX = (sec: number) => padX + (sec / maxT) * (width - padX - 8);
    const toY = (val: number) => height / 2 - (val / maxVal) * (height / 2 - padY);

    // Construct SVG paths using relative jitter centered at 0
    const raPath = pts.reduce((acc, p, i) => `${acc} ${i === 0 ? 'M' : 'L'} ${toX(p.elapsedSec).toFixed(1)} ${toY(p.deltaRa - meanRa).toFixed(1)}`, '');
    const decPath = pts.reduce((acc, p, i) => `${acc} ${i === 0 ? 'M' : 'L'} ${toX(p.elapsedSec).toFixed(1)} ${toY(p.deltaDec - meanDec).toFixed(1)}`, '');

    return {
      width,
      height,
      padX,
      maxVal,
      raPath,
      decPath,
      zeroY: height / 2,
    };
  }, [session]);

  return (
    <div
      className="planetarium-info-card planetarium-info-card--top-right planetarium-info-card--alignment"
      role="dialog"
      aria-label={`Alignment sync info for ${titleText}`}
    >
      {/* Header */}
      <div className="planetarium-info-card__header">
        <div className="planetarium-info-card__avatar-container">
          <div className="planetarium-info-card__avatar planetarium-info-card__avatar--alignment">
            <div className="planetarium-info-card__star-dot planetarium-info-card__star-dot--alignment" />
          </div>
          <div className="planetarium-info-card__title-group">
            <h3 className="planetarium-info-card__title planetarium-info-card__title--compact">{titleText}</h3>
            <div className="planetarium-info-card__subtitle">
              <span>{isMultiFrame ? `${session?.totalFrames} Frames Session` : 'Plate Solve Sync'}</span>
              <span className="planetarium-info-card__divider">&bull;</span>
              <span className={statusBadge}>{totalError <= 30 ? 'ALIGNED' : 'TRACKING'}</span>
            </div>
          </div>
        </div>
        <button
          className="planetarium-info-card__close"
          onClick={onClose}
          aria-label="Close panel"
        >
          &times;
        </button>
      </div>

      {/* 2D Polar Target Graphics */}
      {polarData && isMultiFrame && (
        <div className="planetarium-alignment-chart">
          <div className="planetarium-alignment-chart__header">
            <span className="planetarium-alignment-chart__label">2D Dispersion Target</span>
            <span>Scale: &plusmn;{polarData.maxRadius}&Prime;</span>
          </div>

          <svg width={polarData.size} height={polarData.size} className="planetarium-alignment-chart__svg">
            {/* Concentric Calibration Rings */}
            {polarData.rings.map((r) => (
              <g key={r}>
                <circle
                  cx={polarData.center}
                  cy={polarData.center}
                  r={r * polarData.scale}
                  className="planetarium-alignment-chart__ring"
                />
                <text
                  x={polarData.center + r * polarData.scale + 2}
                  y={polarData.center - 3}
                  className="planetarium-alignment-chart__ring-label"
                >
                  {r}&Prime;
                </text>
              </g>
            ))}

            {/* Crosshair Axes */}
            <line x1={0} y1={polarData.center} x2={polarData.size} y2={polarData.center} className="planetarium-alignment-chart__axis" />
            <line x1={polarData.center} y1={0} x2={polarData.center} y2={polarData.size} className="planetarium-alignment-chart__axis" />

            {/* 1-Sigma Dispersion Ellipse */}
            <ellipse
              cx={polarData.center}
              cy={polarData.center}
              rx={polarData.ellipseRx}
              ry={polarData.ellipseRy}
              className="planetarium-alignment-chart__ellipse"
            />

            {/* Scatter Points */}
            {polarData.projectedPoints.map((pt, i) => (
              <circle
                key={i}
                cx={pt.x}
                cy={pt.y}
                r={3.5}
                className="planetarium-alignment-chart__point"
                opacity={pt.alpha}
              />
            ))}
          </svg>

          <div className="planetarium-alignment-chart__legend">
            <span className="planetarium-alignment-chart__legend-item planetarium-alignment-chart__legend-item--ellipse">&bull; 1&sigma; Error Ellipse</span>
            <span className="planetarium-alignment-chart__legend-item planetarium-alignment-chart__legend-item--point">&bull; Solved Frame Centroid</span>
          </div>
        </div>
      )}

      {/* Time-Series Tracking Chart */}
      {timeData && isMultiFrame && (
        <div className="planetarium-alignment-chart planetarium-alignment-chart--time">
          <div className="planetarium-alignment-chart__header">
            <span className="planetarium-alignment-chart__label">Tracking Drift vs Time</span>
            <span className="planetarium-alignment-chart__legend-item--ra">&Delta;RA (cyan)</span>
            <span className="planetarium-alignment-chart__legend-item--dec">&Delta;Dec (red)</span>
          </div>

          <svg width={timeData.width} height={timeData.height} className="planetarium-alignment-chart__svg">
            {/* Zero Axis */}
            <line x1={timeData.padX} y1={timeData.zeroY} x2={timeData.width} y2={timeData.zeroY} className="planetarium-alignment-chart__zero-axis" />
            <text x={4} y={timeData.zeroY + 3} className="planetarium-alignment-chart__ring-label">0&Prime;</text>
            <text x={4} y={12} className="planetarium-alignment-chart__axis-label">+{timeData.maxVal}&Prime;</text>
            <text x={4} y={timeData.height - 4} className="planetarium-alignment-chart__axis-label">-{timeData.maxVal}&Prime;</text>

            {/* Traces */}
            <path d={timeData.raPath} className="planetarium-alignment-chart__trace planetarium-alignment-chart__trace--ra" />
            <path d={timeData.decPath} className="planetarium-alignment-chart__trace planetarium-alignment-chart__trace--dec" />
          </svg>
        </div>
      )}

      {/* Structured Content Table */}
      <div className="planetarium-info-card__content planetarium-info-card__content--tight">
        <table className="planetarium-info-card__table">
          <tbody>
            {isMultiFrame ? (
              <>
                <tr>
                  <td>Tracking Dispersion (RMS)</td>
                  <td className={errorClassFor(totalError)}>
                    {session.rmsTotal.toFixed(2)}&Prime;
                  </td>
                </tr>
                <tr>
                  <td>&Delta;RA / &Delta;Dec RMS</td>
                  <td>
                    {session.rmsRa.toFixed(2)}&Prime; / {session.rmsDec.toFixed(2)}&Prime;
                  </td>
                </tr>
                <tr>
                  <td>Initial Slew Error</td>
                  <td className="planetarium-alignment-chart__value--warn">
                    {session.initialErrorArcsec >= 60
                      ? `${(session.initialErrorArcsec / 60).toFixed(1)}'`
                      : `${session.initialErrorArcsec.toFixed(1)}"`}
                  </td>
                </tr>
                <tr>
                  <td>Total Sub-Frames</td>
                  <td>{session.totalFrames} frames</td>
                </tr>
                <tr>
                  <td>Total Exposure Time</td>
                  <td>{formatDuration(session.elapsedSeconds)}</td>
                </tr>
                {Math.abs(session.driftSlopeDecArcsecPerMin) > 0.001 && (
                  <tr>
                    <td>Polar Drift Rate (Dec)</td>
                    <td>{session.driftSlopeDecArcsecPerMin > 0 ? `+${session.driftSlopeDecArcsecPerMin.toFixed(2)}` : session.driftSlopeDecArcsecPerMin.toFixed(2)}&Prime;/min</td>
                  </tr>
                )}
              </>
            ) : (
              <>
                <tr>
                  <td>Pointing Error</td>
                  <td className={errorClassFor(totalError)}>
                    {totalError.toFixed(1)}&Prime;
                  </td>
                </tr>
                <tr>
                  <td>&Delta;RA / &Delta;Dec Offset</td>
                  <td>
                    {dRa >= 0 ? `+${dRa.toFixed(1)}"` : `${dRa.toFixed(1)}"`} / {dDec >= 0 ? `+${dDec.toFixed(1)}"` : `${dDec.toFixed(1)}"`}
                  </td>
                </tr>
              </>
            )}
            <tr>
              <td>Target Coordinates</td>
              <td>{formatRA(raNum)} / {formatDec(decNum)}</td>
            </tr>
            <tr>
              <td>Altitude / Azimuth</td>
              <td>{altNum.toFixed(1)}&deg; / {azNum.toFixed(1)}&deg;</td>
            </tr>
            <tr>
              <td>Meridian Side</td>
              <td>
                <span className={isWest ? 'planetarium-info-card__badge planetarium-info-card__badge--yellow' : 'planetarium-info-card__badge planetarium-info-card__badge--green'}>
                  {isWest ? 'West of Meridian' : 'East of Meridian'}
                </span>
              </td>
            </tr>
            {attempt?.timestamp && (
              <tr>
                <td>Solve Timestamp</td>
                <td>
                  {new Date(attempt.timestamp * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
