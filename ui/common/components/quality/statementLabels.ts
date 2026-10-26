/**
 * @fileoverview Words and number formats for the quality components.
 *
 * Every statement type, severity and confidence level has a word here, so a
 * screen never tells them apart by color alone. The functions only format for
 * display; they decide nothing about quality.
 */

import { Confidence, Severity, StatementKind } from '../../types/backendTypes';

/**
 * Gives the word for a statement type.
 * @param kind The statement's kind.
 * @return The word shown beside the type icon.
 */
export function statementKindWord(kind: StatementKind): string {
    switch (kind) {
        case StatementKind.WARNING:
            return 'Warning';
        case StatementKind.RECOMMENDATION:
            return 'Recommendation';
        case StatementKind.RESULT:
            return 'Result';
        case StatementKind.CONSTRAINT:
            return 'Limit';
        case StatementKind.INSUFFICIENT_DATA:
            return 'Not enough data';
        default:
            return String(kind);
    }
}

/**
 * Gives the word for a severity.
 * @param severity The statement's severity, if it has one.
 * @return The word, or an empty string when there is no severity.
 */
export function severityWord(severity: Severity | null | undefined): string {
    switch (severity) {
        case Severity.INFO:
            return 'Info';
        case Severity.ADVICE:
            return 'Advice';
        case Severity.WARNING:
            return 'Warning';
        default:
            return '';
    }
}

/**
 * Gives the word for a confidence level.
 * @param confidence The statement's confidence.
 * @return "Measured", "Partly measured" or "Tentative".
 */
export function confidenceWord(confidence: Confidence): string {
    switch (confidence) {
        case Confidence.MEASURED:
            return 'Measured';
        case Confidence.PARTLY_MEASURED:
            return 'Partly measured';
        case Confidence.DESIGN_ESTIMATE:
            return 'Tentative';
        default:
            return String(confidence);
    }
}

/**
 * Formats a number for display with at most four significant digits.
 * @param value The number, or a value of another type to show as text.
 * @return The text.
 */
export function formatValue(value: number | string | null | undefined): string {
    if (value === null || value === undefined) return 'not available';
    if (typeof value === 'number') return String(Number(value.toPrecision(4)));
    return value;
}

/**
 * Makes a stable key for a statement in a list.
 * @param rule The statement's rule name.
 * @param index The statement's position in the list.
 * @return The key.
 */
export function statementKey(rule: string, index: number): string {
    return `${rule}#${index}`;
}
