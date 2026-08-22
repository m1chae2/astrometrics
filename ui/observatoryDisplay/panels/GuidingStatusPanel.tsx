/**
 * Description: Guiding Status Display Panels.
 * Implements KStars/Ekos-style autoguiding monitoring including 2D bullseye target plots
 * and real-time interactive multi-trace time-series tracking for error drift, star SNR,
 * rolling RMS, and pulse corrections.
 * # REQ: OBS-7: Autoguiding Monitoring & Control
 */

import React, { useState, useMemo } from 'react';
import { GuidingSample } from '../../common/types/backendTypes';
import { TimeSeriesPlot } from '../../common/components/plotting/TimeSeriesPlot';
import { ScatterPlot } from '../../common/components/plotting/ScatterPlot';
// Styles handled by panels.css

export interface GuidingStatusProps {
    history: GuidingSample[];
}

/**
 * GuidingTrendsPlot Component
 *
 * Visualizes the history of guiding corrections (dRA, dDEC), guide star SNR,
 * rolling RMS, and pulse corrections over a dynamically selectable history window.
 * Features an interactive toolbar for toggling individual metrics and scaling plot history.
 *
 * REQ: OBS-7: Autoguiding Monitoring & Control
 * REQ: OBS-7.3: The display SHALL visualize guiding errors (dRA, dDEC) over time.
 */
export const GuidingTrendsPlot: React.FC<GuidingStatusProps> = ({ history }) => {
    const [showRA, setShowRA] = useState<boolean>(true);
    const [showDEC, setShowDEC] = useState<boolean>(true);
    const [showSNR, setShowSNR] = useState<boolean>(true);
    const [showRMS, setShowRMS] = useState<boolean>(true);
    const [showCorr, setShowCorr] = useState<boolean>(true);
    const [maxSamples, setMaxSamples] = useState<number>(50);

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

        if (showSNR) {
            datasets.push({
                name: 'SNR',
                color: '#34c759',
                values: slicedHistory.map(s => s.snr ?? 0),
                timestamps,
                yaxis: 'y2'
            });
        }

        if (showRMS) {
            datasets.push({
                name: 'RMS RA',
                color: '#ff9500',
                values: slicedHistory.map(s => s.rmsRa ?? 0),
                timestamps
            });
            datasets.push({
                name: 'RMS DEC',
                color: '#af52de',
                values: slicedHistory.map(s => s.rmsDec ?? 0),
                timestamps
            });
        }

        if (showCorr) {
            datasets.push({
                name: 'Corr RA (s)',
                color: '#007aff',
                values: slicedHistory.map(s => (s.pulseRa ?? 0) / 1000),
                timestamps
            });
            datasets.push({
                name: 'Corr DEC (s)',
                color: '#5856d6',
                values: slicedHistory.map(s => (s.pulseDec ?? 0) / 1000),
                timestamps
            });
        }

        return datasets;
    }, [slicedHistory, showRA, showDEC, showSNR, showRMS, showCorr]);

    return (
        <div id="guiding-trends-plot" className="guiding-status__plot" style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
            <div style={{ flex: 1, minHeight: 0 }}>
                <TimeSeriesPlot data={data} yLabel="arcsec / seconds" yRange={[-3, 3]} />
            </div>

            {/* Interactive Control Panel */}
            <div className="guiding-controls">
                <div className="guiding-controls__group">
                    <label className="guiding-controls__label guiding-controls__label--ra">
                        <input
                            type="checkbox"
                            className="guiding-controls__checkbox"
                            checked={showRA}
                            onChange={(e) => setShowRA(e.target.checked)}
                        />
                        dRA
                    </label>

                    <label className="guiding-controls__label guiding-controls__label--dec">
                        <input
                            type="checkbox"
                            className="guiding-controls__checkbox"
                            checked={showDEC}
                            onChange={(e) => setShowDEC(e.target.checked)}
                        />
                        dDEC
                    </label>

                    <div className="guiding-controls__divider" />

                    <label className="guiding-controls__label guiding-controls__label--snr">
                        <input
                            type="checkbox"
                            className="guiding-controls__checkbox"
                            checked={showSNR}
                            onChange={(e) => setShowSNR(e.target.checked)}
                        />
                        SNR
                    </label>

                    <label className="guiding-controls__label guiding-controls__label--rms">
                        <input
                            type="checkbox"
                            className="guiding-controls__checkbox"
                            checked={showRMS}
                            onChange={(e) => setShowRMS(e.target.checked)}
                        />
                        RMS
                    </label>

                    <label className="guiding-controls__label guiding-controls__label--corr">
                        <input
                            type="checkbox"
                            className="guiding-controls__checkbox"
                            checked={showCorr}
                            onChange={(e) => setShowCorr(e.target.checked)}
                        />
                        Pulses
                    </label>
                </div>

                <div className="guiding-controls__slider-container">
                    <span>History:</span>
                    <input
                        type="range"
                        className="guiding-controls__slider"
                        min="10"
                        max="100"
                        step="5"
                        value={maxSamples}
                        onChange={(e) => setMaxSamples(parseInt(e.target.value))}
                    />
                    <span className="guiding-controls__slider-val">{maxSamples} pts</span>
                </div>
            </div>
        </div>
    );
};

/**
 * GuidingTargetPlot Component
 *
 * Visualizes the scatter of guiding corrections as a 2D bullseye plot.
 * REQ: OBS-7.4: The display SHALL provide a 2D scatter plot of guiding errors (Bullseye).
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
