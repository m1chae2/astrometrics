/**
 * @file StarInspector.tsx
 * @description The top of the FITS viewer's star panel. The identity line gives the star's name, its catalog
 * magnitude, the same type and pattern chips the Astronomy Manager shows, and a button that opens the star
 * there. Under it, "Measurable here?" lists plain yes or no rows with the value behind each, so the person can
 * tell at a glance whether this star can be trusted on this frame.
 */
import React from 'react';
import { Button } from '../components/Button';
import { StarBadges } from '../components/StarBadges';
import { StatusChip } from '../components/StatusChip';
import { formatCatalogMagnitude } from '../utils/starDisplayFormat';
import type { Spectrum, StarProfileMeasurement } from '../types/backendTypes';
import { buildMeasurableRows } from './utils/starMeasurable';

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
}

/**
 * Shows the identity line and the "Measurable here?" list.
 *
 * @param props See {@link StarInspectorProps}.
 * @returns The two blocks.
 */
export const StarInspector: React.FC<StarInspectorProps> = ({ measurement, starName, identity, frameWidthPx, onOpenInManager }) => {
  const magnitude = identity ? formatCatalogMagnitude(identity) : null;
  const rows = buildMeasurableRows(measurement, frameWidthPx);
  return (
    <>
      {(starName || onOpenInManager) && (
        <div className="star-profile__identity" data-testid="star-profile-identity">
          {starName && <span className="star-profile__name" data-testid="star-profile-name">{starName}</span>}
          {magnitude !== null && <span className="star-profile__magnitude" title="Brightness in magnitudes. A smaller number is brighter.">{`mag ${magnitude}`}</span>}
          <StarBadges star={identity} />
          {onOpenInManager && (
            <Button size="small" variant="secondary" onClick={onOpenInManager} data-testid="star-profile-open-manager">
              Open in Astronomy Manager
            </Button>
          )}
        </div>
      )}
      <div className="star-profile__measurable" data-testid="star-profile-measurable">
        <span className="star-profile__caption">Measurable here?</span>
        <ul className="star-profile__rows">
          {rows.map((row) => (
            <li key={row.id} className="star-profile__row" title={row.detail} data-testid={`star-profile-row-${row.id}`}>
              <StatusChip status={row.ok ? 'ok' : 'warning'} label={row.ok ? 'Yes' : 'No'} />
              <span className="star-profile__row-label">{row.label}</span>
              <span className="star-profile__row-value">{row.value}</span>
            </li>
          ))}
        </ul>
      </div>
    </>
  );
};
