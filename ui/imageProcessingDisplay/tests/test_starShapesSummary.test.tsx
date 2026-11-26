/**
 * @file test_starShapesSummary.test.tsx
 * @description Tests of the frame status timeline in Frame Analysis: lanes only for statuses that are
 * present, one mark per frame, the count line, night breaks, the status line when there are no frames,
 * that clicking a mark picks that frame, and that the night labels under the axis are flat, short and
 * never overlap.
 */
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { StarShapesSummary, NOT_MEASURED_NOTE } from '../components/StarShapesSummary';
import { buildStarShapeChart, describeFrameCounts, zoomXRange, panXRange, pickNightLabels, estimateLabelWidth, FULL_X_RANGE } from '../components/StarShapeChart';
import type { NightSegment } from '../components/StarShapeChart';
import type { FrameStatus, FrameStatusReport, StarShapeReview } from '../../common/types/backendTypes';
import { describeFrameStatus } from '../utils/frameStatus';

// Plotly cannot draw in the test environment, so the base plot is a stand-in that shows what it was given.
const plotProps: { current: { data: any[]; layout: any; onClick?: (event: any) => void; onHover?: (event: any) => void; onUnhover?: () => void } | null } = { current: null };
vi.mock('../../common/components/plotting/BasePlot', () => ({
    BasePlot: (props: { data: any[]; layout: any; onClick?: (event: any) => void; onHover?: (event: any) => void; onUnhover?: () => void }) => {
        plotProps.current = props;
        return <div data-testid="base-plot" />;
    },
}));

const DAY = 86400;

const review = (): StarShapeReview => ({
    targetId: 'NGC 7331', scope: 'all', frameCount: 3,
    roundness: { state: 'elongated', sentence: 's' }, direction: { state: 'shared', sentence: 's' },
    standout: { state: 'found', sentence: 's' }, corners: { state: 'not_validated', sentence: 's' },
    series: [
        { path: '/lib/a.fits', timestamp: 1000, elongation: 1.1 },
        { path: '/lib/b.fits', timestamp: 2000, elongation: 1.2 },
        { path: '/lib/c.fits', timestamp: 3000, elongation: 1.9 },
    ],
    standoutFrames: [{ path: '/lib/c.fits', elongation: 1.9, spreadsAboveTypical: 7 }],
});

const frame = (name: string, state: string, kind: string | null, date: string): FrameStatus => ({
    path: `/lib/${name}`, name, state, kind, date, reason: kind ? `${kind} reason` : '',
});
const report = (): FrameStatusReport => ({
    stackedAt: 5000,
    frames: [
        frame('a.fits', 'stacked', null, ''), frame('b.fits', 'stacked', null, ''), frame('c.fits', 'stacked', null, ''),
        frame('d.fits', 'set_aside', 'clouded', '1970-01-01T00:00:05Z'), frame('e.fits', 'set_aside', 'trailed', '1970-01-01T00:00:06Z'),
    ],
});
const statusesOf = (frames: FrameStatus[]) => new Map(frames.map((item) => [
    item.path, describeFrameStatus(item, item.path === '/lib/c.fits' ? 1.9 : null, 1.2, 5000),
]));

describe('StarShapesSummary', () => {
    it('shows the count line and the chart', () => {
        const frames = report().frames!;
        render(<StarShapesSummary review={review()} frameReport={report()} frameStatuses={statusesOf(frames)} selectedPath={null} onSelectFrame={() => {}} />);
        expect(screen.getByTestId('star-shape-count').textContent).toBe('5 frames: 2 included, 2 set aside (1 cloud, 1 trailed), 1 flagged');
        expect(screen.getByTestId('star-shape-chart')).toBeTruthy();
    });

    it('says plainly when there are no frames, and draws no chart', () => {
        render(<StarShapesSummary review={{ ...review(), series: [] }} selectedPath={null} onSelectFrame={() => {}} />);
        expect(screen.getByTestId('star-shape-status').textContent).toBe(NOT_MEASURED_NOTE);
        expect(screen.queryByTestId('star-shape-chart')).toBeNull();
    });

    it('shows nothing without a review', () => {
        const { container } = render(<StarShapesSummary review={null} selectedPath={null} onSelectFrame={() => {}} />);
        expect(container.firstChild).toBeNull();
    });

    it('picks the frame whose mark is clicked, set-aside frames included', () => {
        const onSelectFrame = vi.fn();
        const frames = report().frames!;
        render(<StarShapesSummary review={review()} frameReport={report()} frameStatuses={statusesOf(frames)} selectedPath={null} onSelectFrame={onSelectFrame} />);
        plotProps.current!.onClick!({ points: [{ customdata: '/lib/d.fits' }] });
        expect(onSelectFrame).toHaveBeenCalledWith('/lib/d.fits');
    });
});

describe('hover label', () => {
    it('sits above the hovered bar, without an arrow, and goes away when the pointer leaves', () => {
        const frames = report().frames!;
        render(<StarShapesSummary review={review()} frameReport={report()} frameStatuses={statusesOf(frames)} selectedPath={null} onSelectFrame={() => {}} />);
        act(() => plotProps.current!.onHover!({ points: [{ text: 'a.fits<br>when', bbox: { x0: 100, x1: 140, y0: 50 } }] }));
        const tip = screen.getByTestId('star-shape-tip');
        expect(tip.textContent).toBe('a.fitswhen');
        expect(tip.style.left).toBe('120px');
        expect(tip.style.top).toBe('50px');
        expect(tip.querySelector('svg, path')).toBeNull();
        expect(plotProps.current!.data.every((trace: { hoverinfo: string }) => trace.hoverinfo === 'none')).toBe(true);
        act(() => plotProps.current!.onUnhover!());
        expect(screen.queryByTestId('star-shape-tip')).toBeNull();
    });
});

describe('buildStarShapeChart', () => {
    const frames = report().frames!;
    const build = (selected: string | null = null) => buildStarShapeChart(review().series!, new Set(['/lib/c.fits']), selected, frames, statusesOf(frames));
    type Points = { base: number[]; x: number[]; y: number[]; customdata: string[]; text: string[] };

    it('makes lanes only for the statuses present, in order', () => {
        const chart = build();
        expect(chart.lanes).toEqual(['Set aside: cloud', 'Set aside: trailed', 'Flagged', 'Included']);
        expect(chart.traces.map((trace) => trace.name)).toEqual(chart.lanes);
    });

    it('draws one mark per frame in its lane at its time', () => {
        const chart = build();
        const flagged = chart.traces[2] as unknown as Points;
        expect(flagged.customdata).toEqual(['/lib/c.fits']);
        expect(flagged.base[0]).toBeGreaterThanOrEqual(0);
        expect(flagged.base[0]).toBeLessThanOrEqual(1);
        expect(flagged.y).toEqual([1]);
        expect((chart.traces[3] as unknown as Points).customdata).toEqual(['/lib/a.fits', '/lib/b.fits']);
        expect(chart.drawnCount).toBe(5);
    });

    it('puts the file name, time and night, not the status or reason, in the hover text', () => {
        const cloud = build().traces[0] as unknown as Points;
        expect(cloud.text[0]).toMatch(/d\.fits/);
        const lines = cloud.text[0].split('<br>');
        expect(lines).toHaveLength(3);
        expect(lines[2]).toMatch(/^Night of .+ '\d\d: 5 frames$/);
        expect(cloud.text[0]).not.toMatch(/Set aside|reason/);
    });

    it('rings the picked frame from its own bar and row', () => {
        const chart = build('/lib/d.fits');
        expect(chart.traces.some((trace) => trace.name === 'Selected')).toBe(false);
        const lane = chart.traces.find((trace) => (trace as unknown as Points).customdata.includes('/lib/d.fits')) as unknown as Points;
        const index = lane.customdata.indexOf('/lib/d.fits');
        expect(chart.selection).toEqual({ x0: lane.base[index], x1: lane.base[index] + lane.x[index], y: lane.y[index] });
        expect(build('/lib/a.fits').selection).not.toEqual(chart.selection);
        expect(build(null).selection).toBeNull();
    });

    it('gives every frame its own place, one section per night, with a break between', () => {
        const night = (start: number, count: number) => Array.from({ length: count }, (_, index) => ({
            path: `/lib/${start}_${index}.fits`, timestamp: start + index * 60, elongation: 1,
        }));
        const series = [...night(1000, 10), ...night(1000 + 12 * DAY, 61)];
        const chart = buildStarShapeChart(series, new Set(), null);
        expect(chart.drawnCount).toBe(71);
        const included = chart.traces[0] as unknown as Points;
        expect(included.base.length).toBe(71);
        expect(new Set(included.base).size).toBe(71);
        expect(chart.nights.map((item) => item.count)).toEqual([10, 61]);
        expect(chart.nights[0].end).toBeLessThan(chart.nights[1].start);
        expect(chart.nightBreaks.length).toBe(1);
        expect(included.base.slice(0, 10).every((x) => x >= chart.nights[0].start && x <= chart.nights[0].end)).toBe(true);
        expect(included.base.slice(10).every((x) => x >= chart.nights[1].start && x <= chart.nights[1].end)).toBe(true);
    });

    it('draws a single night as one section with no break', () => {
        const chart = buildStarShapeChart(review().series!, new Set(), null);
        expect(chart.nights.length).toBe(1);
        expect(chart.nightBreaks).toEqual([]);
        expect(new Set((chart.traces[0] as unknown as Points).base).size).toBe(3);
    });

    it('keeps a one-frame night visible next to a long one', () => {
        const series = [
            { path: '/lib/a.fits', timestamp: 1000, elongation: 1 },
            { path: '/lib/b.fits', timestamp: 1000 + DAY, elongation: 1 },
            { path: '/lib/c.fits', timestamp: 1000 + DAY + 3600, elongation: 1 },
        ];
        const chart = buildStarShapeChart(series, new Set(), null);
        expect(chart.nights.length).toBe(2);
        expect(chart.nights[0].end - chart.nights[0].start).toBeGreaterThan(0.1);
    });
});

describe('describeFrameCounts', () => {
    it('writes the count line', () => {
        expect(describeFrameCounts({ Included: 117, 'Set aside: trailed': 5, Flagged: 10 }))
            .toBe('132 frames: 117 included, 5 set aside (5 trailed), 10 flagged');
    });
});

describe('frame status timeline zoom', () => {
    const rangeNow = () => plotProps.current!.layout.xaxis.range as [number, number];

    it('zooms in around the pointer, never past the full range, and resets on double-click', () => {
        render(<StarShapesSummary review={review()} frameReport={report()} frameStatuses={statusesOf(report().frames!)} selectedPath={null} onSelectFrame={() => {}} />);
        const chart = screen.getByTestId('star-shape-chart');
        // Zooming out at the full range is left to the page.
        expect(fireEvent.wheel(chart, { deltaY: 200 })).toBe(true);
        expect(rangeNow()).toEqual([FULL_X_RANGE[0], FULL_X_RANGE[1]]);
        // Zooming in is handled (the page does not scroll).
        expect(fireEvent.wheel(chart, { deltaY: -300 })).toBe(false);
        const zoomed = rangeNow();
        expect(zoomed[1] - zoomed[0]).toBeLessThan(1.02);
        fireEvent.wheel(chart, { deltaY: 5000 });
        expect(rangeNow()[1] - rangeNow()[0]).toBeCloseTo(FULL_X_RANGE[1] - FULL_X_RANGE[0]);
        fireEvent.wheel(chart, { deltaY: -300 });
        fireEvent.doubleClick(chart);
        expect(rangeNow()).toEqual([FULL_X_RANGE[0], FULL_X_RANGE[1]]);
    });

    it('keeps the zoomed range inside the full range and keeps the point under the pointer', () => {
        const [start, end] = zoomXRange([-0.01, 1.01], 1, -500);
        expect(end).toBeCloseTo(1.01);
        expect(start).toBeGreaterThan(-0.01);
        expect(zoomXRange([0.4, 0.6], 0.5, -100)[0]).toBeGreaterThan(0.4);
    });
});

describe('frame status timeline hover and click', () => {
    it('picks the nearest mark in x and y, so a set-aside mark is its own frame', () => {
        const onSelectFrame = vi.fn();
        render(<StarShapesSummary review={review()} frameReport={report()} frameStatuses={statusesOf(report().frames!)} selectedPath={null} onSelectFrame={onSelectFrame} />);
        const layout = plotProps.current!.layout;
        expect(layout.hovermode).toBe('closest');
        expect(layout.hoverdistance).toBeLessThanOrEqual(10);
        const traces = plotProps.current!.data as unknown as Array<{ name: string; x: number[]; y: number[]; customdata: string[] }>;
        const cloud = traces.find((trace) => trace.name === 'Set aside: cloud')!;
        const included = traces.find((trace) => trace.name === 'Included')!;
        expect(cloud.y[0]).not.toBe(included.y[0]);
        plotProps.current!.onClick!({ points: [{ customdata: cloud.customdata[0] }] });
        expect(onSelectFrame).toHaveBeenCalledWith('/lib/d.fits');
    });
});

describe('frame status timeline outline', () => {
    const outline = () => (plotProps.current!.layout.shapes as Array<{ type: string; x0: number; x1: number; y0: number; y1: number }>).filter((shape) => shape.type === 'rect');

    it('stays on the bar through the click, the viewer load, a re-read of the statuses, hover and unhover', () => {
        const props = () => ({ review: review(), frameReport: report(), frameStatuses: statusesOf(report().frames!), onSelectFrame: () => {} });
        const view = render(<StarShapesSummary {...props()} selectedPath={null} />);
        expect(outline()).toHaveLength(0);
        // The click sets the selection; the viewer then loads the file and the screen re-reads everything.
        view.rerender(<StarShapesSummary {...props()} selectedPath="/lib/d.fits" />);
        const first = outline()[0];
        expect(first).toBeDefined();
        view.rerender(<StarShapesSummary {...props()} selectedPath="/lib/d.fits" />);
        act(() => { plotProps.current!.onHover!({ points: [{ text: 'd', bbox: { x0: 0, x1: 4, y0: 0 } }] }); });
        act(() => { plotProps.current!.onUnhover!(); });
        expect(outline()).toEqual([first]);
        // The same frame written with backslashes or inside _excluded still gets the outline; another frame moves it.
        view.rerender(<StarShapesSummary {...props()} selectedPath={"\\lib\\d.fits"} />);
        expect(outline()).toEqual([first]);
        view.rerender(<StarShapesSummary {...props()} selectedPath="/lib/a.fits" />);
        expect(outline()[0]).not.toEqual(first);
    });
});

describe('frame status timeline pan', () => {
    it('clamps a pan to the full range', () => {
        expect(panXRange([0.2, 0.4], -5)[0]).toBeCloseTo(FULL_X_RANGE[0]);
        const right = panXRange([0.2, 0.4], 5);
        expect(right[1]).toBeCloseTo(FULL_X_RANGE[1]);
        expect(right[1] - right[0]).toBeCloseTo(0.2);
    });

    it('stops zooming at the minimum span', () => {
        const [start, end] = zoomXRange([0.4, 0.6], 0.5, -100000, 0.1);
        expect(end - start).toBeCloseTo(0.1);
    });

    it('drags to pan, ignores the click that ends a drag, and still picks on a plain click', () => {
        const onSelectFrame = vi.fn();
        render(<StarShapesSummary review={review()} frameReport={report()} frameStatuses={statusesOf(report().frames!)} selectedPath={null} onSelectFrame={onSelectFrame} />);
        const chart = screen.getByTestId('star-shape-chart');
        const rangeNow = () => plotProps.current!.layout.xaxis.range as [number, number];
        fireEvent.wheel(chart, { deltaY: -400 });
        const zoomed = rangeNow();
        const click = () => plotProps.current!.onClick!({ points: [{ customdata: '/lib/d.fits' }] });
        // A press with no movement is a click.
        fireEvent.mouseDown(chart, { clientX: 100, button: 0 });
        fireEvent.mouseUp(window, { clientX: 100 });
        click();
        expect(onSelectFrame).toHaveBeenCalledTimes(1);
        // A drag moves the range (width is 0 in jsdom, so it only has to be flagged as a drag) and picks nothing.
        fireEvent.mouseDown(chart, { clientX: 100, button: 0 });
        fireEvent.mouseMove(window, { clientX: 140 });
        fireEvent.mouseUp(window, { clientX: 140 });
        click();
        expect(onSelectFrame).toHaveBeenCalledTimes(1);
        expect(rangeNow()[1] - rangeNow()[0]).toBeCloseTo(zoomed[1] - zoomed[0]);
    });
});

describe('frame status timeline bars', () => {
    it('draws no text on the plot; the file name and time are only in the hover tooltip', () => {
        const chart = buildStarShapeChart(review().series ?? [], new Set(), '/lib/a.fits', report().frames, statusesOf(report().frames!));
        expect(chart.traces.length).toBeGreaterThan(0);
        for (const trace of chart.traces) {
            expect((trace as { textposition?: string }).textposition).toBe('none');
            expect((trace as { hoverinfo?: string }).hoverinfo).toBe('none');
        }
    });

    it('draws back-to-back frames as bars that meet, with a minimum width for short ones', () => {
        const start = 1_000_000;
        const series = [
            { path: '/lib/a.fits', timestamp: start, elongation: 1 },
            { path: '/lib/b.fits', timestamp: start + 60, elongation: 1 },
            { path: '/lib/c.fits', timestamp: start + 600, elongation: 1 },
        ];
        const exposures = new Map([['/lib/a.fits', 60], ['/lib/b.fits', 60], ['/lib/c.fits', 60]]);
        const bars = buildStarShapeChart(series, new Set(), null, null, new Map(), { exposureSeconds: exposures })
            .traces[0] as unknown as { base: number[]; x: number[] };
        expect(bars.base[0] + bars.x[0]).toBeCloseTo(bars.base[1], 9);
        expect(bars.base[1] + bars.x[1]).toBeLessThan(bars.base[2]);
        const thin = buildStarShapeChart(series, new Set(), null, null, new Map(), { minWidth: 0.05 })
            .traces[0] as unknown as { x: number[] };
        expect(thin.x.every((width) => width >= 0.05)).toBe(true);
    });
});

describe('night labels under the time axis', () => {
    const FONT = 12;
    /** Makes evenly spaced nights, one a week from 2023-11-03, with labels like the chart's. */
    const nightsOf = (count: number): NightSegment[] => {
        const width = 1 / count;
        return Array.from({ length: count }, (_, index) => {
            const date = new Date(2023, 10, 3 + index * 7);
            return {
                label: date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }), year: date.getFullYear(),
                count: 10, start: index * width, end: (index + 0.8) * width,
            };
        });
    };
    /** Checks that no two labels shown touch, at a given drawing width and range. */
    const noOverlap = (ticks: { tickvals: number[]; ticktext: string[] }, range: [number, number], widthPx: number) => {
        const pixel = (place: number) => ((place - range[0]) / (range[1] - range[0])) * widthPx;
        for (let index = 1; index < ticks.tickvals.length; index++) {
            const gap = pixel(ticks.tickvals[index]) - pixel(ticks.tickvals[index - 1]);
            const room = (estimateLabelWidth(ticks.ticktext[index], FONT) + estimateLabelWidth(ticks.ticktext[index - 1], FONT)) / 2;
            expect(gap).toBeGreaterThanOrEqual(room);
        }
    };

    it('thins many nights in a narrow plot so the labels do not overlap, keeping the first and last', () => {
        const nights = nightsOf(20);
        const range: [number, number] = [FULL_X_RANGE[0], FULL_X_RANGE[1]];
        const ticks = pickNightLabels(nights, range, 280, FONT);
        expect(ticks.tickvals.length).toBeGreaterThan(1);
        expect(ticks.tickvals.length).toBeLessThan(20);
        noOverlap(ticks, range, 280);
        expect(ticks.tickvals[0]).toBeCloseTo((nights[0].start + nights[0].end) / 2);
        expect(ticks.tickvals[ticks.tickvals.length - 1]).toBeCloseTo((nights[19].start + nights[19].end) / 2);
        expect(ticks.ticktext.every((text) => !text.includes('<br>') && !/frame/.test(text))).toBe(true);
    });

    it('shows more labels when zoomed in, and only the nights in view', () => {
        const nights = nightsOf(20);
        const full = pickNightLabels(nights, [FULL_X_RANGE[0], FULL_X_RANGE[1]], 280, FONT);
        const zoomed = pickNightLabels(nights, [0, 0.5], 280, FONT);
        const density = (ticks: { tickvals: number[] }, span: number) => ticks.tickvals.length / span;
        expect(density(zoomed, 0.5)).toBeGreaterThan(density(full, 1.02));
        expect(zoomed.tickvals.every((place) => place >= 0 && place <= 0.5)).toBe(true);
        noOverlap(zoomed, [0, 0.5], 280);
    });

    it('adds the year on the first label and where the year changes', () => {
        const nights = nightsOf(14);
        const ticks = pickNightLabels(nights, [FULL_X_RANGE[0], FULL_X_RANGE[1]], 4000, FONT);
        expect(ticks.ticktext).toHaveLength(14);
        expect(ticks.ticktext[0]).toMatch(/ '23$/);
        expect(ticks.ticktext[1]).not.toMatch(/'/);
        // The night after Dec 29, 2023 is Jan 5, 2024.
        const firstOf2024 = nights.findIndex((night) => night.year === 2024);
        expect(ticks.ticktext[firstOf2024]).toMatch(/ '24$/);
        expect(ticks.ticktext[firstOf2024 + 1]).not.toMatch(/'/);
    });

    it('draws the labels flat in the chart', () => {
        const frames = report().frames!;
        render(<StarShapesSummary review={review()} frameReport={report()} frameStatuses={statusesOf(frames)} selectedPath={null} onSelectFrame={() => {}} />);
        const xaxis = plotProps.current!.layout.xaxis;
        expect(xaxis.tickangle).toBe(0);
        expect(xaxis.ticktext.length).toBe(xaxis.tickvals.length);
    });
});
