/**
 * @fileoverview Type definitions for INDI (Instrument Neutral Distributed Interface) protocol data.
 */

/**
 * Represents a single INDI property and its associated metadata.
 */
export interface IndiPropertyData {
    name?: string;
    label?: string;
    type?: string;
    value?: unknown;
    elements?: Record<string, unknown>;
    perm?: string;
    /** Flexible index for any other INDI metadata. */
    [key: string]: unknown;
}
