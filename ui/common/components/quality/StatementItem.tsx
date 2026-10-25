/**
 * @fileoverview One quality statement: a warning, recommendation, limit or
 * "not enough data" note.
 *
 * The statement's type is told by an icon AND a word, its severity by a word,
 * and its confidence by a text chip. The reason, values, limits and evidence
 * behind a statement are not shown here; they are in the library's reply for
 * anyone who reads it through the API. Text is selectable.
 */

import React, { useId } from 'react';
import { ActionClass, StatementKind } from '../../types/backendTypes';
import type { CaptureSetting, TargetStatement } from '../../types/backendTypes';
import { ConfidenceChip } from './ConfidenceChip';
import { StatementTypeIcon } from './StatementTypeIcon';
import { SEND_TO_PLAN_UNAVAILABLE_TEXT } from './useStatementActions';
import type { StatementActions } from './useStatementActions';
import { confidenceWord, formatValue, severityWord, statementKindWord } from './statementLabels';

export interface StatementItemProps {
    /** The statement, as the library returned it. */
    statement: TargetStatement;
    /** A key that tells this statement apart in its list. */
    itemKey: string;
    /** The action controller. Without it the item shows no action buttons. */
    actions?: StatementActions;
}

/**
 * Writes a capture setting as short text.
 * @param setting The capture setting.
 * @return Text such as "ASI533, L, 60 s, gain 100".
 */
function describeCaptureSetting(setting: CaptureSetting): string {
    const parts: string[] = [setting.camera];
    if (setting.filterName) parts.push(setting.filterName);
    if (setting.exposureSeconds !== null && setting.exposureSeconds !== undefined) {
        parts.push(`${formatValue(setting.exposureSeconds)} s`);
    }
    if (setting.gainSetting !== null && setting.gainSetting !== undefined) parts.push(`gain ${setting.gainSetting}`);
    return parts.join(', ');
}

/**
 * Shows one statement.
 * @param props The statement, its key and the optional actions.
 * @return The list item.
 */
export const StatementItem: React.FC<StatementItemProps> = ({ statement, itemKey, actions }) => {
    const reasonId = useId();
    const typeWord = statementKindWord(statement.kind);
    const severity = severityWord(statement.severity);
    const evidence = statement.evidence ?? [];
    const rowIds = evidence.flatMap((reference) =>
        reference.rowId !== null && reference.rowId !== undefined ? [reference.rowId] : [],
    );
    const outcome = actions?.outcomes[itemKey];
    const isCalibration = statement.actionClass === ActionClass.CALIBRATION;
    const canSend = Boolean(statement.proposedAction);
    const showEvidence = Boolean(actions) && rowIds.length > 0;
    const showCalibration = Boolean(actions) && isCalibration;
    const showSend = Boolean(actions) && canSend;
    const accessibleName = [
        typeWord,
        severity ? `severity ${severity}` : '',
        confidenceWord(statement.confidence),
    ]
        .filter(Boolean)
        .join(', ');

    return (
        <li
            className={`statement-item statement-item--${statement.kind}`}
            aria-label={`${accessibleName}: ${statement.message}`}
            data-severity={statement.severity ?? 'none'}
        >
            <div className="statement-item__head">
                <StatementTypeIcon kind={statement.kind} severity={statement.severity} />
                <span className="statement-item__type">{typeWord}</span>
                {severity && statement.kind !== StatementKind.INSUFFICIENT_DATA && (
                    <span className="statement-item__severity">Severity: {severity}</span>
                )}
                <ConfidenceChip confidence={statement.confidence} />
                {statement.captureSetting && (
                    <span className="statement-item__setting">{describeCaptureSetting(statement.captureSetting)}</span>
                )}
            </div>
            <p className="statement-item__message">{statement.message}</p>

            {(showEvidence || showCalibration || showSend) && (
                <div className="statement-item__buttons">
                    {actions && rowIds.length > 0 && (
                        <button
                            type="button"
                            className="btn btn--tiny quality-button"
                            onClick={() => actions.showFrames(rowIds, statement.message)}
                        >
                            Evidence
                        </button>
                    )}
                    {actions && isCalibration && (
                        <button
                            type="button"
                            className="btn btn--tiny quality-button"
                            onClick={() => actions.showCalibrationRow(statement)}
                        >
                            Show in calibration library
                        </button>
                    )}
                    {actions && canSend && (
                        <button
                            type="button"
                            className="btn btn--tiny quality-button"
                            disabled={!actions.sendToPlanAvailable || outcome?.kind === 'sending'}
                            aria-disabled={!actions.sendToPlanAvailable || outcome?.kind === 'sending'}
                            aria-describedby={actions.sendToPlanAvailable ? undefined : reasonId}
                            onClick={() => {
                                if (!actions.sendToPlanAvailable) return;
                                void actions.sendToPlan(itemKey, statement);
                            }}
                        >
                            Send to plan
                        </button>
                    )}
                </div>
            )}
            {actions && canSend && !actions.sendToPlanAvailable && (
                <p id={reasonId} className="statement-item__reason-note">
                    {SEND_TO_PLAN_UNAVAILABLE_TEXT}
                </p>
            )}

            {actions && (
                <div aria-live="polite" className="statement-item__outcome-live">
                    {outcome?.kind === 'sending' && <span>Sending to plan…</span>}
                    {outcome?.kind === 'added' && (
                        <span>
                            Added to the plan{outcome.detail ? `: ${outcome.detail}` : '.'}
                        </span>
                    )}
                    {outcome?.kind === 'merged' && (
                        <span>
                            Merged into the plan{outcome.detail ? `: ${outcome.detail}` : '.'}
                        </span>
                    )}
                    {outcome?.kind === 'conflict' && (
                        <span>
                            The plan changed in another window; reloaded. Send again?{' '}
                            <button
                                type="button"
                                className="btn btn--tiny quality-button"
                                onClick={() => void actions.sendToPlan(itemKey, statement)}
                            >
                                Send again
                            </button>
                        </span>
                    )}
                    {outcome?.kind === 'noPlan' && (
                        <span>
                            There is no plan yet.{' '}
                            <button
                                type="button"
                                className="btn btn--tiny quality-button"
                                onClick={() => void actions.createPlanAndSend(itemKey, statement)}
                            >
                                Create tonight&apos;s plan
                            </button>
                        </span>
                    )}
                </div>
            )}
            {actions && outcome?.kind === 'rejected' && (
                <div role="alert" className="statement-item__rejection">
                    <span>The plan did not take this statement: {outcome.reason}</span>
                    <button
                        type="button"
                        className="btn btn--tiny quality-button"
                        onClick={() => actions.dismissOutcome(itemKey)}
                    >
                        Dismiss
                    </button>
                </div>
            )}

        </li>
    );
};
