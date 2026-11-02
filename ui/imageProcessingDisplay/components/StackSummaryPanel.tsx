/**
 * @file StackSummaryPanel.tsx
 * @description The "Stack Summary" block of the Image Processing Display's Data tab.
 * It answers in plain words how a target's current stack was made: how many frames
 * went in and how many were set aside, how many pixels were rejected, how wide the
 * stars are against what the input frames predict, the warnings, and what happened to
 * each exposure group. The numbers are the ones the stacking step saved with the
 * stack, so nothing is measured again. The warnings are the failed checks of the stacking
 * job that made the stack, each with its number and its limit. A stack that has no job id
 * (older data) shows the failed checks stored for its target and file instead. The panel
 * only reports; it does not rate a stack. The whole block is a disclosure that is closed
 * at first and shows one line (frames, date), so it does not push the session frames far
 * down the tab. It remembers nothing between visits.
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

/** The star width numbers, as the backend sends them inside `StackSummary.star_width_px`. */
export interface StarWidthView {
    stack?: number | null;
    expected_from_inputs?: number | null;
    median_of_inputs?: number | null;
}

/** One frame left out of the stack, as the backend sends it inside `StackSummary.skipped_reasons`. */
export interface SkippedFrameView {
    file?: string;
    reason?: string;
}

/**
 * Writes a share of pixels as a percentage with a unit.
 * @param fraction The share, from 0 to 1.
 * @return Text such as `0.8% of pixels`, or `not recorded`.
 */
export const formatPixelShare = (fraction: number | null | undefined): string => {
    if (fraction == null) return 'not recorded';
    const percent = fraction * 100;
    const digits = percent > 0 && percent < 0.1 ? 3 : 1;
    return `${percent.toFixed(digits)}% of pixels`;
};

/**
 * Writes a star width in pixels.
 * @param pixels The width (FWHM), in pixels.
 * @return Text such as `2.31 px`, or `not recorded`.
 */
export const formatStarWidth = (pixels: number | null | undefined): string =>
    pixels == null ? 'not recorded' : `${pixels.toFixed(2)} px`;

export { formatExposureSeconds };

/**
 * Says how far an exposure group was moved to line up with the others.
 * @param group The exposure group.
 * @return Text such as `1.4 px`, `Not moved (reference)`, or a dash.
 */
export const describeGroupShift = (group: ExposureGroupView): string => {
    const shift = group.alignment_shift_pixels;
    if (!shift || shift.length < 2) {
        return group.left_out_reason ? '—' : 'Not moved (reference)';
    }
    return `${Math.hypot(shift[0], shift[1]).toFixed(1)} px`;
};

/**
 * Says whether an exposure group clipped, and where.
 * @param group The exposure group.
 * @return `No`, or what clipped.
 */
export const describeGroupClipping = (group: ExposureGroupView): string => {
    const parts: string[] = [];
    if (group.saturated) parts.push('Bright stars at the camera limit');
    if (group.clipped_at_zero) parts.push('Sky pixels cut off at zero');
    return parts.length > 0 ? parts.join('; ') : 'No';
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

    const starWidth = (summary.star_width_px ?? {}) as StarWidthView;
    const groups = (summary.exposure_groups ?? []) as unknown as ExposureGroupView[];
    const skipped = (summary.skipped_reasons ?? []) as SkippedFrameView[];
    const madeAt = new Date(summary.made_at);
    const madeAtText = Number.isNaN(madeAt.getTime()) ? summary.made_at : madeAt.toLocaleString();

    return (
        <details className="stack-summary">
            <summary className="stack-summary__title">
                <span className="stacked-image-panel__label">
                    {`${label} summary: ${summary.frames_stacked} of ${summary.frames_submitted} frames, `
                        + `made ${madeAtText}`}
                </span>
            </summary>

            <dl className="stack-summary__list">
                <dt>Frames used</dt>
                <dd>{summary.frames_stacked} of {summary.frames_submitted} frames</dd>

                {summary.frames_set_aside_before_stacking != null && (
                    <>
                        <dt>Set aside before stacking</dt>
                        <dd>
                            {summary.frames_set_aside_before_stacking} frames
                            {summary.frames_set_aside_before_stacking > 0 && (
                                <details
                                    className="stack-summary__details"
                                    onToggle={(event) => setShowSetAside(event.currentTarget.open)}
                                >
                                    <summary>Why they were set aside</summary>
                                    {showSetAside && (
                                        <SetAsideFramesList
                                            targetId={targetId}
                                            count={summary.frames_set_aside_before_stacking}
                                            refreshKey={refreshKey}
                                        />
                                    )}
                                </details>
                            )}
                        </dd>
                    </>
                )}

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

                <dt>Pixels rejected</dt>
                <dd>
                    {formatPixelShare(summary.rejected_pixel_fraction)}
                </dd>

                <dt>Star width (FWHM)</dt>
                <dd>
                    {formatStarWidth(starWidth.stack)} in the stack
                    {starWidth.expected_from_inputs != null && (
                        <>, against {formatStarWidth(starWidth.expected_from_inputs)} expected from the input frames</>
                    )}{' '}
                    (the input frames' median is {formatStarWidth(starWidth.median_of_inputs)}).
                </dd>

                {summary.calibration_mismatches ? (
                    <>
                        <dt>Calibration mismatches</dt>
                        <dd>{summary.calibration_mismatches} (frames whose calibration files did not match)</dd>
                    </>
                ) : null}
            </dl>

            <div className="stack-summary__flags">
                <span className="stacked-image-panel__label">Warnings:</span>{' '}
                {warningsFailed ? (
                    <span className="stack-summary__flag" role="alert">Could not read the warnings.</span>
                ) : warnings == null ? (
                    <span className="stack-summary--muted">Reading the warnings...</span>
                ) : warnings.length > 0 ? (
                    <>
                        <span>
                            Each is a check of this stack that failed, with the number measured and its limit.
                        </span>
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
                    <span className="stack-summary--muted">None raised.</span>
                )}
            </div>

            {groups.length > 0 && (
                <table className="stack-summary__groups">
                    <caption>Exposure groups (frames with the same exposure length are stacked together)</caption>
                    <thead>
                        <tr>
                            <th scope="col">Exposure</th>
                            <th scope="col">Frames used</th>
                            <th scope="col">Clipped</th>
                            <th scope="col">Moved to line up</th>
                            <th scope="col">Left out</th>
                        </tr>
                    </thead>
                    <tbody>
                        {groups.map((group) => (
                            <tr key={group.exposure_seconds}>
                                <th scope="row">{formatExposureSeconds(group.exposure_seconds)}</th>
                                <td>{group.frames_stacked} of {group.frames_submitted}</td>
                                <td>{describeGroupClipping(group)}</td>
                                <td>{describeGroupShift(group)}</td>
                                <td>{group.left_out_reason ?? 'No, it is in the stack'}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            )}

            <StackComparisonControl targetId={targetId} kind={kind} />
            <PreviewRemakeControl targetId={targetId} kind={kind} onShowPicture={onShowPicture} />
        </details>
    );
};
