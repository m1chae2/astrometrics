/**
 * @file test_starShapesSummary.test.tsx
 * @description Tests of the Star shapes block in Frame Analysis: the one-line verdict, the corner note
 * marked "not yet validated", the chart's points (flagged frames drawn as a different shape, a dashed
 * line at the session median, the picked frame ringed) and that clicking a point picks that frame.
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { StarShapesSummary, describeCorners, describeStarShapeVerdict, NOT_MEASURED_NOTE } from '../components/StarShapesSummary';
import { buildStarShapeChart } from '../components/StarShapeChart';
import type { StarShapeReview } from '../../common/types/backendTypes';

// Plotly cannot draw in the test environment, so the base plot is a stand-in that shows what it was given.
const plotProps: { current: { data: any[]; layout: any; onClick?: (event: any) => void } | null } = { current: null };
vi.mock('../../common/components/plotting/BasePlot', () => ({
    BasePlot: (props: { data: any[]; layout: any; onClick?: (event: any) => void }) => {
        plotProps.current = props;
        return <div data-testid="base-plot" />;
    },
}));

const review = (overrides: Partial<StarShapeReview> = {}): StarShapeReview => ({
    targetId: 'NGC 7331', scope: 'all', frameCount: 3,
    roundness: { state: 'elongated', sentence: 'Stars are stretched more than usual for your equipment (stretch 1.30).' },
    direction: { state: 'shared', sentence: 'The stars are stretched the same way (about 40 degrees).' },
    standout: { state: 'found', sentence: '1 frame has stars stretched far more than the others.' },
    corners: { state: 'not_validated', sentence: 'Corner stars are 1.08 times as wide as center stars. This is not yet validated.', numbers: { corner_over_center: 1.08 } },
    series: [
        { path: '/lib/a.fits', timestamp: 1000, elongation: 1.1 },
        { path: '/lib/b.fits', timestamp: 2000, elongation: 1.2 },
        { path: '/lib/c.fits', timestamp: 3000, elongation: 1.9 },
        { path: '/lib/d.fits', timestamp: null, elongation: 1.2 },
    ],
    standoutFrames: [{ path: '/lib/c.fits', elongation: 1.9, spreadsAboveTypical: 7 }],
    ...overrides,
});

describe('verdict line', () => {
    it('joins the roundness and direction sentences for stretched stars', () => {
        expect(describeStarShapeVerdict(review())).toBe(
            'Stars are stretched more than usual for your equipment (stretch 1.30). The stars are stretched the same way (about 40 degrees).',
        );
    });

    it('leaves the direction out when the stars are round', () => {
        const round = review({
            roundness: { state: 'round', sentence: 'Stars are round enough (stretch 1.02), as usual for your equipment.' },
            direction: { state: 'not_needed', sentence: 'The stars are round, so their direction does not matter.' },
        });
        expect(describeStarShapeVerdict(round)).toBe('Stars are round enough (stretch 1.02), as usual for your equipment.');
    });

    it('has no line when nothing is measured', () => {
        expect(describeStarShapeVerdict(review({ roundness: { state: 'not_measured', sentence: 'none' } }))).toBeNull();
        expect(describeStarShapeVerdict(null)).toBeNull();
    });
});

describe('corner note', () => {
    it('writes the ratio from the numbers', () => {
        expect(describeCorners(review().corners)).toBe('Corner stars are 1.08 times as wide as center stars.');
        expect(describeCorners({ state: 'not_measured', sentence: '' })).toBeNull();
    });
});

describe('StarShapesSummary', () => {
    it('shows the verdict, the corner note with its not-validated chip, and the chart', () => {
        render(<StarShapesSummary review={review()} selectedPath={null} onSelectFrame={() => {}} />);
        expect(screen.getByTestId('star-shape-verdict').textContent).toMatch(/stretched more than usual/);
        expect(screen.getByTestId('star-shape-corners').textContent).toMatch(/1\.08 times as wide/);
        expect(screen.getByText('Not yet validated')).toBeTruthy();
        expect(screen.getByTestId('star-shape-chart')).toBeTruthy();
    });

    it('says plainly when nothing is measured, and draws no chart', () => {
        render(<StarShapesSummary
            review={review({ roundness: { state: 'not_measured', sentence: 's' }, series: [], corners: { state: 'not_measured', sentence: '' } })}
            selectedPath={null} onSelectFrame={() => {}}
        />);
        expect(screen.getByTestId('star-shape-verdict').textContent).toBe(NOT_MEASURED_NOTE);
        expect(screen.queryByTestId('star-shape-chart')).toBeNull();
        expect(screen.queryByTestId('star-shape-corners')).toBeNull();
    });

    it('shows nothing without a review', () => {
        const { container } = render(<StarShapesSummary review={null} selectedPath={null} onSelectFrame={() => {}} />);
        expect(container.firstChild).toBeNull();
    });

    it('picks the frame whose point is clicked', () => {
        const onSelectFrame = vi.fn();
        render(<StarShapesSummary review={review()} selectedPath={null} onSelectFrame={onSelectFrame} />);
        plotProps.current!.onClick!({ points: [{ customdata: '/lib/c.fits' }] });
        expect(onSelectFrame).toHaveBeenCalledWith('/lib/c.fits');
        fireEvent.click(screen.getByTestId('star-shape-chart'));
        expect(onSelectFrame).toHaveBeenCalledTimes(1);
    });
});

/** Reads the paths each point of a trace carries. */
const customOf = (trace: unknown): string[] => (trace as { customdata: string[] }).customdata;

describe('buildStarShapeChart', () => {
    const series = review().series!;

    it('draws only frames with a time and an elongation, flagged ones as diamonds', () => {
        const chart = buildStarShapeChart(series, new Set(['/lib/c.fits']), null);
        const [normal, flagged] = chart.traces;
        expect(chart.drawnCount).toBe(3);
        expect(customOf(normal)).toEqual(['/lib/a.fits', '/lib/b.fits']);
        expect(normal.x).toEqual([1000000, 2000000]);
        expect(customOf(flagged)).toEqual(['/lib/c.fits']);
        expect(flagged.marker?.symbol).toBe('diamond');
        expect(normal.marker?.symbol).toBe('circle');
        expect(chart.traces).toHaveLength(2);
    });

    it('rings the picked frame and finds the session median', () => {
        const chart = buildStarShapeChart(series, new Set(['/lib/c.fits']), '/lib/b.fits');
        expect(chart.traces).toHaveLength(3);
        expect(customOf(chart.traces[2])).toEqual(['/lib/b.fits']);
        expect(chart.traces[2].marker?.symbol).toBe('circle-open');
        expect(chart.typical).toBe(1.2);
    });

    it('draws the dashed median line and its label on the plot', () => {
        render(<StarShapesSummary review={review()} selectedPath={null} onSelectFrame={() => {}} />);
        const { layout } = plotProps.current!;
        expect(layout.shapes[0].line.dash).toBe('dash');
        expect(layout.shapes[0].y0).toBe(1.2);
        expect(layout.annotations[0].text).toBe('session median 1.20');
    });
});
