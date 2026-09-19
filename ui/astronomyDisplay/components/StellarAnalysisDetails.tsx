/**
 * @fileoverview Detailed analytical metrics component in AstronomyDisplay: a
 * light curve summary, the spectrum's best-matching type, and the Lomb-Scargle
 * periodogram and Box-fitting Least Squares (BLS) transit parameters.
 */

import React from 'react';
import '../styles/astronomyDisplay.css';
import {
    describeTemplateMatch,
    formatFalseAlarmProbability,
    formatPeriod,
    formatTimeSpan,
    isSignificantVerdict,
    shortFeatureName,
} from '../utils/starDisplayFormat';
import { SpectralFeatureResult } from '../../common/types/spectralFeatureTypes';

/** Fewest measurements the Lomb-Scargle period search accepts. Matches the backend analyzer. */
const MINIMUM_POINTS_FOR_PERIOD_SEARCH = 5;

/** Fewest measurements the BLS transit search accepts. Matches the backend analyzer. */
const MINIMUM_POINTS_FOR_TRANSIT_SEARCH = 8;

/**
 * A false-alarm probability above this means a pattern this strong would show up
 * by chance more than 1 time in 100, so the period is not trustworthy.
 */
const SIGNIFICANT_FALSE_ALARM_PROBABILITY = 0.01;

/** Number of closest spectral types listed under the best match. */
const LISTED_SPECTRAL_TYPE_CANDIDATES = 3;

export interface StellarAnalysisDetailsProps {
    /** Detailed astronomy data containing photometry periodogram and transitCandidate. */
    astronomyData?: any;
    /** Runs the period and transit search for the shown star. */
    onAnalyze?: () => void;
    /** Whether a period search is running now. */
    isAnalyzing?: boolean;
    /** Message from the last failed period search, if any. */
    analysisError?: string | null;
}

/**
 * Renders a light curve summary, spectrum classification, and periodogram and BLS analysis details.
 */
export const StellarAnalysisDetails: React.FC<StellarAnalysisDetailsProps> = ({
    astronomyData,
    onAnalyze,
    isAnalyzing = false,
    analysisError = null,
}) => {
    const photometry = astronomyData?.photometry;
    const periodogram = photometry?.periodogram;
    const transitCandidate = photometry?.transitCandidate;
    const spectroscopy = astronomyData?.spectroscopy;

    const timestamps: string[] = photometry?.timestamps ?? [];
    const pointCount = timestamps.length;
    const timeSpanText = pointCount > 1 ? formatTimeSpan(timestamps[0], timestamps[pointCount - 1]) : '';
    const scatterPercent =
        typeof photometry?.coefficientOfVariation === 'number'
            ? photometry.coefficientOfVariation * 100
            : null;

    const measuredSpectralType: string = spectroscopy?.selfDeterminedSpectralType || '';
    const spectralRms = spectroscopy?.selfDeterminedSpectralTypeRms;
    const templateMatch = describeTemplateMatch(spectroscopy, astronomyData?.spectralType);
    const spectralCandidates: any[] = spectroscopy?.selfDeterminedSpectralTypeCandidates ?? [];
    const testedFeatures = (spectroscopy?.probableSpectralFeatures ?? []) as SpectralFeatureResult[];
    const periodogramIsSignificant = isSignificantVerdict(periodogram?.verdict);
    const transitIsSignificant = isSignificantVerdict(transitCandidate?.verdict);
    const hasSearchResult = !!periodogram || !!transitCandidate;

    const canAnalyze = !!onAnalyze && pointCount >= MINIMUM_POINTS_FOR_PERIOD_SEARCH;

    if (pointCount === 0 && !measuredSpectralType && testedFeatures.length === 0 && !periodogram && !transitCandidate) {
        return (
            <div className="stellar-analysis-details__empty">
                <span>This star has no light curve or classified spectrum to analyze.</span>
            </div>
        );
    }

    let periodSearchNote = '';
    if (!hasSearchResult && pointCount > 0) {
        if (pointCount < MINIMUM_POINTS_FOR_PERIOD_SEARCH) {
            periodSearchNote = `A period search needs at least ${MINIMUM_POINTS_FOR_PERIOD_SEARCH} measurements; this star has ${pointCount}.`;
        } else {
            periodSearchNote =
                `Not run yet. It would search ${pointCount} measurements` +
                (timeSpanText ? ` spanning ${timeSpanText}` : '') +
                ', so it can only find patterns shorter than that span.' +
                (pointCount < MINIMUM_POINTS_FOR_TRANSIT_SEARCH
                    ? ` The transit search needs at least ${MINIMUM_POINTS_FOR_TRANSIT_SEARCH}.`
                    : '');
        }
    }

    return (
        <div className="stellar-analysis-details">
            {pointCount > 0 && (
                <div className="stellar-analysis-details__section">
                    <div className="stellar-analysis-details__section-title">Light Curve</div>
                    <div className="stellar-analysis-details__grid">
                        <div className="analysis-row">
                            <span className="analysis-label">Measurements:</span>
                            <span className="analysis-value">{pointCount}</span>
                        </div>
                        {timeSpanText && (
                            <div className="analysis-row">
                                <span className="analysis-label">Time span:</span>
                                <span className="analysis-value">{timeSpanText}</span>
                            </div>
                        )}
                        {scatterPercent !== null && (
                            <div className="analysis-row" title="Standard deviation divided by the mean flux">
                                <span className="analysis-label">Scatter:</span>
                                <span className="analysis-value">{scatterPercent.toFixed(2)} %</span>
                            </div>
                        )}
                    </div>
                </div>
            )}

            {measuredSpectralType && (
                <div className="stellar-analysis-details__section">
                    <div className="stellar-analysis-details__section-title">Spectrum vs Reference Spectra</div>
                    <div className="stellar-analysis-details__grid">
                        <div className="analysis-row">
                            <span className="analysis-label">{templateMatch?.isPoor ? 'Result:' : 'Closest reference:'}</span>
                            <span className="analysis-value">
                                {templateMatch?.isPoor
                                    ? 'No good match'
                                    : `${measuredSpectralType}${typeof spectralRms === 'number' ? ` (${(spectralRms * 100).toFixed(0)}% off)` : ''}`}
                            </span>
                        </div>
                        {spectralCandidates.slice(0, LISTED_SPECTRAL_TYPE_CANDIDATES).map((candidate) => (
                            <div className="analysis-row" key={candidate.spectral_type}>
                                <span className="analysis-label">{candidate.spectral_type}:</span>
                                <span className="analysis-value">{(Number(candidate.rms) * 100).toFixed(0)}% off</span>
                            </div>
                        ))}
                    </div>
                    <div className="stellar-analysis-details__note">
                        Each number is how far the spectrum's shape is from that reference (lower is closer). A
                        difference above 15% counts as no good match. This compares shapes; it does not measure the star.
                    </div>
                </div>
            )}

            {testedFeatures.length > 0 && (
                <div className="stellar-analysis-details__section">
                    <div className="stellar-analysis-details__section-title">Absorption Features</div>
                    <div className="stellar-analysis-details__table-wrapper">
                    <table className="stellar-analysis-details__table">
                        <thead>
                            <tr>
                                <th>Feature</th>
                                <th>Result</th>
                                <th title="How far below the continuum the dip is">Depth</th>
                                <th title="The depth a star of the expected type should show">Expect</th>
                                <th title="The chance noise like this spectrum's gives a dip at least this significant">Noise</th>
                                <th title="A model-based chance the line is present, assuming the expected type">Present</th>
                            </tr>
                        </thead>
                        <tbody>
                            {testedFeatures.map((feature) => (
                                <tr key={feature.feature} className={`feature-row feature-row--${feature.verdict}`}>
                                    <td className="feature-name-cell">{shortFeatureName(feature.feature)}</td>
                                    <td>{{ detected: 'Detected', possible: 'Possible', not_detected: 'Not seen', not_covered: 'No data' }[feature.verdict]}</td>
                                    <td>{feature.depth !== undefined ? `${(feature.depth * 100).toFixed(0)}%` : '–'}</td>
                                    <td>{typeof feature.expected_depth === 'number' ? `${(feature.expected_depth * 100).toFixed(0)}%` : '–'}</td>
                                    <td>{feature.p_value !== undefined ? formatFalseAlarmProbability(feature.p_value) : '–'}</td>
                                    <td>{typeof feature.probability_present === 'number' ? `${Math.round(feature.probability_present * 100)}%` : '–'}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    </div>
                    <div className="stellar-analysis-details__note">
                        "Noise" is the chance that noise like this spectrum's produces a dip this strong. Only detected and
                        possible features are marked on the spectrum. "Present" assumes the star is the catalog type and is
                        not a calibrated probability.
                    </div>
                </div>
            )}

            {hasSearchResult && (
                <div className="stellar-analysis-details__section">
                    <div className="stellar-analysis-details__section-title">Repeating Patterns</div>

                    {periodogram && (
                        <div className="stellar-analysis-details__result">
                            <div className="stellar-analysis-details__result-title">Smooth cycle (Lomb-Scargle)</div>
                            {periodogramIsSignificant ? (
                                <div className="stellar-analysis-details__grid">
                                    <div className="analysis-row">
                                        <span className="analysis-label">{periodogram.verdict === 'detected' ? 'Detected period:' : 'Possible period:'}</span>
                                        <span className="analysis-value">
                                            {formatPeriod(periodogram.bestPeriodDays)}
                                        </span>
                                    </div>
                                    <div className="analysis-row">
                                        <span className="analysis-label">Chance of noise:</span>
                                        <span className="analysis-value">{formatFalseAlarmProbability(periodogram.falseAlarmProbability)}</span>
                                    </div>
                                    <div className="analysis-row">
                                        <span className="analysis-label">Cycles seen:</span>
                                        <span className="analysis-value">{periodogram.cyclesObserved !== null && periodogram.cyclesObserved !== undefined ? periodogram.cyclesObserved.toFixed(1) : 'n/a'}</span>
                                    </div>
                                </div>
                            ) : (
                                <div className="stellar-analysis-details__note">
                                    {periodogram.verdict === 'insufficient_data'
                                        ? periodogram.note
                                        : `No significant cycle. The strongest peak would appear by chance ${formatFalseAlarmProbability(periodogram.falseAlarmProbability)} of the time.`}
                                    {periodogram.searchedMinPeriodDays != null && periodogram.searchedMaxPeriodDays != null && (
                                        <> Searched periods from {(Number(periodogram.searchedMinPeriodDays) * 1440).toFixed(0)} min to {(Number(periodogram.searchedMaxPeriodDays) * 1440).toFixed(0)} min.</>
                                    )}
                                </div>
                            )}
                        </div>
                    )}

                    {transitCandidate && (
                        <div className="stellar-analysis-details__result">
                            <div className="stellar-analysis-details__result-title">Repeating dip (transit or eclipse)</div>
                            {transitIsSignificant ? (
                                <div className="stellar-analysis-details__grid">
                                    <div className="analysis-row">
                                        <span className="analysis-label">{transitCandidate.verdict === 'detected' ? 'Detected period:' : 'Possible period:'}</span>
                                        <span className="analysis-value">{formatPeriod(transitCandidate.periodDays)}</span>
                                    </div>
                                    <div className="analysis-row">
                                        <span className="analysis-label">Depth:</span>
                                        <span className="analysis-value">{Number(transitCandidate.transitDepthMag).toFixed(4)} mag</span>
                                    </div>
                                    <div className="analysis-row">
                                        <span className="analysis-label">Duration:</span>
                                        <span className="analysis-value">{Number(transitCandidate.transitDurationHours).toFixed(2)} hrs</span>
                                    </div>
                                    <div className="analysis-row">
                                        <span className="analysis-label">Dips seen:</span>
                                        <span className="analysis-value">{transitCandidate.transitCount} ({transitCandidate.pointsInTransit} measurements)</span>
                                    </div>
                                    <div className="analysis-row">
                                        <span className="analysis-label">Chance of noise:</span>
                                        <span className="analysis-value">{formatFalseAlarmProbability(transitCandidate.falseAlarmProbability)}</span>
                                    </div>
                                </div>
                            ) : (
                                <div className="stellar-analysis-details__note">
                                    {transitCandidate.verdict === 'insufficient_data'
                                        ? transitCandidate.note
                                        : `No significant repeating dip. The strongest one would appear by chance ${formatFalseAlarmProbability(transitCandidate.falseAlarmProbability)} of the time.`}
                                    {transitCandidate.note && transitCandidate.verdict !== 'insufficient_data' ? ` ${transitCandidate.note}` : ''}
                                </div>
                            )}
                        </div>
                    )}
                </div>
            )}

            {(periodSearchNote || canAnalyze || analysisError) && (
                <div className="stellar-analysis-details__section">
                    <div className="stellar-analysis-details__section-title">Period Search</div>
                    {periodSearchNote && (
                        <div className="stellar-analysis-details__note">{periodSearchNote}</div>
                    )}
                    {analysisError && (
                        <div className="stellar-analysis-details__note stellar-analysis-details__note--error">
                            {analysisError}
                        </div>
                    )}
                    {canAnalyze && (
                        <button
                            type="button"
                            className="stellar-analysis-details__run-btn"
                            onClick={onAnalyze}
                            disabled={isAnalyzing}
                        >
                            {isAnalyzing
                                ? 'Analyzing…'
                                : hasSearchResult
                                  ? 'Re-run period analysis'
                                  : 'Run period analysis'}
                        </button>
                    )}
                </div>
            )}
        </div>
    );
};
