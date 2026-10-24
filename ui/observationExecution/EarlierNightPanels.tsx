/**
 * @fileoverview The panels for an earlier night, drawn from stored data: the
 * night's statements, one row per target (loss account, needs-attention badge
 * as returned, "Open in Planner"), the target changes as a timeline and the
 * night's guiding summary.
 *
 * There is no live record for an earlier night, so the page says so instead
 * of showing the live verdict. Nothing here sorts or adds up the numbers: the
 * rows come in the order the library returned them and each part of the loss
 * stays apart.
 */

import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { callBackend } from '../common/services/backendApi';
import { qualityQueryKeys } from '../common/queries/qualityQueryKeys';
import { StatementKind } from '../common/types/backendTypes';
import type { GuidingNightSummary, LossAccount, LossShare, NightTargetQuality } from '../common/types/backendTypes';
import { navigateToElement } from '../common/utils/displayCoordinator';
import { QualityPanelState } from '../common/components/quality/QualityPanelState';
import { StatementList } from '../common/components/quality/StatementList';
import { StatementTypeIcon } from '../common/components/quality/StatementTypeIcon';
import { formatSeconds, formatValue, severityWord } from '../common/components/quality/statementLabels';
import { OBSERVATION_PLANNER_MODE } from '../common/components/quality/useStatementActions';
import { formatMoment } from './utils/formatDisplay';

/**
 * Writes a list of loss shares, each apart.
 * @param shares The shares.
 * @return Text such as `Clouds 5 min; Wind 2 min`, or an empty string.
 */
function describeShares(shares: LossShare[] | undefined): string {
    return (shares ?? []).map((share) => `${share.cause} ${formatSeconds(share.seconds)}`).join('; ');
}

/**
 * The loss account of one target, each part apart.
 * @param props The account and the captured time.
 * @return The account, or a note when there is none.
 */
const LossAccountView: React.FC<{ account: LossAccount | null | undefined; capturedSeconds: number }> = ({
    account,
    capturedSeconds,
}) => {
    if (!account) return <p className="loss-account">No loss account for this target.</p>;
    return (
        <dl className="data-quality__numbers loss-account">
            <dt>Captured</dt>
            <dd>{formatSeconds(capturedSeconds)}</dd>
            <dt>Set aside</dt>
            <dd>
                {formatSeconds(account.setAsideSeconds)}
                {describeShares(account.setAside) ? ` (${describeShares(account.setAside)})` : ''}
            </dd>
            <dt>Kept-frame deficit</dt>
            <dd>{describeShares(account.keptFrameDeficit) || 'none listed'}</dd>
            <dt>Cause unknown</dt>
            <dd>
                {formatSeconds(account.causeUnknownSeconds)}
                {account.causeUnknownReason ? ` (${account.causeUnknownReason})` : ''}
            </dd>
            <dt>Reference</dt>
            <dd>{account.referenceName}</dd>
        </dl>
    );
};

/**
 * One target's row.
 * @param props The target's quality for the night.
 * @return The row.
 */
const NightTargetRow: React.FC<{ target: NightTargetQuality }> = ({ target }) => {
    const attention = target.needsAttention;
    const count = attention.warningCount;
    const worst = severityWord(attention.worstSeverity);
    const openInPlanner = () => {
        void navigateToElement({
            targetDisplay: OBSERVATION_PLANNER_MODE,
            targetElement: 'dataQuality',
            action: 'openInPlanner',
            payload: { targetId: target.targetId },
        });
    };
    return (
        <li className="night-target" data-testid={`night-target-${target.targetId}`}>
            <div className="night-target__head">
                <strong>{target.targetId}</strong>
                <span className="night-target__badge">
                    <StatementTypeIcon kind={StatementKind.WARNING} severity={attention.worstSeverity} />{' '}
                    {count === 0 ? 'No warnings' : `${count} ${count === 1 ? 'warning' : 'warnings'}${worst ? `, worst: ${worst}` : ''}`}
                </span>
                <span>
                    {target.framesTaken} frames taken, {target.framesSetAside} set aside
                </span>
                <button
                    type="button"
                    className="btn btn--tiny"
                    aria-label={`Open in Planner: ${target.targetId}`}
                    onClick={openInPlanner}
                >
                    Open in Planner
                </button>
            </div>
            <LossAccountView account={target.lossAccount} capturedSeconds={target.capturedSeconds} />
        </li>
    );
};

/**
 * The guiding summary of the night.
 * @param props The summary.
 * @return The summary view.
 */
const GuidingSummaryView: React.FC<{ summary: GuidingNightSummary }> = ({ summary }) => {
    if (!summary.available) {
        return <p className="quality-state">Not enough data: {summary.reason || 'no guiding data for this night'}.</p>;
    }
    return (
        <dl className="data-quality__numbers">
            <dt>Guiding error per axis</dt>
            <dd>{summary.rmsPerAxisArcsec === null || summary.rmsPerAxisArcsec === undefined ? 'not available' : `${formatValue(summary.rmsPerAxisArcsec)} arcsec`}</dd>
            <dt>Guide frames lost</dt>
            <dd>{formatValue(summary.lostFraction)}</dd>
            <dt>Excursions</dt>
            <dd>{formatValue(summary.excursionCount)}</dd>
            <dt>Analysis</dt>
            <dd>
                {summary.flagged ? 'Flagged' : 'Not flagged'}
                {summary.flagReasons && summary.flagReasons.length > 0 ? `: ${summary.flagReasons.join(', ')}` : ''}
            </dd>
        </dl>
    );
};

/**
 * The target changes of the night as a timeline.
 * @param props The night id.
 * @return The timeline with its states.
 */
const TargetChangesTimeline: React.FC<{ nightId: string }> = ({ nightId }) => {
    const changes = useQuery({
        queryKey: qualityQueryKeys.targetChanges(nightId),
        queryFn: () => callBackend('history:target_changes', { session_id: nightId }),
    });
    if (changes.isLoading) return <QualityPanelState state="loading" />;
    if (changes.isError) {
        return <QualityPanelState state="error" subject="target changes" onRetry={() => void changes.refetch()} />;
    }
    const rows = changes.data ?? [];
    if (rows.length === 0) return <QualityPanelState state="empty" emptyMessage="No target changes recorded for this night." />;
    return (
        <ol className="target-changes" aria-label="Target changes">
            {rows.map((change) => (
                <li key={`${change.source}-${change.time}`}>
                    <time dateTime={new Date(change.time * 1000).toISOString()}>
                        {formatMoment(new Date(change.time * 1000).toISOString())}
                    </time>{' '}
                    {change.targetId ? `Slewed to ${change.targetId}` : 'Slewed to a place with no library target'}
                    {change.note ? ` (${change.note})` : ''}
                </li>
            ))}
        </ol>
    );
};

export interface EarlierNightPanelsProps {
    /** The chosen night's id. */
    nightId: string;
}

/**
 * The earlier-night panels.
 * @param props The night id.
 * @return The panels.
 */
export const EarlierNightPanels: React.FC<EarlierNightPanelsProps> = ({ nightId }) => {
    const night = useQuery({
        queryKey: qualityQueryKeys.nightQuality(nightId),
        queryFn: () => callBackend('history:night_quality', { session_id: nightId }),
    });

    let review: React.ReactNode;
    if (night.isLoading) review = <QualityPanelState state="loading" />;
    else if (night.isError) review = <QualityPanelState state="error" onRetry={() => void night.refetch()} />;
    else if (!night.data || ((night.data.targets ?? []).length === 0 && (night.data.nightStatements ?? []).length === 0)) {
        review = <QualityPanelState state="empty" emptyMessage="No frames for this night. Sync to fetch new frames." />;
    } else {
        const targets = night.data.targets ?? [];
        const statements = night.data.nightStatements ?? [];
        review = (
            <>
                <section className="execution-section" aria-label="Night statements">
                    <h3 className="execution-section__title">Night statements</h3>
                    {statements.length === 0 ? (
                        <p className="quality-state">No statements about the whole night.</p>
                    ) : (
                        <StatementList statements={statements} label="Night statements" />
                    )}
                </section>
                <section className="execution-section" aria-label="Targets of the night">
                    <h3 className="execution-section__title">Targets of the night</h3>
                    <ul className="night-targets">
                        {targets.map((target) => (
                            <NightTargetRow key={target.targetId} target={target} />
                        ))}
                    </ul>
                </section>
                <section className="execution-section" aria-label="Night guiding summary">
                    <h3 className="execution-section__title">Guiding</h3>
                    <GuidingSummaryView summary={night.data.guiding} />
                </section>
            </>
        );
    }

    return (
        <div className="earlier-night">
            <p className="quality-state" data-testid="no-live-record">
                No live record for an earlier night
            </p>
            {review}
            <section className="execution-section" aria-label="Target changes of the night">
                <h3 className="execution-section__title">Target changes</h3>
                <TargetChangesTimeline nightId={nightId} />
            </section>
        </div>
    );
};
