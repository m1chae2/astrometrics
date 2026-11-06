/**
 * @file StackComparisonControl.tsx
 * @description Compares a target's current stack with the stack that came before it. It sits
 * behind a "Compare with the previous stack" disclosure in the Stack Summary block. Both stacks
 * are measured the same way and each number gets one plain sentence. The block does not say
 * which stack is better, because that depends on what changed. A number that could not be
 * measured says so and gives the reason. A target with no previous stack says that instead.
 */
import React from 'react';
import { UpdatingBar } from '../../common/components/Skeleton';
import {
    stackComparisonReportOf,
    startStackComparison,
    type StackComparisonReportView,
    type StackKind,
} from '../../common/services/imaging/stackReviewService';
import { useReviewJob } from '../hooks/useReviewJob';
import { Button } from '../../common/components/Button';

import { Skeleton } from '../../common/components/Skeleton';
interface StackComparisonControlProps {
    /** The target whose stack to compare. */
    targetId: string;
    /** The imaging stack or the spectral stack. */
    kind: StackKind;
}

/** Said when the target has no kept previous stack. */
export const NO_PREVIOUS_STACK = 'There is no previous stack to compare with.';

/**
 * Collects the notes that say how each stack's noise was found.
 * @param report The comparison report.
 * @return One line for each note, labelled with the stack it belongs to.
 */
export const collectComparisonNotes = (report: StackComparisonReportView): string[] => {
    const comparison = report.comparison;
    if (!comparison) return [];
    return [
        ...(comparison.before.notes ?? []).map((note) => `Previous stack: ${note}`),
        ...(comparison.after.notes ?? []).map((note) => `Current stack: ${note}`),
    ];
};

/**
 * The "Compare with the previous stack" disclosure.
 */
export const StackComparisonControl: React.FC<StackComparisonControlProps> = ({ targetId, kind }) => {
    const { state, run } = useReviewJob<StackComparisonReportView>({
        targetId,
        jobType: 'diagnostics',
        readResult: stackComparisonReportOf,
    });
    const busy = state.phase === 'starting' || state.phase === 'running';
    const report = state.phase === 'done' ? state.result : null;
    const notes = report ? collectComparisonNotes(report) : [];

    return (
        <details className="stack-summary__details stack-comparison">
            <summary>Compare with the previous stack</summary>
            <p className="stack-summary--muted">
                Measures the current stack and the one before it the same way. The noise is the
                number the target report uses, measured in the same empty patches of sky. The
                result does not say which stack is better. That depends on what changed.
            </p>
            <Button
                variant="secondary"
                size="small"
                onClick={() => void run(() => startStackComparison(targetId, kind))}
                disabled={busy}
            >
                Compare
            </Button>
            <div className="stack-comparison__status" role="status" aria-live="polite">
                {busy && (
                    <span>
                        Measuring both stacks. If a stack must be plate solved,
                        this can take about ten minutes.
                        <UpdatingBar label="Measuring both stacks" inline />
                    </span>
                )}
                {report && !report.comparison && (
                    <div>
                        <div>{NO_PREVIOUS_STACK}</div>
                        {(report.notes ?? []).map((note) => (
                            <div key={note} className="stack-summary--muted">{note}</div>
                        ))}
                    </div>
                )}
                {report?.comparison && (
                    <div>
                        <ul>
                            {report.comparison.summary.map((line) => (
                                <li key={line}>{line}</li>
                            ))}
                        </ul>
                        {notes.length > 0 && (
                            <details className="stack-summary__details">
                                <summary>How the noise was found</summary>
                                <ul>
                                    {notes.map((note) => (
                                        <li key={note}>{note}</li>
                                    ))}
                                </ul>
                            </details>
                        )}
                    </div>
                )}
            </div>
            {state.phase === 'failed' && (
                <div className="stack-summary--error" role="alert">
                    Could not compare the stacks: {state.message}
                </div>
            )}
        </details>
    );
};
