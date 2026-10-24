/**
 * @fileoverview One queue entry of the night plan: its target, its status, its
 * exposure requests with the dither setting folded in, and the buttons to move,
 * edit or remove it.
 *
 * The entry is drawn as the library stored it. Edits go through `plan:edit`
 * (the parent passes the callbacks); the library refuses an edit it cannot
 * take, and the parent shows its reason.
 */

import React, { useRef, useState } from 'react';
import type { ExposureRequest, QueuedObservationPackage } from '../common/types/backendTypes';
import { formatValue } from '../common/components/quality/statementLabels';

export interface PlanQueueEntryProps {
    /** The entry. */
    entry: QueuedObservationPackage;
    /** True when the entry is first in the queue, so "Move up" is off. */
    isFirst: boolean;
    /** True when the entry is last in the queue, so "Move down" is off. */
    isLast: boolean;
    /** True while a plan write is running. */
    busy: boolean;
    /** Moves the entry one place earlier (-1) or later (1). */
    onMove: (entryId: string, direction: -1 | 1) => void;
    /** Asks to take the entry out of the queue. */
    onRemove: (entryId: string, trigger: HTMLElement | null) => void;
    /** Saves new exposure requests for the entry. Resolves to `true` when the library accepted them. */
    onSaveExposures: (entryId: string, requests: ExposureRequest[]) => Promise<boolean>;
}

/**
 * Writes a number or an empty string for an input.
 * @param value A number, null or undefined.
 * @return The text for the input.
 */
function toInputText(value: number | null | undefined): string {
    return value === null || value === undefined ? '' : String(value);
}

/**
 * Reads a number from an input's text.
 * @param text The text.
 * @return The number, `null` for empty text, or `NaN` when the text is not a number.
 */
function fromInputText(text: string): number | null {
    return text.trim() === '' ? null : Number(text);
}

/**
 * Writes one exposure request as short text.
 * @param request The request.
 * @return Text such as `40 x 60 s, filter L, gain 100, offset 10, bin 1`.
 */
export function describeRequest(request: ExposureRequest): string {
    const parts = [`${request.count} x ${formatValue(request.exposureSec)} s`];
    if (request.filter) parts.push(`filter ${request.filter}`);
    if (request.gainSetting !== null && request.gainSetting !== undefined) parts.push(`gain ${request.gainSetting}`);
    if (request.offset !== null && request.offset !== undefined) parts.push(`offset ${request.offset}`);
    if (request.binning !== null && request.binning !== undefined) parts.push(`bin ${request.binning}`);
    return parts.join(', ');
}

/**
 * Writes the dither setting of an entry.
 * @param entry The entry.
 * @return `No dither`, or text such as `Dither every 3 frames, 5 px`.
 */
function describeDither(entry: QueuedObservationPackage): string {
    const dither = entry.ditherConfig;
    if (!dither || !dither.enabled) return 'No dither';
    return `Dither every ${dither.everyNFrames ?? '?'} frames, ${dither.pixels ?? '?'} px`;
}

/**
 * The edit form for an entry's exposure requests.
 * @param props The requests, the save and cancel callbacks and whether a write is running.
 * @return The form.
 */
const ExposureForm: React.FC<{
    entryId: string;
    requests: ExposureRequest[];
    busy: boolean;
    onSave: (requests: ExposureRequest[]) => Promise<boolean>;
    onCancel: () => void;
}> = ({ entryId, requests, busy, onSave, onCancel }) => {
    const [rows, setRows] = useState(() =>
        requests.map((request) => ({
            filter: request.filter ?? '',
            exposureSec: toInputText(request.exposureSec),
            count: toInputText(request.count),
            gainSetting: toInputText(request.gainSetting),
            offset: toInputText(request.offset),
            binning: toInputText(request.binning),
        })),
    );
    const [problem, setProblem] = useState('');

    /** Changes one field of one row. */
    const change = (index: number, field: keyof (typeof rows)[number], value: string) => {
        setRows((previous) => previous.map((row, rowIndex) => (rowIndex === index ? { ...row, [field]: value } : row)));
    };

    /** Checks the numbers, then saves. */
    const submit = async (event: React.FormEvent) => {
        event.preventDefault();
        const next: ExposureRequest[] = [];
        for (let index = 0; index < rows.length; index += 1) {
            const row = rows[index];
            const exposureSec = fromInputText(row.exposureSec);
            const count = fromInputText(row.count);
            if (exposureSec === null || Number.isNaN(exposureSec) || exposureSec <= 0) {
                setProblem(`Request ${index + 1}: the exposure must be a number above 0.`);
                return;
            }
            if (count === null || !Number.isInteger(count) || count < 1) {
                setProblem(`Request ${index + 1}: the count must be a whole number of 1 or more.`);
                return;
            }
            const optional = [fromInputText(row.gainSetting), fromInputText(row.offset), fromInputText(row.binning)];
            if (optional.some((value) => value !== null && Number.isNaN(value))) {
                setProblem(`Request ${index + 1}: gain, offset and binning must be numbers or empty.`);
                return;
            }
            next.push({
                ...requests[index],
                filter: (row.filter || undefined) as ExposureRequest['filter'],
                exposureSec,
                count,
                gainSetting: optional[0],
                offset: optional[1],
                binning: optional[2],
            });
        }
        setProblem('');
        await onSave(next);
    };

    return (
        <form className="plan-entry__form" onSubmit={(event) => void submit(event)} aria-label={`Edit exposures of ${entryId}`}>
            {rows.map((row, index) => (
                <fieldset key={index} className="plan-entry__fieldset">
                    <legend>Request {index + 1}</legend>
                    <label>Filter <input value={row.filter} onChange={(event) => change(index, 'filter', event.target.value)} /></label>
                    <label>Exposure (s) <input inputMode="decimal" value={row.exposureSec} onChange={(event) => change(index, 'exposureSec', event.target.value)} /></label>
                    <label>Count <input inputMode="numeric" value={row.count} onChange={(event) => change(index, 'count', event.target.value)} /></label>
                    <label>Gain <input inputMode="numeric" value={row.gainSetting} onChange={(event) => change(index, 'gainSetting', event.target.value)} /></label>
                    <label>Offset <input inputMode="numeric" value={row.offset} onChange={(event) => change(index, 'offset', event.target.value)} /></label>
                    <label>Binning <input inputMode="numeric" value={row.binning} onChange={(event) => change(index, 'binning', event.target.value)} /></label>
                </fieldset>
            ))}
            {problem && <p role="alert" className="plan-entry__problem">{problem}</p>}
            <div className="plan-entry__buttons">
                <button type="submit" className="btn btn--tiny" disabled={busy}>Save exposures</button>
                <button type="button" className="btn btn--tiny" onClick={onCancel}>Cancel</button>
            </div>
        </form>
    );
};

/**
 * One queue entry.
 * @param props The entry and the edit callbacks.
 * @return The list item.
 */
export const PlanQueueEntry: React.FC<PlanQueueEntryProps> = ({
    entry,
    isFirst,
    isLast,
    busy,
    onMove,
    onRemove,
    onSaveExposures,
}) => {
    const [editing, setEditing] = useState(false);
    const editReference = useRef<HTMLButtonElement>(null);
    const removeReference = useRef<HTMLButtonElement>(null);
    const requests = entry.exposureRequests ?? [];

    return (
        <li className="plan-entry" aria-label={`Queue entry ${entry.targetId}`}>
            <div className="plan-entry__head">
                <strong>{entry.targetId}</strong>
                {entry.status && <span className="plan-entry__status">{entry.status}</span>}
                <span className="plan-entry__dither">{describeDither(entry)}</span>
            </div>
            {editing ? (
                <ExposureForm
                    entryId={entry.targetId}
                    requests={requests}
                    busy={busy}
                    onSave={async (next) => {
                        const accepted = await onSaveExposures(entry.id, next);
                        if (accepted) {
                            setEditing(false);
                            editReference.current?.focus();
                        }
                        return accepted;
                    }}
                    onCancel={() => {
                        setEditing(false);
                        window.setTimeout(() => editReference.current?.focus(), 0);
                    }}
                />
            ) : (
                <ul className="plan-entry__requests">
                    {requests.map((request, index) => (
                        <li key={index}>
                            {String(request.frameType)}: {describeRequest(request)}
                        </li>
                    ))}
                    {requests.length === 0 && <li>No exposure requests.</li>}
                </ul>
            )}
            <div className="plan-entry__buttons">
                <button
                    type="button"
                    className="btn btn--tiny"
                    disabled={isFirst || busy}
                    aria-label={`Move ${entry.targetId} up`}
                    onClick={() => onMove(entry.id, -1)}
                >
                    Move up
                </button>
                <button
                    type="button"
                    className="btn btn--tiny"
                    disabled={isLast || busy}
                    aria-label={`Move ${entry.targetId} down`}
                    onClick={() => onMove(entry.id, 1)}
                >
                    Move down
                </button>
                <button
                    ref={editReference}
                    type="button"
                    className="btn btn--tiny"
                    aria-label={`Edit exposures of ${entry.targetId}`}
                    aria-expanded={editing}
                    onClick={() => setEditing((value) => !value)}
                >
                    Edit exposures
                </button>
                <button
                    ref={removeReference}
                    type="button"
                    className="btn btn--tiny"
                    disabled={busy}
                    aria-label={`Remove ${entry.targetId} from the plan`}
                    onClick={() => onRemove(entry.id, removeReference.current)}
                >
                    Remove
                </button>
            </div>
        </li>
    );
};
