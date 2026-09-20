/**
 * @fileoverview Detailed analytical metrics component in AstronomyDisplay: a
 * light curve summary, the spectrum's best-matching type, and the Lomb-Scargle
 * periodogram and Box-fitting Least Squares (BLS) transit parameters.
 */

import React, { useState } from 'react';
import '../styles/astronomyDisplay.css';
import {
    describeFeatureVerdict,
    describeMissingPattern,
    describeTemplateMatch,
    formatFalseAlarmProbability,
    formatPeriod,
    formatTimestampsSpan,
    hasReferenceExpectations,
    isSignificantVerdict,
    isDepthMeaningful,
    shortFeatureName,
    splitFeaturesForTable,
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

/** A small "i" that shows a longer explanation when hovered, so the panel itself can stay short. */
const InfoTip: React.FC<{ text: string }> = ({ text }) => (
    <span className="stellar-analysis-details__info" title={text} role="img" aria-label={text}>
        ⓘ
    </span>
);

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
    // Features the test found nothing at are folded away until asked for.
    const [showNothingFoundFeatures, setShowNothingFoundFeatures] = useState<boolean>(false);

    const timestamps: string[] = photometry?.timestamps ?? [];
    const pointCount = timestamps.length;
    const timeSpanText = formatTimestampsSpan(timestamps);

    const measuredSpectralType: string = spectroscopy?.selfDeterminedSpectralType || '';
    const spectralRms = spectroscopy?.selfDeterminedSpectralTypeRms;
    const templateMatch = describeTemplateMatch(spectroscopy, astronomyData?.spectralType);
    const spectralCandidates: any[] = spectroscopy?.selfDeterminedSpectralTypeCandidates ?? [];
    const testedFeatures = (spectroscopy?.probableSpectralFeatures ?? []) as SpectralFeatureResult[];
    const featureTable = splitFeaturesForTable(testedFeatures);
    // Expect and Present compare with a reference spectrum, so they are only listed when the star has one.
    const showReferenceColumns = hasReferenceExpectations(testedFeatures);
    const listedFeatures = showNothingFoundFeatures ? testedFeatures : featureTable.shown;
    const periodogramIsSignificant = isSignificantVerdict(periodogram?.verdict);
    const transitIsSignificant = isSignificantVerdict(transitCandidate?.verdict);
    const hasSearchResult = !!periodogram || !!transitCandidate;

    // A photometry run already searches the target's own star and its brightest stars. The button is for
    // any other star, so it is only offered while a star has no result.
    const canAnalyze = !!onAnalyze && pointCount >= MINIMUM_POINTS_FOR_PERIOD_SEARCH && !hasSearchResult;

    if (pointCount === 0 && !measuredSpectralType && testedFeatures.length === 0 && !periodogram && !transitCandidate) {
        return (
            <div className="stellar-analysis-details__empty">
                <span>This star has no light curve or classified spectrum to analyze.</span>
            </div>
        );
    }

    // A short line for the panel, and the fuller explanation for its tooltip.
    let periodSearchNote = '';
    let periodSearchNoteDetail = '';
    if (!hasSearchResult && pointCount > 0) {
        if (pointCount < MINIMUM_POINTS_FOR_PERIOD_SEARCH) {
            periodSearchNote = `Needs at least ${MINIMUM_POINTS_FOR_PERIOD_SEARCH} measurements; this star has ${pointCount}.`;
        } else {
            periodSearchNote = 'Not run yet.';
            periodSearchNoteDetail =
                `It would search ${pointCount} measurements` +
                (timeSpanText ? ` spanning ${timeSpanText}` : '') +
                ', so it can only find patterns shorter than that span.' +
                (pointCount < MINIMUM_POINTS_FOR_TRANSIT_SEARCH
                    ? ` The repeating-dip search needs at least ${MINIMUM_POINTS_FOR_TRANSIT_SEARCH} measurements.`
                    : '');
        }
    }

    const missingCycle = periodogram && !periodogramIsSignificant ? describeMissingPattern(periodogram, 'peak') : null;
    const missingDip =
        transitCandidate && !transitIsSignificant ? describeMissingPattern(transitCandidate, 'repeating dip') : null;

    return (
        <div className="stellar-analysis-details">
            {pointCount > 0 && (
                <div className="stellar-analysis-details__section">
                    <div className="stellar-analysis-details__section-title">Light Curve</div>
                    <div className="stellar-analysis-details__grid">
                        <div className="analysis-row" title="How many brightness measurements were made.">
                            <span className="analysis-label">Measurements:</span>
                            <span className="analysis-value">{pointCount}</span>
                        </div>
                        {timeSpanText && (
                            <div className="analysis-row" title="Time from the first measurement to the last.">
                                <span className="analysis-label">Time span:</span>
                                <span className="analysis-value">{timeSpanText}</span>
                            </div>
                        )}
                    </div>
                </div>
            )}

            {measuredSpectralType && (
                <div className="stellar-analysis-details__section">
                    <div className="stellar-analysis-details__section-title">
                        Stellar Classification
                        <InfoTip text="Sorts the star by type (O, B, A, F, G, K, M, hottest to coolest) by comparing the shape of its spectrum with reference stars of known type. Each percentage is how far off the shape is; lower is closer. More than 15% off counts as no good match." />
                    </div>
                    <div className="stellar-analysis-details__grid">
                        <div
                            className="analysis-row"
                            title={
                                templateMatch?.isPoor
                                    ? "No reference star's shape was within 15% of this spectrum."
                                    : 'The reference star whose shape is closest to this spectrum.'
                            }
                        >
                            <span className="analysis-label">{templateMatch?.isPoor ? 'Result:' : 'Closest reference:'}</span>
                            <span className="analysis-value">
                                {templateMatch?.isPoor
                                    ? 'No good match'
                                    : `${measuredSpectralType}${typeof spectralRms === 'number' ? ` (${(spectralRms * 100).toFixed(0)}% off)` : ''}`}
                            </span>
                        </div>
                        {spectralCandidates.slice(0, LISTED_SPECTRAL_TYPE_CANDIDATES).map((candidate) => (
                            <div
                                className="analysis-row"
                                key={candidate.spectral_type}
                                title={`How far this spectrum's shape is from a ${candidate.spectral_type} star's (lower is closer).`}
                            >
                                <span className="analysis-label">{candidate.spectral_type}:</span>
                                <span className="analysis-value">{(Number(candidate.rms) * 100).toFixed(0)}% off</span>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {testedFeatures.length > 0 && (
                <div className="stellar-analysis-details__section">
                    <div className="stellar-analysis-details__section-title">
                        Absorption Features
                        <InfoTip text="Dips in the spectrum where an element absorbs light. Green lines on the plot are found features; red lines are dips the noise is too large to confirm. Hover a result for what it means." />
                    </div>
                    {listedFeatures.length > 0 && (
                        <div className="stellar-analysis-details__table-wrapper">
                            <table className="stellar-analysis-details__table">
                                <thead>
                                    <tr>
                                        <th>Feature</th>
                                        <th>Result</th>
                                        <th title="How far the dip goes below the spectrum around it.">Depth</th>
                                        {showReferenceColumns && (
                                            <th title="How deep this dip should be for the matched star type.">Expect</th>
                                        )}
                                        <th title="How often random noise alone makes a dip this deep. Lower means more likely real.">
                                            Noise
                                        </th>
                                        {showReferenceColumns && (
                                            <th title="Estimated chance the feature is present, if the star is the matched type. Not a precise probability.">
                                                Present
                                            </th>
                                        )}
                                    </tr>
                                </thead>
                                <tbody>
                                    {listedFeatures.map((feature) => {
                                        const verdictDescription = describeFeatureVerdict(feature.verdict);
                                        return (
                                            <tr key={feature.feature} className={`feature-row feature-row--${feature.verdict}`}>
                                                <td className="feature-name-cell">{shortFeatureName(feature.feature)}</td>
                                                <td title={verdictDescription.explanation}>{verdictDescription.label}</td>
                                                <td>
                                                    {isDepthMeaningful(feature.verdict) && feature.depth !== undefined
                                                        ? `${(feature.depth * 100).toFixed(0)}%`
                                                        : '–'}
                                                </td>
                                                {showReferenceColumns && (
                                                    <td>
                                                        {typeof feature.expected_depth === 'number'
                                                            ? `${(feature.expected_depth * 100).toFixed(0)}%`
                                                            : '–'}
                                                    </td>
                                                )}
                                                <td>
                                                    {feature.p_value !== undefined
                                                        ? formatFalseAlarmProbability(feature.p_value)
                                                        : '–'}
                                                </td>
                                                {showReferenceColumns && (
                                                    <td>
                                                        {typeof feature.probability_present === 'number'
                                                            ? `${Math.round(feature.probability_present * 100)}%`
                                                            : '–'}
                                                    </td>
                                                )}
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}
                    {featureTable.hidden.length > 0 && (
                        <button
                            type="button"
                            className="stellar-analysis-details__run-btn"
                            title={featureTable.hiddenSummary}
                            onClick={() => setShowNothingFoundFeatures((shown) => !shown)}
                        >
                            {showNothingFoundFeatures ? 'Hide' : 'Show'} {featureTable.hidden.length} more
                        </button>
                    )}
                </div>
            )}

            {hasSearchResult && (
                <div className="stellar-analysis-details__section">
                    <div className="stellar-analysis-details__section-title">
                        Repeating Patterns
                        <InfoTip text="Looks for brightness that rises and falls, or dips, on a regular schedule. 'Chance of noise' is how often random scatter would make a pattern this strong; lower is more convincing." />
                    </div>

                    {periodogram && (
                        <div className="stellar-analysis-details__result">
                            <div
                                className="stellar-analysis-details__result-title"
                                title="A Lomb-Scargle search: finds brightness that rises and falls regularly, like a pulsating star."
                            >
                                Smooth cycle
                            </div>
                            {periodogramIsSignificant ? (
                                <div className="stellar-analysis-details__grid">
                                    <div className="analysis-row">
                                        <span className="analysis-label">{periodogram.verdict === 'detected' ? 'Detected period:' : 'Possible period:'}</span>
                                        <span className="analysis-value">
                                            {formatPeriod(periodogram.bestPeriodDays)}
                                        </span>
                                    </div>
                                    <div
                                        className="analysis-row"
                                        title="How often random scatter would make a pattern this strong. Lower is more convincing."
                                    >
                                        <span className="analysis-label">Chance of noise:</span>
                                        <span className="analysis-value">{formatFalseAlarmProbability(periodogram.falseAlarmProbability)}</span>
                                    </div>
                                    <div
                                        className="analysis-row"
                                        title="How many full cycles fit in the time observed. More is more convincing."
                                    >
                                        <span className="analysis-label">Cycles seen:</span>
                                        <span className="analysis-value">{periodogram.cyclesObserved !== null && periodogram.cyclesObserved !== undefined ? periodogram.cyclesObserved.toFixed(1) : 'n/a'}</span>
                                    </div>
                                </div>
                            ) : (
                                missingCycle && (
                                    <div className="stellar-analysis-details__grid">
                                        <div className="analysis-row" title={missingCycle.explanation}>
                                            <span className="analysis-label">Result:</span>
                                            <span className="analysis-value">{missingCycle.value}</span>
                                        </div>
                                    </div>
                                )
                            )}
                        </div>
                    )}

                    {transitCandidate && (
                        <div className="stellar-analysis-details__result">
                            <div
                                className="stellar-analysis-details__result-title"
                                title="Brightness drops at regular intervals, like a planet crossing its star or two stars eclipsing each other."
                            >
                                Repeating dip
                            </div>
                            {transitIsSignificant ? (
                                <div className="stellar-analysis-details__grid">
                                    <div className="analysis-row">
                                        <span className="analysis-label">{transitCandidate.verdict === 'detected' ? 'Detected period:' : 'Possible period:'}</span>
                                        <span className="analysis-value">{formatPeriod(transitCandidate.periodDays)}</span>
                                    </div>
                                    <div className="analysis-row" title="How much the star dims during a dip, in magnitudes.">
                                        <span className="analysis-label">Depth:</span>
                                        <span className="analysis-value">{Number(transitCandidate.transitDepthMag).toFixed(4)} mag</span>
                                    </div>
                                    <div className="analysis-row" title="How long each dip lasts.">
                                        <span className="analysis-label">Duration:</span>
                                        <span className="analysis-value">{Number(transitCandidate.transitDurationHours).toFixed(2)} hrs</span>
                                    </div>
                                    <div className="analysis-row" title="How many separate dips were seen, and how many measurements fall inside them.">
                                        <span className="analysis-label">Dips seen:</span>
                                        <span className="analysis-value">{transitCandidate.transitCount} ({transitCandidate.pointsInTransit} measurements)</span>
                                    </div>
                                    <div
                                        className="analysis-row"
                                        title="How often random scatter would make a dip pattern this strong. Lower is more convincing."
                                    >
                                        <span className="analysis-label">Chance of noise:</span>
                                        <span className="analysis-value">{formatFalseAlarmProbability(transitCandidate.falseAlarmProbability)}</span>
                                    </div>
                                </div>
                            ) : (
                                missingDip && (
                                    <div className="stellar-analysis-details__grid">
                                        <div className="analysis-row" title={missingDip.explanation}>
                                            <span className="analysis-label">Result:</span>
                                            <span className="analysis-value">{missingDip.value}</span>
                                        </div>
                                    </div>
                                )
                            )}
                        </div>
                    )}
                </div>
            )}

            {(periodSearchNote || canAnalyze || analysisError) && (
                <div className="stellar-analysis-details__section">
                    <div className="stellar-analysis-details__section-title">Period Search</div>
                    {periodSearchNote && (
                        <div className="stellar-analysis-details__note" title={periodSearchNoteDetail || undefined}>
                            {periodSearchNote}
                        </div>
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
                            {isAnalyzing ? 'Analyzing…' : 'Run period analysis'}
                        </button>
                    )}
                </div>
            )}
        </div>
    );
};
