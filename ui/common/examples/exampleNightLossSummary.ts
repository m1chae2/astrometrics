/**
 * @fileoverview Example night loss summary, shown until wave L1 adds the real read.
 *
 * The panel that shows this value must carry the "Example data" badge. Every
 * part is kept apart; the UI never adds them together.
 */

/** The night loss summary. Replace with the generated type in wave L. */
export interface ExampleNightLossSummary {
    /** Time on target captured, in seconds. */
    capturedSeconds: number;
    /** Time in frames set aside, in seconds. */
    setAsideSeconds: number;
    /** Time lost in kept frames compared with the reference, in seconds. */
    keptFrameDeficitSeconds: number;
    /** Time lost for a cause the library could not find, in seconds. */
    causeUnknownSeconds: number | null;
    /** The science goal the loss is measured against. */
    goal: string;
    /** The name of the reference the loss is measured against. */
    referenceName: string;
}

/** Example night loss summary. */
export const exampleNightLossSummary: ExampleNightLossSummary = {
    capturedSeconds: 7200,
    setAsideSeconds: 300,
    keptFrameDeficitSeconds: 540,
    causeUnknownSeconds: 120,
    goal: 'Faint point source',
    referenceName: 'Best night of this setting',
};
