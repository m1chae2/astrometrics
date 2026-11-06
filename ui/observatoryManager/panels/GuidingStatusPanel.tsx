/**
 * Description: Guiding Status Display Panels.
 * Implements KStars/Ekos-style autoguiding monitoring including 2D bullseye target plots
 * and real-time interactive multi-trace time-series tracking for error drift, star SNR,
 * rolling RMS, and pulse corrections. The Guiding Trends panel keeps its trace
 * checkboxes and its History slider in the panel header, so the plot gets the whole body.
 * With no samples yet it shows a short message instead of an empty plot.
 * While the view shows a past night (chosen at the top of the Observatory Manager), the
 * panel shows that night's guiding summary in place of the live plot and its controls.
 * When Live has no samples (the mount is not guiding), the panel shows the summary of the
 * most recent night that has guiding data, titled with its date, and switches back to the
 * live plot as soon as samples arrive.
 */

import React, { useState, useMemo } from 'react';
import { Checkbox } from '../../common/components/Checkbox';
import { Slider } from '../../common/components/Slider';
import { useQuery } from '@tanstack/react-query';
import { callBackend } from '../../common/services/backendApi';
import { qualityQueryKeys } from '../../common/queries/qualityQueryKeys';
import { SectionPanel } from '../../common/components/SectionPanel';
import { GuidingSample } from '../../common/types/backendTypes';
import { TimeSeriesPlot } from '../../common/components/plotting/TimeSeriesPlot';
import { ScatterPlot } from '../../common/components/plotting/ScatterPlot';
import { GuidingNightSummaryView } from './GuidingNightSummaryView';
import { useObservatoryNight } from '../ObservatoryNightContext';
// Styles handled by panels.css

export interface GuidingStatusProps {
    history: GuidingSample[];
}

/**
 * GuidingTrendsPlot Component
 *
 * The Guiding Trends panel: the history of guiding corrections (dRA, dDEC), guide star SNR,
 * rolling RMS, and pulse corrections over a dynamically selectable history window.
 * The panel header holds the checkboxes that toggle each trace and the History slider.
 *
 */
export const GuidingTrendsPlot: React.FC<GuidingStatusProps> = ({ history }) => {
    const [showRA, setShowRA] = useState<boolean>(true);
    const [showDEC, setShowDEC] = useState<boolean>(true);
    const [showSNR, setShowSNR] = useState<boolean>(true);
    const [showRMS, setShowRMS] = useState<boolean>(true);
    const [showCorr, setShowCorr] = useState<boolean>(true);
    const [maxSamples, setMaxSamples] = useState<number>(50);
    const { isLive, nightId } = useObservatoryNight();

    // With no live samples, fall back to the newest night that has guiding data.
    const noLiveSamples = history.length === 0;
    const nightsQuery = useQuery({
        queryKey: qualityQueryKeys.nights(),
        queryFn: () => callBackend('history:nights', {}),
        enabled: isLive && noLiveSamples,
        retry: false,
    });
    const lastGuidedNight = useMemo(() => {
        const withGuiding = (nightsQuery.data ?? []).filter((night) => night.hasGuidingData);
        // Newest first by date, so the answer does not depend on the order the backend lists them.
        return [...withGuiding].sort((first, second) => second.nightDate.localeCompare(first.nightDate))[0] ?? null;
    }, [nightsQuery.data]);
    const showLastNight = isLive && noLiveSamples && lastGuidedNight !== null;

    // Limit guiding history to the user-selected window size
    const slicedHistory = useMemo(() => {
        return history.slice(-maxSamples);
    }, [history, maxSamples]);

    // Build the dynamic datasets based on active checkbox filters
    const data = useMemo(() => {
        const datasets: any[] = [];
        const timestamps = slicedHistory.map(s => s.time);

        if (showRA) {
            datasets.push({
                name: 'dRA',
                color: '#00ffff',
                values: slicedHistory.map(s => s.dra),
                timestamps
            });
        }

        if (showDEC) {
            datasets.push({
                name: 'dDEC',
                color: '#ff3b30',
                values: slicedHistory.map(s => s.ddec),
                timestamps
            });
        }

        const hasValidSNR = slicedHistory.some(s => s.snr != null && s.snr > 0 && Math.abs(s.snr - 22.0) > 1e-3);
        if (showSNR && hasValidSNR) {
            datasets.push({
                name: 'SNR',
                color: '#34c759',
                values: slicedHistory.map(s => (s.snr != null && Math.abs(s.snr - 22.0) > 1e-3 ? s.snr : null)),
                timestamps,
                yaxis: 'y2'
            });
        }

        if (showRMS) {
            datasets.push({
                name: 'RMS RA',
                color: '#ff9500',
                values: slicedHistory.map(s => s.rmsRa ?? (s as any).rms_ra ?? 0),
                timestamps
            });
            datasets.push({
                name: 'RMS DEC',
                color: '#af52de',
                values: slicedHistory.map(s => s.rmsDec ?? (s as any).rms_dec ?? 0),
                timestamps
            });
        }

        if (showCorr) {
            datasets.push({
                name: 'Corr RA (s)',
                color: '#007aff',
                values: slicedHistory.map(s => (s.pulseRa ?? (s as any).pulse_ra ?? 0) / 1000),
                timestamps
            });
            datasets.push({
                name: 'Corr DEC (s)',
                color: '#5856d6',
                values: slicedHistory.map(s => (s.pulseDec ?? (s as any).pulse_dec ?? 0) / 1000),
                timestamps
            });
        }

        return datasets;
    }, [slicedHistory, showRA, showDEC, showSNR, showRMS, showCorr]);

    // Adaptive yRange to keep small guide trends crisp and visible
    const yRange = useMemo<[number, number]>(() => {
        let maxVal = 0.5;
        for (const s of slicedHistory) {
            if (s.dra != null) maxVal = Math.max(maxVal, Math.abs(s.dra));
            if (s.ddec != null) maxVal = Math.max(maxVal, Math.abs(s.ddec));
            const rRa = s.rmsRa ?? (s as any).rms_ra;
            if (rRa != null) maxVal = Math.max(maxVal, Math.abs(rRa));
            const rDec = s.rmsDec ?? (s as any).rms_dec;
            if (rDec != null) maxVal = Math.max(maxVal, Math.abs(rDec));
        }
        const limit = Math.max(1.5, Math.min(5.0, Math.ceil(maxVal * 1.3 * 2) / 2));
        // A little room past the outer ticks, so the bottom tick label never sits on the first time label.
        const padded = limit * 1.08;
        return [-padded, padded];
    }, [slicedHistory]);

    const headerControls = (
        <span className="guiding-trends__controls">
            {isLive && !showLastNight && (<>
            <Checkbox
                className="guiding-controls__label guiding-controls__label--ra"
                label="dRA"
                checked={showRA}
                onChange={(e) => setShowRA(e.target.checked)}
            />
            <Checkbox
                className="guiding-controls__label guiding-controls__label--dec"
                label="dDEC"
                checked={showDEC}
                onChange={(e) => setShowDEC(e.target.checked)}
            />
            <Checkbox
                className="guiding-controls__label guiding-controls__label--snr"
                label="SNR"
                checked={showSNR}
                onChange={(e) => setShowSNR(e.target.checked)}
            />
            <Checkbox
                className="guiding-controls__label guiding-controls__label--rms"
                label="RMS"
                checked={showRMS}
                onChange={(e) => setShowRMS(e.target.checked)}
            />
            <Checkbox
                className="guiding-controls__label guiding-controls__label--corr"
                label="Pulses"
                checked={showCorr}
                onChange={(e) => setShowCorr(e.target.checked)}
            />
            <label className="guiding-controls__slider-container guiding-trends__history">
                <span>History:</span>
                <Slider
                    className="guiding-controls__slider"
                    min="10"
                    max="100"
                    step="5"
                    value={maxSamples}
                    onChange={(e) => setMaxSamples(parseInt(e.target.value))}
                />
                <span className="guiding-controls__slider-val">{maxSamples} pts</span>
            </label>
            </>)}
        </span>
    );

    return (
        <SectionPanel
            title={showLastNight ? `Last night with guiding: ${lastGuidedNight.nightDate}` : 'Guiding Trends'}
            className="observatory-guiding-trends"
            headerContent={headerControls}
        >
            {!isLive && nightId !== null ? (
                <GuidingNightSummaryView nightId={nightId} />
            ) : showLastNight ? (
                <GuidingNightSummaryView nightId={lastGuidedNight.nightId} />
            ) : (
            <div id="guiding-trends-plot" className="guiding-status__plot guiding-status__plot--trends">
                {slicedHistory.length === 0 ? (
                    // With no samples the time axis would only read 00:00:00, so say so in words instead.
                    <div className="guiding-trends__empty">No guiding data yet. The trends appear once guiding starts.</div>
                ) : (
                    <div className="guiding-trends__plot-fill">
                        <TimeSeriesPlot data={data} yLabel="arcsec / seconds" yRange={yRange} />
                    </div>
                )}
            </div>
            )}
        </SectionPanel>
    );
};

/**
 * GuidingTargetPlot Component
 *
 * Visualizes the scatter of guiding corrections as a 2D bullseye plot.
 */
export const GuidingTargetPlot: React.FC<GuidingStatusProps> = ({ history }) => {
    // Map history to ScatterPlot data format
    const data = [{
        x: history.map(s => s.dra),
        y: history.map(s => s.ddec),
        name: 'Guide Error',
        color: '#ffffff'
    }];

    return (
        <div id="guiding-target-plot" className="guiding-status__plot">
            <ScatterPlot
                data={data}
                xLabel="dRA"
                yLabel="dDEC"
                xRange={[5, -5]}
                yRange={[-5, 5]}
                showCircles={true}
            />
        </div>
    );
};
