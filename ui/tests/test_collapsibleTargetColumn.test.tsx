/**
 * @file test_collapsibleTargetColumn.test.tsx
 * @description Checks that the left target column (RadioListManager) folds to a thin strip and
 * opens again, reports its state with `aria-expanded`, and that Target Controls keeps only the
 * height of its content while the list takes the leftover height.
 */

import React, { useState } from 'react';
import { describe, expect, it } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '@testing-library/react';
import { RadioListManager } from '../common/radioList/RadioListManager';
import { usePersistentFlag } from '../common/hooks/usePersistentFlag';

/** A column whose folded state is kept in storage, like the real screens. */
function Harness({ persist, selectedId, items }: { persist: boolean; selectedId?: string; items?: { id: string; value: string; label: string }[] }) {
    const [saved, setSaved] = usePersistentFlag('test:targetListCollapsed', false);
    const [local, setLocal] = useState(false);
    return (
        <RadioListManager
            title="Targets"
            actionsTitle="Target Controls"
            topPanel={<div>filters</div>}
            topPanelTitle="Filter Targets"
            items={items ?? [{ id: 'M 81', value: 'M 81', label: 'M 81' }]}
            selectedId={selectedId}
            onSelect={() => undefined}
            actions={<div>buttons</div>}
            collapsed={persist ? saved : local}
            onCollapsedChange={persist ? setSaved : setLocal}
            hideLabel="Hide target list"
            showLabel="Show target list"
        />
    );
}

describe('collapsible target column', () => {
    it('folds to a strip and opens again, with aria-expanded', async () => {
        localStorage.clear();
        render(<Harness persist={false} />);
        const hide = screen.getByTitle('Hide target list');
        expect(hide.textContent).toBe('Filter Targets');
        expect(hide.getAttribute('aria-expanded')).toBe('true');
        await userEvent.click(hide);
        expect(screen.queryByText('Target Controls')).toBeNull();
        const show = screen.getByTitle('Show target list');
        expect(show.getAttribute('aria-expanded')).toBe('false');
        await userEvent.click(show);
        expect(screen.getByText('Target Controls')).toBeTruthy();
    });

    it('can be used from the keyboard', async () => {
        localStorage.clear();
        render(<Harness persist={false} />);
        screen.getByTitle('Hide target list').focus();
        await userEvent.keyboard('{Enter}');
        expect(document.querySelector('.collapsible-column--collapsed')).not.toBeNull();
        screen.getByTitle('Show target list').focus();
        await userEvent.keyboard('{Enter}');
        expect(document.querySelector('.collapsible-column--collapsed')).toBeNull();
    });

    it('remembers the folded state', async () => {
        localStorage.clear();
        const first = render(<Harness persist />);
        await userEvent.click(screen.getByTitle('Hide target list'));
        first.unmount();
        render(<Harness persist />);
        expect(screen.getByTitle('Show target list')).toBeTruthy();
    });

    it('sizes Target Controls to its content and gives the list the leftover height', () => {
        render(<Harness persist={false} />);
        const controls = screen.getByText('Target Controls').closest('.panel') as HTMLElement;
        expect(controls.className).toContain('manager__actions-panel');
        expect(controls.className).toContain('flex-auto');
        expect(controls.className).not.toContain('flex-fill');
        const list = screen.getByText('Targets').closest('.panel') as HTMLElement;
        expect(list.className).toContain('flex-fill');
    });

    it('names the selected target on the folded strip', async () => {
        render(<Harness persist={false} selectedId="M 81" />);
        await userEvent.click(screen.getByTitle('Hide target list'));
        const strip = screen.getByRole('button', { name: 'Filter Targets, M 81' });
        expect(strip.textContent).toBe('Filter Targets');
        expect(screen.getByText('M 81').className).toContain('panel__collapsed-note');
    });

    it('keeps the plain title when no target is selected', async () => {
        render(<Harness persist={false} />);
        await userEvent.click(screen.getByTitle('Hide target list'));
        expect(screen.getByTitle('Show target list').textContent).toBe('Filter Targets');
    });

    it('shows a long target name in full, with the tooltip and accessible name', async () => {
        const longName = 'M 52 - Bubble Nebula';
        render(<Harness persist={false} selectedId="x" items={[{ id: 'x', value: 'x', label: longName }]} />);
        await userEvent.click(screen.getByTitle('Hide target list'));
        const strip = screen.getByTitle(`Filter Targets, ${longName}`);
        expect(screen.getByText(longName).className).toContain('panel__collapsed-note');
        expect(strip.getAttribute('aria-label')).toBe(`Filter Targets, ${longName}`);
    });
});
