/**
 * @file calibrationLine.ts
 * @description Turns the target's light-frame groups into the report shown in the Calibration
 * panel of the Data tab. The backend runs the same dark, flat and bias selection that stacking
 * runs and sends the result with each group, so a check mark here means stacking finds a match.
 * Nothing is matched again in the UI. The report lists the groups that miss a calibration frame
 * type, and the groups that only have a near match (frames at another setting), so the panel can
 * answer one question first: am I missing calibration frames?
 */
import type { CalibrationGroupMatch, GroupedFrameStat } from '../../common/types/backendTypes';
import { formatExposureSeconds } from './stackFormatting';

/** How well one kind of calibration frame fits one group. */
export type CalibrationPartStatus = 'ok' | 'near' | 'missing';

/** The result for one kind of calibration frame (darks, flats or bias) in one group. */
export interface CalibrationPart {
    /** The plural name of the frames: "darks", "flats" or "bias". */
    noun: string;
    /** Found, found only at another setting (a near match), or not found. */
    status: CalibrationPartStatus;
    /** How many frames stacking would use. */
    count: number;
    /** The reason in words: why nothing was found, or why the match is only near. Empty when ok. */
    reason: string;
}

/** The calibration result for one group of light frames. */
export interface CalibrationGroupReport {
    /** The name of the group, such as "L 20 s" or "120 s". */
    label: string;
    /** The camera settings of the group in short words, such as "gain 800, offset 0, 1×1". */
    settings: string;
    /** How many light frames are in the group. */
    lightCount: number;
    /** Darks, flats and bias, in that order. */
    parts: CalibrationPart[];
}

/** What the Calibration panel shows. */
export interface CalibrationReport {
    /** Every group, sorted by filter and exposure. */
    groups: CalibrationGroupReport[];
    /** The groups that have at least one kind of frame missing. */
    missing: CalibrationGroupReport[];
    /** The groups with no missing kind, but at least one near match to check. */
    nearMatches: CalibrationGroupReport[];
}

/** Whether a filter name means "no filter". */
const isNoFilter = (filter: string | null | undefined): boolean =>
    !filter || filter === 'None' || filter === 'None (No Filter)';

/** Writes a numeric setting without a useless ".0" ("10.0" becomes "10"); other text is kept. */
const plainNumber = (value: string | null | undefined): string => {
    if (value === null || value === undefined || value === '') return '';
    const number = Number(value);
    return Number.isNaN(number) ? value : String(number);
};

/**
 * Reads one backend match into a part.
 * @param match What the stacker would use, or missing when the backend sent nothing.
 * @param noun The plural name of the frames.
 * @return The part with its status and reason.
 */
const readPart = (match: CalibrationGroupMatch | null | undefined, noun: string): CalibrationPart => {
    const count = match?.count ?? 0;
    if (count <= 0) {
        return { noun, status: 'missing', count: 0, reason: match?.problem || `no ${noun} found` };
    }
    const fallbacks = match?.fallbacks ?? [];
    if (fallbacks.length > 0) {
        return { noun, status: 'near', count, reason: fallbacks.join('; ') };
    }
    return { noun, status: 'ok', count, reason: '' };
};

/**
 * Builds the calibration report.
 * @param groups The target's light-frame groups with the stacker's calibration match.
 * @return The report, or null when the target has no light frames.
 */
export const buildCalibrationReport = (groups: GroupedFrameStat[]): CalibrationReport | null => {
    if (groups.length === 0) return null;
    const sorted = [...groups].sort((a, b) =>
        a.filter.localeCompare(b.filter) || parseFloat(a.exposure) - parseFloat(b.exposure));
    const reports = sorted.map((group): CalibrationGroupReport => {
        const seconds = parseFloat(group.exposure);
        const exposure = Number.isNaN(seconds) ? group.exposure : formatExposureSeconds(seconds);
        const label = isNoFilter(group.filter) ? exposure : `${group.filter} ${exposure}`;
        const settings = [
            group.iso ? `gain ${plainNumber(group.iso)}` : '',
            group.offset ? `offset ${plainNumber(group.offset)}` : '',
            group.binning ? group.binning.replace('x', '×') : '',
        ].filter(Boolean).join(', ');
        return {
            label,
            settings,
            lightCount: group.count,
            parts: [
                readPart(group.darkMatch, 'darks'),
                readPart(group.flatMatch, 'flats'),
                readPart(group.biasMatch, 'bias'),
            ],
        };
    });
    const hasStatus = (report: CalibrationGroupReport, status: CalibrationPartStatus): boolean =>
        report.parts.some((part) => part.status === status);
    return {
        groups: reports,
        missing: reports.filter((report) => hasStatus(report, 'missing')),
        nearMatches: reports.filter((report) => !hasStatus(report, 'missing') && hasStatus(report, 'near')),
    };
};

/** Capitalizes the first letter of a sentence. */
const capitalize = (text: string): string => text.charAt(0).toUpperCase() + text.slice(1);

/** Writes a temperature with a real minus sign, so "-6.6" reads as "−6.6". */
const temperatureText = (value: string): string => value.replace('-', '\u2212');

/**
 * Turns one near-match reason from the library into a short phrase that follows the frame name,
 * such as "are 3.4 °C colder than the lights (−10.0 vs −6.6 °C)". The library writes four
 * wordings (a dark at another temperature, a light with no temperature, frames at another gain
 * and offset, and the "frames binned differently" block, which is a miss and not a near match),
 * so each one is matched by its fixed text. Any other wording is returned as null.
 * @param reason The full sentence from the library.
 * @return The short phrase, or null when the wording is not known.
 */
export const shortFallbackPhrase = (reason: string): string | null => {
    const temperature = reason.match(
        /No dark frame was within [\d.]+ C of .*? taken at (-?[\d.]+) C, so the nearest dark master, taken at (-?[\d.]+) C .*?, was applied, ([\d.]+) C away/,
    );
    if (temperature) {
        const [, lights, darks, away] = temperature;
        const direction = Number(darks) < Number(lights) ? 'colder' : 'warmer';
        return `are ${away} \u00b0C ${direction} than the lights `
            + `(${temperatureText(darks)} vs ${temperatureText(lights)} \u00b0C)`;
    }
    if (/temperature of .* is not recorded and the library holds darks/.test(reason)) {
        return 'were used without a temperature check (the lights have no temperature)';
    }
    const settings = reason.match(
        /No \w+ frames matched gain (\S+) and offset ([^\s,]+), so \w+ frames taken at (.+?) were applied/,
    );
    if (settings) {
        return `were taken at ${settings[3]}, not gain ${plainNumber(settings[1])}, offset ${plainNumber(settings[2])}`;
    }
    return null;
};

/**
 * Writes the one short "Check" line for a group's near matches.
 * @param group The group.
 * @return A line such as "Darks for L 30 s are 3.4 °C colder than the lights (−10.0 vs −6.6 °C)".
 */
export const nearMatchLine = (group: CalibrationGroupReport): string =>
    group.parts.filter((part) => part.status === 'near').map((part) => {
        const phrase = shortFallbackPhrase(part.reason);
        const subject = `${capitalize(part.noun)} for ${group.label}`;
        return phrase ? `${subject} ${phrase}` : `${subject}: ${capitalize(part.reason)}`;
    }).join('. ');

/** The full library sentences of a group's near matches, for the row's tooltip. */
export const nearMatchDetail = (group: CalibrationGroupReport): string =>
    group.parts.filter((part) => part.status === 'near').map((part) => part.reason).join('\n');
