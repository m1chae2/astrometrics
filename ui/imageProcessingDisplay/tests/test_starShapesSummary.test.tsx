/**
 * @file test_starShapesSummary.test.tsx
 * @description Tests of the frame status timeline in Frame Analysis: lanes only for statuses that are
 * present, one mark per frame, the count line, night breaks, the status line when there are no frames,
 * and that clicking a mark picks that frame.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { StarShapesSummary, NOT_MEASURED_NOTE } from '../components/StarShapesSummary';
import { buildStarShapeChart, describeFrameCounts } from '../components/StarShapeChart';
import type { FrameStatus, FrameStatusReport, StarShapeReview } from '../../common/types/backendTypes';
import { describeFrameStatus } from '../utils/frameStatus';

// Plotly cannot draw in the test environment, so the base plot is a stand-in that shows what it was given.
const plotProps: { current: { data: any[]; layout: any; onClick?: (event: any) => void } | null } = { current: null };
vi.mock('../../common/components/plotting/BasePlot', () => ({
    BasePlot: (props: { data: any[]; layout: any; onClick?: (event: any) => void }) => {
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

describe('buildStarShapeChart', () => {
    const frames = report().frames!;
    const build = (selected: string | null = null) => buildStarShapeChart(review().series!, new Set(['/lib/c.fits']), selected, frames, statusesOf(frames));
    type Points = { x: number[]; y: number[]; customdata: string[]; text: string[] };

    it('makes lanes only for the statuses present, in order', () => {
        const chart = build();
        expect(chart.lanes).toEqual(['Set aside: cloud', 'Set aside: trailed', 'Flagged', 'Included']);
        expect(chart.traces.map((trace) => trace.name)).toEqual(chart.lanes);
    });

    it('draws one mark per frame in its lane at its time', () => {
        const chart = build();
        const flagged = chart.traces[2] as unknown as Points;
        expect(flagged.customdata).toEqual(['/lib/c.fits']);
        expect(flagged.x).toEqual([3000000]);
        expect(flagged.y).toEqual([1]);
        expect((chart.traces[3] as unknown as Points).customdata).toEqual(['/lib/a.fits', '/lib/b.fits']);
        expect(chart.drawnCount).toBe(5);
    });

    it('puts the file name, status and reason in the hover text', () => {
        const cloud = build().traces[0] as unknown as Points;
        expect(cloud.text[0]).toMatch(/d\.fits/);
        expect(cloud.text[0]).toMatch(/Set aside: cloud/);
        expect(cloud.text[0]).toMatch(/clouded reason/);
    });

    it('rings the picked frame', () => {
        const chart = build('/lib/d.fits');
        const ring = chart.traces[chart.traces.length - 1];
        expect(ring.name).toBe('Selected');
        expect((ring as unknown as Points).customdata).toEqual(['/lib/d.fits']);
    });

    it('separates nights with a break halfway across a long gap', () => {
        const nights = [
            { path: '/lib/a.fits', timestamp: 1000, elongation: 1 },
            { path: '/lib/b.fits', timestamp: 1000 + DAY, elongation: 1 },
        ];
        expect(buildStarShapeChart(nights, new Set(), null).nightBreaks).toEqual([((1000 + 1000 + DAY) / 2) * 1000]);
    });
});

describe('describeFrameCounts', () => {
    it('writes the count line', () => {
        expect(describeFrameCounts({ Included: 117, 'Set aside: trailed': 5, Flagged: 10 }))
            .toBe('132 frames: 117 included, 5 set aside (5 trailed), 10 flagged');
    });
});
