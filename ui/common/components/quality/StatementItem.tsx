/**
 * @fileoverview One quality statement: a warning, recommendation, limit or
 * "not enough data" note.
 *
 * The statement's type is told by an icon AND a word, its severity by a word
 * (a result has none), and its confidence by a text chip. The text is the
 * plain-language sentence; the technical message is not shown (it is in the
 * library's reply for anyone who reads it through the API). Text is selectable.
 */

import React from 'react';
import { ActionClass, StatementKind } from '../../types/backendTypes';
import type { CaptureSetting, TargetStatement } from '../../types/backendTypes';
import { ConfidenceChip } from './ConfidenceChip';
import { StatementTypeIcon } from './StatementTypeIcon';
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
    const typeWord = statementKindWord(statement.kind);
    const severity = statement.kind === StatementKind.RESULT ? '' : severityWord(statement.severity);
    const evidence = statement.evidence ?? [];
    const rowIds = evidence.flatMap((reference) =>
        reference.rowId !== null && reference.rowId !== undefined ? [reference.rowId] : [],
    );
    // The library sometimes leaves plain_message empty, so fall back to the technical message rather than show nothing.
    const text = statement.plainMessage ? statement.plainMessage : statement.message;
    const isCalibration = statement.actionClass === ActionClass.CALIBRATION;
    const showEvidence = Boolean(actions) && rowIds.length > 0;
    const showCalibration = Boolean(actions) && isCalibration;
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
            aria-label={`${accessibleName}: ${text}`}
            data-severity={statement.severity ?? 'none'}
        >
            <div className="statement-item__head">
                <StatementTypeIcon kind={statement.kind} severity={statement.severity} />
                <span className="statement-item__type">{typeWord}</span>
                {severity && statement.kind !== StatementKind.INSUFFICIENT_DATA && statement.kind !== StatementKind.RESULT && (
                    <span className="statement-item__severity">Severity: {severity}</span>
                )}
                <ConfidenceChip confidence={statement.confidence} />
                {statement.captureSetting && (
                    <span className="statement-item__setting">{describeCaptureSetting(statement.captureSetting)}</span>
                )}
            </div>
            <p className="statement-item__message">{text}</p>

            {(showEvidence || showCalibration) && (
                <div className="statement-item__buttons">
                    {actions && rowIds.length > 0 && (
                        <button
                            type="button"
                            className="btn btn--tiny quality-button"
                            onClick={() => actions.showFrames(rowIds, text)}
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
                </div>
            )}
        </li>
    );
};
