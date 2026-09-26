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
import { EmissionLineResult, SpectralFeatureResult } from '../../common/types/spectralFeatureTypes';
import {
    assignLabelRows,
    formatFalseAlarmProbability,
    inconclusiveFeatures,
    shortFeatureName,
    emissionLineMarks,
    significantFeatures,
} from '../utils/starDisplayFormat';

interface Props {
    astronomyData: (Spectrum & { wavelength: number[]; spectrumFlux: number[] }) | null;
    loading: boolean;
    error?: string | null;
    active?: boolean;
    selectedTimestamps?: Set<string>;
    showFeatures?: boolean;
    onToggleFeatures?: () => void;
}

interface Selection {
    id: string;
    x: number;
    y: number;
    left: number;
    top: number;
    idx: number;
}

// Feature labels are printed horizontally above the plot, on rows that are stacked
// when neighbors would overlap. These sizes are in pixels.
const FEATURE_LABEL_FONT_SIZE = 12;
// About the width of one character of 12 px text.
const FEATURE_LABEL_CHARACTER_WIDTH = 7;
const FEATURE_LABEL_GAP = 6;
// The height of one row of labels, and the space between the top of the plot and the first row.
const FEATURE_LABEL_ROW_HEIGHT = 17;
const FEATURE_LABEL_FIRST_ROW_OFFSET = 10;
// The plot is wider than this on most screens. A low estimate only makes labels use an extra row
// where they would not have overlapped, and never lets two overlap.
const ASSUMED_PLOT_WIDTH = 700;
// Space kept above the plot for things other than labels, and the plot's normal top margin.
const PLOT_TOP_MARGIN = 20;

// REQ: AST-2: Spectroscopy Visualization
export const SpectrumViewer: React.FC<Props> = ({
    astronomyData,
    loading,
    error,
    active = false,
    selectedTimestamps,
    showFeatures: controlledShowFeatures,
    onToggleFeatures,
}) => {
    const containerRef = useRef<HTMLDivElement | null>(null);
    const [selections, setSelections] = useState<Selection[]>([]);

    // Clear selections when data changes significantly (signature check)
    const generateSpectrumSignature = (sd: any | null): string => {
        if (!sd || !Array.isArray(sd.wavelength)) return 'null';
        const wl = sd.wavelength;
        const first = wl.length ? wl[0] : NaN;
        const last = wl.length ? wl[wl.length - 1] : NaN;
        return `${wl.length}-${first.toFixed(2)}-${last.toFixed(2)}`;
    };

    const prevSigRef = useRef<string>('null');
    useEffect(() => {
        const sig = generateSpectrumSignature(astronomyData);
        if (sig !== prevSigRef.current) {
            prevSigRef.current = sig;
            setSelections([]);
        }
    }, [astronomyData]);

    const handleChartClick = useCallback((e: any) => {
        if (!e || !e.points || !e.points.length) return;
        const point = e.points[0];
        const evt = e.event;
        const container = containerRef.current;
        const pos = computeTooltipPosition(container, evt?.clientX, evt?.clientY);

        const wl = astronomyData?.wavelength || [];
        const idx = findNearestIndex(wl, point.x);

        const id = `${Date.now().toString(36)}-${Math.round(Math.random() * 1e9).toString(36)}`;

        const newSel = { id, x: point.x, y: point.y, left: pos.left, top: pos.top, idx };
        setSelections((s) => [...s, newSel]);
    }, [astronomyData, selections]);

    const removeSelectionMarker = (id: string) => {
        setSelections((s) => s.filter((x) => x.id !== id));
    };

    const [isOverlayingEpochs, setIsOverlayingEpochs] = useState<boolean>(true);
    const [internalShowFeatures, setInternalShowFeatures] = useState<boolean>(true);
    const showFeatures = controlledShowFeatures !== undefined ? controlledShowFeatures : internalShowFeatures;
    const handleToggleFeatures = onToggleFeatures || (() => setInternalShowFeatures((v) => !v));

    // Every named absorption feature (Balmer series, Ca II H&K, etc.) the
    // backend tested for. Those it judged detected or possible are drawn in
    // green; those with a dip too noisy to confirm are drawn in red; the rest
    // are listed only in the Stellar Analysis panel.
    const testedSpectralFeatures = useMemo<SpectralFeatureResult[]>(
        () => (astronomyData?.spectroscopy?.probableSpectralFeatures ?? []) as SpectralFeatureResult[],
        [astronomyData]
    );
    const probableSpectralFeatures = useMemo(
        () => significantFeatures(testedSpectralFeatures),
        [testedSpectralFeatures]
    );
    const inconclusiveSpectralFeatures = useMemo(
        () => inconclusiveFeatures(testedSpectralFeatures),
        [testedSpectralFeatures]
    );
    // Detected and unclear emission lines (glowing-gas sources) are marked the same way as absorption features.
    const emissionLineMarkers = useMemo(
        () => emissionLineMarks((astronomyData?.spectroscopy?.emissionLines ?? []) as EmissionLineResult[]),
        [astronomyData]
    );
    // Green and red lines share one drawing path below; they differ only in color and wording.
    const markedSpectralFeatures = useMemo(
        () => [...probableSpectralFeatures, ...inconclusiveSpectralFeatures, ...emissionLineMarkers],
        [probableSpectralFeatures, inconclusiveSpectralFeatures, emissionLineMarkers]
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

        const detectedColor = getVar('--slate-blue-light', '#4b8ec8');
        const inconclusiveColor = getVar('--plot-red', '#ff5252');
        const featureColor = (feature: SpectralFeatureResult) =>
            feature.verdict === 'inconclusive' ? inconclusiveColor : detectedColor;
        // A detected feature is drawn as a solid green line, a possible one as
        // a dotted, fainter green line, and an inconclusive one (a dip the
        // noise is too large to confirm) as a dotted red line. The line sits
        // where the dip was actually found, which can differ a little from the
        // feature's rest wavelength.
        const featureX = (feature: SpectralFeatureResult) =>
            feature.measured_wavelength_angstrom ?? feature.wavelength_angstrom;
        const featureShapes = showFeatures ? markedSpectralFeatures.map((f) => ({
            type: 'line',
            x0: featureX(f),
            y0: 0,
            x1: featureX(f),
            y1: 1,
            xref: 'x',
            yref: 'paper',
            opacity: f.verdict === 'detected' ? 0.9 : 0.5,
            line: {
                color: featureColor(f),
                width: 1,
                dash: f.verdict === 'detected' ? 'solid' : 'dot'
            }
        })) : [];

        base.shapes = [...selectionShapes, ...featureShapes];

        // Horizontal labels above the plot. Neighbors that would overlap go on higher rows, joined to
        // their line by a thin leader so it stays clear which line each name belongs to.
        const labelTexts = markedSpectralFeatures.map((f) => {
            const name = shortFeatureName(String(f.feature));
            return { plain: name, shown: f.verdict === 'detected' ? `<b>${name}</b>` : name };
        });
        const wavelengths = (astronomyData?.wavelength ?? []).filter((value: number) => Number.isFinite(value));
        const axisSpan = wavelengths.length > 1 ? Math.max(...wavelengths) - Math.min(...wavelengths) : 0;
        const labelRows = assignLabelRows(
            markedSpectralFeatures.map((f, index) => ({ x: featureX(f), text: labelTexts[index].plain })),
            axisSpan,
            ASSUMED_PLOT_WIDTH,
            FEATURE_LABEL_CHARACTER_WIDTH,
            FEATURE_LABEL_GAP
        );
        const rowsUsed = showFeatures && labelRows.length > 0 ? Math.max(...labelRows) + 1 : 0;
        if (rowsUsed > 0) {
            base.margin = {
                ...base.margin,
                t: PLOT_TOP_MARGIN + FEATURE_LABEL_FIRST_ROW_OFFSET + rowsUsed * FEATURE_LABEL_ROW_HEIGHT,
            };
        }

        base.annotations = showFeatures ? markedSpectralFeatures.map((f, index) => {
            const verdictText = { detected: 'Detected', possible: 'Possible', inconclusive: 'Inconclusive: too noisy to tell' }[
                f.verdict as 'detected' | 'possible' | 'inconclusive'
            ];
            const details = [
                verdictText,
                f.depth !== undefined ? `depth ${(f.depth * 100).toFixed(0)}%` : '',
                f.p_value !== undefined ? `chance of noise doing this: ${formatFalseAlarmProbability(f.p_value)}` : '',
                typeof f.probability_present === 'number' ? `model probability present: ${Math.round(f.probability_present * 100)}%` : '',
            ].filter((part) => part !== '').join(', ');
            return {
                x: featureX(f),
                y: 1,
                xref: 'x',
                yref: 'paper',
                xanchor: 'center',
                yanchor: 'bottom',
                // The label sits `ay` pixels above the top of the line, joined to it by a leader.
                showarrow: true,
                arrowhead: 0,
                arrowwidth: 1,
                arrowcolor: featureColor(f),
                ax: 0,
                ay: -(FEATURE_LABEL_FIRST_ROW_OFFSET + labelRows[index] * FEATURE_LABEL_ROW_HEIGHT),
                text: labelTexts[index].shown,
                hovertext: details,
                captureevents: true,
                font: { color: featureColor(f), size: FEATURE_LABEL_FONT_SIZE },
            };
        }) : [];

        return base;
    }, [selections, markedSpectralFeatures, showFeatures, astronomyData]);

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

    const hasEpochs = Array.isArray(astronomyData?.spectraHistory) && (astronomyData?.spectraHistory?.length ?? 0) > 1;
    const hasFeatures = testedSpectralFeatures.length > 0 || emissionLineMarkers.length > 0;
    const hasInternalFeaturesButton = hasFeatures && !onToggleFeatures;
    const showToolbar = hasEpochs || hasInternalFeaturesButton;

    return (
        <div className="astronomy-viewer-root astronomy-viewer-root--full-height" ref={containerRef}>
            {showToolbar && (
                <div className="astronomy-viewer__toolbar">
                    {hasInternalFeaturesButton && (
                        <div className="astronomy-viewer__segmented-control">
                            <button
                                type="button"
                                className={`segmented-btn ${!showFeatures ? 'active' : ''}`}
                                onClick={handleToggleFeatures}
                            >
                                Hide Feature Lines
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
                                Overlay All Epochs ({astronomyData?.spectraHistory?.length ?? 0})
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
