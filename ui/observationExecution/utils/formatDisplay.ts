/**
 * @fileoverview Text formats for Observation Execution: clock times, "25 min
 * ago", night labels and sync lines. These functions only format values for
 * display. They decide nothing about quality or about which night is which.
 */

import { formatPlanningTime } from '../../common/components/PlanningTimeIndicator';
import { formatSeconds } from '../../common/components/quality/statementLabels';

/**
 * Writes a time for display: the clock time, plus the date when it is not today.
 * @param isoText A time as ISO 8601 text.
 * @param now The current moment.
 * @return Text such as `23:40` or `Oct 23, 23:40`.
 */
export function formatMoment(isoText: string, now: Date = new Date()): string {
    return formatPlanningTime(isoText, now);
}

/**
 * Writes how long ago a time was.
 * @param isoText A time as ISO 8601 text.
 * @param now The current moment.
 * @return Text such as `25 min ago`, or `just now` for a time in the future or under a second ago.
 */
export function formatAge(isoText: string, now: Date = new Date()): string {
    const moment = new Date(isoText);
    if (Number.isNaN(moment.getTime())) return '';
    const seconds = (now.getTime() - moment.getTime()) / 1000;
    if (seconds < 1) return 'just now';
    return `${formatSeconds(seconds)} ago`;
}

/**
 * Writes one sync line, such as `Logs: last sync 23:40, 25 min ago`.
 * @param label What was synced, such as "Logs" or "Frames".
 * @param isoText When it was last synced, or null for never.
 * @param now The current moment.
 * @return The line, or `Logs: never synced` when there is no time.
 */
export function formatSyncLine(label: string, isoText: string | null, now: Date = new Date()): string {
    if (!isoText) return `${label}: never synced`;
    return `${label}: last sync ${formatMoment(isoText, now)}, ${formatAge(isoText, now)}`;
}

/**
 * Writes the label of one night in the night selector.
 * @param nightDate The night's date as `YYYY-MM-DD` text.
 * @param targetCount How many targets the night imaged.
 * @return Text such as `2026-10-23 (Fri), 3 targets`.
 */
export function formatNightLabel(nightDate: string, targetCount: number): string {
    const [year, month, day] = nightDate.split('-').map(Number);
    const parsed = new Date(year, (month || 1) - 1, day || 1);
    const weekday = Number.isNaN(parsed.getTime())
        ? ''
        : ` (${parsed.toLocaleDateString('en-US', { weekday: 'short' })})`;
    const noun = targetCount === 1 ? 'target' : 'targets';
    return `${nightDate}${weekday}, ${targetCount} ${noun}`;
}
