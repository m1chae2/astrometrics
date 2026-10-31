/**
 * @file calibrationHealth.tsx
 * @description How the calibration library shows the health of a set of
 * darks, biases or flats. The health comes from `calibration:assess`, one
 * record for each set, and is joined to a table row by `rowKey`, the key the
 * counts of `calibration:query` carry too. A set is "OK" when every check that
 * ran passed, "Warning" when one failed, and "Not checked" when nothing could
 * be checked. "Not checked" is a different answer from "OK": the reason is
 * shown beside it. The word and a shape say it, not color alone.
 */

import React from 'react';
import { StatusChip, type StatusChipStatus } from '../../common/components/StatusChip';
import type { CalibrationSetHealth, GateResult } from '../../common/types/backendTypes';

/** The words a check name is shown as. */
const CHECK_LABELS: Record<string, string> = {
    calibration_frame_count: 'Number of frames',
    dark_zero_pixel_fraction: 'Cut off at zero',
    bias_zero_pixel_fraction: 'Cut off at zero',
    dark_light_leak: 'Light on the sensor',
    dark_ceiling_share: 'Saturated pixels',
    dark_level: 'Brightness level',
    bias_level: 'Brightness level',
    dark_noise: 'Noise',
    bias_noise: 'Noise',
    flat_level: 'Flat brightness',
    flat_noise: 'Flat noise',
};

/** The most files of concern one detail view lists. */
const MAXIMUM_CONCERNS_SHOWN = 5;

/** How bad each status is, so the worst of several sets can be picked. */
const STATUS_RANK: Record<string, number> = { warning: 2, good: 1, not_checked: 0 };

/**
 * Gives the word and chip shape for a set's status.
 * @param status The status of a set: good, warning or not_checked.
 * @return The chip status and the word to show.
 */
export function healthWord(status: string | null | undefined): { chip: StatusChipStatus; word: string } {
    if (status === 'good') return { chip: 'ok', word: 'OK' };
    if (status === 'warning') return { chip: 'warning', word: 'Warning' };
    return { chip: 'neutral', word: 'Not checked' };
}

/**
 * Picks the worst record among several for one row key (two telescopes can share a flat row).
 * @param records The health records that share one key.
 * @return The record with the worst status, or undefined when there is none.
 */
export function worstHealth(records: CalibrationSetHealth[]): CalibrationSetHealth | undefined {
    return records.reduce<CalibrationSetHealth | undefined>(
        (worst, record) =>
            worst === undefined || (STATUS_RANK[String(record.status)] ?? 0) > (STATUS_RANK[String(worst.status)] ?? 0)
                ? record
                : worst,
        undefined,
    );
}

/**
 * Joins the health records to table rows by row key.
 * @param records The answer of `calibration:assess`. Anything that is not a list is treated as empty.
 * @return A map from row key to the worst record of that key.
 */
export function healthByRowKey(records: unknown): Map<string, CalibrationSetHealth> {
    const grouped = new Map<string, CalibrationSetHealth[]>();
    if (Array.isArray(records)) {
        for (const record of records as CalibrationSetHealth[]) {
            if (!record || typeof record.rowKey !== 'string') continue;
            grouped.set(record.rowKey, [...(grouped.get(record.rowKey) ?? []), record]);
        }
    }
    const joined = new Map<string, CalibrationSetHealth>();
    for (const [key, list] of grouped) {
        const worst = worstHealth(list);
        if (worst) joined.set(key, worst);
    }
    return joined;
}

/**
 * Shows a set's health as a chip with the word.
 * @param props.health The set's health, or undefined before any check ran.
 * @return The chip, or a dash.
 */
export const HealthChip: React.FC<{ health: CalibrationSetHealth | undefined }> = ({ health }) => {
    if (!health) return <span aria-label="Not yet checked">—</span>;
    const { chip, word } = healthWord(String(health.status));
    return <StatusChip status={chip} label={word} />;
};

/**
 * Words a check's result in one short phrase.
 * @param check One check of a set.
 * @return For example "Passed", "Failed: ..." or "Not checked: ...".
 */
function checkPhrase(check: GateResult): string {
    const detail = check.detail ? `: ${check.detail}` : '';
    if (String(check.status) === 'passed') return 'Passed';
    if (String(check.status) === 'failed') return `Failed${detail}`;
    return `Not checked${detail}`;
}

/**
 * Shows the plain sentences behind a set's health: the summary, each check and the files of concern.
 * @param props.health The set's health.
 * @return The detail block.
 */
export const HealthDetail: React.FC<{ health: CalibrationSetHealth }> = ({ health }) => {
    const concerns = health.framesOfConcern ?? [];
    return (
        <div className="calibration-health-detail" data-testid="calibration-health-detail">
            <p className="calibration-health-summary">{health.summary}</p>
            <ul className="calibration-health-checks">
                {(health.checks ?? []).map((check) => (
                    <li key={check.name}>
                        <strong>{CHECK_LABELS[check.name] ?? check.name}</strong> — {checkPhrase(check)}
                    </li>
                ))}
            </ul>
            {concerns.length > 0 && (
                <div className="calibration-health-files">
                    <span>Files to look at:</span>
                    <ul>
                        {concerns.slice(0, MAXIMUM_CONCERNS_SHOWN).map((concern) => (
                            <li key={concern.path} title={concern.reason}>
                                {concern.path.split('/').pop()} — {concern.reason}
                            </li>
                        ))}
                    </ul>
                    {concerns.length > MAXIMUM_CONCERNS_SHOWN && (
                        <span>and {concerns.length - MAXIMUM_CONCERNS_SHOWN} more</span>
                    )}
                </div>
            )}
        </div>
    );
};
