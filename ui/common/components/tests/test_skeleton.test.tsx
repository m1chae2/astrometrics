/**
 * @fileoverview Tests for Skeleton, UpdatingBar, PanelBody and the SectionPanel loading props.
 */

import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import fs from 'node:fs';
import path from 'node:path';
import { Skeleton, UpdatingBar, PanelBody } from '../Skeleton';
import { SectionPanel } from '../SectionPanel';
import { EmptyState } from '../EmptyState';

describe('Skeleton', () => {
    it('is a status region with its hidden label and no aria-busy', () => {
        render(<Skeleton variant="rows" label="Loading guiding for this night" />);
        const region = screen.getByRole('status');
        expect(region).not.toHaveAttribute('aria-busy');
        expect(region).toHaveTextContent('Loading guiding for this night');
    });

    it('draws rows with the requested columns and count (plus a header row)', () => {
        const { container } = render(<Skeleton variant="rows" label="x" columns={4} count={3} />);
        expect(container.querySelectorAll('.skeleton__row')).toHaveLength(4);
        expect(container.querySelectorAll('.skeleton__row')[1].querySelectorAll('.skeleton__bar')).toHaveLength(4);
    });

    it('draws list items with the requested lines', () => {
        const { container } = render(<Skeleton variant="list" label="x" count={2} lines={3} />);
        expect(container.querySelectorAll('.skeleton__item')).toHaveLength(2);
        expect(container.querySelectorAll('.skeleton__item')[0].querySelectorAll('.skeleton__bar')).toHaveLength(3);
    });

    it('draws key/value pairs, a chart, a block and a line', () => {
        const kv = render(<Skeleton variant="keyValue" label="x" count={5} />);
        expect(kv.container.querySelectorAll('.skeleton__pair')).toHaveLength(5);
        const chart = render(<Skeleton variant="chart" label="x" />);
        expect(chart.container.querySelector('.skeleton__bar--chart')).not.toBeNull();
        const block = render(<Skeleton variant="block" label="x" />);
        expect(block.container.querySelector('.skeleton__bar')).toBeNull();
        expect(block.container.querySelector('.skeleton--block > .updating-bar')).not.toBeNull();
        const line = render(<Skeleton variant="line" label="x" />);
        expect(line.container.querySelectorAll('.skeleton__bar')).toHaveLength(1);
    });

    it('turns the pulse off for reduced motion in its stylesheet', () => {
        const css = fs.readFileSync(path.resolve(__dirname, '../Skeleton.css'), 'utf8');
        expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)\s*{\s*\.skeleton__bar\s*{\s*animation: none/);
        expect(css).toMatch(/\.updating-bar::after\s*{\s*animation: none/);
    });
});

describe('PanelBody', () => {
    const skeleton = { variant: 'list' as const, label: 'Loading things' };

    it('shows the skeleton, not the children, while loading', () => {
        render(<PanelBody loading skeleton={skeleton}><p>content</p></PanelBody>);
        expect(screen.getByText('Loading things')).toBeInTheDocument();
        expect(screen.queryByText('content')).toBeNull();
    });

    it('keeps the content and shows the updating bar while refreshing', () => {
        render(<PanelBody loading={false} refreshing skeleton={skeleton}><p>content</p></PanelBody>);
        expect(screen.getByText('content')).toBeInTheDocument();
        expect(screen.getByRole('progressbar', { name: 'Updating' })).toBeInTheDocument();
        expect(screen.queryByRole('status')).toBeNull();
    });

    it('shows neither when settled', () => {
        render(<PanelBody loading={false} skeleton={skeleton}><p>content</p></PanelBody>);
        expect(screen.queryByRole('progressbar')).toBeNull();
        expect(screen.queryByRole('status')).toBeNull();
    });
});

describe('SectionPanel loading props', () => {
    it('shows the skeleton while loading and keeps the title', () => {
        render(
            <SectionPanel title="Guiding" loading skeleton={{ variant: 'chart', label: 'Loading guiding' }}>
                <p>content</p>
            </SectionPanel>,
        );
        expect(screen.getByText('Guiding')).toBeInTheDocument();
        expect(screen.getByText('Loading guiding')).toBeInTheDocument();
        expect(screen.queryByText('content')).toBeNull();
    });

    it('keeps content and shows an updating bar while refreshing', () => {
        render(<SectionPanel title="Guiding" refreshing><p>content</p></SectionPanel>);
        expect(screen.getByText('content')).toBeInTheDocument();
        expect(screen.getByRole('progressbar', { name: 'Updating Guiding' })).toBeInTheDocument();
    });
});

describe('EmptyState and UpdatingBar', () => {
    it('shows a skeleton for loading, and text (no skeleton) for empty', () => {
        const { rerender } = render(<EmptyState variant="loading" message="Loading targets" />);
        expect(screen.getByRole('status')).toHaveTextContent('Loading targets');
        rerender(<EmptyState message="No targets" />);
        expect(screen.queryByRole('status')).toBeNull();
        expect(screen.getByText('No targets')).toBeInTheDocument();
    });

    it('labels the inline bar', () => {
        render(<UpdatingBar label="Making the picture" inline />);
        expect(screen.getByRole('progressbar', { name: 'Making the picture' })).toHaveClass('updating-bar--inline');
    });
});
