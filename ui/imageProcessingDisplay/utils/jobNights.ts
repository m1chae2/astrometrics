/**
 * @file jobNights.ts
 * @description Small pure helpers that sort processing jobs into observing nights.
 * An observing night runs from local noon to the next local noon, so a session
 * that crosses midnight stays together. All times use the viewer's local clock.
 */
import { ProcessingJob } from '../../common/types/backendTypes';

/** One observing night and the jobs created during it. */
export interface JobNight {
    /** Local date of the noon that starts the night, as `YYYY-MM-DD`. Unique per night. */
    nightKey: string;
    /** Heading for the night, for example `Wed Nov 4`. */
    label: string;
    /** The jobs created during the night, newest first. */
    jobs: ProcessingJob[];
}

const WEEKDAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/**
 * Finds the start of the observing night that contains a moment.
 *
 * @param {Date} moment - Any moment in time.
 * @returns {Date} Local noon on the same day, or on the day before when the moment is before noon.
 */
export function getNightStart(moment: Date): Date {
    const start = new Date(moment.getFullYear(), moment.getMonth(), moment.getDate(), 12, 0, 0, 0);
    if (moment.getTime() < start.getTime()) {
        start.setDate(start.getDate() - 1);
    }
    return start;
}

/**
 * Formats a moment as a local ISO time without a time zone, for the backend's `before` cutoff.
 * The backend stores job times the same way, so the two compare correctly as text.
 *
 * @param {Date} moment - The moment to format.
 * @returns {string} Text such as `2026-11-05T12:00:00`.
 */
export function toLocalIsoTime(moment: Date): string {
    const pad = (value: number): string => String(value).padStart(2, '0');
    return `${moment.getFullYear()}-${pad(moment.getMonth() + 1)}-${pad(moment.getDate())}`
        + `T${pad(moment.getHours())}:${pad(moment.getMinutes())}:${pad(moment.getSeconds())}`;
}

/**
 * Builds a short heading for the night that starts at a given noon.
 *
 * @param {Date} nightStart - Local noon that starts the night.
 * @returns {string} Text such as `Wed Nov 4`.
 */
export function formatNightLabel(nightStart: Date): string {
    return `${WEEKDAY_NAMES[nightStart.getDay()]} ${MONTH_NAMES[nightStart.getMonth()]} ${nightStart.getDate()}`;
}

/**
 * Tells whether a job was created during the night that contains `now`.
 *
 * @param {ProcessingJob} job - The job to test.
 * @param {Date} now - The current time.
 * @returns {boolean} True when the job was created since the current night began.
 */
export function isFromCurrentNight(job: ProcessingJob, now: Date): boolean {
    if (!job.createdAt) return false;
    const createdTime = new Date(job.createdAt).getTime();
    if (Number.isNaN(createdTime)) return false;
    return createdTime >= getNightStart(now).getTime();
}

/**
 * Groups jobs into observing nights, newest night first. Jobs with no usable
 * creation time are left out because they cannot be placed on a night.
 *
 * @param {ProcessingJob[]} jobs - Jobs in any order.
 * @returns {JobNight[]} One entry per night, each with its jobs newest first.
 */
export function groupJobsByNight(jobs: ProcessingJob[]): JobNight[] {
    const nightsByKey = new Map<string, JobNight & { startTime: number }>();
    for (const job of jobs) {
        if (!job.createdAt) continue;
        const created = new Date(job.createdAt);
        if (Number.isNaN(created.getTime())) continue;
        const nightStart = getNightStart(created);
        const nightKey = toLocalIsoTime(nightStart).slice(0, 10);
        let night = nightsByKey.get(nightKey);
        if (!night) {
            night = { nightKey, label: formatNightLabel(nightStart), jobs: [], startTime: nightStart.getTime() };
            nightsByKey.set(nightKey, night);
        }
        night.jobs.push(job);
    }
    const nights = Array.from(nightsByKey.values()).sort((a, b) => b.startTime - a.startTime);
    return nights.map(({ nightKey, label, jobs: nightJobs }) => ({
        nightKey,
        label,
        jobs: [...nightJobs].sort(
            (a, b) => new Date(b.createdAt as string).getTime() - new Date(a.createdAt as string).getTime()
        ),
    }));
}
