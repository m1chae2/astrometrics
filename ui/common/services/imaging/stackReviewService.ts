/**
 * @file stackReviewService.ts
 * @description Calls the backend for the checks a person can run on a finished stack: making the
 * preview picture again, reading why frames were set aside, and reading the warnings a stack
 * raised. The two slow checks start a background job; the answer is the result, or the job to
 * follow.
 */

import { callBackend } from '../backendApi';
import { GateStatus, SubjectLevel } from '../../types/backendTypes';
import type {
    JobWarning,
    PreviewRemakeResult,
    ProcessingJob,
    QualityMeasurement,
    SetAsideFrameRecord,
    StackReviewStart,
} from '../../types/backendTypes';

/** The event the image viewer listens for, so it reads a rewritten picture again. */
export const STACK_PICTURE_REMADE_EVENT = 'stackPictureRemade';

/** The imaging stack or the spectral stack of a target. */
export type StackKind = 'imaging' | 'spectral';

/** The choices for one preview run. A choice left out uses the app setting. */
export interface PreviewRemakeOptions {
    /** Turn the Cosmic Clarity denoise on or off for this run. */
    denoise?: boolean;
    /** The denoise strength for this run, from 0 to 1. */
    denoiseStrength?: number;
    /** Turn the star toning on or off for this run. */
    starToning?: boolean;
}

/**
 * Starts making a target's preview picture again.
 * @param targetId The target.
 * @param kind The imaging or spectral stack.
 * @param options The choices for this one run.
 * @return The result when it finished at once, or the job to follow.
 */
export async function startPreviewRemake(
    targetId: string,
    kind: StackKind,
    options: PreviewRemakeOptions = {}
): Promise<StackReviewStart> {
    // Silent: the panel shows the error beside the button, so no second message pops up.
    return callBackend(
        'processing:remake_preview',
        {
            target_id: targetId,
            kind,
            denoise: options.denoise,
            denoise_strength: options.denoiseStrength,
            star_toning: options.starToning,
        },
        { silent: true }
    );
}

/**
 * Reads one background job.
 * @param jobId The job's id.
 * @return The job, or `null` if the backend has no such job.
 */
export async function fetchJob(jobId: string): Promise<ProcessingJob | null> {
    return callBackend('processing:get_job', { job_id: jobId }, { silent: true });
}

/**
 * Lists the frames set aside for a target, with the reason on record for each.
 * @param targetId The target.
 * @return One record per frame in the target's `_excluded` folders.
 */
export async function fetchSetAsideFrames(targetId: string): Promise<SetAsideFrameRecord[]> {
    return callBackend('quality:set_aside_frames', { target: targetId }, { silent: true });
}

/** One warning of a stack, in the fields the Stack Summary shows. */
export interface StackWarning {
    /** The registered metric name, such as `stacking.gate_flat_noise`. */
    metric: string;
    /** The number that was checked, or `null` when the check has no number. */
    value: number | null;
    /** The limit the number was checked against, or `null` when it has none. */
    limit: number | null;
    /** The unit of the value and the limit; empty when it has none. */
    unit: string;
    /** One sentence from the check. */
    reason: string;
    /** What the stacking step did about it, when it acted. */
    actionTaken: string | null;
    /** The validation register entry behind the limit. */
    registerEntry: string | null;
}

/**
 * Turns a warning of a job into the fields the panel shows.
 * @param warning The warning, as `jobs:query` sends it.
 * @return The same warning in panel form.
 */
const stackWarningFromJob = (warning: JobWarning): StackWarning => ({
    metric: warning.metric,
    value: warning.value ?? null,
    limit: warning.limit ?? null,
    unit: warning.unit ?? '',
    reason: warning.reason ?? '',
    actionTaken: warning.action_taken ?? null,
    registerEntry: warning.register_entry ?? null,
});

/**
 * Turns a stored measurement row with a failed check into the fields the panel shows.
 * @param row The row, as `quality:query` sends it.
 * @return The row in panel form.
 */
const stackWarningFromRow = (row: QualityMeasurement): StackWarning => ({
    metric: row.metric,
    value: row.value ?? null,
    limit: row.checkLimit?.value ?? null,
    unit: row.checkLimit?.unit ?? row.unit ?? '',
    reason: row.reason ?? '',
    actionTaken: row.actionTaken ?? null,
    registerEntry: row.checkLimit?.registerEntry ?? null,
});

/**
 * Lists the warnings a stacking job detected.
 * @param jobId The id of the stacking job that made the stack.
 * @return One warning for each failed check of the job; empty when there are none.
 */
export async function fetchStackJobWarnings(jobId: string): Promise<StackWarning[]> {
    const answer = await callBackend(
        'jobs:query', { job_id: jobId, detail: 'warnings' }, { silent: true });
    return (answer.warnings ?? []).map(stackWarningFromJob);
}

/**
 * Lists the failed checks stored for one stack, found by its target and file name. This is for a
 * stack that has no job id (older data), so its warnings are still shown.
 * @param targetId The target.
 * @param stackPath The stack file's path; only its file name is used.
 * @return One warning for each check of that stack whose newest stored result failed.
 */
export async function fetchStoredStackWarnings(
    targetId: string, stackPath: string | null | undefined
): Promise<StackWarning[]> {
    if (!stackPath) return [];
    const fileName = stackPath.split(/[\\/]/).pop() ?? stackPath;
    const rows = await callBackend(
        'quality:query',
        { target_id: targetId, level: SubjectLevel.STACK, library_only: true, latest_only: true },
        { silent: true }
    );
    return (rows ?? [])
        .filter((row) => row.subject.subjectId === `${targetId}/${fileName}` && row.checkOutcome === GateStatus.FAILED)
        .map(stackWarningFromRow);
}

/**
 * Reads the preview remake result out of a finished job.
 * @param job The job.
 * @return The result the job stored, or `null` if it has none.
 */
export function previewRemakeResultOf(job: ProcessingJob): PreviewRemakeResult | null {
    const stored = job.outputMetrics?.result;
    return stored ? (stored as PreviewRemakeResult) : null;
}

/** One stack's measurements, as the backend sends them inside a job result (snake_case keys). */
export interface StackMeasurementsView {
    path: string;
    sky_level?: number | null;
    normalized_noise?: number | null;
    normalized_noise_uncertainty?: number | null;
    not_measured?: Record<string, string>;
    notes?: string[];
}

/** The comparison of two stacks, as the backend sends it inside a job result. */
export interface StackComparisonView {
    before: StackMeasurementsView;
    after: StackMeasurementsView;
    /** One plain sentence for each number. The comparison does not pick a winner. */
    summary: string[];
}

/** The stack report, as the backend sends it inside a job result. */
export interface StackComparisonReportView {
    stack_path: string;
    comparison?: StackComparisonView | null;
    notes?: string[];
}

/**
 * Starts comparing a target's stack with the one before it.
 * @param targetId The target.
 * @param kind The imaging or spectral stack.
 * @return The report when it finished at once, or the job to follow.
 */
export async function startStackComparison(targetId: string, kind: StackKind): Promise<StackReviewStart> {
    return callBackend('quality:measure_stack', { target_id: targetId, kind }, { silent: true });
}

/**
 * Reads the stack comparison report out of a finished job.
 * @param job The job.
 * @return The report the job stored, or `null` if it has none.
 */
export function stackComparisonReportOf(job: ProcessingJob): StackComparisonReportView | null {
    const stored = job.outputMetrics?.result;
    return stored ? (stored as StackComparisonReportView) : null;
}
