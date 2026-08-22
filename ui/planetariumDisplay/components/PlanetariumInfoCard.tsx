/**
 * @module PlanetariumInfoCard
 * @fileoverview Floating details card for a selected celestial object.
 *
 * Displays coordinates (sexagesimal and decimal), altitude/azimuth, hour angle,
 * rise/set times, meridian flip status, and inline spectrum preview for selected
 * stars. Also renders checkboxes to open the full scientific plot panels.
 *
 * REQ: PLN-2.4, REQ: PLN-2.5
 */

import React from 'react';
import { PlanetariumSource, ObserverLocation } from '../../common/types/planetariumTypes';
import { SpectrumViewer } from '../../astronomyDisplay/components/SpectrumViewer';
import { ParsedAstronomyData } from '../../astronomyDisplay/hooks/useSpectrumData';
import { safeParse, formatRA, formatDec, formatNumber } from '../utils/coordinateUtils';

/**
 * Props for PlanetariumInfoCard.
 */
interface Props {
  /** The celestial object to display. */
  source: PlanetariumSource;
  /** Observer geographic position, used for contextual display. */
  observerLocation: ObserverLocation | null;
  /** Callback to close and deselect the card. */
  onClose: () => void;
  /** Whether the full spectroscopy plot panel is currently visible. */
  showSpectraPlot: boolean;
  onShowSpectraPlotChange: (show: boolean) => void;
  /** Whether the full photometry plot panel is currently visible. */
  showPhotometryPlot: boolean;
  onShowPhotometryPlotChange: (show: boolean) => void;
  /** Astronomy data payload from useSpectrumData, used for the inline spectrum preview. */
  astronomyData?: ParsedAstronomyData | null;
  plotLoading?: boolean;
  plotError?: string | null;
}

/**
 * Maps Harvard spectral class letters to approximate visual colour hex values.
 * Used to tint the star avatar to match the object's colour temperature.
 *
 * @see https://en.wikipedia.org/wiki/Stellar_classification
 */
const spectralColors: Record<string, string> = {
  O: '#4e9eff',
  B: '#a2c4ff',
  A: '#ffffff',
  F: '#fcf8d5',
  G: '#ffea75',
  K: '#ffb969',
  M: '#ff7752',
};

/**
 * Floating details panel rendered over the canvas when a source is selected.
 *
 * Spectral class colours follow the Harvard stellar classification sequence
 * (O, B, A, F, G, K, M) and are used to tint the avatar glow to match the
 * physical colour temperature of the selected star.
 *
 * @func PlanetariumInfoCard
 * @param {Props} props - Component props.
 * @returns {React.ReactElement} The rendered info card.
 */
export const PlanetariumInfoCard: React.FC<Props> = ({
  source,
  observerLocation,
  onClose,
  showSpectraPlot,
  onShowSpectraPlotChange,
  showPhotometryPlot,
  onShowPhotometryPlotChange,
  astronomyData,
  plotLoading = false,
  plotError = null
}) => {
  const spectralClass = (source.spectralType || 'A').charAt(0).toUpperCase();
  const starGlowColor = spectralColors[spectralClass] || '#ffffff';

  const isTarget = source.type === 'target';

  // Build meridian flip status message from server-provided flip timing fields
  let flipMessage = '--';
  if (source.flipRequired) {
    flipMessage = 'Flip Required!';
  } else if (source.timeToFlipSeconds !== undefined) {
    const minutesUntilFlip = source.timeToFlipSeconds / 60.0;
    if (minutesUntilFlip < 0) {
      // Past meridian but within flip delay window
      flipMessage = `Post-meridian (Flip in ${Math.abs(minutesUntilFlip).toFixed(1)}m)`;
    } else {
      flipMessage = `Transit in ${minutesUntilFlip.toFixed(1)}m`;
    }
  }

  const spectraHistory = astronomyData?.spectraHistory ?? [];

  return (
    <div className="planetarium-info-card" role="dialog" aria-label={`Information for ${source.id}`}>
      <div className="planetarium-info-card__header">
        <div className="planetarium-info-card__avatar-container">
          <div
            className="planetarium-info-card__avatar"
            style={{
              border: `2px solid ${starGlowColor}`,
              boxShadow: `0 0 12px ${starGlowColor}, inset 0 0 8px ${starGlowColor}`
            }}
          >
            <div className="planetarium-info-card__star-dot" style={{ backgroundColor: starGlowColor }} />
          </div>
          <div className="planetarium-info-card__title-group">
            <h3 className="planetarium-info-card__title">{source.name}</h3>
            <div className="planetarium-info-card__subtitle">
              <span>{isTarget ? 'Target Object' : 'Stellar Object'}</span>
              {!isTarget && source.spectralType && (
                <>
                  <span className="planetarium-info-card__divider">&bull;</span>
                  <span>Class {source.spectralType}</span>
                </>
              )}
            </div>
          </div>
        </div>
        <button className="planetarium-info-card__close" onClick={onClose} aria-label="Close panel">
          &times;
        </button>
      </div>

      <div className="planetarium-info-card__content">
        <table className="planetarium-info-card__table">
          <tbody>
            {!isTarget && (
              <>
                <tr>
                  <td>Magnitude</td>
                  <td>{formatNumber(source.magnitude, 2)}</td>
                </tr>
                <tr>
                  <td>Spectral Type</td>
                  <td>{source.spectralType || 'N/A'}</td>
                </tr>
              </>
            )}
            <tr>
              <td>RA/Dec (Dec)</td>
              <td>{formatNumber(source.ra, 4)}° / {formatNumber(source.dec, 4)}°</td>
            </tr>
            <tr>
              <td>RA/Dec (Sexa)</td>
              <td>{formatRA(source.ra)} / {formatDec(source.dec)}</td>
            </tr>
            <tr>
              <td>Alt/Az</td>
              <td>{formatNumber(source.altitude, 2)}° / {formatNumber(source.azimuth, 2)}°</td>
            </tr>
            <tr>
              <td>Hour Angle</td>
              <td>{formatNumber(source.hourAngle, 4)} h</td>
            </tr>
            <tr>
              <td>Rise/Set</td>
              <td>{source.riseTime || '--'} / {source.setTime || '--'}</td>
            </tr>
          </tbody>
        </table>

        {(spectraHistory.length > 0 && !isTarget) && (
          <div className="planetarium-info-card__embedded-plot">
            <h5>Spectrum</h5>
            <div className="planetarium-info-card__embedded-plot-canvas">
              <SpectrumViewer
                astronomyData={astronomyData ?? null}
                loading={plotLoading}
                error={plotError}
                active={true}
              />
            </div>
          </div>
        )}

        <div className="planetarium-checkboxes">
          {source.hasSpectra && (
            <label className="planetarium-checkbox-label">
              <input
                type="checkbox"
                checked={showSpectraPlot}
                onChange={(e) => onShowSpectraPlotChange(e.target.checked)}
                className="planetarium-checkbox-input"
              />
              Show Spectroscopy Plot
            </label>
          )}

          {source.hasPhotometry && (
            <label className="planetarium-checkbox-label">
              <input
                type="checkbox"
                checked={showPhotometryPlot}
                onChange={(e) => onShowPhotometryPlotChange(e.target.checked)}
                className="planetarium-checkbox-input"
              />
              Show Photometry Plot
            </label>
          )}
        </div>
      </div>
    </div>
  );
};
