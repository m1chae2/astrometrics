/**
 * @file calibrationLine.ts
 * @description Turns the target's light-frame groups into the one "Calibration" line of the Data
 * tab. The backend runs the same dark, flat and bias selection that stacking runs and sends the
 * result with each group, so a check mark here means stacking finds a match. Nothing is matched
 * again in the UI.
 */
import type { CalibrationGroupMatch, GroupedFrameStat } from '../../common/types/backendTypes';
import { formatExposureSeconds } from './stackFormatting';

/** The line, its warning flag and the longer text shown on hover. */
export interface CalibrationLine {
    /** The short text of the line, without the "Calibration:" label. */
    text: string;
    /** True when something is missing or only a near match, so the line is shown as a warning. */
    hasWarning: boolean;
    /** The full detail for each group, one line per group, for the hover text. */
    detail: string;
}

/** The short and the long wording of one calibration kind for one group. */
interface PartText {
    short: string;
    long: string;
    isClean: boolean;
}

/** Whether a filter name means "no filter". */
const isNoFilter = (filter: string | null | undefined): boolean =>
    !filter || filter === 'None' || filter === 'None (No Filter)';

/**
 * Writes "70 darks ✓", "20 bias ⚠" (a near match was used) or "no bias at gain 100, offset 10 ✗".
 * @param match What the stacker would use, or missing when the backend sent nothing.
 * @param noun The plural name of the frames.
 * @return The short text, the long text with the reasons, and whether the match is clean.
 */
const describe = (match: CalibrationGroupMatch | null | undefined, noun: string): PartText => {
    const count = match?.count ?? 0;
    if (count <= 0) {
        const reason = match?.problem || `no ${noun}`;
        return { short: `${reason} ✗`, long: `${reason} ✗`, isClean: false };
    }
    const fallbacks = match?.fallbacks ?? [];
    if (fallbacks.length > 0) {
        return { short: `${count} ${noun} ⚠`, long: `${count} ${noun} ⚠ (${fallbacks.join('; ')})`, isClean: false };
    }
    return { short: `${count} ${noun} ✓`, long: `${count} ${noun} ✓`, isClean: true };
};

/**
 * Builds the calibration line.
 * @param groups The target's light-frame groups with the stacker's calibration match.
 * @return The line, or null when the target has no light frames.
 */
export const buildCalibrationLine = (groups: GroupedFrameStat[]): CalibrationLine | null => {
    if (groups.length === 0) return null;
    const sorted = [...groups].sort((a, b) =>
        a.filter.localeCompare(b.filter) || parseFloat(a.exposure) - parseFloat(b.exposure));
    const showLabel = sorted.length > 1 || sorted.some((g) => !isNoFilter(g.filter));
    let hasWarning = false;
    const bits: string[] = [];
    const details: string[] = [];
    for (const group of sorted) {
        const seconds = parseFloat(group.exposure);
        const exposure = Number.isNaN(seconds) ? group.exposure : formatExposureSeconds(seconds);
        const label = isNoFilter(group.filter) ? exposure : `${group.filter} ${exposure}`;
        const parts = [
            describe(group.darkMatch, 'darks'),
            describe(group.flatMatch, 'flats'),
            describe(group.biasMatch, 'bias'),
        ];
        if (parts.some((p) => !p.isClean)) hasWarning = true;
        const prefix = showLabel ? `${label}: ` : '';
        bits.push(prefix + parts.map((p) => p.short).join(' · '));
        const settings = [`gain ${group.iso}`, group.offset ? `offset ${group.offset}` : '',
            group.binning ? `binning ${group.binning}` : ''].filter(Boolean).join(', ');
        details.push(`${group.count} lights, ${label} (${settings}): ${parts.map((p) => p.long).join(' · ')}`);
    }
    return { text: bits.join('  |  '), hasWarning, detail: details.join('\n') };
};
