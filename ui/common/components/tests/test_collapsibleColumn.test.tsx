/**
 * @file test_collapsibleColumn.test.tsx
 * @description Checks the folded strip of CollapsibleColumn: the title alone, the title with the
 * selected item, and the title with the selected item and a second detail (the picked star), and
 * the right-edge strip and the bottom bar.
 */

import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CollapsibleColumn } from '../CollapsibleColumn';

/**
 * Renders a folded column.
 * @param selectedLabel The selected item, if any.
 * @param selectedDetail The second item, if any.
 * @param onCollapsedChange The open handler.
 */
function renderStrip(selectedLabel?: string, selectedDetail?: string, onCollapsedChange = vi.fn()) {
    render(
        <CollapsibleColumn
            collapsed
            onCollapsedChange={onCollapsedChange}
            stripTitle="Targets"
            selectedLabel={selectedLabel}
            selectedDetail={selectedDetail}
            showLabel="Show target list"
        >
            <div>open column</div>
        </CollapsibleColumn>,
    );
}

describe('CollapsibleColumn strip', () => {
    /** With nothing selected the strip names only its title and says how to open it. */
    it('shows the title alone', () => {
        renderStrip();
        const strip = screen.getByRole('button', { name: 'Targets' });
        expect(strip.getAttribute('title')).toBe('Show target list');
        expect(document.querySelector('.panel__collapsed-note')).toBeNull();
    });

    /** The selected item shows under the title. */
    it('shows the selected item', () => {
        renderStrip('Albireo');
        expect(screen.getByRole('button', { name: 'Targets, Albireo' })).toBeTruthy();
        expect(document.querySelector('.panel__collapsed-note')?.textContent).toBe('Albireo');
    });

    /** A second detail (the picked star) follows the item, and the hover text holds it all. */
    it('shows the selected star after the target, and opens on click', async () => {
        const onCollapsedChange = vi.fn();
        renderStrip('Albireo', 'HD 338506', onCollapsedChange);
        const strip = screen.getByRole('button', { name: 'Targets, Albireo, HD 338506' });
        expect(strip.getAttribute('title')).toBe('Targets, Albireo, HD 338506');
        expect(document.querySelector('.panel__collapsed-note')?.textContent).toBe('Albireo · HD 338506');
        await userEvent.click(strip);
        expect(onCollapsedChange).toHaveBeenCalledWith(false);
    });

    /** Open, it shows the column itself. */
    it('shows the column when open', () => {
        render(
            <CollapsibleColumn collapsed={false} onCollapsedChange={vi.fn()} stripTitle="Targets" showLabel="Show">
                <div>open column</div>
            </CollapsibleColumn>,
        );
        expect(screen.getByText('open column')).toBeTruthy();
    });

    /**
     * A right-edge column folds to its own strip class, so the rule that narrows the display's
     * left column does not apply to it.
     */
    it('folds a right-edge column to a right strip', async () => {
        const onCollapsedChange = vi.fn();
        render(
            <CollapsibleColumn
                collapsed
                side="right"
                onCollapsedChange={onCollapsedChange}
                stripTitle="Console"
                selectedLabel="Terminal"
                showLabel="Show the Console"
            >
                <div>open column</div>
            </CollapsibleColumn>,
        );
        expect(document.querySelector('.collapsible-column--collapsed-right')).not.toBeNull();
        expect(document.querySelector('.collapsible-column--collapsed')).toBeNull();
        await userEvent.click(screen.getByRole('button', { name: 'Console, Terminal' }));
        expect(onCollapsedChange).toHaveBeenCalledWith(false);
    });

    /** A block under a viewer folds to a bar across the bottom, drawn as a panel folded down. */
    it('folds a bottom block to a bar', async () => {
        const onCollapsedChange = vi.fn();
        render(
            <CollapsibleColumn
                collapsed
                side="bottom"
                onCollapsedChange={onCollapsedChange}
                stripTitle="File Browser"
                selectedLabel="12 frames"
                showLabel="Expand the File Browser"
            >
                <div>open column</div>
            </CollapsibleColumn>,
        );
        expect(document.querySelector('.collapsible-column--collapsed-bottom')).not.toBeNull();
        expect(document.querySelector('.panel--collapsed-down')).not.toBeNull();
        await userEvent.click(screen.getByRole('button', { name: 'File Browser, 12 frames' }));
        expect(onCollapsedChange).toHaveBeenCalledWith(false);
    });
});
