/**
 * @fileoverview The quality strip: a short line of what matters most about a
 * target, for screens that have no room for the whole Data Quality panel.
 *
 * It shows the first warning and the first recommendation in the order the
 * library returned them (from the night's row for the target when a night is
 * given, else from the target report), the night's loss summary, and
 * "Open in Planner". The loss summary is example data until wave L1.
 */

import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { callBackend } from '../../services/backendApi';
import { qualityQueryKeys } from '../../queries/qualityQueryKeys';
import { useTargetReport } from '../../queries/qualityAdvisoryQueries';
import { StatementKind } from '../../types/backendTypes';
import type { TargetStatement } from '../../types/backendTypes';
import { navigateToElement } from '../../utils/displayCoordinator';
import { exampleNightLossSummary } from '../../examples';
import { ExampleDataBadge } from './ExampleDataBadge';
import { QualityPanelState } from './QualityPanelState';
import { StatementItem } from './StatementItem';
import { formatSeconds } from './statementLabels';
import { OBSERVATION_PLANNER_MODE } from './useStatementActions';
import './quality.css';

export interface QualityStripProps {
    /** The target's id. */
    targetId: string | null | undefined;
    /** The observing night, when the strip is for one night. */
    nightId?: string;
}

/**
 * Reads the quality of one stored night.
 * @param nightId The night's id. With none the query does not run.
 * @return The query result with the `NightQuality`.
 */
const useNightQuality = (nightId: string | undefined) =>
    useQuery({
        queryKey: qualityQueryKeys.nightQuality(nightId ?? ''),
        queryFn: () => callBackend('history:night_quality', { session_id: nightId as string }),
        enabled: Boolean(nightId),
    });

/**
 * Takes the first statement of one kind in the order returned.
 * @param statements The statements.
 * @param kind The kind to look for.
 * @return The statement, or undefined.
 */
function firstOfKind(statements: TargetStatement[], kind: StatementKind): TargetStatement | undefined {
    return statements.find((statement) => statement.kind === kind);
}

/**
 * The quality strip for one target.
 * @param props The target id and optional night id.
 * @return The strip.
 */
export const QualityStrip: React.FC<QualityStripProps> = ({ targetId, nightId }) => {
    const report = useTargetReport(nightId ? null : targetId);
    const night = useNightQuality(nightId);
    const source = nightId ? night : report;
    const statements: TargetStatement[] | undefined = nightId
        ? night.data?.targets?.find((row) => row.targetId === targetId)?.statements
        : report.data?.statements;

    const openInPlanner = () => {
        void navigateToElement({
            targetDisplay: OBSERVATION_PLANNER_MODE,
            targetElement: 'dataQuality',
            action: 'openInPlanner',
            payload: { targetId },
        });
    };

    let body: React.ReactNode;
    if (!targetId) body = <QualityPanelState state="noTarget" />;
    else if (source.isLoading) body = <QualityPanelState state="loading" />;
    else if (source.isError) body = <QualityPanelState state="error" onRetry={() => void source.refetch()} />;
    else if (!statements || statements.length === 0) {
        body = <QualityPanelState state="empty" emptyMessage="No warnings or recommendations yet." />;
    } else {
        const warning = firstOfKind(statements, StatementKind.WARNING);
        const recommendation = firstOfKind(statements, StatementKind.RECOMMENDATION);
        body = (
            <ul className="statement-list__items" aria-label="Most important statements">
                {warning && <StatementItem itemKey="strip-warning" statement={warning} />}
                {recommendation && <StatementItem itemKey="strip-recommendation" statement={recommendation} />}
                {!warning && !recommendation && <li className="quality-state">No warnings or recommendations.</li>}
            </ul>
        );
    }

    return (
        <section className="quality-strip" aria-label="Quality strip" data-example="true">
            {body}
            <div className="quality-strip__loss">
                <strong>Tonight&apos;s loss</strong> <ExampleDataBadge />
                <dl className="data-quality__numbers">
                    <dt>Captured</dt>
                    <dd>{formatSeconds(exampleNightLossSummary.capturedSeconds)}</dd>
                    <dt>Set aside</dt>
                    <dd>{formatSeconds(exampleNightLossSummary.setAsideSeconds)}</dd>
                    <dt>Kept-frame deficit</dt>
                    <dd>{formatSeconds(exampleNightLossSummary.keptFrameDeficitSeconds)}</dd>
                    <dt>Cause unknown</dt>
                    <dd>{formatSeconds(exampleNightLossSummary.causeUnknownSeconds)}</dd>
                    <dt>Goal</dt>
                    <dd>{exampleNightLossSummary.goal}</dd>
                    <dt>Reference</dt>
                    <dd>{exampleNightLossSummary.referenceName}</dd>
                </dl>
            </div>
            <button type="button" className="btn btn--tiny quality-button" onClick={openInPlanner}>
                Open in Planner
            </button>
        </section>
    );
};
