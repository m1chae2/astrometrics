/**
 * @file jobSubtitle.ts
 * @description Builds the subtitle of a Recent Jobs card from what the backend recorded on the job.
 * An analysis job names its steps ("Astrometry → Photometry"). A stacking job names the kind of
 * stack ("Luminance" or "Spectral"). A job with nothing recorded gets no subtitle from here, so the
 * card keeps showing its message.
 */
import { ProcessingJob } from '../../common/types/backendTypes';

/** The words shown for each analysis stage the backend records. */
const STAGE_LABELS: Record<string, string> = {
    astrometry: 'Astrometry',
    photometry: 'Photometry',
    spectroscopy: 'Spectroscopy',
    spectral_variation: 'Spectral Variation',
    asteroids: 'Asteroids',
};

/** The words shown for each kind of stack. */
const STACK_KIND_LABELS: Record<string, string> = {
    imaging: 'Luminance',
    spectral: 'Spectral',
};

/** The longest skip reason, in characters, that still fits after a stage name on one line. */
const MAX_SKIP_REASON_LENGTH = 24;

/** The steps the backend records on an analysis job (`inputMetrics.steps`). */
interface RecordedSteps {
    requested?: string[];
    prerequisites?: string[];
    run?: string[];
    skipped?: Record<string, string>;
    finished?: boolean;
}

/**
 * Gives a stage its display name.
 *
 * @param {string} stage - The stage name the backend uses, such as `spectral_variation`.
 * @returns {string} The name shown, such as `Spectral Variation`.
 */
const stageLabel = (stage: string): string => STAGE_LABELS[stage] ?? stage;

/**
 * Writes a skipped stage as `Photometry (skipped: needs astrometry)`. The reason is kept only
 * when its first clause is short enough for one line.
 *
 * @param {string} stage - The skipped stage.
 * @param {string} reason - The reason the backend gave.
 * @returns {string} The stage with its short note.
 */
const skippedLabel = (stage: string, reason: string): string => {
    const shortReason = reason.split(':')[0].trim();
    if (shortReason && shortReason.length <= MAX_SKIP_REASON_LENGTH) {
        return `${stageLabel(stage)} (skipped: ${shortReason})`;
    }
    return `${stageLabel(stage)} (skipped)`;
};

/**
 * Joins prerequisite stages and chosen stages: prerequisites first, an arrow, then the stages
 * the user chose, joined by plus signs.
 *
 * @param {string[]} prerequisites - Display names of the stages added first.
 * @param {string[]} chosen - Display names of the stages the user chose.
 * @returns {string} For example `Astrometry → Photometry + Spectroscopy`.
 */
const joinSteps = (prerequisites: string[], chosen: string[]): string => {
    const chosenText = chosen.join(' + ');
    if (prerequisites.length === 0) return chosenText;
    const prerequisiteText = prerequisites.join(' + ');
    return chosenText ? `${prerequisiteText} → ${chosenText}` : prerequisiteText;
};

/**
 * Builds the subtitle for an analysis job from its recorded steps.
 *
 * @param {RecordedSteps} steps - The steps recorded on the job.
 * @returns {string | null} The subtitle, or `null` when no stage is named.
 */
const analysisSubtitle = (steps: RecordedSteps): string | null => {
    const requested = steps.requested ?? [];
    const skipped = steps.skipped ?? {};
    if (!steps.finished) {
        const added = (steps.prerequisites ?? []).map(stageLabel);
        const chosen = requested.map(stageLabel);
        return chosen.length > 0 ? joinSteps(added, chosen) : null;
    }
    const run = steps.run ?? [];
    const prerequisitesRun = (steps.prerequisites ?? []).filter((stage) => run.includes(stage));
    const chosenRun = run.filter((stage) => !prerequisitesRun.includes(stage));
    const skippedChosen = Object.keys(skipped).filter((stage) => requested.includes(stage));
    const chosen = [
        ...chosenRun.map(stageLabel),
        ...skippedChosen.map((stage) => skippedLabel(stage, skipped[stage])),
    ];
    const text = joinSteps(prerequisitesRun.map(stageLabel), chosen);
    return text || null;
};

/**
 * Builds the subtitle for a Recent Jobs card, or `null` when the card should keep the job's
 * message: a failed job (it shows its error), a job type with nothing recorded, and jobs saved
 * before the steps were recorded.
 *
 * @param {Pick<ProcessingJob, 'jobType' | 'status' | 'inputMetrics'>} job - The job.
 * @returns {string | null} The subtitle, or `null`.
 */
export const jobStepsSubtitle = (
    job: Pick<ProcessingJob, 'jobType' | 'status' | 'inputMetrics'>,
): string | null => {
    if (job.status === 'failed') return null;
    const metrics = job.inputMetrics ?? {};
    if (job.jobType === 'analysis') {
        const steps = metrics.steps as RecordedSteps | undefined;
        return steps ? analysisSubtitle(steps) : null;
    }
    if (job.jobType === 'stacking') {
        const kinds = (metrics.stackKinds as string[] | undefined) ?? [];
        const labels = kinds.map((kind) => STACK_KIND_LABELS[kind] ?? kind);
        return labels.length > 0 ? labels.join(' + ') : null;
    }
    return null;
};

/**
 * Gives the text a Recent Jobs card shows under a job: the steps or stack kind when the job
 * recorded them, else its message without the closing punctuation (jobs saved before the short
 * messages end with a period).
 *
 * @param {Pick<ProcessingJob, 'jobType' | 'status' | 'inputMetrics' | 'message'>} job - The job.
 * @returns {string | null} The text, or `null` when there is nothing to show.
 */
export const jobCardText = (
    job: Pick<ProcessingJob, 'jobType' | 'status' | 'inputMetrics' | 'message'>,
): string | null => {
    const steps = jobStepsSubtitle(job);
    if (steps) return steps;
    const message = (job.message ?? '').replace(/[.…\s]+$/u, '').trim();
    return message || null;
};
