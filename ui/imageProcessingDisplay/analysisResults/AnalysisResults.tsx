import React from 'react';
import { SectionPanel } from '../../common/components/SectionPanel';
import '../imageProcessingDisplay.css';
import { AnalysisResult } from '../../common/types/backendTypes';

export interface AnalysisResultsProps {
    results: (AnalysisResult & { status?: string }) | null;
}

/**
 * Component to display the results of a variability analysis.
 * Shows statistics and a list of top candidates.
 */
export const AnalysisResults: React.FC<AnalysisResultsProps> = ({ results }) => {
    if (!results) return null;

    const total = results.totalImages ?? '-';
    const mode = results.analysisMode;

    if (mode === 'spectroscopy') {
        return (
            <div className="analysis-results-content">
                <div className="metrics-grid">
                    <div className="metric-box">
                        <div className="metric-label">Total Images</div>
                        <div className="metric-value">{total}</div>
                    </div>
                    <div className="metric-box">
                        <div className="metric-label">Stars Processed</div>
                        <div className="metric-value success">{results.starsProcessed ?? 0}</div>
                    </div>
                    <div className="metric-box">
                        <div className="metric-label">Spectra Extracted</div>
                        <div className="metric-value success">{results.spectraExtracted ?? 0}</div>
                    </div>
                </div>
                <div className="analysis-header analysis-results__header">Spectroscopy Results</div>
                <div className="analysis-results__message">
                    Star data and extracted spectra have been saved to the global StellarObject registry.
                </div>
            </div>
        );
    }

    if (mode === 'photometry') {
        return (
            <div className="analysis-results-content">
                <div className="metrics-grid">
                    <div className="metric-box">
                        <div className="metric-label">Total Images</div>
                        <div className="metric-value">{total}</div>
                    </div>
                    <div className="metric-box">
                        <div className="metric-label">Stars Found</div>
                        <div className="metric-value success">{results.starsFound ?? 0}</div>
                    </div>
                    <div className="metric-box">
                        <div className="metric-label">Frames Processed</div>
                        <div className="metric-value success">{results.framesProcessed ?? 0}</div>
                    </div>
                </div>
            </div>
        );
    }

    const rejected = results.rejectedCount ?? 0;
    const accepted = typeof total === 'number' ? total - rejected : '-';
    const candidates = results.variableCandidates || [];

    return (
        <div className="analysis-results-content">
            <div className="metrics-grid">
                <div className="metric-box">
                    <div className="metric-label">Total Images</div>
                    <div className="metric-value">{total}</div>
                </div>
                <div className="metric-box">
                    <div className="metric-label">Rejected</div>
                    <div className="metric-value error">{rejected}</div>
                </div>
                <div className="metric-box">
                    <div className="metric-label">Accepted</div>
                    <div className="metric-value success">{accepted}</div>
                </div>
            </div>
        </div>
    );
};
