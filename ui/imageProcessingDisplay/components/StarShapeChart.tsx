/**
 * @file StarShapeChart.tsx
 * @description The frame status timeline of a target: when each frame was taken (x) and what happened to
 * it (y). Each status that has frames is a lane, listed top to bottom as set aside (cloud, trailed, off
 * field, other), flagged, then included. Each frame is one small square in its lane at its time, so a
 * cloud passage shows as a run of marks in the cloud lane. Hovering a mark gives the file name, time,
 * status and the reason. The frame picked in the File Browser has a ring around it, and clicking a mark
 * picks that frame. A dotted line separates nights. A one-line count of the frames sits above the chart.
 */
import React, { useMemo } from 'react';
import { BasePlot } from '../../common/components/plotting/BasePlot';
import { buildLayout, getVar, getVarAsNum } from '../../common/utils/plotTools';
import type { PlotlyTrace } from '../../common/utils/plotTools';
import type { FrameStarShape, FrameStatus } from '../../common/types/backendTypes';
import { setAsideCategory, type FrameStatusInfo } from '../utils/frameStatus';

/** The lanes, top to bottom. */
export const LANES = [
    'Set aside: cloud', 'Set aside: trailed', 'Set aside: off field', 'Set aside: other', 'Flagged', 'Included',
] as const;

/** One lane name. */
export type Lane = typeof LANES[number];

/** A gap between two frames longer than this starts a new night (milliseconds). */
const NIGHT_GAP_MS = 6 * 3600 * 1000;

/** What {@link buildStarShapeChart} gives back. */
export interface StarShapeChartData {
    /** The Plotly traces: one per lane that has frames, then the picked frame. */
    traces: PlotlyTrace[];
    /** The lanes that have frames, top to bottom. */
    lanes: Lane[];
    /** The number of frames in each lane that has frames. */
    counts: Partial<Record<Lane, number>>;
    /** How many frames are drawn (they have a time). */
    drawnCount: number;
    /** Times (milliseconds since 1970) halfway across each gap between nights. */
    nightBreaks: number[];
}

/** One frame of the chart before it is sorted into a lane. */
interface ChartFrame {
    path: string;
    /** Milliseconds since 1970. */
    time: number;
    lane: Lane;
    statusLabel: string;
    reason: string;
}

/**
 * Reads a status date such as "2026-10-03 08:15:30" as a time.
 * @param date The date text, or nothing.
 * @return Milliseconds since 1970, or `null` when it cannot be read.
 */
function parseStatusDate(date: string | undefined): number | null {
    if (!date) return null;
    const parsed = Date.parse(date.replace(' ', 'T'));
    return Number.isFinite(parsed) ? parsed : null;
}

/**
 * Writes the one-line count above the chart.
 * @param counts The number of frames in each lane.
 * @return For example "123 frames: 117 included, 5 set aside (5 trailed), 10 flagged".
 */
export function describeFrameCounts(counts: Partial<Record<Lane, number>>): string {
    const total = Object.values(counts).reduce((sum, count) => sum + (count ?? 0), 0);
    const parts: string[] = [];
    if (counts.Included) parts.push(`${counts.Included} included`);
    const kinds = LANES.filter((lane) => lane.startsWith('Set aside') && counts[lane]);
    if (kinds.length > 0) {
        const setAside = kinds.reduce((sum, lane) => sum + (counts[lane] ?? 0), 0);
        parts.push(`${setAside} set aside (${kinds.map((lane) => `${counts[lane]} ${lane.slice('Set aside: '.length)}`).join(', ')})`);
    }
    if (counts.Flagged) parts.push(`${counts.Flagged} flagged`);
    return `${total} ${total === 1 ? 'frame' : 'frames'}: ${parts.join(', ')}`;
}

/**
 * Builds the timeline's traces from the frames' statuses and times.
 * @param series The review's per-frame numbers, whose times are used first.
 * @param flaggedPaths The paths of the frames the review flags.
 * @param selectedPath The frame picked in the File Browser, or `null`.
 * @param frames The backend's per-frame statuses (including set-aside frames), or `null`.
 * @param statuses The File Browser's status of each frame by path.
 * @return The traces, the lanes, the counts and the night breaks.
 */
export function buildStarShapeChart(
    series: FrameStarShape[],
    flaggedPaths: ReadonlySet<string>,
    selectedPath: string | null,
    frames: readonly FrameStatus[] | null = null,
    statuses: ReadonlyMap<string, FrameStatusInfo> = new Map(),
): StarShapeChartData {
    const byPath = new Map<string, FrameStatus>((frames ?? []).map((frame) => [frame.path, frame]));
    const seriesByPath = new Map(series.map((frame) => [frame.path, frame]));
    const all: ChartFrame[] = [];
    for (const path of new Set<string>([...seriesByPath.keys(), ...byPath.keys()])) {
        const measured = seriesByPath.get(path);
        const status = byPath.get(path);
        const info = statuses.get(path);
        const time = typeof measured?.timestamp === 'number' ? measured.timestamp * 1000 : parseStatusDate(status?.date);
        if (time === null) continue;
        const kind = info?.kind ?? (flaggedPaths.has(path) ? 'flagged' : 'included');
        let lane: Lane = kind === 'flagged' ? 'Flagged' : 'Included';
        if (kind === 'set-aside') lane = `Set aside: ${setAsideCategory(status?.kind)}` as Lane;
        all.push({ path, time, lane, statusLabel: info?.label ?? lane, reason: info?.tooltip ?? status?.reason ?? '' });
    }
    all.sort((first, second) => first.time - second.time);
    const lanes = LANES.filter((lane) => all.some((frame) => frame.lane === lane));
    const counts: Partial<Record<Lane, number>> = {};
    for (const lane of lanes) counts[lane] = all.filter((frame) => frame.lane === lane).length;
    const yOf = (frame: ChartFrame): number => lanes.length - 1 - lanes.indexOf(frame.lane);
    const pointsOf = (group: ChartFrame[]) => ({
        x: group.map((frame) => frame.time),
        y: group.map(yOf),
        customdata: group.map((frame) => frame.path),
        text: group.map((frame) => {
            const reason = frame.reason ? `<br>${frame.reason}` : '';
            return `${frame.path.split('/').pop()}<br>${new Date(frame.time).toLocaleString()}<br>${frame.statusLabel}${reason}`;
        }),
    });
    const colorOf = (lane: Lane): string => {
        if (lane === 'Flagged') return getVar('--plot-orange', '#e95420');
        if (lane === 'Included') return getVar('--plot-green', '#26a269');
        return getVar('--plot-red', '#e01b24');
    };
    const traces: PlotlyTrace[] = lanes.map((lane) => ({
        ...pointsOf(all.filter((frame) => frame.lane === lane)), type: 'scatter', mode: 'markers', name: lane,
        hoverinfo: 'text', showlegend: false, marker: { symbol: 'square', size: 7, color: colorOf(lane) },
    }));
    const picked = all.filter((frame) => frame.path === selectedPath);
    if (picked.length > 0) {
        traces.push({
            ...pointsOf(picked), type: 'scatter', mode: 'markers', name: 'Selected', hoverinfo: 'text', showlegend: false,
            marker: { symbol: 'square-open', size: 14, color: getVar('--plot-selection', '#e01b24') },
        });
    }
    const nightBreaks: number[] = [];
    for (let index = 1; index < all.length; index += 1) {
        if (all[index].time - all[index - 1].time > NIGHT_GAP_MS) nightBreaks.push((all[index].time + all[index - 1].time) / 2);
    }
    return { traces, lanes, counts, drawnCount: all.length, nightBreaks };
}

interface StarShapeChartProps {
    /** The review's per-frame numbers. */
    series: FrameStarShape[];
    /** The paths of the frames the review flags. */
    flaggedPaths: ReadonlySet<string>;
    /** The frame picked in the File Browser, or `null`. */
    selectedPath: string | null;
    /** The backend's per-frame statuses (including set-aside frames), or `null` until read. */
    frames?: readonly FrameStatus[] | null;
    /** The File Browser's status of each frame by path. */
    statuses?: ReadonlyMap<string, FrameStatusInfo>;
    /** Called with a frame's path when its mark is clicked. */
    onSelectFrame: (path: string) => void;
}

/**
 * Draws the frame status timeline.
 * @param props See {@link StarShapeChartProps}.
 * @return The chart, or `null` when no frame has a time.
 */
export const StarShapeChart: React.FC<StarShapeChartProps> = ({ series, flaggedPaths, selectedPath, frames = null, statuses, onSelectFrame }) => {
    const chart = useMemo(() => buildStarShapeChart(series, flaggedPaths, selectedPath, frames, statuses), [series, flaggedPaths, selectedPath, frames, statuses]);
    const layout = useMemo(() => {
        const fontSize = getVarAsNum('--compact-font-size', 13) - 1;
        const base = buildLayout({
            xTitle: 'time taken', yTitle: '', xTitleFont: { size: fontSize },
            margin: { l: 8, r: 8, t: 8, b: 40 },
        });
        const top = chart.lanes.length - 1;
        return {
            ...base,
            font: { ...base.font, size: fontSize },
            xaxis: { ...base.xaxis, type: 'date', tickfont: { size: fontSize } },
            yaxis: {
                ...base.yaxis, tickfont: { size: fontSize }, range: [-0.6, top + 0.6], fixedrange: true, zeroline: false,
                tickmode: 'array', tickvals: chart.lanes.map((_, index) => top - index), ticktext: [...chart.lanes], automargin: true, ticklabelposition: 'outside',
            },
            autosize: true,
            showlegend: false,
            shapes: chart.nightBreaks.map((time) => ({
                type: 'line', xref: 'x', yref: 'paper', x0: time, x1: time, y0: 0, y1: 1,
                line: { color: getVar('--plot-reference-line', '#aaaaaa'), width: 1, dash: 'dot' },
            })),
        };
    }, [chart.lanes, chart.nightBreaks]);

    if (chart.drawnCount === 0) return null;
    return (
        <>
        <p className="star-shape-chart__count" data-testid="star-shape-count">{describeFrameCounts(chart.counts)}</p>
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
        </>
    );
};
