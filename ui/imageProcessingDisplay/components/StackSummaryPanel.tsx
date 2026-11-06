/**
 * @file StackSummaryPanel.tsx
 * @description The "Stack Summary" block of the Image Processing Display's Data tab.
 * It answers in plain words how a target's current stack was made. The closed line shows
 * how many frames were stacked. Inside are the stack date, the frames set aside or left out
 * (each shown only when there are some, with the reasons), the warnings, and, when the
 * stack has more than one exposure length, a closed list of the exposure groups with
 * short notes. The numbers are the ones the stacking step saved with the stack, so nothing
 * is measured again. The warnings are the failed checks of the stacking job that made the
 * stack, each with its number and its limit; a stack with no job id (older data) shows the
 * failed checks stored for its target and file instead. A check that passed gets no row.
 * The panel only reports; it does not rate a stack. It remembers nothing between visits.
 */
import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { callBackend } from '../../common/services/backendApi';
import { BackendError } from '../../common/services/backendError';
import type { StackSummary } from '../../common/types/backendTypes';
import { PreviewRemakeControl } from './PreviewRemakeControl';
import { StackComparisonControl } from './StackComparisonControl';
import { SetAsideFramesList } from './SetAsideFramesList';
import { formatExposureSeconds } from '../utils/stackFormatting';
import {
    fetchStackJobWarnings,
    fetchStoredStackWarnings,
    type StackWarning,
} from '../../common/services/imaging/stackReviewService';
import { describeStackWarningTitle, describeWarningNumbers } from '../utils/stackWarnings';

/** Which stack to summarize. */
export type StackSummaryKind = 'imaging' | 'spectral';

interface StackSummaryPanelProps {
    /** The selected target's id. With none, the panel shows nothing. */
    targetId: string | null;
    /** The imaging stack (default) or the spectral stack. */
    kind?: StackSummaryKind;
    /** Changes when a stack finishes or the library reloads, so the panel reads again. */
    refreshKey?: string | number;
    /** Opens the stack's picture in the viewer, called after a new picture is made and on display. */
    onShowPicture?: (stackPath: string) => void;
}

/** One exposure group, as the backend sends it inside `StackSummary.exposure_groups`. */
export interface ExposureGroupView {
    exposure_seconds: number;
    frames_submitted: number;
    frames_stacked: number;
    saturated?: boolean;
    clipped_at_zero?: boolean;
    /** How far the group was moved to line up, as [rows, columns] in pixels. `null` for the reference group. */
    alignment_shift_pixels?: number[] | null;
    left_out_reason?: string | null;
}

/** One frame left out of the stack, as the backend sends it inside `StackSummary.skipped_reasons`. */
export interface SkippedFrameView {
    file?: string;
    reason?: string;
}

export { formatExposureSeconds };

/**
 * Writes the short notes for one exposure group: why it was left out, or what to know about it.
 * @param group The exposure group.
 * @return Notes joined by "; ", or a dash when nothing applies.
 */
export const describeGroupNotes = (group: ExposureGroupView): string => {
    const notes: string[] = [];
    if (group.left_out_reason) {
        notes.push(group.left_out_reason);
    } else if (group.frames_stacked < group.frames_submitted) {
        notes.push('some frames left out');
    }
    if (group.saturated) notes.push('bright stars clipped');
    if (group.clipped_at_zero) notes.push('sky cut off at zero');
    return notes.length > 0 ? notes.join('; ') : '—';
};

/**
 * Shows one stack's summary for one target, or an empty note when it has no stack.
 * A target with no saved stack summary is a normal case, so the backend's
 * `not_found` answer becomes a muted note and not an error.
 */
export const StackSummaryPanel: React.FC<StackSummaryPanelProps> = ({
    targetId, kind = 'imaging', refreshKey, onShowPicture,
}) => {
    const label = kind === 'spectral' ? 'Spectral stack' : 'Imaging stack';
    // The set-aside list is read only after a person opens it.
    const [showSetAside, setShowSetAside] = React.useState(false);
    const { data: summary, isLoading, isError } = useQuery<StackSummary | null>({
        queryKey: ['stackSummary', { target_id: targetId, kind }, refreshKey ?? ''],
        queryFn: async () => {
            try {
                return await callBackend(
                    'processing:stack_summary', { target: targetId as string, kind }, { silent: true });
            } catch (error) {
                if (error instanceof BackendError && error.code === 'not_found') return null;
                throw error;
            }
        },
        enabled: Boolean(targetId),
    });

    // The warnings come from the stacking job that made the stack. Without a job id, or when the
    // job has none on record, the stored failed checks of this stack are read instead.
    const { data: warnings, isError: warningsFailed } = useQuery<StackWarning[]>({
        queryKey: ['stackWarnings', { target_id: targetId, kind }, summary?.job_id ?? '', summary?.made_at ?? '',
            refreshKey ?? ''],
        queryFn: async () => {
            const fromJob = summary?.job_id ? await fetchStackJobWarnings(summary.job_id) : [];
            if (fromJob.length > 0) return fromJob;
            return fetchStoredStackWarnings(targetId as string, summary?.stack_path);
        },
        enabled: Boolean(targetId) && Boolean(summary),
    });

    if (!targetId) return null;
    if (isLoading) {
        return <div className="stack-summary stack-summary--muted">Loading {label.toLowerCase()} summary...</div>;
    }
    if (isError) {
        return (
            <div className="stack-summary stack-summary--error" role="alert">
                Could not load the {label.toLowerCase()} summary.
            </div>
        );
    }
    if (!summary) {
        return (
            <div className="stack-summary stack-summary--muted">
                {label}: no saved summary yet. Stack this target to see one.
            </div>
        );
    }

    const groups = (summary.exposure_groups ?? []) as unknown as ExposureGroupView[];
    const skipped = (summary.skipped_reasons ?? []) as SkippedFrameView[];
    const madeAt = new Date(summary.made_at);
    const madeAtText = Number.isNaN(madeAt.getTime()) ? summary.made_at : madeAt.toLocaleString();

    // Warnings stay readable while the block is closed: the count goes on the closed line.
    const warningCount = warnings?.length ?? 0;

    return (
        <details className="stack-summary">
            <summary className="stack-summary__title">
                <span className="stacked-image-panel__label">
                    {`${label} summary: ${summary.frames_stacked} of ${summary.frames_submitted} frames`}
                </span>
                {warningCount > 0 && (
                    <span className="stack-summary__flag" data-testid="stack-warning-count">
                        {` · ${warningCount} ${warningCount === 1 ? 'warning' : 'warnings'}`}
                    </span>
                )}
            </summary>

            <dl className="stack-summary__list">
                <dt>Stack date</dt>
                <dd>{madeAtText}</dd>

                {(summary.frames_set_aside_before_stacking ?? 0) > 0 && (
                    <>
                        <dt>Set aside before stacking</dt>
                        <dd>
                            {summary.frames_set_aside_before_stacking} frames
                            <details
                                className="stack-summary__details"
                                onToggle={(event) => setShowSetAside(event.currentTarget.open)}
                            >
                                <summary>Why they were set aside</summary>
                                {showSetAside && (
                                    <SetAsideFramesList
                                        targetId={targetId}
                                        count={summary.frames_set_aside_before_stacking as number}
                                        refreshKey={refreshKey}
                                    />
                                )}
                            </details>
                        </dd>
                    </>
                )}

                {summary.frames_skipped > 0 && (
                    <>
                        <dt>Left out while stacking</dt>
                        <dd>
                            {summary.frames_skipped} frames
                            {skipped.length > 0 && (
                                <details className="stack-summary__details">
                                    <summary>Why</summary>
                                    <ul>
                                        {skipped.map((frame, index) => (
                                            <li key={`${frame.file}-${index}`}>
                                                {frame.file}: {frame.reason}
                                            </li>
                                        ))}
                                    </ul>
                                    {summary.frames_skipped > skipped.length && (
                                        <div className="stack-summary--muted">
                                            Showing the first {skipped.length} of {summary.frames_skipped} frames.
                                        </div>
                                    )}
                                </details>
                            )}
                        </dd>
                    </>
                )}
            </dl>

            <div className="stack-summary__flags">
                <span className="stacked-image-panel__label">Warnings:</span>{' '}
                {warningsFailed ? (
                    <span className="stack-summary__flag" role="alert">Could not read the warnings.</span>
                ) : warnings == null ? (
                    <span className="stack-summary--muted">Reading the warnings...</span>
                ) : warnings.length > 0 ? (
                    <>
                        <ul className="stack-summary__flag-list">
                            {warnings.map((warning, index) => {
                                const numbers = describeWarningNumbers(warning);
                                return (
                                    <li key={`${warning.metric}-${index}`} className="stack-summary__flag">
                                        <strong>{describeStackWarningTitle(warning.metric)}.</strong>{' '}
                                        {numbers || warning.reason}
                                        {' '}Outcome: failed.
                                        {warning.actionTaken ? ` What was done: ${warning.actionTaken}.` : ''}
                                        {warning.registerEntry ? ` Limit source: ${warning.registerEntry}.` : ''}
                                    </li>
                                );
                            })}
                        </ul>
                    </>
                ) : (
                    <span className="stack-summary--muted">none</span>
                )}
            </div>

            {groups.length > 1 && (
                <details className="stack-summary__details">
                    <summary>{`Exposure groups (${groups.length})`}</summary>
                    <table className="stack-summary__groups">
                        <thead>
                            <tr>
                                <th scope="col">Exposure</th>
                                <th scope="col">Frames used</th>
                                <th scope="col">Notes</th>
                            </tr>
                        </thead>
                        <tbody>
                            {groups.map((group) => (
                                <tr key={group.exposure_seconds}>
                                    <th scope="row">{formatExposureSeconds(group.exposure_seconds)}</th>
                                    <td>{group.frames_stacked} of {group.frames_submitted}</td>
                                    <td>{describeGroupNotes(group)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </details>
            )}

            <StackComparisonControl targetId={targetId} kind={kind} />
            <PreviewRemakeControl targetId={targetId} kind={kind} onShowPicture={onShowPicture} />
        </details>
    );
};
