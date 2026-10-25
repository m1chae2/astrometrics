/**
 * @fileoverview The live session verdict: a state word, its reason, the
 * Tentative chip, and the alerts as one sentence plus a count.
 *
 * The verdict and the alert sentence are example data until wave L1. The raw
 * session flags are not shown here; they are in `history:live_session_status`
 * for anyone who reads them through the API. The verdict text and the alert
 * count are the only live regions (polite): a screen reader hears them only
 * when their text changes.
 */

import React from 'react';
import { exampleLiveVerdict } from '../common/examples';
import { Confidence } from '../common/types/backendTypes';
import { ConfidenceChip } from '../common/components/quality/ConfidenceChip';
import { ExampleDataBadge } from '../common/components/quality/ExampleDataBadge';
import { ExecutionPanel } from './ExecutionPanel';

/**
 * The live verdict panel.
 * @return The panel.
 */
export const LiveVerdictPanel: React.FC = () => {
    const verdict = exampleLiveVerdict;

    return (
        <ExecutionPanel title="Live session verdict" headerContent={<ExampleDataBadge />} example className="live-verdict">
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
            </div>
        </ExecutionPanel>
    );
};
