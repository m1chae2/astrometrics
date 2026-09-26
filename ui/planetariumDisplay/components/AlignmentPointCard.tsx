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

interface Props {
  /** The selected alignment source object. */
  source: PlanetariumSource;
  /** Observer geographic position for contextual calculations. */
  observerLocation: ObserverLocation | null;
  /** Callback to close the card and deselect. */
  onClose: () => void;
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
    let maxRadius = Math.max(0.04, peakError * 1.25);
    // Round to a clean decimal depending on magnitude
    if (maxRadius < 0.2) {
      maxRadius = Math.ceil(maxRadius * 100) / 100;
    } else if (maxRadius < 1.0) {
      maxRadius = Math.ceil(maxRadius * 20) / 20;
    } else if (maxRadius < 10.0) {
      maxRadius = Math.ceil(maxRadius * 2) / 2;
    } else {
      maxRadius = Math.ceil(maxRadius / 5) * 5;
    }

    // SVG coordinate mapping: center is (80, 80)
    const size = 160;
    const center = size / 2;
    const scale = (center - 14) / maxRadius; // pixels per arcsec

    // Generate 2 or 3 clean concentric rings regardless of maxRadius magnitude
    let rawStep = maxRadius / 3;
    let ringStep = 0.5;
    if (rawStep < 0.05) ringStep = 0.02;
    else if (rawStep < 0.15) ringStep = 0.05;
    else if (rawStep < 0.35) ringStep = 0.2;
    else if (rawStep < 0.75) ringStep = 0.5;
    else if (rawStep < 2.5) ringStep = 1.0;
    else if (rawStep < 7.5) ringStep = 5.0;
    else if (rawStep < 25.0) ringStep = 10.0;
    else if (rawStep < 75.0) ringStep = 50.0;
    else ringStep = Math.ceil(rawStep / 50) * 50;

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

    let maxVal = Math.max(0.04, peakVal * 1.25);
    if (maxVal < 0.2) {
      maxVal = Math.ceil(maxVal * 100) / 100;
    } else if (maxVal < 1.0) {
      maxVal = Math.ceil(maxVal * 20) / 20;
    } else if (maxVal < 10.0) {
      maxVal = Math.ceil(maxVal * 2) / 2;
    } else {
      maxVal = Math.ceil(maxVal / 5) * 5;
    }

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
      className="planetarium-info-card planetarium-info-card--top-right"
      style={{ width: '320px', maxHeight: '88vh', overflowY: 'auto' }}
      role="dialog"
      aria-label={`Alignment sync info for ${titleText}`}
    >
      {/* Header */}
      <div className="planetarium-info-card__header">
        <div className="planetarium-info-card__avatar-container">
          <div
            className="planetarium-info-card__avatar"
            style={{
              border: '2px solid #10b981',
              boxShadow: '0 0 10px rgba(16, 185, 129, 0.4)',
            }}
          >
            <div
              className="planetarium-info-card__star-dot"
              style={{ backgroundColor: '#10b981', transform: 'rotate(45deg)', borderRadius: '2px' }}
            />
          </div>
          <div className="planetarium-info-card__title-group">
            <h3 className="planetarium-info-card__title" style={{ fontSize: '14px', lineHeight: '1.2' }}>{titleText}</h3>
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
        <div style={{ padding: '8px 16px 0', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%', fontSize: '11px', color: '#94a3b8', marginBottom: '4px' }}>
            <span style={{ fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px' }}>2D Dispersion Target</span>
            <span>Scale: &plusmn;{polarData.maxRadius}&Prime;</span>
          </div>

          <svg width={polarData.size} height={polarData.size} style={{ background: 'rgba(15, 23, 42, 0.6)', borderRadius: '6px', border: '1px solid rgba(255,255,255,0.06)' }}>
            {/* Concentric Calibration Rings */}
            {polarData.rings.map((r) => (
              <g key={r}>
                <circle
                  cx={polarData.center}
                  cy={polarData.center}
                  r={r * polarData.scale}
                  fill="none"
                  stroke="rgba(255, 255, 255, 0.12)"
                  strokeDasharray="3 3"
                />
                <text
                  x={polarData.center + r * polarData.scale + 2}
                  y={polarData.center - 3}
                  fill="rgba(148, 163, 184, 0.6)"
                  fontSize="9px"
                  fontFamily="monospace"
                >
                  {r}&Prime;
                </text>
              </g>
            ))}

            {/* Crosshair Axes */}
            <line x1={0} y1={polarData.center} x2={polarData.size} y2={polarData.center} stroke="rgba(255, 255, 255, 0.15)" strokeWidth={1} />
            <line x1={polarData.center} y1={0} x2={polarData.center} y2={polarData.size} stroke="rgba(255, 255, 255, 0.15)" strokeWidth={1} />

            {/* 1-Sigma Dispersion Ellipse */}
            <ellipse
              cx={polarData.center}
              cy={polarData.center}
              rx={polarData.ellipseRx}
              ry={polarData.ellipseRy}
              fill="rgba(16, 185, 129, 0.08)"
              stroke="#10b981"
              strokeWidth={1.2}
              strokeDasharray="4 2"
            />

            {/* Scatter Points */}
            {polarData.projectedPoints.map((pt, i) => (
              <circle
                key={i}
                cx={pt.x}
                cy={pt.y}
                r={3.5}
                fill={`rgba(56, 189, 248, ${pt.alpha})`}
                stroke="rgba(255, 255, 255, 0.4)"
                strokeWidth={0.75}
              />
            ))}
          </svg>

          <div style={{ display: 'flex', gap: '12px', fontSize: '10px', color: '#94a3b8', marginTop: '4px' }}>
            <span style={{ color: '#10b981' }}>&bull; 1&sigma; Error Ellipse</span>
            <span style={{ color: '#38bdf8' }}>&bull; Solved Frame Centroid</span>
          </div>
        </div>
      )}

      {/* Time-Series Tracking Chart */}
      {timeData && isMultiFrame && (
        <div style={{ padding: '10px 16px 0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%', fontSize: '11px', color: '#94a3b8', marginBottom: '4px' }}>
            <span style={{ fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px' }}>Tracking Drift vs Time</span>
            <span style={{ color: '#38bdf8' }}>&Delta;RA (cyan)</span>
            <span style={{ color: '#f43f5e' }}>&Delta;Dec (red)</span>
          </div>

          <svg width={timeData.width} height={timeData.height} style={{ background: 'rgba(15, 23, 42, 0.6)', borderRadius: '6px', border: '1px solid rgba(255,255,255,0.06)' }}>
            {/* Zero Axis */}
            <line x1={timeData.padX} y1={timeData.zeroY} x2={timeData.width} y2={timeData.zeroY} stroke="rgba(255, 255, 255, 0.15)" strokeDasharray="2 2" />
            <text x={4} y={timeData.zeroY + 3} fill="rgba(148, 163, 184, 0.6)" fontSize="9px" fontFamily="monospace">0&Prime;</text>
            <text x={4} y={12} fill="rgba(148, 163, 184, 0.5)" fontSize="9px" fontFamily="monospace">+{timeData.maxVal}&Prime;</text>
            <text x={4} y={timeData.height - 4} fill="rgba(148, 163, 184, 0.5)" fontSize="9px" fontFamily="monospace">-{timeData.maxVal}&Prime;</text>

            {/* Traces */}
            <path d={timeData.raPath} fill="none" stroke="#38bdf8" strokeWidth={1.5} opacity={0.85} />
            <path d={timeData.decPath} fill="none" stroke="#f43f5e" strokeWidth={1.5} opacity={0.85} />
          </svg>
        </div>
      )}

      {/* Structured Content Table */}
      <div className="planetarium-info-card__content" style={{ paddingTop: '10px' }}>
        <table className="planetarium-info-card__table">
          <tbody>
            {isMultiFrame ? (
              <>
                <tr>
                  <td>Tracking Dispersion (RMS)</td>
                  <td style={{ color: totalError <= 30 ? '#4ade80' : totalError <= 90 ? '#facc15' : '#f87171', fontWeight: 600 }}>
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
                  <td style={{ color: '#facc15' }}>
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
                  <td style={{ color: totalError <= 30 ? '#4ade80' : totalError <= 90 ? '#facc15' : '#f87171', fontWeight: 600 }}>
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
