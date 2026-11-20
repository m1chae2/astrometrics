/**
 * @file StarShapeChart.tsx
 * @description The chart of how stretched the stars are in each frame, in the order the frames were
 * taken. Each dot is one frame (x = time taken, y = elongation, where 1 is round). A frame the star-shape
 * review flags is drawn as a diamond in a second color, so it does not depend on color alone. A dashed
 * line marks the session's typical (median) elongation. The frame picked in the File Browser has a ring
 * around it. Clicking a dot picks that frame, the same as clicking its row in the File Browser.
 */
import React, { useMemo } from 'react';
import { BasePlot } from '../../common/components/plotting/BasePlot';
import { buildLayout, getVar, getVarAsNum } from '../../common/utils/plotTools';
import type { PlotlyTrace } from '../../common/utils/plotTools';
import type { FrameStarShape } from '../../common/types/backendTypes';
import { sessionTypicalElongation } from '../utils/starShapeStats';

/** What {@link buildStarShapeChart} gives back. */
export interface StarShapeChartData {
    /** The Plotly traces: normal frames, flagged frames, then the picked frame. */
    traces: PlotlyTrace[];
    /** The session's typical elongation (the dashed line), or `null` when no frame is measured. */
    typical: number | null;
    /** How many frames have a time and an elongation, and so are drawn. */
    drawnCount: number;
}

/** Writes a time the way Plotly reads it on a date axis: milliseconds since 1970. */
const toPlotTime = (seconds: number): number => seconds * 1000;

/**
 * Builds the chart's traces from the review's per-frame numbers.
 * @param series The review's per-frame numbers.
 * @param flaggedPaths The paths of the frames the review flags.
 * @param selectedPath The frame picked in the File Browser, or `null`.
 * @return The traces, the typical elongation and the number of frames drawn.
 */
export function buildStarShapeChart(
    series: FrameStarShape[],
    flaggedPaths: ReadonlySet<string>,
    selectedPath: string | null,
): StarShapeChartData {
    const drawn = series.filter(
        (frame) => typeof frame.timestamp === 'number' && typeof frame.elongation === 'number' && Number.isFinite(frame.elongation),
    );
    const pointsOf = (frames: FrameStarShape[]) => ({
        x: frames.map((frame) => toPlotTime(frame.timestamp as number)),
        y: frames.map((frame) => frame.elongation as number),
        customdata: frames.map((frame) => frame.path),
        text: frames.map((frame) => `${frame.path.split('/').pop()}: elongation ${(frame.elongation as number).toFixed(2)}`),
    });
    const normal = drawn.filter((frame) => !flaggedPaths.has(frame.path));
    const flagged = drawn.filter((frame) => flaggedPaths.has(frame.path));
    const traces: PlotlyTrace[] = [
        {
            ...pointsOf(normal), type: 'scatter', mode: 'markers', name: 'Frames', hoverinfo: 'text',
            marker: { symbol: 'circle', size: 6, color: getVar('--plot-blue', '#3584e4') },
        },
        {
            ...pointsOf(flagged), type: 'scatter', mode: 'markers', name: 'Flagged', hoverinfo: 'text',
            marker: { symbol: 'diamond', size: 9, color: getVar('--plot-orange', '#e95420') },
        },
    ];
    const picked = drawn.filter((frame) => frame.path === selectedPath);
    if (picked.length > 0) {
        traces.push({
            ...pointsOf(picked), type: 'scatter', mode: 'markers', name: 'Selected', hoverinfo: 'text',
            marker: { symbol: 'circle-open', size: 15, color: getVar('--plot-selection', '#e01b24') },
        });
    }
    return { traces, typical: sessionTypicalElongation(drawn), drawnCount: drawn.length };
}

interface StarShapeChartProps {
    /** The review's per-frame numbers. */
    series: FrameStarShape[];
    /** The paths of the frames the review flags. */
    flaggedPaths: ReadonlySet<string>;
    /** The frame picked in the File Browser, or `null`. */
    selectedPath: string | null;
    /** Called with a frame's path when its dot is clicked. */
    onSelectFrame: (path: string) => void;
}

/**
 * Draws the elongation-by-frame chart.
 * @param props See {@link StarShapeChartProps}.
 * @return The chart, or `null` when no frame has both a time and an elongation.
 */
export const StarShapeChart: React.FC<StarShapeChartProps> = ({ series, flaggedPaths, selectedPath, onSelectFrame }) => {
    const chart = useMemo(() => buildStarShapeChart(series, flaggedPaths, selectedPath), [series, flaggedPaths, selectedPath]);
    const layout = useMemo(() => {
        const fontSize = getVarAsNum('--compact-font-size', 13) - 1;
        const base = buildLayout({
            xTitle: 'time taken', yTitle: 'stretch', xTitleFont: { size: fontSize }, yTitleFont: { size: fontSize },
            margin: { l: 52, r: 10, t: 8, b: 40 },
        });
        const typical = chart.typical;
        return {
            ...base,
            font: { ...base.font, size: fontSize },
            xaxis: { ...base.xaxis, type: 'date', tickfont: { size: fontSize } },
            yaxis: { ...base.yaxis, tickfont: { size: fontSize } },
            shapes: typical === null ? [] : [{
                type: 'line', xref: 'paper', yref: 'y', x0: 0, x1: 1, y0: typical, y1: typical,
                line: { color: getVar('--plot-reference-line', '#aaaaaa'), width: 1, dash: 'dash' },
            }],
            annotations: typical === null ? [] : [{
                x: 1, xref: 'paper', xanchor: 'right', y: typical, yref: 'y', yanchor: 'bottom',
                text: `session median ${typical.toFixed(2)}`, showarrow: false, font: { size: fontSize },
            }],
        };
    }, [chart.typical]);

    if (chart.drawnCount === 0) return null;
    return (
        <div className="star-shape-chart" data-testid="star-shape-chart">
            <BasePlot
                data={chart.traces}
                layout={layout}
                config={{ responsive: true, displayModeBar: false }}
                onClick={(event: { points?: Array<{ customdata?: unknown }> }) => {
                    const path = event?.points?.[0]?.customdata;
                    if (typeof path === 'string') onSelectFrame(path);
                }}
                style={{ width: '100%', height: '100%' }}
            />
        </div>
    );
};
