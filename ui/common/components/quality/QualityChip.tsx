/**
 * @fileoverview The quality chip: the statements about one science result
 * (for example a star's spectrum or light curve), in compact form.
 *
 * It shows the first statement with its type icon, word and confidence chip,
 * and "Show all N" for the rest, in the order the library returned them. A statement with
 * evidence rows or a calibration action also offers "Evidence" and "Show in calibration library".
 */

import React from 'react';
import { useResultStatements } from '../../queries/qualityAdvisoryQueries';
import { QualityPanelState } from './QualityPanelState';
import { StatementList } from './StatementList';
import { useStatementActions } from './useStatementActions';
import './quality.css';

export interface QualityChipProps {
    /** The target's id. */
    targetId: string | null | undefined;
    /** The id of the result's object, such as a star. */
    objectId: string | null | undefined;
}

/**
 * The quality chip for one science result.
 * @param props The target and object ids.
 * @return The chip.
 */
export const QualityChip: React.FC<QualityChipProps> = ({ targetId, objectId }) => {
    const query = useResultStatements(targetId, objectId);
    // Hooks cannot be conditional, so a missing target gives the actions an empty id; no statement shows then.
    const actions = useStatementActions(targetId ?? '');
    let body: React.ReactNode;
    if (!targetId || !objectId) body = <QualityPanelState state="noTarget" />;
    else if (query.isLoading) body = <QualityPanelState state="loading" />;
    else if (query.isError) {
        body = <QualityPanelState state="error" subject="quality statements" onRetry={() => void query.refetch()} />;
    } else if (!query.data || query.data.length === 0) {
        body = <QualityPanelState state="empty" emptyMessage="No quality statements about this result." />;
    } else {
        body = <StatementList statements={query.data} topCount={1} actions={actions} label="Quality of this result" />;
    }
    return <div className="quality-chip">{body}</div>;
};
