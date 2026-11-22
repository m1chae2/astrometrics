/**
 * @file StarShapeChart.tsx
 * @description The frame status timeline of a target: when each frame was taken (x) and what happened to
 * it (y). Each status that has frames is a lane, listed top to bottom as set aside (cloud, trailed, off
 * field, other), flagged, then included. Each frame is one small square in its lane at its time, so a
 * cloud passage shows as a run of marks in the cloud lane. Hovering a mark gives the file name, time,
 * status and the reason. The frame picked in the File Browser has a ring around it, and clicking a mark
 * picks that frame. The time axis is broken: each night gets its own section, spread over that night's
 * real capture times and labeled with its date and frame count, so no frame hides behind another. A
 * dotted line marks each break between nights (the days between them are not drawn). A one-line count
 * of the frames sits above the chart.
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
    /** The nights, in time order, each with its place on the broken axis. */
    nights: NightSegment[];
    /** Places on the broken axis of the breaks between nights (empty for one night). */
    nightBreaks: number[];
}

/** One night's section of the broken time axis. */
export interface NightSegment {
    /** The label under the section, for example "Oct 3<br>61 frames". */
    label: string;
    /** The number of frames in the night. */
    count: number;
    /** Where the section starts on the broken axis (0 to 1). */
    start: number;
    /** Where the section ends on the broken axis (0 to 1). */
    end: number;
}

/** The space between two nights on the broken axis, as a fraction of the whole axis. */
const BREAK_FRACTION = 0.02;

/** Each night gets at least this share of the drawn width, so a short night stays readable. */
const MIN_NIGHT_SHARE = 0.15;

/** One frame of the chart before it is sorted into a lane. */
interface ChartFrame {
    path: string;
    /** Milliseconds since 1970. */
    time: number;
    lane: Lane;
    statusLabel: string;
    reason: string;
    /** The place on the broken axis, 0 to 1. */
    x: number;
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
 * Splits time-sorted frames into nights and gives each frame its place on the broken axis. A night's
 * width follows how long it ran, but never less than a share of the axis, so short nights stay visible.
 * @param all The frames, sorted by time. Each frame's `x` is set here.
 * @return The nights, in order, with the part of the axis each one uses.
 */
function placeNights(all: ChartFrame[]): NightSegment[] {
    const groups: ChartFrame[][] = [];
    for (const frame of all) {
        const last = groups[groups.length - 1];
        if (last && frame.time - last[last.length - 1].time <= NIGHT_GAP_MS) last.push(frame);
        else groups.push([frame]);
    }
    if (groups.length === 0) return [];
    const spans = groups.map((group) => Math.max(group[group.length - 1].time - group[0].time, 1));
    const totalSpan = spans.reduce((sum, span) => sum + span, 0);
    const floor = Math.min(MIN_NIGHT_SHARE, 1 / groups.length);
    const raw = spans.map((span) => Math.max(span / totalSpan, floor));
    const rawTotal = raw.reduce((sum, share) => sum + share, 0);
    const room = 1 - BREAK_FRACTION * (groups.length - 1);
    let cursor = 0;
    return groups.map((group, index) => {
        const width = (raw[index] / rawTotal) * room;
        const start = cursor;
        cursor += width + BREAK_FRACTION;
        const first = group[0].time;
        for (const frame of group) {
            frame.x = group.length === 1 ? start + width / 2 : start + ((frame.time - first) / spans[index]) * width;
        }
        const date = new Date(first).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
        return { label: `${date}<br>${group.length} ${group.length === 1 ? 'frame' : 'frames'}`, count: group.length, start, end: start + width };
    });
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
        // The hover text keeps the reason for a set-aside or flagged frame; "in the last stack" notes are left out.
        const reason = kind === 'included' || kind === 'not-stacked' ? '' : (info?.tooltip ?? status?.reason ?? '');
        all.push({ path, time, lane, statusLabel: info?.label ?? lane, reason, x: 0 });
    }
    all.sort((first, second) => first.time - second.time);
    const nights = placeNights(all);
    const lanes = LANES.filter((lane) => all.some((frame) => frame.lane === lane));
    const counts: Partial<Record<Lane, number>> = {};
    for (const lane of lanes) counts[lane] = all.filter((frame) => frame.lane === lane).length;
    const yOf = (frame: ChartFrame): number => lanes.length - 1 - lanes.indexOf(frame.lane);
    const pointsOf = (group: ChartFrame[]) => ({
        x: group.map((frame) => frame.x),
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
    const nightBreaks = nights.slice(1).map((night, index) => (nights[index].end + night.start) / 2);
    return { traces, lanes, counts, drawnCount: all.length, nights, nightBreaks };
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
            xTitle: '', yTitle: '',
            margin: { l: 8, r: 8, t: 8, b: 28 },
        });
        const top = chart.lanes.length - 1;
        return {
            ...base,
            font: { ...base.font, size: fontSize },
            xaxis: {
                ...base.xaxis, type: 'linear', range: [-0.01, 1.01], fixedrange: true, zeroline: false, showgrid: false,
                tickfont: { size: fontSize }, tickmode: 'array',
                tickvals: chart.nights.map((night) => (night.start + night.end) / 2), ticktext: chart.nights.map((night) => night.label),
            },
            yaxis: {
                ...base.yaxis, tickfont: { size: fontSize }, range: [-0.6, top + 0.6], fixedrange: true, zeroline: false,
                tickmode: 'array', tickvals: chart.lanes.map((_, index) => top - index), ticktext: [...chart.lanes], automargin: true, ticklabelposition: 'outside',
            },
            autosize: true,
            showlegend: false,
            shapes: chart.nightBreaks.map((place) => ({
                type: 'line', xref: 'x', yref: 'paper', x0: place, x1: place, y0: 0, y1: 1,
                line: { color: getVar('--plot-reference-line', '#aaaaaa'), width: 1, dash: 'dot' },
            })),
        };
    }, [chart.lanes, chart.nights, chart.nightBreaks]);

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
