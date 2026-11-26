/**
 * @file ProfileMarkerLayer.tsx
 * @description Draws the marker on the star the star profile panel measured: a ring one FWHM
 * across at the star's center, the line of the cut or the two circles of the background ring,
 * and the neighbor stars left out of the ring.
 */
import React from 'react';
import type { ProfileMarker } from './hooks/useStarMeasurement';

/** Props for ProfileMarkerLayer. */
interface Props {
  /** The marker, in screen pixels (`useStarMeasurement`). */
  marker: ProfileMarker;
}

/**
 * The measured star's marker as one SVG layer.
 *
 * @param props See `Props`.
 * @returns The SVG layer.
 */
export const ProfileMarkerLayer: React.FC<Props> = ({ marker }) => (
  <svg className="fits-renderer__profile-marker" data-overlay-layer="true" data-testid="star-profile-marker" aria-hidden="true">
    {marker.cut && (
      <line
        className="fits-renderer__profile-cut"
        x1={marker.cut.x1}
        y1={marker.cut.y1}
        x2={marker.cut.x2}
        y2={marker.cut.y2}
      />
    )}
    {marker.neighbors.map((neighbor, index) => (
      <circle
        // The neighbors of one measurement are listed in a fixed order, so the index is a stable key.
        key={index}
        className="fits-renderer__profile-neighbor"
        data-testid="star-profile-neighbor"
        cx={neighbor.x}
        cy={neighbor.y}
        r={neighbor.radius}
      />
    ))}
    {marker.band && (
      <>
        <circle className="fits-renderer__profile-band" data-testid="star-profile-band-inner" cx={marker.screenX} cy={marker.screenY} r={marker.band.inner} />
        <circle className="fits-renderer__profile-band" data-testid="star-profile-band-outer" cx={marker.screenX} cy={marker.screenY} r={marker.band.outer} />
      </>
    )}
    <circle
      className={`fits-renderer__profile-ring${!marker.starFound ? ' fits-renderer__profile-ring--neutral' : marker.radius < 4 ? ' fits-renderer__profile-ring--small' : ''}`}
      cx={marker.screenX}
      cy={marker.screenY}
      r={marker.radius}
    >
      <title>{marker.starFound ? 'Circle one FWHM across' : 'Center of the measurement'}</title>
    </circle>
  </svg>
);
