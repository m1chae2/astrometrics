/**
 * @fileoverview The Ekos summary: the plan as rows to enter in Ekos's Capture
 * module, in Capture order, each with a "Copy" button and a "done in Ekos" tick.
 *
 * The rows come from `exampleSequenceSummary` until wave L2 delivers the real
 * summary on the plan read, so the panel carries the "Example data" badge. The
 * "done in Ekos" tick is stored through the plan in wave L2; until then it is
 * shown off, with the reason as visible text. "Copied" is announced politely.
 */

import React, { useRef, useState } from 'react';
import { exampleSequenceSummary } from '../common/examples';
import { ExampleDataBadge } from '../common/components/quality/ExampleDataBadge';

/** The visible reason why the "done in Ekos" ticks are off. */
export const DONE_IN_EKOS_UNAVAILABLE_TEXT = 'Saving "done in Ekos" arrives with the library plan update';

/** How long "Copied" stays on screen, in milliseconds. */
const COPIED_MESSAGE_MS = 3000;

/**
 * The Ekos summary panel.
 * @return The panel.
 */
export const EkosSummaryPanel: React.FC = () => {
    const [copiedMessage, setCopiedMessage] = useState('');
    const timerReference = useRef<ReturnType<typeof setTimeout> | null>(null);

    /** Copies one row's text and announces it. */
    const copyRow = async (rowNumber: number, copyText: string) => {
        try {
            await navigator.clipboard.writeText(copyText);
            setCopiedMessage(`Copied row ${rowNumber}`);
        } catch {
            setCopiedMessage(`Could not copy row ${rowNumber}. Select the text and copy it.`);
        }
        if (timerReference.current) clearTimeout(timerReference.current);
        timerReference.current = setTimeout(() => setCopiedMessage(''), COPIED_MESSAGE_MS);
    };

    return (
        <section className="ekos-summary" aria-label="Ekos summary" data-example="true">
            <h4>
                Ekos summary <ExampleDataBadge />
            </h4>
            <p id="ekos-done-reason" className="ekos-summary__reason">{DONE_IN_EKOS_UNAVAILABLE_TEXT}</p>
            <ol className="ekos-summary__rows">
                {exampleSequenceSummary.map((row, index) => (
                    <li key={row.rowId} className="ekos-summary__row">
                        <span className="ekos-summary__text">{row.copyText}</span>
                        <button
                            type="button"
                            className="btn btn--tiny"
                            aria-label={`Copy row ${index + 1}: ${row.copyText}`}
                            onClick={() => void copyRow(index + 1, row.copyText)}
                        >
                            Copy
                        </button>
                        <label>
                            <input
                                type="checkbox"
                                checked={row.doneInEkos}
                                disabled
                                aria-disabled="true"
                                aria-describedby="ekos-done-reason"
                                readOnly
                            />{' '}
                            done in Ekos
                        </label>
                    </li>
                ))}
            </ol>
            <div aria-live="polite" role="status" className="ekos-summary__copied">
                {copiedMessage}
            </div>
        </section>
    );
};
