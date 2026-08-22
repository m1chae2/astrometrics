/**
 * @fileoverview Client-side validation for RA/Dec coordinate entry.
 * Aligns with the Google TypeScript Style Guide.
 *
 * Mirrors what the backend parser accepts so the user sees a mistake while
 * typing rather than as a server error: sexagesimal with any of the usual
 * separators (`12 30 45`, `12:30:45`, `12h30m45s`) or a bare decimal. Right
 * ascension is interpreted in hours and declination in degrees, matching the
 * units the backend parses each with.
 */

/** Splits a coordinate string into its numeric components. */
function splitComponents(value: string): string[] {
    return value
        .replace(/[hHmMsSdD°′″'":]/g, ' ')
        .trim()
        .split(/\s+/)
        .filter((part) => part.length > 0);
}

/**
 * Converts a sexagesimal or decimal coordinate string to a single number.
 *
 * @param value Raw user input.
 * @return The value in its leading unit (hours for RA, degrees for Dec), or
 *     null if it is not parseable.
 */
function parseSexagesimal(value: string): number | null {
    const components = splitComponents(value);
    if (components.length === 0 || components.length > 3) return null;

    const isNegative = /^\s*-/.test(value);

    let total = 0;
    for (let index = 0; index < components.length; index++) {
        const parsed = Number(components[index]);
        if (!Number.isFinite(parsed)) return null;

        // Minutes and seconds are magnitudes; the sign belongs to the whole
        // value and is taken from the leading component only.
        const magnitude = Math.abs(parsed);
        if (index > 0 && magnitude >= 60) return null;

        total += magnitude / 60 ** index;
    }

    return isNegative ? -total : total;
}

/**
 * Validates a right ascension entry.
 *
 * @param value Raw user input; an empty string is treated as "not supplied".
 * @return An error message, or null when the value is acceptable.
 */
export function validateRightAscension(value: string): string | null {
    const trimmed = value.trim();
    if (!trimmed) return null;

    const hours = parseSexagesimal(trimmed);
    if (hours === null) {
        return 'RA must be sexagesimal (e.g. 09:55:33) or decimal hours';
    }
    if (hours < 0 || hours > 24) {
        return 'RA must be between 0 and 24 hours';
    }
    return null;
}

/**
 * Validates a declination entry.
 *
 * @param value Raw user input; an empty string is treated as "not supplied".
 * @return An error message, or null when the value is acceptable.
 */
export function validateDeclination(value: string): string | null {
    const trimmed = value.trim();
    if (!trimmed) return null;

    const degrees = parseSexagesimal(trimmed);
    if (degrees === null) {
        return 'Dec must be sexagesimal (e.g. +69:03:55) or decimal degrees';
    }
    if (degrees < -90 || degrees > 90) {
        return 'Dec must be between -90 and +90 degrees';
    }
    return null;
}
