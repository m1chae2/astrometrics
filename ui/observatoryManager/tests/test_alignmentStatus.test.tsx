/**
 * @file test_alignmentStatus.test.tsx
 * @description Tests for the Alignment table (its columns, the status word behind
 * each symbol, and that repeated library records are shown as they are) and for
 * the Guiding Trends message shown before any guiding samples.
 */

import React from 'react';
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { AlignmentStatus } from '../panels/AlignmentStatus';
import { GuidingTrendsPlot } from '../panels/GuidingStatusPanel';

describe('AlignmentStatus', () => {
    /** Each row shows time, target, a status symbol with its word, and right-aligned deltas. */
    it('shows time, target, status word and deltas', () => {
        const timestamp = new Date(2026, 0, 18, 5, 58).getTime() / 1000;
        const attempt = { status: 'warning', deltaRaArcsec: 119.03, deltaDecArcsec: -1587.57, timestamp, targetName: 'NGC 7331' };
        render(<AlignmentStatus alignmentAttempts={[attempt, attempt]} />);
        const table = screen.getByRole('table');
        const headers = within(table).getAllByRole('columnheader').map((header) => header.textContent);
        expect(headers).toEqual(['Time', 'Target', 'Status', 'dRA (″)', 'dDEC (″)']);
        // The library's records are shown as stored, repeats included.
        expect(within(table).getAllByText('NGC 7331')).toHaveLength(2);
        expect(within(table).getAllByRole('img', { name: 'Outside tolerance' })).toHaveLength(2);
        expect(within(table).getAllByText('119.03')).toHaveLength(2);
        expect(within(table).getAllByText(/05:58/)).toHaveLength(2);
    });

    /** A five-digit offset such as -19152.94 is shown whole, in a column wide enough for it (#133). */
    it('shows a five-digit dDEC value in full and keeps the number columns wide enough', () => {
        const attempt = { status: 'failed', deltaRaArcsec: 12.5, deltaDecArcsec: -19152.94, timestamp: 0, targetName: 'M 81' };
        render(<AlignmentStatus alignmentAttempts={[attempt]} />);
        expect(screen.getByText('-19152.94')).toBeInTheDocument();
        const css = readFileSync('ui/observatoryManager/observatoryManager.css', 'utf8');
        const width = Number(/\.alignment__header--number \{ width: (\d+)px/.exec(css)?.[1]);
        // At 13 px with tabular digits, nine characters need about 9 x 8 px plus 8 px of padding.
        expect(width).toBeGreaterThanOrEqual(80);
        expect(css).toMatch(/\.alignment__cell--number \{[^}]*white-space: nowrap/);
    });
});

describe('GuidingTrendsPlot', () => {
    /** With no samples the panel says so in words instead of drawing a plot whose time axis reads 00:00:00. */
    it('shows a message before any guiding data', () => {
        render(<GuidingTrendsPlot history={[]} />);
        expect(screen.getByText(/No guiding data yet/)).toBeTruthy();
    });
});
