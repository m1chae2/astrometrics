/**
 * @file ChecklistDialog.tsx
 * @description The dialog that lists tonight's session checklist, opened by the "Checklist (N)"
 * button in the Execution Queue header. Each item has a tick box; each item shows the library's plain-words `label`; ticking sends the tick to the
 * library (with the revision the window last read). Ticked items show when they were ticked.
 * Escape and Close shut it, Tab stays inside it while it is open, and the caller returns focus to
 * the button that opened it.
 */
import React, { useEffect, useRef } from 'react';
import { ChecklistItem } from '../common/types/backendTypes';
import './queueProgress.css';

export interface ChecklistDialogProps {
    /** The session's checklist, in the order the library returned it. */
    items: ChecklistItem[];
    /** True when tonight has no session yet (the empty text then explains how to start one). */
    hasSession: boolean;
    /** Called with the item id when the user ticks an item. */
    onTick: (itemId: string) => void;
    /** Text of a refused tick, or null. Stays until the dialog closes. */
    errorMessage: string | null;
    onClose: () => void;
}

/**
 * Shows the checklist dialog.
 * @param props See {@link ChecklistDialogProps}.
 * @return The dialog element.
 */
export const ChecklistDialog: React.FC<ChecklistDialogProps> = ({ items, hasSession, onTick, errorMessage, onClose }) => {
    const dialogRef = useRef<HTMLDivElement>(null);

    // Move focus into the dialog when it opens, close on Escape, and keep Tab inside the dialog.
    useEffect(() => {
        dialogRef.current?.focus();
        const handleKey = (event: KeyboardEvent) => {
            if (event.key === 'Escape') {
                onClose();
                return;
            }
            if (event.key !== 'Tab' || !dialogRef.current) return;
            const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
                'button:not([disabled]), input:not([disabled]), [href], select, textarea, [tabindex]:not([tabindex="-1"])',
            ));
            if (focusable.length === 0) {
                event.preventDefault();
                return;
            }
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            const active = document.activeElement;
            if (event.shiftKey && (active === first || active === dialogRef.current)) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && active === last) {
                event.preventDefault();
                first.focus();
            }
        };
        window.addEventListener('keydown', handleKey);
        return () => window.removeEventListener('keydown', handleKey);
    }, [onClose]);

    return (
        <div className="checklist-overlay">
            <div
                className="checklist-dialog"
                role="dialog"
                aria-modal="true"
                aria-labelledby="checklist-dialog-title"
                tabIndex={-1}
                ref={dialogRef}
            >
                <h2 id="checklist-dialog-title" className="checklist-dialog__title">Checklist for tonight</h2>
                {errorMessage && <div className="checklist-dialog__error" role="alert">{errorMessage}</div>}
                {items.length === 0 ? (
                    <p>
                        {hasSession
                            ? 'Nothing on the checklist. Items appear when new frames show a fix to try.'
                            : 'No session yet. Add a package to start tonight’s session.'}
                    </p>
                ) : (
                    <ul className="checklist-dialog__list">
                        {items.map((item, index) => {
                            const itemId = item.itemId ?? String(index);
                            const inputId = `checklist-item-${itemId}`;
                            const isTicked = !!item.tickedAt;
                            return (
                                <li key={itemId} className="checklist-dialog__item">
                                    <input
                                        id={inputId}
                                        type="checkbox"
                                        checked={isTicked}
                                        disabled={isTicked}
                                        onChange={() => onTick(itemId)}
                                    />
                                    <label htmlFor={inputId}>
                                        {item.label ?? item.note ?? ''}
                                        {isTicked && item.tickedAt && (
                                            <span className="checklist-dialog__ticked-at">
                                                {' '}(ticked {new Date(item.tickedAt).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })})
                                            </span>
                                        )}
                                    </label>
                                </li>
                            );
                        })}
                    </ul>
                )}
                <div className="checklist-dialog__actions">
                    <button type="button" className="btn btn--tiny" onClick={onClose}>Close</button>
                </div>
            </div>
        </div>
    );
};
