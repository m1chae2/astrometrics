/**
 * @file StarShapeChart.tsx
 * @description The frame status timeline of a target: when each frame was taken (x) and what happened to
 * it (y). Each status that has frames is a lane, listed top to bottom as set aside (cloud, trailed, off
 * field, other), flagged, then included. Each frame is one small square in its lane at its time, so a
 * cloud passage shows as a run of marks in the cloud lane. Hovering a mark gives the file name and time. The frame picked in the File Browser has a ring around it, and clicking a mark
 * picks that frame. The time axis is broken: each night gets its own section, spread over that night's
 * real capture times, so no frame hides behind another. Each night is labeled with its date, written flat;
 * the year is added on the first label and wherever it changes. Only the labels that fit side by side at the
 * plot's width are shown, so zooming in shows more of them. A frame's hover label names its night and that
 * night's frame count. A dotted line marks each break between nights (the days between them are not drawn).
 * A one-line count
 * of the frames sits above the chart. The mouse wheel (or a two-finger scroll) over the plot zooms the
 * time axis around the pointer, all nights together, never wider than the full range; a double-click
 * returns to the full range. When zoomed in, dragging the empty plot pans along x, kept inside the
 * full range; a drag never picks a frame, a plain click does.
 */
import { readThemeToken, readThemeTokenNumber } from '../../common/utils/themeToken';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { BasePlot } from '../../common/components/plotting/BasePlot';
import { buildLayout } from '../../common/utils/plotTools';
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
    /** The Plotly traces: one per lane that has frames. */
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
    /** The picked frame's own box on the axes (start, end, row), or `null` when none is picked. */
    selection: { x0: number; x1: number; y: number } | null;
}

/** One night's section of the broken time axis. */
export interface NightSegment {
    /** The night's date without the year, for example "Oct 3". */
    label: string;
    /** The night's year, for example 2025. */
    year: number;
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

/** The full x range of the chart, a little wider than 0 to 1 so the end marks are not clipped. */
export const FULL_X_RANGE: readonly [number, number] = [-0.01, 1.01];

/** The narrowest x range zoom can reach. */
const MIN_X_SPAN = 0.01;

/**
 * Zooms an x range around a point. The range never grows past the full range.
 * @param range The current range.
 * @param pointerFraction Where the pointer is across the plot, 0 (left) to 1 (right).
 * @param deltaY The wheel's vertical movement; negative zooms in, positive zooms out.
 * @param minSpan The narrowest range allowed.
 * @return The new range.
 */
export function zoomXRange(
    range: readonly [number, number], pointerFraction: number, deltaY: number, minSpan: number = MIN_X_SPAN,
): [number, number] {
    const fullSpan = FULL_X_RANGE[1] - FULL_X_RANGE[0];
    const span = range[1] - range[0];
    const newSpan = Math.min(fullSpan, Math.max(Math.min(minSpan, fullSpan), span * Math.exp(deltaY * 0.002)));
    const anchor = range[0] + span * pointerFraction;
    let start = anchor - newSpan * pointerFraction;
    start = Math.min(Math.max(start, FULL_X_RANGE[0]), FULL_X_RANGE[1] - newSpan);
    return [start, start + newSpan];
}

/**
 * Slides an x range sideways, keeping it inside the full range.
 * @param range The current range.
 * @param shift How far to move it, in axis units (positive moves right).
 * @return The new range, with the same width.
 */
export function panXRange(range: readonly [number, number], shift: number): [number, number] {
    const span = range[1] - range[0];
    const start = Math.min(Math.max(range[0] + shift, FULL_X_RANGE[0]), FULL_X_RANGE[1] - span);
    return [start, start + span];
}

/** How far the pointer must move, in pixels, before a press counts as a drag and not a click. */
const DRAG_THRESHOLD_PX = 4;

/** One frame of the chart before it is sorted into a lane. */
interface ChartFrame {
    path: string;
    /** Milliseconds since 1970. */
    time: number;
    lane: Lane;
    /** How long the exposure ran, in milliseconds (0 when unknown). */
    durationMs: number;
    /** The place on the broken axis where the exposure starts, 0 to 1. */
    x: number;
    /** The place on the broken axis where the exposure ends. */
    xEnd: number;
    /** Which night the frame belongs to (its place in the list of nights). */
    nightIndex: number;
}

/** What the chart can draw beyond the frames' statuses. */
export interface StarShapeChartOptions {
    /** The exposure of each frame in seconds, by path. A frame with none is drawn as thin as the minimum width. */
    exposureS?: ReadonlyMap<string, number>;
    /** The narrowest a bar is drawn, in axis units, so short frames stay visible when zoomed out. */
    minWidth?: number;
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
    const spans = groups.map((group) => Math.max(Math.max(...group.map((frame) => frame.time + frame.durationMs)) - group[0].time, 1));
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
            frame.nightIndex = index;
            frame.x = group.length === 1 ? start + width / 2 : start + ((frame.time - first) / spans[index]) * width;
            frame.xEnd = group.length === 1 ? frame.x : start + ((frame.time + frame.durationMs - first) / spans[index]) * width;
        }
        const date = new Date(first);
        const label = date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
        return { label, year: date.getFullYear(), count: group.length, start, end: start + width };
    });
}

/**
 * Writes a night's date with its two-digit year, for example "Nov 3 '23".
 * @param night The night.
 * @return The date and year.
 */
export function nightDateWithYear(night: NightSegment): string {
    return `${night.label} '${String(night.year % 100).padStart(2, '0')}`;
}

/** The labels to show under the time axis: where each one goes and what it says. */
export interface NightTicks {
    /** The places on the broken axis of the labels shown. */
    tickvals: number[];
    /** The text of each label shown. */
    ticktext: string[];
}

/** The space kept between two labels, in pixels. */
const LABEL_GAP_PX = 12;

/**
 * Guesses how wide a label is drawn, in pixels. Most letters and digits are a bit over half the font size wide.
 * @param text The label.
 * @param fontSize The font size in pixels.
 * @return The guessed width in pixels.
 */
export function estimateLabelWidth(text: string, fontSize: number): number {
    return text.length * fontSize * 0.6;
}

/**
 * Picks the night labels that fit side by side. It walks the nights in view from left to right and keeps a
 * label only when it does not touch the last kept one. The first and the last night in view are kept when
 * they can be. The year is added to the first label shown and to any label whose year differs from the one
 * before it.
 * @param nights The nights, in time order.
 * @param xRange The part of the axis in view.
 * @param plotWidthPx The width of the drawing area, in pixels.
 * @param fontSize The label font size, in pixels.
 * @return The places and texts of the labels to show.
 */
export function pickNightLabels(
    nights: readonly NightSegment[], xRange: readonly [number, number], plotWidthPx: number, fontSize: number,
): NightTicks {
    const span = xRange[1] - xRange[0];
    if (span <= 0 || plotWidthPx <= 0) return { tickvals: [], ticktext: [] };
    const pixelOf = (place: number): number => ((place - xRange[0]) / span) * plotWidthPx;
    const inView = nights
        .map((night) => ({ night, place: (night.start + night.end) / 2 }))
        .filter((item) => item.place >= xRange[0] && item.place <= xRange[1]);
    type Kept = { night: NightSegment; place: number; text: string; left: number; right: number };
    /** Makes a label at a night, written with the year when it differs from the label before it. */
    const labelAt = (item: { night: NightSegment; place: number }, before: Kept | undefined): Kept => {
        const text = before && before.night.year === item.night.year ? item.night.label : nightDateWithYear(item.night);
        const half = (estimateLabelWidth(text, fontSize) + LABEL_GAP_PX) / 2;
        const center = pixelOf(item.place);
        return { night: item.night, place: item.place, text, left: center - half, right: center + half };
    };
    const kept: Kept[] = [];
    for (const item of inView) {
        const candidate = labelAt(item, kept[kept.length - 1]);
        if (kept.length === 0 || candidate.left >= kept[kept.length - 1].right) kept.push(candidate);
    }
    // Keep the last night in view too: it takes the place of the label before it when the two would touch.
    const lastItem = inView[inView.length - 1];
    if (lastItem && kept.length > 0 && kept[kept.length - 1].night !== lastItem.night) {
        if (kept.length > 1 && labelAt(lastItem, kept[kept.length - 2]).left < kept[kept.length - 1].right) {
            kept.pop();
        }
        const last = labelAt(lastItem, kept[kept.length - 1]);
        if (last.left >= kept[kept.length - 1].right) kept.push(last);
    }
    return { tickvals: kept.map((item) => item.place), ticktext: kept.map((item) => item.text) };
}

/**
 * Reduces a frame path to what names the frame: forward slashes and no `_excluded` folder.
 * @param path A frame's path as the chart, the viewer or the File Browser wrote it.
 * @return The path to compare with another one.
 */
export function pathIdentity(path: string): string {
    return path.replace(/\\/g, '/').replace(/\/_excluded(?=\/)/g, '');
}

/**
 * Builds the timeline's traces from the frames' statuses and times.
 * @param series The review's per-frame numbers, whose times are used first.
 * @param flaggedPaths The paths of the frames the review flags.
 * @param selectedPath The frame picked in the File Browser, or `null`.
 * @param frames The backend's per-frame statuses (including set-aside frames), or `null`.
 * @param statuses The File Browser's status of each frame by path.
 * @param options The exposures and the narrowest bar.
 * @return The traces, the lanes, the counts and the night breaks.
 */
export function buildStarShapeChart(
    series: FrameStarShape[],
    flaggedPaths: ReadonlySet<string>,
    selectedPath: string | null,
    frames: readonly FrameStatus[] | null = null,
    statuses: ReadonlyMap<string, FrameStatusInfo> = new Map(),
    options: StarShapeChartOptions = {},
): StarShapeChartData {
    const known = [...(options.exposureS?.values() ?? [])].filter((value) => value > 0).sort((a, b) => a - b);
    const typicalSeconds = known.length > 0 ? known[Math.floor(known.length / 2)] : 0;
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
        const seconds = options.exposureS?.get(path) ?? typicalSeconds;
        all.push({ path, time, lane, durationMs: seconds * 1000, x: 0, xEnd: 0, nightIndex: 0 });
    }
    all.sort((first, second) => first.time - second.time);
    const nights = placeNights(all);
    const lanes = LANES.filter((lane) => all.some((frame) => frame.lane === lane));
    const counts: Partial<Record<Lane, number>> = {};
    for (const lane of lanes) counts[lane] = all.filter((frame) => frame.lane === lane).length;
    const yOf = (frame: ChartFrame): number => lanes.length - 1 - lanes.indexOf(frame.lane);
    const minWidth = options.minWidth ?? 0;
    const pointsOf = (group: ChartFrame[]) => ({
        // Each frame is a horizontal bar from its start to its end, so frames taken back to back touch.
        base: group.map((frame) => frame.x),
        x: group.map((frame) => Math.max(frame.xEnd - frame.x, minWidth)),
        y: group.map(yOf),
        customdata: group.map((frame) => frame.path),
        text: group.map((frame) => {
            const night = nights[frame.nightIndex];
            const nightLine = `Night of ${nightDateWithYear(night)}: ${night.count} ${night.count === 1 ? 'frame' : 'frames'}`;
            return `${frame.path.split('/').pop()}<br>${new Date(frame.time).toLocaleString()}<br>${nightLine}`;
        }),
    });
    const colorOf = (lane: Lane): string => {
        if (lane === 'Flagged') return readThemeToken('--plot-orange', '#e95420');
        if (lane === 'Included') return readThemeToken('--plot-green', '#26a269');
        return readThemeToken('--plot-red', '#e01b24');
    };
    const traces: PlotlyTrace[] = lanes.map((lane) => ({
        ...pointsOf(all.filter((frame) => frame.lane === lane)), type: 'bar', orientation: 'h', width: 0.7, textposition: 'none', name: lane,
        hoverinfo: 'none', showlegend: false, // A thin outline in the panel color separates frames that touch.
        marker: { color: colorOf(lane), line: { width: 1, color: readThemeToken('--background-secondary', '#28282c') } },
    }));
    // The picked frame's outline is a layout shape, drawn from the frame's own start, end and row on every render,
    // so hovering, zooming, panning or a change of the traces cannot lose it.
    // The match is exact first, then on the path's identity (separators and the `_excluded` folder ignored), so a
    // path written another way by the viewer or the File Browser still finds the frame.
    const selectedKey = selectedPath === null ? null : pathIdentity(selectedPath);
    const picked = selectedPath === null ? undefined
        : all.find((frame) => frame.path === selectedPath) ?? all.find((frame) => pathIdentity(frame.path) === selectedKey);
    const selection = picked
        ? { x0: picked.x, x1: picked.x + Math.max(picked.xEnd - picked.x, minWidth), y: yOf(picked) }
        : null;
    const nightBreaks = nights.slice(1).map((night, index) => (nights[index].end + night.start) / 2);
    return { traces, lanes, counts, drawnCount: all.length, nights, nightBreaks, selection };
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
    /** The exposure of each frame in seconds, by path, or `undefined` when not known. */
    exposureS?: ReadonlyMap<string, number>;
    /** Called with a frame's path when its mark is clicked. */
    onSelectFrame: (path: string) => void;
}

/**
 * Draws the frame status timeline.
 * @param props See {@link StarShapeChartProps}.
 * @return The chart, or `null` when no frame has a time.
 */
export const StarShapeChart: React.FC<StarShapeChartProps> = ({ series, flaggedPaths, selectedPath, frames = null, statuses, exposureS, onSelectFrame }) => {
    const [xRange, setXRange] = useState<[number, number]>([FULL_X_RANGE[0], FULL_X_RANGE[1]]);
    // A bar is never drawn narrower than three pixels, whatever the zoom.
    const [plotWidth, setPlotWidth] = useState(600);
    const minWidth = (3 / Math.max(plotWidth, 1)) * (xRange[1] - xRange[0]);
    const chart = useMemo(
        () => buildStarShapeChart(series, flaggedPaths, selectedPath, frames, statuses, { exposureS, minWidth }),
        [series, flaggedPaths, selectedPath, frames, statuses, exposureS, minWidth],
    );
    const wheelRef = useRef<HTMLDivElement>(null);
    const hasChart = chart.drawnCount > 0;
    const xRangeRef = useRef(xRange);
    useEffect(() => { xRangeRef.current = xRange; }, [xRange]);
    // Zoom stops at about five frames' width, so the user cannot zoom into empty space.
    const minSpanRef = useRef(MIN_X_SPAN);
    const minSpan = Math.max(MIN_X_SPAN, 5 / Math.max(1, chart.drawnCount));
    useEffect(() => { minSpanRef.current = minSpan; }, [minSpan]);
    const [dragging, setDragging] = useState(false);
    // The label of the mark under the pointer and where its top edge is, in the chart's own pixels.
    const [tip, setTip] = useState<{ text: string; left: number; top: number } | null>(null);
    // True from the first drag movement until the next press, so the click that ends a drag picks nothing.
    const draggedRef = useRef(false);
    const isZoomed = xRange[1] - xRange[0] < FULL_X_RANGE[1] - FULL_X_RANGE[0] - 1e-9;

    /** Starts a pan when the plot is zoomed in; moves are followed on the window until the button is released. */
    const startPan = (event: React.MouseEvent<HTMLDivElement>) => {
        draggedRef.current = false;
        if (event.button !== 0 || !isZoomed) return;
        const startX = event.clientX;
        const startRange = xRangeRef.current;
        const width = event.currentTarget.getBoundingClientRect().width;
        const onMove = (move: MouseEvent) => {
            const moved = move.clientX - startX;
            if (!draggedRef.current && Math.abs(moved) <= DRAG_THRESHOLD_PX) return;
            draggedRef.current = true;
            setDragging(true);
            if (width <= 0) return;
            const span = startRange[1] - startRange[0];
            setXRange(panXRange(startRange, -(moved / width) * span));
        };
        const onUp = () => {
            window.removeEventListener('mousemove', onMove);
            window.removeEventListener('mouseup', onUp);
            setDragging(false);
        };
        window.addEventListener('mousemove', onMove);
        window.addEventListener('mouseup', onUp);
    };

    // A native listener, because React's wheel handler is passive and could not stop the page from scrolling.
    useEffect(() => {
        const node = wheelRef.current;
        if (!node) return undefined;
        if (node.getBoundingClientRect().width > 0) setPlotWidth(node.getBoundingClientRect().width);
        const onWheel = (event: WheelEvent) => {
            const current = xRangeRef.current;
            const atFull = current[1] - current[0] >= FULL_X_RANGE[1] - FULL_X_RANGE[0] - 1e-9;
            // Zooming out from the full range does nothing, so the page keeps its scroll.
            if (atFull && event.deltaY >= 0) return;
            const box = node.getBoundingClientRect();
            const fraction = box.width > 0 ? Math.min(1, Math.max(0, (event.clientX - box.left) / box.width)) : 0.5;
            event.preventDefault();
            setXRange(zoomXRange(current, fraction, event.deltaY, minSpanRef.current));
        };
        node.addEventListener('wheel', onWheel, { passive: false });
        // Follow the panel's width, so the axis labels are thinned again when the panel is resized.
        const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => {
            const width = node.getBoundingClientRect().width;
            if (width > 0) setPlotWidth(width);
        });
        observer?.observe(node);
        return () => {
            node.removeEventListener('wheel', onWheel);
            observer?.disconnect();
        };
    }, [hasChart]);

    const layout = useMemo(() => {
        const fontSize = readThemeTokenNumber('--compact-font-size', 13) - 1;
        const base = buildLayout({
            xTitle: '', yTitle: '',
            margin: { l: 8, r: 8, t: 8, b: 28 },
        });
        const top = chart.lanes.length - 1;
        // The drawing area is the panel less its margins and the lane names on the left.
        const laneNameWidth = Math.max(0, ...chart.lanes.map((lane) => estimateLabelWidth(lane, fontSize))) + 8;
        const ticks = pickNightLabels(chart.nights, xRange, plotWidth - 16 - laneNameWidth, fontSize);
        return {
            ...base,
            font: { ...base.font, size: fontSize },
            xaxis: {
                ...base.xaxis, type: 'linear', range: xRange, fixedrange: true, zeroline: false, showgrid: false,
                tickfont: { size: fontSize }, tickmode: 'array', tickangle: 0, ticktext: ticks.ticktext, tickvals: ticks.tickvals,
            },
            yaxis: {
                ...base.yaxis, tickfont: { size: fontSize }, range: [-0.6, top + 0.6], fixedrange: true, zeroline: false,
                tickmode: 'array', tickvals: chart.lanes.map((_, index) => top - index), ticktext: [...chart.lanes], automargin: true, ticklabelposition: 'outside',
            },
            autosize: true,
            showlegend: false,
            // Pick the mark nearest in both x and y, and only when the pointer is on it, so every lane's marks can be reached.
            hovermode: 'closest',
            barmode: 'overlay',
            hoverdistance: 6,
            shapes: [
                ...chart.nightBreaks.map((place) => ({
                    type: 'line', xref: 'x', yref: 'paper', x0: place, x1: place, y0: 0, y1: 1,
                    line: { color: readThemeToken('--plot-reference-line', '#aaaaaa'), width: 1, dash: 'dot' },
                })),
                ...(chart.selection ? [{
                    type: 'rect', xref: 'x', yref: 'y', layer: 'above',
                    x0: chart.selection.x0, x1: chart.selection.x1, y0: chart.selection.y - 0.45, y1: chart.selection.y + 0.45,
                    fillcolor: 'rgba(0,0,0,0)', line: { width: 2, color: readThemeToken('--plot-selection', '#e01b24') },
                }] : []),
            ],
        };
    }, [chart.lanes, chart.nights, chart.nightBreaks, chart.selection, xRange, plotWidth]);

    if (chart.drawnCount === 0) return null;
    return (
        <>
        <p className="star-shape-chart__count" data-testid="star-shape-count">{describeFrameCounts(chart.counts)}</p>
        <div
            className="star-shape-chart"
            data-testid="star-shape-chart"
            ref={wheelRef}
            style={isZoomed ? { cursor: dragging ? 'grabbing' : 'grab' } : undefined}
            onMouseDown={startPan}
            onDoubleClick={() => setXRange([FULL_X_RANGE[0], FULL_X_RANGE[1]])}
        >
            <BasePlot
                data={chart.traces}
                layout={layout}
                config={{ responsive: true, displayModeBar: false }}
                onClick={(event: { points?: Array<{ customdata?: unknown }> }) => {
                    const path = event?.points?.[0]?.customdata;
                    if (draggedRef.current) return;
                    if (typeof path === 'string') onSelectFrame(path);
                }}
                onHover={(event: { points?: Array<{ text?: unknown; bbox?: { x0: number; x1: number; y0: number } }> }) => {
                    const point = event?.points?.[0];
                    const box = wheelRef.current?.getBoundingClientRect();
                    if (!point?.bbox || !box || typeof point.text !== 'string') return;
                    setTip({ text: point.text, left: (point.bbox.x0 + point.bbox.x1) / 2 - box.left, top: point.bbox.y0 - box.top });
                }}
                onUnhover={() => setTip(null)}
                style={{ width: '100%', height: '100%' }}
            />
            {tip && (
                <div className="star-shape-chart__tip" data-testid="star-shape-tip" style={{ left: tip.left, top: tip.top }}>
                    {tip.text.split('<br>').map((line) => <div key={line}>{line}</div>)}
                </div>
            )}
        </div>
        </>
    );
};
