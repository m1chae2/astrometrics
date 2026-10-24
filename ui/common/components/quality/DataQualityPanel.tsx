/**
 * @fileoverview The Data Quality panel: what the library says about one
 * target's data.
 *
 * It draws the target report as returned: the frames and time per capture
 * setting (the two effective-time numbers each labeled, never merged), the
 * loss account with set-aside time and kept-frame deficit kept apart, the
 * statements, the notes and context, the starting point for a new target and
 * the forecast checks. It sorts and sums nothing.
 */

import React from 'react';
import { SectionPanel } from '../SectionPanel';
import { useTargetReport } from '../../queries/qualityAdvisoryQueries';
import type {
    CaptureSettingSummary,
    ForecastCheck,
    LossAccount,
    LossShare,
    StartingPoint,
} from '../../types/backendTypes';
import { ConfidenceChip } from './ConfidenceChip';
import { QualityPanelState } from './QualityPanelState';
import { StatementList } from './StatementList';
import { formatSeconds, formatValue } from './statementLabels';
import { useStatementActions } from './useStatementActions';
import './quality.css';

export interface DataQualityPanelProps {
    /** The current target's id. */
    targetId: string | null | undefined;
}

/**
 * Writes the capture setting of a summary as short text.
 * @param summary The capture setting summary.
 * @return The text.
 */
function settingLabel(summary: CaptureSettingSummary): string {
    const setting = summary.captureSetting;
    const parts: string[] = [setting.camera];
    if (setting.filterName) parts.push(setting.filterName);
    if (setting.exposureSeconds !== null && setting.exposureSeconds !== undefined) {
        parts.push(`${formatValue(setting.exposureSeconds)} s`);
    }
    if (setting.gainSetting !== null && setting.gainSetting !== undefined) parts.push(`gain ${setting.gainSetting}`);
    return parts.join(', ');
}

/**
 * Shows a list of loss shares.
 * @param props The heading and the shares.
 * @return The list, or nothing when there are no shares.
 */
const LossShares: React.FC<{ heading: string; shares: LossShare[] | undefined }> = ({ heading, shares }) => {
    if (!shares || shares.length === 0) return null;
    return (
        <div className="loss-account__group">
            <strong>{heading}</strong>
            <ul>
                {shares.map((share) => (
                    <li key={share.cause}>
                        {share.cause}: {formatSeconds(share.seconds)}
                        {share.recoverableWithTime ? ' (recoverable with more time)' : ' (not recoverable with time)'}
                        {share.modelDerived ? ', from a model' : ', measured'}
                    </li>
                ))}
            </ul>
        </div>
    );
};

/**
 * Shows one loss account, with its parts apart.
 * @param props The account.
 * @return The account view.
 */
const LossAccountView: React.FC<{ account: LossAccount }> = ({ account }) => (
    <div className="loss-account">
        <p>
            Compared with {account.referenceName} for the goal &quot;{String(account.goal).replace(/_/g, ' ')}&quot;.
        </p>
        <p>Set-aside time: {formatSeconds(account.setAsideSeconds)}</p>
        <LossShares heading="Set aside, by cause" shares={account.setAside} />
        <LossShares heading="Kept-frame deficit, by cause" shares={account.keptFrameDeficit} />
        <p>
            Cause unknown:{' '}
            {account.causeUnknownSeconds !== null && account.causeUnknownSeconds !== undefined
                ? formatSeconds(account.causeUnknownSeconds)
                : 'not available'}
            {account.causeUnknownReason ? ` (${account.causeUnknownReason})` : ''}
        </p>
        <p>
            Transparency {account.transparencyIncluded ? 'included' : 'not included'}
            {account.transparencyReason ? `: ${account.transparencyReason}` : ''}
        </p>
    </div>
);

/**
 * Shows the starting point for a new target.
 * @param props The starting point.
 * @return The view.
 */
const StartingPointView: React.FC<{ startingPoint: StartingPoint }> = ({ startingPoint }) => (
    <section className="data-quality__section" aria-label="Starting point">
        <h4>
            Starting point <ConfidenceChip confidence={startingPoint.confidence} />
        </h4>
        <p>
            Exposure:{' '}
            {startingPoint.exposureSeconds !== null && startingPoint.exposureSeconds !== undefined
                ? `${formatValue(startingPoint.exposureSeconds)} s`
                : 'not available'}
            {startingPoint.exposureReason ? ` (${startingPoint.exposureReason})` : ''}
        </p>
        <p>
            Clipping cap:{' '}
            {startingPoint.clippingCapSeconds !== null && startingPoint.clippingCapSeconds !== undefined
                ? `${formatValue(startingPoint.clippingCapSeconds)} s`
                : 'not available'}
            {startingPoint.clippingCapReason ? ` (${startingPoint.clippingCapReason})` : ''}
        </p>
    </section>
);

/**
 * Shows one forecast check.
 * @param props The check.
 * @return The view.
 */
const ForecastCheckView: React.FC<{ check: ForecastCheck }> = ({ check }) => (
    <li>
        Outcome: {String(check.outcome).replace(/_/g, ' ')}. Forecast when planned{' '}
        {formatValue(check.forecastWhenPlanned.noiseChangeFraction)} (tolerance{' '}
        {formatValue(check.forecastWhenPlanned.tolerance)}), measured {formatValue(check.measured.noiseChangeFraction)}{' '}
        (tolerance {formatValue(check.measured.tolerance)}). {check.paired ? 'Paired' : 'Not paired'}; weighting mode{' '}
        {check.weightingMode}.{check.reason ? ` ${check.reason}` : ''}
    </li>
);

/**
 * The Data Quality panel for one target.
 * @param props The target id.
 * @return The panel.
 */
export const DataQualityPanel: React.FC<DataQualityPanelProps> = ({ targetId }) => {
    const query = useTargetReport(targetId);
    const actions = useStatementActions(targetId ?? '');
    const report = query.data;

    let body: React.ReactNode;
    if (!targetId) {
        body = <QualityPanelState state="noTarget" />;
    } else if (query.isLoading) {
        body = <QualityPanelState state="loading" />;
    } else if (query.isError) {
        body = <QualityPanelState state="error" onRetry={() => void query.refetch()} />;
    } else if (
        !report ||
        ((report.captureSettings ?? []).length === 0 &&
            (report.statements ?? []).length === 0 &&
            !report.startingPoint &&
            (report.notes ?? []).length === 0)
    ) {
        body = <QualityPanelState state="empty" />;
    } else {
        body = (
            <>
                <p className="data-quality__attention">
                    {report.needsAttention.warningCount} warnings need attention.
                </p>
                {(report.captureSettings ?? []).map((summary, index) => (
                    <section key={index} className="data-quality__section" aria-label={`Capture setting ${settingLabel(summary)}`}>
                        <h4>{settingLabel(summary)}</h4>
                        <dl className="data-quality__numbers">
                            <dt>Frames taken</dt>
                            <dd>{summary.framesTaken}</dd>
                            <dt>Frames set aside</dt>
                            <dd>{summary.framesSetAside}</dd>
                            <dt>Nights</dt>
                            <dd>{summary.nights}</dd>
                            <dt>Captured time</dt>
                            <dd>{formatSeconds(summary.capturedSeconds)}</dd>
                            <dt>Weight-effective time</dt>
                            <dd>{formatSeconds(summary.weightEffectiveSeconds)}</dd>
                            <dt>Efficiency-effective time</dt>
                            <dd>{formatSeconds(summary.efficiencyEffectiveSeconds)}</dd>
                        </dl>
                        {summary.lossAccount && <LossAccountView account={summary.lossAccount} />}
                    </section>
                ))}
                {(report.statements ?? []).length > 0 && (
                    <section className="data-quality__section" aria-label="Statements">
                        <StatementList statements={report.statements ?? []} topCount={3} actions={actions} />
                    </section>
                )}
                {report.startingPoint && <StartingPointView startingPoint={report.startingPoint} />}
                {(report.forecastChecks ?? []).length > 0 && (
                    <section className="data-quality__section" aria-label="Forecast checks">
                        <h4>Forecast checks</h4>
                        <ul>
                            {(report.forecastChecks ?? []).map((check, index) => (
                                <ForecastCheckView key={index} check={check} />
                            ))}
                        </ul>
                    </section>
                )}
                {(report.notes ?? []).length > 0 && (
                    <section className="data-quality__section" aria-label="Notes">
                        <h4>Notes</h4>
                        <ul>
                            {(report.notes ?? []).map((note) => (
                                <li key={note}>{note}</li>
                            ))}
                        </ul>
                    </section>
                )}
                {((report.context ?? []).length > 0 || report.contextOmittedReason) && (
                    <section className="data-quality__section" aria-label="Context">
                        <h4>Context</h4>
                        <ul>
                            {(report.context ?? []).map((reading, index) => (
                                <li key={index}>
                                    {reading.metric} ({reading.level}): {formatValue(reading.value)} {reading.unit ?? ''}
                                </li>
                            ))}
                        </ul>
                        {report.contextOmittedReason && <p>{report.contextOmittedReason}</p>}
                    </section>
                )}
            </>
        );
    }

    return (
        <SectionPanel title="Data Quality" id="data-quality-panel" className="data-quality">
            <div className="data-quality__body" aria-busy={query.isLoading || undefined}>
                {body}
            </div>
        </SectionPanel>
    );
};
