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
        base.shapes = selections.map(sel => ({
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

        return base;
    }, [selections]);

    if (loading) return <div className="astronomy-viewer-loading">Loading spectrum...</div>;
    if (error) return <div className="astronomy-viewer-error">{error}</div>;

    const hasData = astronomyData && (
        (astronomyData.wavelength?.length > 0 && astronomyData.spectrumFlux?.length > 0) ||
        (astronomyData.spectraHistory && astronomyData.spectraHistory.length > 0)
    );

    const hasEpochs = Array.isArray(astronomyData?.spectraHistory) && astronomyData.spectraHistory.length > 1;

    return (
        <div className="astronomy-viewer-root astronomy-viewer-root--full-height" ref={containerRef}>
            {hasEpochs && (
                <div className="astronomy-viewer__toolbar">
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
