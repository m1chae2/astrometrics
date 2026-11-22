import React from 'react';
import { SectionPanel } from '../../common/components/SectionPanel';
import '../imageProcessingDisplay.css';
import { AnalysisResult } from '../../common/types/backendTypes';

export interface AnalysisResultsProps {
    results: (AnalysisResult & { status?: string }) | null;
}

/**
 * Component to display the results of an analysis job: the saved-to-registry note for spectroscopy.
 *
 * @param props See {@link AnalysisResultsProps}.
 * @returns The results, or nothing when there are none.
 */
export const AnalysisResults: React.FC<AnalysisResultsProps> = ({ results }) => {
    if (!results) return null;
    return <AnalysisResultsBody results={results} />;
};

/**
 * Draws the note for one analysis result; only spectroscopy has one.
 *
 * @param props See {@link AnalysisResultsProps}. The results are never null here.
 * @returns The note, or nothing for other modes.
 */
const AnalysisResultsBody: React.FC<{ results: AnalysisResult }> = ({ results }) => {

    if (results.analysisMode === 'spectroscopy') {
        return (
            <div className="analysis-results-content">
                <div className="analysis-header analysis-results__header">Spectroscopy Results</div>
                <div className="analysis-results__message">
                    Star data and extracted spectra have been saved to the global StellarObject registry.
                </div>
            </div>
        );
    }

    return null;
};
