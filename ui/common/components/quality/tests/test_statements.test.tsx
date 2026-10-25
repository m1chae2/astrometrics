/**
 * @fileoverview Tests of the statement components: icon and words for each
 * kind and severity, the confidence labels, expansion and "Show all N".
 */

import React from 'react';
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Confidence, Severity, StatementKind } from '../../../types/backendTypes';
import { StatementItem } from '../StatementItem';
import { StatementList } from '../StatementList';
import { ExampleDataBadge } from '../ExampleDataBadge';
import { makeStatement } from './qualityTestData';

describe('StatementItem', () => {
    /** Each kind shows its word and an icon, and the name carries the type and confidence words. */
    it.each([
        [StatementKind.WARNING, 'Warning'],
        [StatementKind.RECOMMENDATION, 'Recommendation'],
        [StatementKind.CONSTRAINT, 'Limit'],
        [StatementKind.INSUFFICIENT_DATA, 'Not enough data'],
    ])('shows the %s kind with icon and word', (kind, word) => {
        const { container } = render(
            <ul>
                <StatementItem itemKey="k" statement={makeStatement({ kind, message: 'Text here.' })} />
            </ul>,
        );
        expect(screen.getAllByText(word).length).toBeGreaterThan(0);
        expect(container.querySelector(`svg.statement-icon--${kind}`)).not.toBeNull();
        expect(screen.getByLabelText(new RegExp(`^${word}.*Measured: Text here\\.`))).toBeTruthy();
    });

    /** A warning of severity info reads differently from one of severity warning. */
    it.each([
        [Severity.INFO, 'Severity: Info'],
        [Severity.ADVICE, 'Severity: Advice'],
        [Severity.WARNING, 'Severity: Warning'],
    ])('shows severity %s as a word', (severity, text) => {
        render(
            <ul>
                <StatementItem itemKey="k" statement={makeStatement({ severity })} />
            </ul>,
        );
        expect(screen.getByText(text)).toBeTruthy();
    });

    /** The three confidence labels. */
    it('shows the three confidence labels', () => {
        render(
            <ul>
                <StatementItem itemKey="a" statement={makeStatement({ confidence: Confidence.MEASURED })} />
                <StatementItem itemKey="b" statement={makeStatement({ confidence: Confidence.PARTLY_MEASURED })} />
                <StatementItem itemKey="c" statement={makeStatement({ confidence: Confidence.DESIGN_ESTIMATE })} />
            </ul>,
        );
        expect(screen.getAllByText('Measured').length).toBe(1);
        expect(screen.getAllByText('Partly measured').length).toBe(1);
        expect(screen.getAllByText('Tentative').length).toBe(1);
    });

    /** The reason, values and evidence stay in the API reply; the item offers no Details button. */
    it('shows no Details button and no reason or values', () => {
        render(
            <ul>
                <StatementItem
                    itemKey="a"
                    statement={makeStatement({
                        reason: 'Because of wind.',
                        values: { fwhm: 3.14159 },
                        evidence: [{ starResult: { starId: 'S1', runId: 'R1', metric: 'star.mag' } }],
                    })}
                />
            </ul>,
        );
        expect(screen.queryByRole('button', { name: 'Details' })).toBeNull();
        expect(screen.queryByText(/Because of wind/)).toBeNull();
        expect(screen.queryByText('3.142')).toBeNull();
    });
});

describe('StatementList', () => {
    const many = [1, 2, 3, 4, 5].map((index) => makeStatement({ rule: `r${index}`, message: `Message ${index}` }));

    /** Shows the first three, then all after "Show all 5" from the keyboard, with focus kept. */
    it('shows the top three and expands from the keyboard with focus kept', async () => {
        const user = userEvent.setup();
        render(<StatementList statements={many} />);
        expect(screen.getAllByRole('listitem').length).toBe(3);
        const button = screen.getByRole('button', { name: 'Show all 5' });
        button.focus();
        await user.keyboard('{Enter}');
        expect(screen.getAllByRole('listitem').length).toBe(5);
        const after = screen.getByRole('button', { name: 'Show fewer' });
        expect(document.activeElement).toBe(after);
    });

    /** Keeps the order the library returned. */
    it('keeps the returned order', () => {
        render(<StatementList statements={many} />);
        const names = screen.getAllByRole('listitem').map((item) => item.getAttribute('aria-label'));
        expect(names[0]).toContain('Message 1');
        expect(names[2]).toContain('Message 3');
    });
});

describe('ExampleDataBadge', () => {
    /** The badge says "Example data". */
    it('shows the words', () => {
        render(<ExampleDataBadge />);
        expect(screen.getByText('Example data')).toBeTruthy();
    });
});
