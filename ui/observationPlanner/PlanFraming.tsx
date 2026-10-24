/**
 * @fileoverview The framing of the night plan, as numbers: the field center,
 * the camera rotation with its unit and convention, and each mosaic panel's
 * center, with "Show on sky".
 *
 * The numbers are drawn as stored. The UI does no framing math.
 */

import React from 'react';
import type { Framing } from '../common/types/backendTypes';
import { formatValue } from '../common/components/quality/statementLabels';

/**
 * The convention of the rotation number. The framing model's description states
 * it ("degrees east of north, the way Ekos and KStars give it"); the model does
 * not return it as a field yet, so the same words are kept here.
 */
export const ROTATION_CONVENTION = 'degrees east of north, the way Ekos and KStars give it';

/**
 * Writes a sky coordinate in degrees with four decimal places (about 0.4 arcsecond).
 * @param degrees The coordinate.
 * @return The text, such as `148.8880`.
 */
function formatDegrees(degrees: number): string {
    return degrees.toFixed(4);
}

export interface PlanFramingProps {
    /** The stored framing, or null/undefined when the plan has none. */
    framing: Framing | null | undefined;
    /** Centers the Planetarium on the framing. */
    onShowOnSky: () => void;
}

/**
 * The framing section.
 * @param props The framing and the "Show on sky" callback.
 * @return The section.
 */
export const PlanFraming: React.FC<PlanFramingProps> = ({ framing, onShowOnSky }) => (
    <section className="plan-section" aria-label="Plan framing">
        <h4>Framing</h4>
        {framing ? (
            <>
                <dl className="plan-framing__numbers">
                    <dt>Center RA</dt>
                    <dd>{formatDegrees(framing.centerRaDeg)} deg</dd>
                    <dt>Center Dec</dt>
                    <dd>{formatDegrees(framing.centerDecDeg)} deg</dd>
                    <dt>Rotation</dt>
                    <dd>
                        {framing.rotationDeg === undefined || framing.rotationDeg === null
                            ? 'Not set'
                            : `${formatValue(framing.rotationDeg)} deg (${ROTATION_CONVENTION})`}
                    </dd>
                </dl>
                {(framing.panels ?? []).length > 0 && (
                    <ul className="plan-framing__panels" aria-label="Mosaic panels">
                        {(framing.panels ?? []).map((panel) => (
                            <li key={panel.panel_id}>
                                {panel.panel_id}: RA {formatDegrees(panel.ra_deg)} deg, Dec {formatDegrees(panel.dec_deg)} deg
                            </li>
                        ))}
                    </ul>
                )}
            </>
        ) : (
            <p className="quality-state">No framing yet. Choose one in the Planetarium and press Send to plan.</p>
        )}
        <button type="button" className="btn btn--tiny" onClick={onShowOnSky}>
            Show on sky
        </button>
    </section>
);
