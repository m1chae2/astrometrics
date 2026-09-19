/**
 * @fileoverview Summary card component displaying stellar metadata, coordinates,
 * spectral classification, and variability status in AstronomyDisplay.
 */

import React from 'react';
import '../styles/astronomyDisplay.css';
import { describeTemplateMatch, formatCatalogMagnitude, formatCoordinateDegrees } from '../utils/starDisplayFormat';

export interface StarSummaryCardProps {
    /** Detailed astronomy/stellar object data. */
    astronomyData?: any;
    /** Currently selected star identifier. */
    starId?: string;
}

/**
 * Formats right ascension and declination as degrees rounded to five decimals.
 * @param ra RA coordinate in degrees or string.
 * @param dec DEC coordinate in degrees or string.
 * @returns Formatted coordinate string with dot separator, or empty if either is missing.
 */
function formatCoordinates(ra?: any, dec?: any): string {
    const rightAscensionText = formatCoordinateDegrees(ra);
    const declinationText = formatCoordinateDegrees(dec);
    if (rightAscensionText === '' || declinationText === '') return '';
    return `${rightAscensionText}° • ${declinationText}°`;
}

/**
 * Switches the app to Planetarium, centered on the given star, in place --
 * the reverse of Planetarium's "Open in Astronomy Manager" action.
 *
 * @param id Star identifier to select once Planetarium mounts.
 * @param name Display name for the star.
 * @param ra Right ascension in degrees.
 * @param dec Declination in degrees.
 * @param hasSpectra Whether spectroscopy data exists for this star.
 * @param hasPhotometry Whether photometry data exists for this star.
 * @returns {void}
 */
function locateInPlanetarium(
    id: string,
    name: string,
    ra: number,
    dec: number,
    hasSpectra: boolean,
    hasPhotometry: boolean
): void {
    const payload = { id, name, ra, dec, hasSpectra, hasPhotometry };
    try {
        window.localStorage.setItem('astronomyLocateStar', JSON.stringify(payload));
        window.localStorage.setItem('appMode', 'Planetarium');
    } catch {
        // Ignore localStorage access failures (e.g. in private browsing)
    }
    window.dispatchEvent(new CustomEvent('astrometrics:modeChange', { detail: 'Planetarium' }));
    window.dispatchEvent(new CustomEvent('astrometrics:planetariumLocateStar', { detail: payload }));
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
    const raNum = typeof ra === 'number' ? ra : parseFloat(ra);
    const decNum = typeof dec === 'number' ? dec : parseFloat(dec);
    const canLocate = Number.isFinite(raNum) && Number.isFinite(decNum);
    const catalogMagnitudeText = formatCatalogMagnitude(astronomyData?.magnitude ?? astronomyData?.mag);
    const templateMatch = describeTemplateMatch(astronomyData?.spectroscopy, spectralType);
    const meanFlux = astronomyData?.photometry?.meanFlux ?? astronomyData?.photometry?.mean_flux;
    const variabilityScore = astronomyData?.variabilityScore ?? astronomyData?.variability_score;
    const targetIds: string[] = Array.isArray(astronomyData?.targetIds) ? astronomyData.targetIds : [];

    return (
        <div className="star-summary-card">
            <div className="star-summary-card__header">
                <span className="star-summary-card__title">{name}</span>
                <span
                    className="star-summary-card__badge spectral-badge"
                    title="Spectral type from a catalog lookup"
                >
                    Catalog: {spectralType || 'Unknown'}
                </span>
                {templateMatch && (
                    <span
                        className={`star-summary-card__badge spectral-badge spectral-badge--measured${
                            templateMatch.isPoor || templateMatch.differsFromCatalog ? ' spectral-badge--caution' : ''
                        }`}
                        title={templateMatch.hoverText}
                    >
                        {templateMatch.badgeText}
                        {!templateMatch.isPoor && templateMatch.differsFromCatalog ? ' · differs from catalog' : ''}
                    </span>
                )}
                {variabilityScore !== undefined && variabilityScore !== null && (
                    <span className="star-summary-card__badge variability-badge">
                        Var Score: {(Number(variabilityScore)).toFixed(2)}
                    </span>
                )}
                {canLocate && (
                    <button
                        type="button"
                        className="star-summary-card__locate-btn"
                        onClick={() => locateInPlanetarium(
                            String(astronomyData?.id || astronomyData?.name || starId),
                            String(name),
                            raNum,
                            decNum,
                            !!(astronomyData?.hasSpectra ?? astronomyData?.has_spectra),
                            !!(astronomyData?.hasPhotometry ?? astronomyData?.has_photometry)
                        )}
                    >
                        Locate in Planetarium
                    </button>
                )}
            </div>

            <div className="star-summary-card__details">
                {formattedCoords && (
                    <span className="star-summary-card__item">
                        <span className="item-label">Coords:</span> {formattedCoords}
                    </span>
                )}
                {catalogMagnitudeText !== null && (
                    <span className="star-summary-card__item">
                        <span className="item-label">Mag:</span> {catalogMagnitudeText}
                    </span>
                )}
                {meanFlux !== undefined && meanFlux !== null && (
                    <span
                        className="star-summary-card__item"
                        title="The star's flux divided by the reference flux of each frame, averaged over the light curve. It has no units."
                    >
                        <span className="item-label">Mean flux ratio:</span> {Number(meanFlux).toFixed(2)}
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
