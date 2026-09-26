/**
 * @fileoverview Photometry viewer component supporting time-series light curve plots
 * and phase-folded orbital light curve rendering in AstronomyDisplay.
 */

import React, { useState, useMemo } from 'react';
import { BasePlot } from '../../common/components/plotting/BasePlot';
import { PlotlyTrace, getVar, buildLayout } from '../../common/utils/plotTools';
import '../styles/astronomyViewer.css';

import { Spectrum } from '../../common/types/backendTypes';
import { formatPeriod, isSignificantVerdict, selectLightCurveSeries } from '../utils/starDisplayFormat';

interface Props {
    astronomyData: Spectrum | null;
    loading: boolean;
    error?: string | null;
    selectedTimestamps?: Set<string>;
}

/**
 * Calculates phase array from timestamps and best period.
 * @param timestamps Array of UTC timestamp strings or dates.
 * @param periodDays Period in days.
 * @returns Array of phase values between 0.0 and 1.0.
 */
function computePhaseArray(timestamps: string[], periodDays: number): number[] {
    if (!timestamps || timestamps.length === 0 || !periodDays || periodDays <= 0) {
        return [];
    }
    const t0 = new Date(timestamps[0]).getTime();
    const periodMs = periodDays * 86400 * 1000;
    return timestamps.map((ts) => {
        const t = new Date(ts).getTime();
        const diff = t - t0;
        const phase = (diff % periodMs) / periodMs;
        return phase < 0 ? phase + 1.0 : phase;
    });
}

// REQ: AST-3: Photometry Visualization
export const PhotometryViewer: React.FC<Props> = ({
    astronomyData,
    loading,
    error,
    selectedTimestamps,
}) => {
    const [isPhaseFolded, setIsPhaseFolded] = useState<boolean>(false);

    const lc = astronomyData?.photometry;
    const periodogram = lc?.periodogram;
    const transitCandidate = lc?.transitCandidate;
    // A period is only offered for phase folding when the search judged it
    // detected or possible. The strongest peak of a noisy light curve is
    // not a period.
    const bestPeriodDays =
        (isSignificantVerdict(periodogram?.verdict) ? periodogram?.bestPeriodDays : 0) ||
        (isSignificantVerdict(transitCandidate?.verdict) ? transitCandidate?.periodDays : 0) ||
        0;

    // Which series is plotted (and how its axis is titled) comes from what
    // the star actually has, not from a fixed choice.
    const lightCurveSeries = useMemo(() => selectLightCurveSeries(lc), [lc]);

    const plotData: PlotlyTrace[] = useMemo(() => {
        if (!astronomyData?.photometry) return [];

        const allX = lc?.timestamps || [];
        const { values: allValues, axisTitle: yLabel } = lightCurveSeries;

        const rawX: string[] = [];
        const y: number[] = [];

        allX.forEach((timestamp, i) => {
            if (selectedTimestamps && !selectedTimestamps.has(timestamp)) return;
            rawX.push(timestamp);
            y.push(allValues[i]);
        });

        if (rawX.length === 0) return [];

        if (isPhaseFolded && bestPeriodDays > 0) {
            const phaseX = computePhaseArray(rawX, bestPeriodDays);
            return [
                {
                    x: phaseX as any,
                    y: y,
                    mode: 'markers',
                    type: 'scatter',
                    marker: { color: getVar('--plot-accent', '#2196f3'), size: 6 },
                    line: { width: 0 },
                    name: `${yLabel} (Phase-Folded P=${formatPeriod(bestPeriodDays)})`,
                },
            ];
        }

        return [
            {
                x: rawX as any,
                y: y,
                mode: 'markers',
                type: 'scatter',
                marker: { color: getVar('--plot-accent', '#2196f3'), size: 6 },
                line: { width: 0 },
                name: yLabel,
            },
        ];
    }, [astronomyData, selectedTimestamps, isPhaseFolded, bestPeriodDays, lc, lightCurveSeries]);

    const layout = useMemo(() => {
        const hasMagnitudes = lightCurveSeries.isMagnitude;
        const xTitle = isPhaseFolded && bestPeriodDays > 0 ? 'Orbital Phase (0.0 - 1.0)' : 'Time (UTC)';
        const base = buildLayout({
            xTitle,
            yTitle: lightCurveSeries.axisTitle,
        });

        if (base.yaxis && hasMagnitudes) {
            (base.yaxis as any).autorange = 'reversed';
        }
        return base;
    }, [isPhaseFolded, bestPeriodDays, lightCurveSeries]);

    if (loading) return <div className="astronomy-viewer-loading">Loading photometry...</div>;
    if (error) return <div className="astronomy-viewer-error">{error}</div>;

    const hasData = plotData.length > 0;

    return (
        <div className="astronomy-viewer-root astronomy-viewer-root--full-height">
            <div className="astronomy-viewer__toolbar">
                {bestPeriodDays > 0 && (
                    <div className="astronomy-viewer__segmented-control">
                        <button
                            type="button"
                            className={`segmented-btn ${!isPhaseFolded ? 'active' : ''}`}
                            onClick={() => setIsPhaseFolded(false)}
                        >
                            Time Series
                        </button>
                        <button
                            type="button"
                            className={`segmented-btn ${isPhaseFolded ? 'active' : ''}`}
                            onClick={() => setIsPhaseFolded(true)}
                        >
                            Phase-Folded ({formatPeriod(bestPeriodDays)})
                        </button>
                    </div>
                )}
            </div>

            <BasePlot
                data={plotData}
                layout={layout}
                config={{ responsive: true, displayModeBar: false }}
                className="astronomy-viewer__plot astronomy-plot astronomy-plot-container"
                data-testid="photometry-plot"
            />
            {!hasData && (
                <div className="astronomy-viewer-empty-overlay">
                    {!astronomyData
                        ? 'Select a star to view photometry'
                        : 'No photometry data available for this object.'}
                </div>
            )}
        </div>
    );
};
