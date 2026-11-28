/**
 * @file stackCheck.ts
 * @description Small helpers for the Check Frames report. They turn the backend's stack
 * prediction into the words, counts and chip statuses the report shows. The backend decides what
 * stacking would do; nothing is decided again here.
 */
import type { PredictedCalibration, PredictedFrame, PredictedGroup } from '../../common/types/backendTypes';
import { formatExposureSeconds } from './stackFormatting';

/**
 * Says what the check that sets frames aside is for, so the lists that show its result can open
 * with it. The same frame checks also set aside frames of a different gain or sky level.
 */
export const SET_ASIDE_PURPOSE =
    'Frames are set aside by a cloud and target alignment check: clouds, stars smeared into streaks, or a frame not pointed at the target. Nothing is deleted.';

/** The library folder names that start the part of a calibration path worth showing. */
const CALIBRATION_ROOT_FOLDERS = ['darks', 'biases', 'flats'];

/** How many path segments to show when the path has no known calibration folder. */
const FALLBACK_SEGMENT_COUNT = 4;

/** How well the chosen calibration frames fit, as a chip status. */
export type CalibrationFit = 'ok' | 'warning' | 'error';

/**
 * Shortens a calibration folder to the part that names the set, such as
 * `darks/ZWO ASI 533MM Pro/0.0/30.0`.
 * @param folder The folder's full path.
 * @return The path from the `darks`, `biases` or `flats` folder on, or its last four segments.
 */
export const shortCalibrationFolder = (folder: string): string => {
    const segments = folder.split(/[\\/]/).filter(Boolean);
    const rootIndex = segments.reduce(
        (found, segment, index) => (CALIBRATION_ROOT_FOLDERS.includes(segment) ? index : found), -1);
    return segments.slice(rootIndex >= 0 ? rootIndex : -FALLBACK_SEGMENT_COUNT).join('/');
};

/**
 * Says how well the chosen calibration frames fit.
 * @param part What the library chose for one kind of frame.
 * @return `error` when no frame is used, `warning` when the library worked around a mismatch.
 */
export const calibrationFit = (part: PredictedCalibration): CalibrationFit => {
    if (part.frame_count === 0 || (part.blocking_flags ?? []).length > 0) return 'error';
    return (part.flags ?? []).length > 0 ? 'warning' : 'ok';
};

/**
 * Writes the heading of one exposure group.
 * @param group The group.
 * @return Text such as `30 s, 10 frames, L`, with the night when the group has one.
 */
export const groupLabel = (group: PredictedGroup): string => {
    const frames = `${group.frames_kept} frame${group.frames_kept === 1 ? '' : 's'}`;
    const parts = [formatExposureSeconds(group.exposure_s), frames, group.filter_name];
    if (group.night) parts.push(group.night);
    return parts.join(', ');
};

/** The words for each kind of reason a frame is set aside or flagged. */
const KIND_WORDS: Record<string, string> = {
    clouded: 'Clouds',
    trailed: 'Trailed stars',
    off_field: 'Off the field',
    uneven_sky: 'Uneven sky, kept',
    minority_gain: 'Different gain',
    already_set_aside: 'Set aside before',
    background_split: 'Different sky background',
};

/**
 * Gives the short name of why a frame is listed.
 * @param frame The set-aside or flagged frame.
 * @return A few words, such as `Clouds`.
 */
export const frameKindWords = (frame: PredictedFrame): string => KIND_WORDS[frame.kind] ?? 'Set aside';

/**
 * Counts the set-aside frames of each kind, for the summary line.
 * @param frames The frames a stack would set aside.
 * @return One text such as `2 clouds, 1 trailed stars`, or an empty text for no frames.
 */
export const summarizeSetAside = (frames: PredictedFrame[]): string => {
    const counts = new Map<string, number>();
    frames.forEach((frame) => counts.set(frameKindWords(frame), (counts.get(frameKindWords(frame)) ?? 0) + 1));
    return Array.from(counts, ([words, count]) => `${count} ${words.toLowerCase()}`).join(', ');
};
