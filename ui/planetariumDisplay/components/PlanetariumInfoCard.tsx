/**
 * @module PlanetariumInfoCard
 * @fileoverview Floating details card for a selected celestial object.
 *
 * Displays coordinates (sexagesimal and decimal), altitude/azimuth, hour angle,
 * rise/set times, and meridian flip status for selected stars and targets.
 * Also renders a button that hands a star off to the Astronomy Manager for
 * its spectroscopy/photometry plots, rather than rendering them inline here.
 *
 * REQ: PLN-2.4, REQ: PLN-2.5
 */

import React from 'react';
import { PlanetariumSource, ObserverLocation } from '../../common/types/planetariumTypes';
import { safeParse, formatRA, formatDec, formatNumber } from '../utils/coordinateUtils';
import { formatCatalogMagnitude } from '../layers/StarOverlay';
import { navigateToElement } from '../../common/utils/displayCoordinator';

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
}

/**
 * Switches to the Astronomy Manager with the given star selected, the
 * reverse of Astronomy Manager's "Locate in Planetarium" action. Routed
 * through the shared displayCoordinator navigation-intent bus so the same
 * hand-off works whether Astronomy Manager is open in this window or another.
 *
 * @param {PlanetariumSource} source - The star to view in the Astronomy Manager.
 * @returns {void}
 */
function viewInAstronomyManager(source: PlanetariumSource): void {
  navigateToElement({
    targetDisplay: 'Astronomy Manager',
    action: 'astronomySelectStar',
    payload: source.id,
    toast: {
      message: `Opening ${source.name} in Astronomy Manager`,
      type: 'info',
      title: 'Astronomy Manager',
    },
  });
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
                  <td>{formatCatalogMagnitude(source, 2)}</td>
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

        {!isTarget && (source.hasSpectra || source.hasPhotometry) && (
          <button
            type="button"
            className="planetarium-info-card__view-in-astronomy-btn"
            onClick={() => viewInAstronomyManager(source)}
          >
            View in Astronomy Manager
          </button>
        )}
      </div>
    </div>
  );
};
