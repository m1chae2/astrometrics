/**
 * @file calibrationLine.ts
 * @description Turns the target's light-frame groups into the report shown in the Calibration
 * panel of the Data tab. The backend runs the same dark, flat and bias selection that stacking
 * runs and sends the result with each group, so a check mark here means stacking finds a match.
 * Nothing is matched again in the UI. The report lists the groups that miss a calibration frame
 * type, and the groups that only have a near match (frames at another setting), so the panel can
 * answer one question first: am I missing calibration frames? The full library sentence of each near
 * match stays in the `reason` of its part, shown as the tooltip of its cell in the all-groups table.
 */
import type { CalibrationGroupMatch, GroupedFrameStat, StackConfigurationRef } from '../../common/types/backendTypes';
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

/** The camera and optic the Calibration section covers. */
export interface CalibrationScope {
    /** The camera name. */
    camera: string;
    /** The optic's focal length in whole millimeters, or null when the scope has no optic. */
    focalLengthMm: number | null;
    /** The scope in words, such as "ZWO ASI 533MM Pro@405mm". */
    label: string;
}

/**
 * Chooses the camera and optic the calibration section follows. A stack or an analysis uses one
 * camera and one optic, so the section covers the same ones: the File Browser's camera and optic when
 * it is filtered to a camera, otherwise the target's current stack configuration.
 * @param browserCamera The File Browser's camera, or "All".
 * @param browserOptic The File Browser's optic as text of whole millimeters, or "" for none.
 * @param stackConfiguration The camera and optic of the target's current stack, if it has one.
 * @return The scope, or null when nothing narrows it (then every group is shown).
 */
export const chooseCalibrationScope = (
    browserCamera: string,
    browserOptic: string,
    stackConfiguration: StackConfigurationRef | null | undefined,
): CalibrationScope | null => {
    let camera = '';
    let focalLengthMm: number | null = null;
    if (browserCamera && browserCamera !== 'All') {
        camera = browserCamera;
        const optic = Number(browserOptic);
        focalLengthMm = browserOptic && Number.isFinite(optic) ? Math.round(optic) : null;
    } else if (stackConfiguration?.camera) {
        camera = stackConfiguration.camera;
        focalLengthMm = stackConfiguration.focalLengthMm ?? null;
    }
    if (!camera) return null;
    return { camera, focalLengthMm, label: focalLengthMm === null ? camera : `${camera}@${focalLengthMm}mm` };
};

/**
 * Keeps the groups of the scope's camera and optic. Groups from other cameras and optics are left out.
 * A group with no optic recorded is kept when the camera matches.
 * @param groups The target's light-frame groups.
 * @param scope The camera and optic to keep, or null to keep every group.
 * @return The groups in the scope.
 */
export const filterGroupsByScope = (
    groups: GroupedFrameStat[],
    scope: CalibrationScope | null,
): GroupedFrameStat[] => {
    if (!scope) return groups;
    return groups.filter((group) =>
        group.camera === scope.camera
        && (scope.focalLengthMm === null
            || group.focalLengthMm === null
            || group.focalLengthMm === undefined
            || group.focalLengthMm === scope.focalLengthMm));
};
