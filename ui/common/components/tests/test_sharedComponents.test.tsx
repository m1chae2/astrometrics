/**
 * @fileoverview Tests of the shared building blocks: Card, Tabs, InfoButton, StatusChip,
 * ProgressBar and FormRow.
 */

import React, { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Card } from '../Card';
import { Tabs, TabPanel } from '../Tabs';
import { InfoButton } from '../InfoButton';
import { StatusChip } from '../StatusChip';
import { ProgressBar } from '../ProgressBar';
import { FormRow, formRowErrorId } from '../FormRow';

describe('Card', () => {
    it('marks the selected state', () => {
        render(<Card title="M 81" selected>Body</Card>);
        expect(screen.getByText('Body').closest('.card')).toHaveAttribute('data-selected', 'true');
    });

    it('sets aria-current on the current card only', () => {
        const { container } = render(<><Card title="A" current>a</Card><Card title="B">b</Card></>);
        const cards = container.querySelectorAll('.card');
        expect(cards[0]).toHaveAttribute('aria-current', 'true');
        expect(cards[1]).not.toHaveAttribute('aria-current');
    });

    it('answers click and the Enter key when it has a handler', async () => {
        const onClick = vi.fn();
        render(<Card title="A" onClick={onClick} ariaLabel="Card A">a</Card>);
        const card = screen.getByRole('button', { name: 'Card A' });
        await userEvent.click(card);
        card.focus();
        await userEvent.keyboard('{Enter}');
        expect(onClick).toHaveBeenCalledTimes(2);
    });
});

/** Test host that keeps the selected tab in state. */
const TabsHost: React.FC = () => {
    const [selectedId, setSelectedId] = useState('one');
    return (
        <>
            <Tabs
                idPrefix="t"
                ariaLabel="Views"
                selectedId={selectedId}
                onChange={setSelectedId}
                tabs={[
                    { id: 'one', label: 'Frames', count: 3 },
                    { id: 'two', label: 'Notes' },
                    { id: 'three', label: 'Plan' },
                ]}
            />
            <TabPanel idPrefix="t" tabId="one" selected={selectedId === 'one'}>First panel</TabPanel>
            <TabPanel idPrefix="t" tabId="two" selected={selectedId === 'two'}>Second panel</TabPanel>
        </>
    );
};

describe('Tabs', () => {
    it('puts the count in the accessible name and selects the first tab', () => {
        render(<TabsHost />);
        const tab = screen.getByRole('tab', { name: 'Frames (3)' });
        expect(tab).toHaveAttribute('aria-selected', 'true');
        expect(screen.getByRole('tabpanel')).toHaveTextContent('First panel');
    });

    it('moves with the arrow keys, Home and End, wrapping around', async () => {
        render(<TabsHost />);
        screen.getByRole('tab', { name: 'Frames (3)' }).focus();
        await userEvent.keyboard('{ArrowRight}');
        expect(screen.getByRole('tab', { name: 'Notes' })).toHaveAttribute('aria-selected', 'true');
        expect(screen.getByRole('tab', { name: 'Notes' })).toHaveFocus();
        expect(screen.getByRole('tabpanel')).toHaveTextContent('Second panel');
        await userEvent.keyboard('{End}');
        expect(screen.getByRole('tab', { name: 'Plan' })).toHaveFocus();
        await userEvent.keyboard('{ArrowRight}');
        expect(screen.getByRole('tab', { name: 'Frames (3)' })).toHaveFocus();
        await userEvent.keyboard('{ArrowLeft}');
        expect(screen.getByRole('tab', { name: 'Plan' })).toHaveFocus();
        await userEvent.keyboard('{Home}');
        expect(screen.getByRole('tab', { name: 'Frames (3)' })).toHaveFocus();
    });

    it('keeps only the selected tab in the Tab key order', () => {
        render(<TabsHost />);
        expect(screen.getByRole('tab', { name: 'Notes' })).toHaveAttribute('tabindex', '-1');
    });
});

describe('InfoButton', () => {
    it('is focusable and hides its text at first', () => {
        render(<InfoButton text="Ekos is in control." />);
        expect(screen.queryByText('Ekos is in control.')).not.toBeInTheDocument();
    });

    it('shows its text on hover', async () => {
        render(<InfoButton text="Ekos is in control." />);
        await userEvent.hover(screen.getByRole('button', { name: 'More information' }));
        expect(screen.getByText('Ekos is in control.')).toBeInTheDocument();
        await userEvent.unhover(screen.getByRole('button', { name: 'More information' }));
        expect(screen.queryByText('Ekos is in control.')).not.toBeInTheDocument();
    });

    it('shows its text on keyboard focus and describes the button with it', async () => {
        render(<InfoButton text="Ekos is in control." label="Why unavailable?" />);
        await userEvent.tab();
        const button = screen.getByRole('button', { name: 'Why unavailable?' });
        expect(button).toHaveFocus();
        expect(button).toHaveAccessibleDescription('Ekos is in control.');
    });

    it('keeps its text after a click until clicked again', async () => {
        render(<InfoButton text="Ekos is in control." />);
        const button = screen.getByRole('button', { name: 'More information' });
        await userEvent.click(button);
        await userEvent.unhover(button);
        expect(screen.getByText('Ekos is in control.')).toBeInTheDocument();
        await userEvent.click(button);
        await userEvent.unhover(button);
        await userEvent.tab();
        expect(screen.queryByText('Ekos is in control.')).not.toBeInTheDocument();
    });
});

describe('StatusChip', () => {
    it('shows an icon and a word', () => {
        const { container } = render(<StatusChip status="warning" label="Check focus" />);
        expect(screen.getByText('Check focus')).toBeInTheDocument();
        expect(container.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
    });
});

describe('ProgressBar', () => {
    it('has progressbar semantics and visible text', () => {
        render(<ProgressBar value={12} max={20} unit="frames" label="Capture progress" />);
        const bar = screen.getByRole('progressbar', { name: 'Capture progress' });
        expect(bar).toHaveAttribute('aria-valuenow', '12');
        expect(bar).toHaveAttribute('aria-valuemax', '20');
        expect(bar).toHaveAttribute('aria-valuetext', '12 of 20 frames');
        expect(screen.getByText('12 of 20 frames')).toBeInTheDocument();
    });

    it('clamps a value above the maximum', () => {
        render(<ProgressBar value={30} max={20} unit="frames" label="Capture progress" />);
        expect(screen.getByText('20 of 20 frames')).toBeInTheDocument();
    });
});

describe('FormRow', () => {
    it('links the label to the input and shows the unit', () => {
        render(<FormRow label="Exposure" htmlFor="exposure" unit="s"><input id="exposure" /></FormRow>);
        expect(screen.getByLabelText('Exposure')).toBeInTheDocument();
        expect(screen.getByText('s')).toBeInTheDocument();
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    it('shows the error as an alert the input can point to', () => {
        render(
            <FormRow label="Exposure" htmlFor="exposure" error="Must be positive">
                <input id="exposure" aria-describedby={formRowErrorId('exposure')} />
            </FormRow>
        );
        expect(screen.getByRole('alert')).toHaveTextContent('Must be positive');
        expect(screen.getByLabelText('Exposure')).toHaveAccessibleDescription('Must be positive');
    });
});
