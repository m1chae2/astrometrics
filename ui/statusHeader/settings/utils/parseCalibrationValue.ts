/**
 * @file parseCalibrationValue.ts
 * @description Best-effort parser for a camera calibration field (e.g.
 * `clip_ceiling_adu`) as returned by the backend's `system:get_config`,
 * which stringifies TOML inline tables Python-repr-style
 * (`"{'value': 65535.0, 'kind': 'assumed', 'source': '...'}"`). Used only
 * for read-only display in Settings — these fields are never edited here.
 */

export interface CalibrationValue {
    value?: number | string;
    kind?: string;
    source?: string;
}

/** Parse a Python-dict-repr string into a plain object, or null if it doesn't look like one. */
export function parseCalibrationValue(raw: unknown): CalibrationValue | null {
    if (typeof raw !== 'string' || !raw.trim().startsWith('{')) return null;
    try {
        // Python repr uses single quotes; this is a narrow, display-only
        // conversion, not a general Python-literal parser.
        const jsonLike = raw.replace(/'/g, '"');
        const parsed = JSON.parse(jsonLike);
        return typeof parsed === 'object' && parsed !== null ? parsed : null;
    } catch {
        return null;
    }
}
