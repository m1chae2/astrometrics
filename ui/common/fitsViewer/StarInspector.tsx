/**
 * @file StarInspector.tsx
 * @description The top of the FITS viewer's star card. The header gives the star's name and a close button.
 * Under it, one muted line gives the sky position, the catalog magnitude, the same type and pattern chips the
 * Astronomy Manager shows, and a button that opens the star there. A stat strip then gives the key numbers
 * (width, signal-to-noise, roundness). "Measurable here?" is a tight list with a small status icon, the check
 * and the value behind it, so the person can tell at a glance whether this star can be trusted on this frame.
 */
import React from 'react';
import { Button } from '../components/Button';
import { StarBadges } from '../components/StarBadges';
import { StatusChip } from '../components/StatusChip';
import { formatCatalogMagnitude } from '../utils/starDisplayFormat';
import type { Spectrum, StarProfileMeasurement } from '../types/backendTypes';
import { buildMeasurableRows, describeRoundness } from './utils/starMeasurable';

/** Props for {@link CloseStarButton}. */
export interface CloseStarButtonProps {
  /** Called when the person closes the card. */
  onClose: () => void;
}

/**
 * The close button of the star card.
 *
 * @param props See {@link CloseStarButtonProps}.
 * @returns The button.
 */
export const CloseStarButton: React.FC<CloseStarButtonProps> = ({ onClose }) => (
  <Button variant="ghost" size="square" className="star-profile__close" onClick={onClose} aria-label="Close star profile" title="Close star profile">
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
      <path fill="currentColor" d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
    </svg>
  </Button>
);

/** Props for {@link StarInspector}. */
export interface StarInspectorProps {
  /** What the backend measured for the star. */
  measurement: StarProfileMeasurement;
  /** The star's name, or null when the image has no names and no plate solution. */
  starName: string | null;
  /** The star's record, or null when it has none or has not loaded. */
  identity: Spectrum | null;
  /** The typical star width of the frame, in pixels, or null when it is not known. */
  frameWidthPx: number | null;
  /** Opens the star in the Astronomy Manager; leave out when the star has no catalog match. */
  onOpenInManager?: () => void;
  /** The width line, for example "FWHM 3.90 px (7.46″)", or the reason the profile is not a star. */
  widthText: string;
  /** Called when the person closes the card. */
  onClose: () => void;
}

/**
 * Splits a star name into its title and its sky position. "Unnamed star at 22h 36m 03.57s +34° 37′ 05.0″"
 * becomes the title "Unnamed star" and the position "22h 36m 03.57s +34° 37′ 05.0″".
 *
 * @param starName The name the viewer built, or null.
 * @returns The title and the position, either of which may be null.
 */
export function splitStarName(starName: string | null): { title: string | null; position: string | null } {
  if (!starName) return { title: null, position: null };
  const marker = ' at ';
  const index = starName.indexOf(marker);
  if (index < 0) return { title: starName, position: null };
  return { title: starName.slice(0, index), position: starName.slice(index + marker.length) };
}

/**
 * Shows the header, the stat strip and the "Measurable here?" list.
 *
 * @param props See {@link StarInspectorProps}.
 * @returns The blocks.
 */
export const StarInspector: React.FC<StarInspectorProps> = ({
  measurement, starName, identity, frameWidthPx, onOpenInManager, widthText, onClose,
}) => {
  const magnitude = identity ? formatCatalogMagnitude(identity) : null;
  const rows = buildMeasurableRows(measurement, frameWidthPx);
  const { title, position } = splitStarName(starName);
  const roundness = describeRoundness(measurement);
  const signalToNoise = measurement.signal_to_noise;
  const hasMeta = position !== null || magnitude !== null || identity !== null || onOpenInManager !== undefined;
  return (
    <>
      <div className="star-profile__identity" data-testid="star-profile-identity">
        <div className="star-profile__header">
          {title
            ? <span className="star-profile__name" data-testid="star-profile-name">{title}</span>
            : <span className="star-profile__title">Star profile</span>}
          <CloseStarButton onClose={onClose} />
        </div>
        {hasMeta && (
          <div className="star-profile__meta">
            {position && <span className="star-profile__position" title="Sky position of the star">{position}</span>}
            {magnitude !== null && <span className="star-profile__magnitude" title="Brightness in magnitudes. A smaller number is brighter.">{`mag ${magnitude}`}</span>}
            <StarBadges star={identity} />
            {onOpenInManager && (
              <Button size="small" variant="secondary" className="star-profile__open" onClick={onOpenInManager} data-testid="star-profile-open-manager">
                Open in Astronomy Manager
              </Button>
            )}
          </div>
        )}
      </div>
      <div className="star-profile__stats" data-testid="star-profile-stats">
        <span
          className={`star-profile__width${measurement.star_found ? '' : ' star-profile__width--plain'}`}
          data-testid="star-profile-width"
        >{widthText}</span>
        {measurement.star_found && signalToNoise !== undefined && signalToNoise !== null && (
          <span className="star-profile__stat" title="Peak brightness divided by the sky noise">{`S/N ${signalToNoise.toFixed(0)}`}</span>
        )}
        {roundness && <span className="star-profile__stat" title="Elongation of the star core: its long width divided by its short width, 1 for a circle">{roundness}</span>}
      </div>
      <div className="star-profile__measurable" data-testid="star-profile-measurable">
        <span className="star-profile__caption">Measurable here?</span>
        <ul className="star-profile__rows">
          {rows.map((row) => (
            <li key={row.id} className="star-profile__row" title={row.detail} data-testid={`star-profile-row-${row.id}`}>
              <StatusChip compact status={row.unknown ? 'neutral' : row.ok ? 'ok' : 'warning'} label={row.unknown ? 'Not known' : row.ok ? 'Yes' : 'No'} />
              <span className="star-profile__row-label">{row.label}</span>
              <span className="star-profile__row-value">{row.value}</span>
            </li>
          ))}
        </ul>
      </div>
    </>
  );
};
