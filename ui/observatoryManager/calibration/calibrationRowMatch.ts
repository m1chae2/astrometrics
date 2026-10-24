/**
 * @file calibrationRowMatch.ts
 * @description Decides which rows of the calibration library a quality
 * statement points at. The match is exact on what the statement carries: the
 * frame type, and the camera, gain, offset and binning of its capture setting
 * wherever the statement gives them. A value the statement leaves out is not
 * compared. Nothing is guessed or matched loosely.
 */

import type { CaptureSetting } from '../../common/types/backendTypes';

/** The three kinds of calibration frame the library holds. */
export type CalibrationFrameType = 'dark' | 'bias' | 'flat';

/** One row of the calibration library, with the frame type it came from. */
export interface CalibrationLibraryRow {
    /** Unique key of the row in the list. */
    rowKey: string;
    /** The kind of frame. */
    frameType: CalibrationFrameType;
    /** The camera name. */
    camera: string;
    /** The gain setting as the library reports it (it calls this field `iso`). */
    gain: string | number | null;
    /** The offset, when the library gives one. */
    offset: number | null;
    /** The binning, when the library gives one. */
    binning: string | number | null;
    /** The exposure in seconds (darks only). */
    exposure: number | null;
    /** The filter (flats only). */
    filter: string | null;
    /** How many frames the row holds. */
    count: number;
}

/** What the `showCalibrationRow` action carries (see `useStatementActions`). */
export interface ShowCalibrationRowPayload {
    /** The frame type the statement's proposed action names, or null. */
    calibrationKey: string | null;
    /** The capture setting the statement is about, or null. */
    captureSetting: CaptureSetting | null;
    /** The rule that produced the statement. */
    rule: string;
}

/**
 * Tells whether a value the statement gives equals the library's value.
 * Numbers and numeric text compare by their text form.
 * @param wanted The statement's value (null or undefined means "not given").
 * @param actual The library row's value.
 * @return True when the statement does not give the value, or the two are equal.
 */
function sameWhenGiven(wanted: string | number | null | undefined, actual: string | number | null): boolean {
    if (wanted === null || wanted === undefined) return true;
    if (actual === null || actual === undefined) return false;
    return String(wanted) === String(actual);
}

/**
 * Tells whether the statement's binning equals the library's binning. A
 * binning of 2 equals the library text "2" or "2x2" (the same setting written
 * two ways).
 * @param wanted The statement's binning factor.
 * @param actual The library row's binning.
 * @return True when the statement gives no binning, or the two name the same setting.
 */
function sameBinning(wanted: number | null | undefined, actual: string | number | null): boolean {
    if (wanted === null || wanted === undefined) return true;
    if (actual === null || actual === undefined) return false;
    const text = String(actual).toLowerCase();
    return text === String(wanted) || text === `${wanted}x${wanted}`;
}

/**
 * Finds the library rows that match a `showCalibrationRow` request.
 * @param rows All rows of the library.
 * @param payload What the statement carries.
 * @return The matching rows in library order. Empty when nothing matches, or
 *     when the statement gives neither a frame type nor a capture setting.
 */
export function matchCalibrationRows(
    rows: CalibrationLibraryRow[],
    payload: ShowCalibrationRowPayload,
): CalibrationLibraryRow[] {
    const frameType = payload.calibrationKey;
    const setting = payload.captureSetting;
    if (!frameType && !setting) return [];
    return rows.filter((row) => {
        if (frameType && row.frameType !== frameType) return false;
        if (!setting) return true;
        return (
            row.camera === setting.camera &&
            sameWhenGiven(setting.gainSetting, row.gain) &&
            sameWhenGiven(setting.offset, row.offset) &&
            sameBinning(setting.binning, row.binning)
        );
    });
}

/** A loosely typed row from `calibration:query`. */
type LooseRow = Record<string, unknown>;

/**
 * Reads the first present field of a loose row, since the reply's rows may use
 * snake_case or camelCase names.
 * @param row The loose row.
 * @param names The field names to try, in order.
 * @return The value, or null.
 */
function field(row: LooseRow, ...names: string[]): unknown {
    for (const name of names) {
        if (row[name] !== undefined && row[name] !== null) return row[name];
    }
    return null;
}

/**
 * Turns the rows of one `calibration:query` list into library rows.
 * @param rows The loose rows (or null).
 * @param frameType The kind of frame the list holds.
 * @return The typed rows, in the order returned.
 */
export function toLibraryRows(rows: LooseRow[] | null | undefined, frameType: CalibrationFrameType): CalibrationLibraryRow[] {
    return (rows ?? []).map((row, index) => {
        const gain = field(row, 'iso', 'gain');
        const binning = field(row, 'binning');
        const offset = field(row, 'offset');
        const exposure = field(row, 'exposure');
        const filter = field(row, 'filter');
        const camera = String(field(row, 'camera') ?? '');
        return {
            rowKey: [frameType, camera, gain, offset, binning, exposure, filter, index].map(String).join('|'),
            frameType,
            camera,
            gain: gain === null ? null : (gain as string | number),
            offset: typeof offset === 'number' ? offset : offset === null ? null : Number(offset),
            binning: binning === null ? null : (binning as string | number),
            exposure: typeof exposure === 'number' ? exposure : null,
            filter: filter === null ? null : String(filter),
            count: Number(field(row, 'count') ?? 0),
        };
    });
}
