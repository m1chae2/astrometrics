/**
 * @fileoverview Example Ekos sequence summary rows, shown until wave L2 adds the real read.
 *
 * The panel that shows this value must carry the "Example data" badge.
 */

/** One row of the Ekos summary. Replace with the generated type in wave L. */
export interface ExampleSequenceRow {
    /** The row's id. */
    rowId: string;
    /** The text to copy into Ekos Capture. */
    copyText: string;
    /** Whether the user marked the row done in Ekos. */
    doneInEkos: boolean;
}

/** Example summary rows, in Ekos Capture order. */
export const exampleSequenceSummary: ExampleSequenceRow[] = [
    { rowId: 'row-1', copyText: 'L, 60 s, 40 frames, gain 100, offset 10, bin 1', doneInEkos: false },
    { rowId: 'row-2', copyText: 'R, 120 s, 20 frames, gain 100, offset 10, bin 1', doneInEkos: false },
];
