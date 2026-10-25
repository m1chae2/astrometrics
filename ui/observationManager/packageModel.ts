/**
 * @file packageModel.ts
 * @description Small display helpers for observation packages in the Observation Manager:
 * the short exposure text ("L 20 x 60 s"), the confidence word, the conversion between the
 * Image Planner's labels and the package model, and the conversion of a package into the
 * sequencer's item rows. These functions only format or reshape values; they never rank,
 * group or decide anything (the library does that).
 */
import { PackageRefreshCounts } from '../common/queries/packageLibraryQueryKeys';
import {
    DitherConfig,
    ExposureRequest,
    FilterType,
    FrameType,
    ObservationPackage,
    RunState,
} from '../common/types/backendTypes';
import type { StatusChipStatus } from '../common/components/StatusChip';

/** One row of the sequencer's item list, as `addToQueue` expects it. */
export interface SequenceItemRow {
    type: string;
    count: number;
    exposure: number;
    delay: number;
    filter: string;
}

/** Labels shown for each frame type, in the Image Planner's words. */
const FRAME_TYPE_LABELS: Record<FrameType, string> = {
    [FrameType.LIGHT]: 'Light',
    [FrameType.DARK]: 'Dark',
    [FrameType.FLAT]: 'Flat',
    [FrameType.BIAS]: 'Bias',
};

/**
 * Turns the Image Planner's label ("Light") into the package model's frame type.
 * @param label The label chosen in the planner.
 * @return The matching frame type (Light when the label is unknown).
 */
export function frameTypeFromLabel(label: string): FrameType {
    const match = (Object.keys(FRAME_TYPE_LABELS) as FrameType[]).find(
        (frameType) => FRAME_TYPE_LABELS[frameType].toLowerCase() === label.toLowerCase()
    );
    return match ?? FrameType.LIGHT;
}

/**
 * Turns a frame type into the label the Image Planner shows.
 * @param frameType The frame type.
 * @return The label, for example "Light".
 */
export function labelFromFrameType(frameType: FrameType): string {
    return FRAME_TYPE_LABELS[frameType] ?? 'Light';
}

/**
 * Writes one exposure request in short form: "L 20 x 60 s". A calibration frame starts with its
 * type instead of a filter ("Dark 20 x 60 s").
 * @param request The exposure request.
 * @return The short text.
 */
export function formatExposureRequest(request: ExposureRequest): string {
    const lead = request.frameType === FrameType.LIGHT
        ? (request.filter ?? FilterType.NONE)
        : labelFromFrameType(request.frameType);
    return `${lead} ${request.count} x ${request.exposureSec} s`;
}

/**
 * Writes all of a package's exposures in short form, in the order stored, for example
 * "L 20 x 60 s, Ha 10 x 300 s".
 * @param observationPackage The package.
 * @return The short text, or an empty string when the package has no exposures.
 */
export function formatExposureSummary(observationPackage: ObservationPackage): string {
    return (observationPackage.exposure_requests ?? []).map(formatExposureRequest).join(', ');
}

/**
 * Turns the confidence code of a suggestion into the word shown to the user.
 * @param confidence The library's confidence code (measured, partly_measured, design_estimate).
 * @return The word: Measured, Partly measured or Tentative; an unknown code is shown as given.
 */
export function confidenceLabel(confidence: string | undefined): string {
    switch (confidence) {
        case 'measured': return 'Measured';
        case 'partly_measured': return 'Partly measured';
        case 'design_estimate': return 'Tentative';
        default: return confidence ?? 'Tentative';
    }
}

/**
 * Reshapes a package's exposures into the sequencer's item rows (the "Add to Sequence" path).
 * @param observationPackage The package.
 * @return One row per exposure request.
 */
export function packageToSequenceItems(observationPackage: ObservationPackage): SequenceItemRow[] {
    return (observationPackage.exposure_requests ?? []).map((request) => ({
        type: labelFromFrameType(request.frameType),
        count: request.count,
        exposure: request.exposureSec,
        delay: request.delaySec ?? 0,
        filter: request.filter ?? FilterType.NONE,
    }));
}

/**
 * Builds a temporary package from a queued sequence so the editor can modify it.
 * @param sequenceId The queue entry's id.
 * @param targetName The sequence's target.
 * @param items The sequence's item rows.
 * @return A package whose id starts with "queue:".
 */
export function packageFromSequence(
    sequenceId: string,
    targetName: string,
    items: SequenceItemRow[]
): ObservationPackage {
    return {
        id: `queue:${sequenceId}`,
        name: targetName,
        target_id: targetName,
        dither_config: null,
        exposure_requests: items.map((item) => ({
            frameType: frameTypeFromLabel(item.type || 'Light'),
            filter: item.filter as FilterType,
            exposureSec: item.exposure,
            count: item.count,
            delaySec: item.delay || 0,
        })),
    };
}

/** A dither setting with every field present, for the Dither Settings panel. */
export interface DitherValues {
    enabled: boolean;
    everyN: number;
    pixels: number;
}

/**
 * Fills the missing fields of a package's dither setting with the panel's defaults.
 * @param dither The package's dither setting, if any.
 * @return Dither values with every field present.
 */
export function ditherValuesOf(dither: DitherConfig | null | undefined): DitherValues {
    return {
        enabled: dither?.enabled ?? false,
        everyN: dither?.everyNFrames ?? 1,
        pixels: dither?.pixels ?? 2,
    };
}

/**
 * Writes the line shown after a suggestion refresh, for example "Suggestions updated: 1 added, 2 removed".
 * Only the counts that are not zero are named.
 * @param counts The counts the `packages:changed` refresh event carries.
 * @return The line, or null when the refresh changed no package.
 */
export function refreshNoticeText(counts: PackageRefreshCounts): string | null {
    const parts: string[] = [];
    if (counts.added) parts.push(`${counts.added} added`);
    if (counts.updated) parts.push(`${counts.updated} updated`);
    if (counts.deleted) parts.push(`${counts.deleted} removed`);
    if (counts.completed) parts.push(`${counts.completed} completed`);
    if (counts.reverted) parts.push(`${counts.reverted} reopened`);
    return parts.length > 0 ? `Suggestions updated: ${parts.join(', ')}` : null;
}

/** The word and chip look shown for each run state of a queue entry. */
const RUN_STATE_DISPLAY: Record<RunState, { word: string; status: StatusChipStatus }> = {
    [RunState.IMAGING]: { word: 'Imaging now', status: 'info' },
    [RunState.UPCOMING]: { word: 'Not imaging', status: 'neutral' },
    [RunState.IN_PROGRESS]: { word: 'Not imaging', status: 'neutral' },
    [RunState.COMPLETE]: { word: 'Done', status: 'ok' },
    [RunState.PARTLY_DONE]: { word: 'Partly done', status: 'warning' },
    [RunState.NOT_RUN]: { word: 'Not run', status: 'neutral' },
    [RunState.UNKNOWN]: { word: 'Progress unknown', status: 'warning' },
};

/**
 * Turns the library's run state of a queue entry into the word and chip look to show.
 * @param state The run state the library returned.
 * @return The word ("Imaging now", "Not imaging", "Done", "Not run", ...) and the chip status.
 */
export function runStateDisplay(state: RunState): { word: string; status: StatusChipStatus } {
    return RUN_STATE_DISPLAY[state] ?? { word: 'Progress unknown', status: 'warning' };
}

/**
 * Formats the time of the progress read for the strip header, as local clock time.
 * @param isoTime The `as_of` time the library returned (ISO 8601), if any.
 * @return Text such as "22:41", or null when there is no valid time.
 */
export function formatAsOfTime(isoTime: string | undefined): string | null {
    if (!isoTime) return null;
    const parsed = new Date(isoTime);
    if (Number.isNaN(parsed.getTime())) return null;
    return parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
