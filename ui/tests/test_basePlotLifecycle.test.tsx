/**
 * @fileoverview Tests that BasePlot never calls Plotly out of order: no react()
 * before newPlot() has finished, and no react() or newPlot() after purge().
 * Out-of-order calls made Plotly fail with "e.emit is not a function".
 */
import React from 'react';
import { render, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const calls: string[] = [];
let releaseNewPlot: (() => void) | null = null;
/** When set, the next react() waits for this promise, so later changes queue behind it. */
let reactGate: Promise<void> | null = null;

vi.mock('../common/utils/plotTools', () => ({
    loadPlotly: () => Promise.resolve({
        newPlot: () => new Promise<void>(resolve => {
            calls.push('newPlot:start');
            releaseNewPlot = () => { calls.push('newPlot:end'); resolve(); };
        }),
        react: () => { calls.push('react'); const gate = reactGate; reactGate = null; return gate ?? Promise.resolve(); },
        purge: () => { calls.push('purge'); },
    }),
    attachClickHandler: vi.fn(),
    detachClickHandler: vi.fn(),
}));

import { BasePlot } from '../common/components/plotting/BasePlot';

/** Lets queued promise callbacks run. */
const flush = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });

describe('BasePlot Plotly call order', () => {
    beforeEach(() => {
        calls.length = 0;
        releaseNewPlot = null;
    });

    it('does not call react while newPlot is still running', async () => {
        const { rerender } = render(<BasePlot data={[]} />);
        await flush();
        rerender(<BasePlot data={[{ x: [1], y: [1] }]} />);
        await flush();
        expect(calls).toEqual(['newPlot:start']);
        await act(async () => { releaseNewPlot?.(); });
        await flush();
        expect(calls.slice(0, 2)).toEqual(['newPlot:start', 'newPlot:end']);
    });

    it('purges only after newPlot has finished when unmounted while loading', async () => {
        const { unmount } = render(<BasePlot data={[]} />);
        await flush();
        unmount();
        await act(async () => { releaseNewPlot?.(); });
        await flush();
        expect(calls).toEqual(['newPlot:start', 'newPlot:end', 'purge']);
    });

    it('never calls react after purge', async () => {
        const { rerender, unmount } = render(<BasePlot data={[]} />);
        await flush();
        await act(async () => { releaseNewPlot?.(); });
        await flush();
        rerender(<BasePlot data={[{ x: [2], y: [2] }]} />);
        unmount();
        await flush();
        const purgeAt = calls.indexOf('purge');
        expect(purgeAt).toBeGreaterThan(-1);
        expect(calls.slice(purgeAt + 1)).not.toContain('react');
    });

    it('merges rapid data changes into one waiting react', async () => {
        const { rerender } = render(<BasePlot data={[]} />);
        await flush();
        await act(async () => { releaseNewPlot?.(); });
        await flush();
        calls.length = 0;
        // Hold the queue with a slow react so later changes pile up behind it.
        let openGate: () => void = () => {};
        reactGate = new Promise<void>(resolve => { openGate = resolve; });
        rerender(<BasePlot data={[{ x: [1], y: [1] }]} />);
        await flush();
        for (let step = 2; step < 8; step++) {
            rerender(<BasePlot data={[{ x: [step], y: [step] }]} />);
        }
        await flush();
        openGate();
        await flush();
        expect(calls.filter(call => call === 'react').length).toBe(2);
    });
});
