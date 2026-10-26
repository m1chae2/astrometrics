/**
 * @fileoverview A list of statements: the first N in the order returned, and
 * a "Show all N" button for the rest.
 *
 * After the list expands, focus stays on the button, which now reads
 * "Show fewer". The UI does not sort or group the statements.
 */

import React, { useRef, useState } from 'react';
import type { TargetStatement } from '../../types/backendTypes';
import { StatementItem } from './StatementItem';
import type { StatementActions } from './useStatementActions';
import { statementKey } from './statementLabels';

export interface StatementListProps {
    /** The statements, in the order the library returned them. */
    statements: TargetStatement[];
    /** How many to show before "Show all N". */
    topCount?: number;
    /** The action controller, if the items offer actions. */
    actions?: StatementActions;
    /** A label for the list, read by screen readers. */
    label?: string;
}

/**
 * Shows statements with the top N and "Show all N".
 * @param props The statements, the count to show first and the actions.
 * @return The list.
 */
export const StatementList: React.FC<StatementListProps> = ({ statements, topCount = 3, actions, label = 'Statements' }) => {
    const [showAll, setShowAll] = useState(false);
    const toggleReference = useRef<HTMLButtonElement>(null);
    const shown = showAll ? statements : statements.slice(0, topCount);
    const hasMore = statements.length > topCount;

    return (
        <div className="statement-list">
            <ul className="statement-list__items" aria-label={label}>
                {shown.map((statement, index) => {
                    const key = statementKey(statement.rule, index);
                    return <StatementItem key={key} itemKey={key} statement={statement} actions={actions} />;
                })}
            </ul>
            {hasMore && (
                <button
                    ref={toggleReference}
                    type="button"
                    className="btn btn--tiny quality-button"
                    aria-expanded={showAll}
                    onClick={() => {
                        setShowAll((value) => !value);
                        toggleReference.current?.focus();
                    }}
                >
                    {showAll ? 'Show fewer' : `Show all ${statements.length}`}
                </button>
            )}
        </div>
    );
};
