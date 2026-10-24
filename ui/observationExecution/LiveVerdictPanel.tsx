/**
 * @fileoverview The live session verdict: a state word, its reason, the
 * Tentative chip, and the alerts as one sentence plus a count, with
 * "Details" for the raw flags.
 *
 * The verdict and the alert sentence are example data until wave L1. The
 * flags behind "Details" are the real `history:live_session_status` flags.
 * The verdict text and the alert count are the only live regions (polite):
 * a screen reader hears them only when their text changes.
 */

import React, { useId, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { callBackend } from '../common/services/backendApi';
import { qualityQueryKeys } from '../common/queries/qualityQueryKeys';
import { exampleLiveVerdict } from '../common/examples';
import { Confidence } from '../common/types/backendTypes';
import { ConfidenceChip } from '../common/components/quality/ConfidenceChip';
import { ExampleDataBadge } from '../common/components/quality/ExampleDataBadge';
import { QualityPanelState } from '../common/components/quality/QualityPanelState';

/**
 * Reads the raw live status, only while the details are open.
 * @param enabled Whether to fetch.
 * @return The query result.
 */
const useLiveSessionStatus = (enabled: boolean) =>
    useQuery({
        queryKey: qualityQueryKeys.liveSessionStatus(),
        queryFn: () => callBackend('history:live_session_status', {}),
        enabled,
    });

/**
 * The live verdict panel.
 * @return The panel.
 */
export const LiveVerdictPanel: React.FC = () => {
    const [detailsOpen, setDetailsOpen] = useState(false);
    const detailsId = useId();
    const status = useLiveSessionStatus(detailsOpen);
    const verdict = exampleLiveVerdict;

    let details: React.ReactNode = null;
    if (detailsOpen) {
        if (status.isLoading) details = <QualityPanelState state="loading" />;
        else if (status.isError) {
            details = <QualityPanelState state="error" subject="live session status" onRetry={() => void status.refetch()} />;
        } else {
            const flags = status.data?.flags ?? [];
            details = (
                <div id={detailsId} className="live-verdict__details">
                    {flags.length === 0 ? (
                        <p>No session flags are recorded.</p>
                    ) : (
                        <ul aria-label="Session flags">
                            {flags.map((flag) => (
                                <li key={flag}>{flag}</li>
                            ))}
                        </ul>
                    )}
                </div>
            );
        }
    }

    return (
        <section className="execution-section live-verdict" aria-label="Live session verdict" data-example="true">
            <h3 className="execution-section__title">
                Live session verdict <ExampleDataBadge />
            </h3>
            <div className="live-verdict__line" aria-live="polite">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden="true" focusable="false">
                    <circle cx="12" cy="12" r="9" />
                    <path d="M7.5 12.5 L11 16 L16.5 8.5" />
                </svg>
                <strong>{verdict.state}</strong>
                <span>{verdict.reason}</span>
            </div>
            <div className="live-verdict__chip">
                <ConfidenceChip confidence={Confidence.DESIGN_ESTIMATE} />
            </div>
            <div className="live-verdict__alerts">
                <span aria-live="polite">
                    {verdict.alertSentence} ({verdict.alertCount} {verdict.alertCount === 1 ? 'alert' : 'alerts'})
                </span>
                <button
                    type="button"
                    className="btn btn--tiny"
                    aria-expanded={detailsOpen}
                    aria-controls={detailsId}
                    onClick={() => setDetailsOpen((value) => !value)}
                >
                    Details
                </button>
            </div>
            {details}
        </section>
    );
};
