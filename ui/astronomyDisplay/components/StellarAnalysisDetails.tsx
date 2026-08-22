/**
 * @fileoverview Detailed analytical metrics component displaying Lomb-Scargle
 * periodogram and Box-fitting Least Squares (BLS) transit parameters in AstronomyDisplay.
 */

import React from 'react';
import '../styles/astronomyDisplay.css';

export interface StellarAnalysisDetailsProps {
    /** Detailed astronomy data containing lightCurve periodogram and transitCandidate. */
    astronomyData?: any;
}

/**
 * Renders periodogram and exoplanet transit analysis details.
 */
export const StellarAnalysisDetails: React.FC<StellarAnalysisDetailsProps> = ({
    astronomyData,
}) => {
    const lightCurve = astronomyData?.lightCurve;
    const periodogram = lightCurve?.periodogram;
    const transitCandidate = lightCurve?.transitCandidate;

    if (!periodogram && !transitCandidate) {
        return (
            <div className="stellar-analysis-details__empty">
                <span>No periodicity or transit metrics computed for this star.</span>
            </div>
        );
    }

    return (
        <div className="stellar-analysis-details">
            {periodogram && (
                <div className="stellar-analysis-details__section">
                    <div className="stellar-analysis-details__section-title">Lomb-Scargle Periodogram</div>
                    <div className="stellar-analysis-details__grid">
                        <div className="analysis-row">
                            <span className="analysis-label">Best Period:</span>
                            <span className="analysis-value">
                                {periodogram.bestPeriodDays !== undefined
                                    ? `${Number(periodogram.bestPeriodDays).toFixed(4)} d (${(Number(periodogram.bestPeriodDays) * 24).toFixed(2)} h)`
                                    : 'N/A'}
                            </span>
                        </div>
                        <div className="analysis-row">
                            <span className="analysis-label">Power:</span>
                            <span className="analysis-value">
                                {periodogram.power !== undefined ? Number(periodogram.power).toFixed(3) : 'N/A'}
                            </span>
                        </div>
                        <div className="analysis-row">
                            <span className="analysis-label">FAP:</span>
                            <span className="analysis-value">
                                {periodogram.falseAlarmProbability !== undefined
                                    ? Number(periodogram.falseAlarmProbability).toExponential(2)
                                    : 'N/A'}
                            </span>
                        </div>
                    </div>
                </div>
            )}

            {transitCandidate && (
                <div className="stellar-analysis-details__section">
                    <div className="stellar-analysis-details__section-title">Exoplanet Transit (BLS)</div>
                    <div className="stellar-analysis-details__grid">
                        <div className="analysis-row">
                            <span className="analysis-label">Period (P):</span>
                            <span className="analysis-value">
                                {transitCandidate.periodDays !== undefined
                                    ? `${Number(transitCandidate.periodDays).toFixed(4)} d`
                                    : 'N/A'}
                            </span>
                        </div>
                        <div className="analysis-row">
                            <span className="analysis-label">Depth:</span>
                            <span className="analysis-value">
                                {transitCandidate.transitDepthMag !== undefined
                                    ? `${Number(transitCandidate.transitDepthMag).toFixed(4)} mag`
                                    : 'N/A'}
                            </span>
                        </div>
                        <div className="analysis-row">
                            <span className="analysis-label">Duration:</span>
                            <span className="analysis-value">
                                {transitCandidate.transitDurationHours !== undefined
                                    ? `${Number(transitCandidate.transitDurationHours).toFixed(2)} hrs`
                                    : 'N/A'}
                            </span>
                        </div>
                        <div className="analysis-row">
                            <span className="analysis-label">Epoch (T0):</span>
                            <span className="analysis-value">
                                {transitCandidate.epochT0 !== undefined
                                    ? Number(transitCandidate.epochT0).toFixed(4)
                                    : 'N/A'}
                            </span>
                        </div>
                        <div className="analysis-row">
                            <span className="analysis-label">Transit SNR:</span>
                            <span className="analysis-value">
                                {transitCandidate.transitSnr !== undefined
                                    ? Number(transitCandidate.transitSnr).toFixed(2)
                                    : 'N/A'}
                            </span>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};
