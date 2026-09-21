/**
 * @fileoverview Summary card component displaying stellar metadata, coordinates,
 * spectral classification, and variability status in AstronomyDisplay.
 */

import React from 'react';
import '../styles/astronomyDisplay.css';
import {
    describePatternBadges,
    describeStarTypeBadges,
    formatCatalogMagnitude,
    formatCoordinateDegrees,
} from '../utils/starDisplayFormat';
import { useOptionalTargetContext } from '../../common/context/TargetContext';
import { emitToast } from '../../common/utils/emitToast';

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
 * Switches the app to Image Processing Display with the specified target selected.
 *
 * @param targetId Target identifier to load in the Image Processing workspace.
 * @param setSelectedTarget Optional setter from TargetContext to directly set selectedTarget.
 * @param setPendingTarget Optional setter from TargetContext to directly set pendingTarget.
 */
function navigateToTarget(
    targetId: string,
    setSelectedTarget?: (id: string) => void,
    setPendingTarget?: (id: string) => void
): void {
    if (!targetId) return;
    try {
        window.localStorage.setItem('selectedTarget', targetId);
        window.localStorage.setItem('appMode', 'Image Processing');
    } catch {
        // Ignore localStorage access failures (e.g. in private browsing)
    }
    setSelectedTarget?.(targetId);
    setPendingTarget?.(targetId);
    window.dispatchEvent(new CustomEvent('astrometrics:targetSelected', { detail: targetId }));
    window.dispatchEvent(new CustomEvent('astrometrics:modeChange', { detail: 'Image Processing' }));
    emitToast(`Opening ${targetId.replace(/_/g, ' ')} in Image Processing`, 'info', 'Targets');
}

/**
 * Renders stellar metadata summary bar above astronomy plots.
 */
export const StarSummaryCard: React.FC<StarSummaryCardProps> = ({
    astronomyData,
    starId,
}) => {
    const targetContext = useOptionalTargetContext();

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
    const typeBadges = describeStarTypeBadges(spectralType, astronomyData?.spectroscopy);
    const patternBadges = describePatternBadges(astronomyData?.photometry);
    const targetIds: string[] = Array.isArray(astronomyData?.targetIds) ? astronomyData.targetIds : [];

    return (
        <div className="star-summary-card">
            <div className="star-summary-card__header">
                <span className="star-summary-card__title">{name}</span>
                {typeBadges.map((badge) => (
                    <span
                        key={badge.text}
                        className={`star-summary-card__badge spectral-badge spectral-badge--${badge.tone}`}
                        title={badge.title}
                    >
                        {badge.text}
                    </span>
                ))}
                {patternBadges.map((badge) => (
                    <span key={badge.text} className="star-summary-card__badge variability-badge" title={badge.title}>
                        {badge.text}
                    </span>
                ))}
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
                    <span className="star-summary-card__item" title="Right ascension and declination, in degrees.">
                        <span className="item-label">RA / Dec:</span> {formattedCoords}
                    </span>
                )}
                {catalogMagnitudeText !== null && (
                    <span className="star-summary-card__item" title="Brightness in magnitudes. A smaller number is brighter.">
                        <span className="item-label">Mag:</span> {catalogMagnitudeText}
                    </span>
                )}
                {targetIds.length > 0 && (
                    <span className="star-summary-card__item">
                        <span className="item-label">Targets:</span>{' '}
                        {targetIds.map((t) => (
                            <button
                                key={t}
                                type="button"
                                className="target-tag-badge"
                                title={`Open ${t} in Image Processing Display`}
                                onClick={() => navigateToTarget(t, targetContext?.setSelectedTarget, targetContext?.setPendingTarget)}
                            >
                                {t}
                            </button>
                        ))}
                    </span>
                )}
            </div>
        </div>
    );
};
