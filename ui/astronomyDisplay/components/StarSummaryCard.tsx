/**
 * @fileoverview Summary card component displaying stellar metadata, coordinates,
 * spectral classification, and variability status in AstronomyDisplay.
 */

import React from 'react';
import '../styles/astronomyDisplay.css';

export interface StarSummaryCardProps {
    /** Detailed astronomy/stellar object data. */
    astronomyData?: any;
    /** Currently selected star identifier. */
    starId?: string;
}

/**
 * Format numerical RA and DEC into standard degree, arcminute, arcsecond strings.
 * @param ra RA coordinate in degrees or string.
 * @param dec DEC coordinate in degrees or string.
 * @returns Formatted coordinate string with dot separator.
 */
function formatCoordinates(ra?: any, dec?: any): string {
    if (ra === undefined || dec === undefined || ra === null || dec === null || ra === '') {
        return '';
    }
    return `${ra}° • ${dec}°`;
}

/**
 * Renders stellar metadata summary bar above astronomy plots.
 */
export const StarSummaryCard: React.FC<StarSummaryCardProps> = ({
    astronomyData,
    starId,
}) => {
    if (!starId && !astronomyData) {
        return null;
    }

    const name = astronomyData?.name || astronomyData?.id || starId || 'Selected Star';
    const spectralType = astronomyData?.spectralType || astronomyData?.spectral_type || astronomyData?.stellarSpectralType || '';
    const ra = astronomyData?.ra ?? astronomyData?.right_ascension;
    const dec = astronomyData?.dec ?? astronomyData?.declination;
    const formattedCoords = formatCoordinates(ra, dec);
    const mag = astronomyData?.magnitude ?? astronomyData?.mag;
    const meanFlux = astronomyData?.meanFlux ?? astronomyData?.mean_flux;
    const variabilityScore = astronomyData?.variabilityScore ?? astronomyData?.variability_score;
    const targetIds: string[] = Array.isArray(astronomyData?.targetIds) ? astronomyData.targetIds : [];

    return (
        <div className="star-summary-card">
            <div className="star-summary-card__header">
                <span className="star-summary-card__title">{name}</span>
                {spectralType && (
                    <span className="star-summary-card__badge spectral-badge">{spectralType}</span>
                )}
                {variabilityScore !== undefined && variabilityScore !== null && (
                    <span className="star-summary-card__badge variability-badge">
                        Var Score: {(Number(variabilityScore)).toFixed(2)}
                    </span>
                )}
            </div>

            <div className="star-summary-card__details">
                {formattedCoords && (
                    <span className="star-summary-card__item">
                        <span className="item-label">Coords:</span> {formattedCoords}
                    </span>
                )}
                {mag !== undefined && mag !== null && mag !== '' && (
                    <span className="star-summary-card__item">
                        <span className="item-label">Mag:</span> {String(mag)}
                    </span>
                )}
                {meanFlux !== undefined && meanFlux !== null && (
                    <span className="star-summary-card__item">
                        <span className="item-label">Mean Flux:</span> {Math.round(Number(meanFlux)).toLocaleString()} ADU
                    </span>
                )}
                {targetIds.length > 0 && (
                    <span className="star-summary-card__item">
                        <span className="item-label">Targets:</span>{' '}
                        {targetIds.map((t) => (
                            <span key={t} className="target-tag-badge">
                                {t}
                            </span>
                        ))}
                    </span>
                )}
            </div>
        </div>
    );
};
