import React, { useRef, useState, useCallback, useMemo, useEffect } from 'react';
import {
    PlotlyTrace,
    findNearestIndex,
    getSelectionTraceName,
    computeTooltipPosition,
    getVar,
    buildLayout
} from '../../common/utils/plotTools';
import { BasePlot } from '../../common/components/plotting/BasePlot';
import '../styles/astronomyViewer.css';

import { Spectrum, SpectralObservation } from '../../common/types/backendTypes';
import { SpectralFeatureResult } from '../../common/types/spectralFeatureTypes';
import { formatFalseAlarmProbability, shortFeatureName, significantFeatures } from '../utils/starDisplayFormat';

interface Props {
    astronomyData: (Spectrum & { wavelength: number[]; spectrumFlux: number[] }) | null;
    loading: boolean;
    error?: string | null;
    active?: boolean;
    selectedTimestamps?: Set<string>;
}

interface Selection {
    id: string;
    x: number;
    y: number;
    left: number;
    top: number;
    idx: number;
}

// REQ: AST-2: Spectroscopy Visualization
export const SpectrumViewer: React.FC<Props> = ({
    astronomyData,
    loading,
    error,
    active = false,
    selectedTimestamps,
}) => {
    const containerRef = useRef<HTMLDivElement | null>(null);
    const [selections, setSelections] = useState<Selection[]>([]);

    // Clear selections when data changes significantly (signature check)
    const generateSpectrumSignature = (sd: any | null): string => {
        if (!sd || !Array.isArray(sd.wavelength)) return 'null';
        const wl = sd.wavelength;
        const first = wl.length ? wl[0] : NaN;
        const last = wl.length ? wl[wl.length - 1] : NaN;
        return `${wl.length}:${Number(first).toFixed(6)}:${Number(last).toFixed(6)}`;
    };

    const signature = useMemo(() => generateSpectrumSignature(astronomyData), [astronomyData]);
    useEffect(() => {
        setSelections([]);
    }, [signature]);

    const handleChartClick = useCallback((ev: any) => {
        // REQ: AST-2.2: The display SHALL allow interactive selection of points on the spectrum.
        // REQ: AST-2.4: The display SHALL allow multiple simultaneous selection markers on the plot.
        if (!ev || !ev.points || ev.points.length === 0 || !astronomyData) return;

        const point = ev.points[0];
        const wl = astronomyData.wavelength;

        const existingSel = selections.find(
            (s) => Math.abs(s.x - point.x) < 1e-9
        );

        if (existingSel) {
            setSelections((s) => s.filter((x) => x.id !== existingSel.id));
            return;
        }

        const clientX = ev.event?.clientX ?? 0;
        const clientY = ev.event?.clientY ?? 0;
        const pos = computeTooltipPosition(containerRef.current, clientX, clientY);
        const idx = findNearestIndex(wl, point.x);

        const id = `${Date.now().toString(36)}-${Math.round(Math.random() * 1e9).toString(36)}`;

        const newSel = { id, x: point.x, y: point.y, left: pos.left, top: pos.top, idx };
        setSelections((s) => [...s, newSel]);
    }, [astronomyData, selections]);

    const removeSelectionMarker = (id: string) => {
        setSelections((s) => s.filter((x) => x.id !== id));
    };

    const [isOverlayingEpochs, setIsOverlayingEpochs] = useState<boolean>(true);
    const [showFeatures, setShowFeatures] = useState<boolean>(true);

    // Every named absorption feature (Balmer series, Ca II H&K, etc.) the
    // backend tested for. Only those it judged detected or possible are drawn
    // on the plot; the rest are listed in the Stellar Analysis panel.
    const testedSpectralFeatures = useMemo<SpectralFeatureResult[]>(
        () => (astronomyData?.spectroscopy?.probableSpectralFeatures ?? []) as SpectralFeatureResult[],
        [astronomyData]
    );
    const probableSpectralFeatures = useMemo(
        () => significantFeatures(testedSpectralFeatures),
        [testedSpectralFeatures]
    );

    const plotData: PlotlyTrace[] = useMemo(() => {
        if (!astronomyData) return [];
        const traces: PlotlyTrace[] = [];

        const accent = getVar('--plot-accent', '#2196f3');
        const selectionColor = getVar('--plot-selection', '#ff4444');
        const palette = ['#4caf50', '#ff9800', '#9c27b0', '#f44336', '#00bcd4', '#e91e63'];

        // 1. Add History Traces (Spectra over time) if overlaying is enabled
        if (isOverlayingEpochs && astronomyData.spectraHistory) {
            astronomyData.spectraHistory.forEach((obs, i) => {
                if (selectedTimestamps && !selectedTimestamps.has(obs.timestamp)) return;

                traces.push({
                    x: obs.wavelengths || [],
                    y: obs.intensities || [],
                    mode: 'lines',
                    line: {
                        color: palette[i % palette.length],
                        width: 1.5,
                        shape: 'spline'
                    },
                    hoverinfo: 'x+y',
                    name: `Obs ${new Date(obs.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
                });
            });
        }

        // 2. Add Primary Trace (most recent/main extraction)
        if (Array.isArray(astronomyData.wavelength) && astronomyData.wavelength.length > 0) {
            traces.push({
                x: astronomyData.wavelength,
                y: astronomyData.spectrumFlux,
                mode: 'lines',
                line: { color: accent, width: 3 },
                hoverinfo: 'x+y',
                name: 'Main Spectrum'
            });
        }

        // 3. Add selection markers
        selections.forEach(sel => {
            traces.push({
                x: [sel.x],
                y: [sel.y],
                mode: 'markers',
                marker: { color: selectionColor, size: 14, symbol: 'circle' },
                hoverinfo: 'none',
                showlegend: false,
                name: getSelectionTraceName(sel.id)
            });
        });

        return traces;
    }, [astronomyData, selections, selectedTimestamps, isOverlayingEpochs]);

    const layout = useMemo(() => {
        const base = buildLayout({
            xTitle: 'Wavelength (Å)',
            yTitle: 'Intensity'
        });

        // REQ: AST-2.6: The display SHALL overlay vertical lines spanning the height of the plot for selections.
        const selectionColor = getVar('--plot-selection', '#ff4444');
        const selectionShapes = selections.map(sel => ({
            type: 'line',
            x0: sel.x,
            y0: 0,
            x1: sel.x,
            y1: 1,
            xref: 'x',
            yref: 'paper',
            line: {
                color: selectionColor,
                width: 1,
                dash: 'dash'
            }
        }));

        const featureColor = getVar('--plot-green', '#00ff00');
        // A detected feature is drawn as a solid line, a possible one as a
        // dotted, fainter line. The line sits where the dip was actually found,
        // which can differ a little from the feature's rest wavelength.
        const featureX = (feature: SpectralFeatureResult) =>
            feature.measured_wavelength_angstrom ?? feature.wavelength_angstrom;
        const featureShapes = showFeatures ? probableSpectralFeatures.map((f) => ({
            type: 'line',
            x0: featureX(f),
            y0: 0,
            x1: featureX(f),
            y1: 1,
            xref: 'x',
            yref: 'paper',
            opacity: f.verdict === 'detected' ? 0.9 : 0.5,
            line: {
                color: featureColor,
                width: 1,
                dash: f.verdict === 'detected' ? 'solid' : 'dot'
            }
        })) : [];

        base.shapes = [...selectionShapes, ...featureShapes];

        base.annotations = showFeatures ? probableSpectralFeatures.map((f) => {
            const label = shortFeatureName(String(f.feature));
            const details = [
                `${f.verdict === 'detected' ? 'Detected' : 'Possible'}`,
                f.depth !== undefined ? `depth ${(f.depth * 100).toFixed(0)}%` : '',
                f.p_value !== undefined ? `chance of noise doing this: ${formatFalseAlarmProbability(f.p_value)}` : '',
                typeof f.probability_present === 'number' ? `model probability present: ${Math.round(f.probability_present * 100)}%` : '',
            ].filter((part) => part !== '').join(', ');
            return {
                x: featureX(f),
                y: 1,
                xref: 'x',
                yref: 'paper',
                yanchor: 'bottom',
                showarrow: false,
                textangle: -90,
                text: label,
                hovertext: details,
                captureevents: true,
                font: { color: featureColor, size: 10 },
                opacity: f.verdict === 'detected' ? 1 : 0.6
            };
        }) : [];

        return base;
    }, [selections, probableSpectralFeatures, showFeatures]);

    if (loading) return <div className="astronomy-viewer-loading">Loading spectrum...</div>;
    if (error) return <div className="astronomy-viewer-error">{error}</div>;

    const hasData = astronomyData && (
        (astronomyData.wavelength?.length > 0 && astronomyData.spectrumFlux?.length > 0) ||
        (astronomyData.spectraHistory && astronomyData.spectraHistory.length > 0)
    );

    // Say so when part of the requested spectrum could not be measured (the
    // trail ran off the picture or past the camera's range), so a spectrum that
    // stops early is not mistaken for a star that goes dark.
    const requestedRange = astronomyData?.spectroscopy?.requestedWavelengthRangeAngstrom;
    const measuredWavelengths = astronomyData?.spectroscopy?.wavelengthsAngstrom ?? [];
    let coverageNote = '';
    if (requestedRange && requestedRange.length === 2 && measuredWavelengths.length > 0) {
        const measuredLow = Math.min(...measuredWavelengths);
        const measuredHigh = Math.max(...measuredWavelengths);
        if (measuredHigh < requestedRange[1] - 50 || measuredLow > requestedRange[0] + 50) {
            coverageNote =
                `Measured ${Math.round(measuredLow).toLocaleString()}-${Math.round(measuredHigh).toLocaleString()} Å of the ` +
                `${Math.round(requestedRange[0]).toLocaleString()}-${Math.round(requestedRange[1]).toLocaleString()} Å requested; ` +
                `the rest ran off the image or past the camera's range.`;
        }
    }

    const hasEpochs = Array.isArray(astronomyData?.spectraHistory) && astronomyData.spectraHistory.length > 1;
    const hasFeatures = testedSpectralFeatures.length > 0;

    return (
        <div className="astronomy-viewer-root astronomy-viewer-root--full-height" ref={containerRef}>
            {(hasEpochs || hasFeatures) && (
                <div className="astronomy-viewer__toolbar">
                    {hasFeatures && (
                        <div className="astronomy-viewer__segmented-control">
                            <button
                                type="button"
                                className={`segmented-btn ${showFeatures ? 'active' : ''}`}
                                onClick={() => setShowFeatures((v) => !v)}
                            >
                                Features ({probableSpectralFeatures.length})
                            </button>
                        </div>
                    )}
                    {hasEpochs && (
                        <div className="astronomy-viewer__segmented-control">
                            <button
                                type="button"
                                className={`segmented-btn ${!isOverlayingEpochs ? 'active' : ''}`}
                                onClick={() => setIsOverlayingEpochs(false)}
                            >
                                Latest Epoch
                            </button>
                            <button
                                type="button"
                                className={`segmented-btn ${isOverlayingEpochs ? 'active' : ''}`}
                                onClick={() => setIsOverlayingEpochs(true)}
                            >
                                Overlay All Epochs ({astronomyData.spectraHistory?.length ?? 0})
                            </button>
                        </div>
                    )}
                </div>
            )}
            <BasePlot
                data={plotData}
                className="astronomy-viewer__plot astronomy-plot astronomy-plot-container"
                layout={layout}
                // REQ: AST-2.5: The display SHALL provide zoom and pan capabilities for detailed inspection of spectral lines.
                config={{ responsive: true, displayModeBar: false }}
                onClick={handleChartClick}
            />

            {coverageNote && <div className="astronomy-viewer__coverage-note">{coverageNote}</div>}

            {!hasData && (
                <div className="astronomy-viewer-empty-overlay">
                    {active ? "No spectral data available for this object." : "Select a star to view its spectrum."}
                </div>
            )}

            {/* Tooltips */}
            {selections.map((sel) => (
                <div
                    key={sel.id}
                    className="astronomy-selection-tooltip"
                    style={{
                        position: 'absolute',
                        left: Math.max(4, sel.left),
                        top: Math.max(4, sel.top),
                    }}
                >
                    {/* REQ: AST-2.3: The display SHALL show a tooltip with precise Wavelength and Intensity values for selected points. */}
                    <button
                        className="astronomy-tooltip-close"
                        onClick={() => removeSelectionMarker(sel.id)}
                        aria-label="Close"
                        type="button"
                    >
                        ×
                    </button>
                    <div className="astronomy-selection-line">
                        λ: {sel.x.toFixed(0)} Å
                    </div>
                </div>
            ))}
        </div>
    );
};
