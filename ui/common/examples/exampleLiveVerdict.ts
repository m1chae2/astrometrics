/**
 * @fileoverview Example live session verdict, shown until wave L1 adds the real read.
 *
 * The panel that shows this value must carry the "Example data" badge. Do not
 * use it to fill in a real reply that is empty or failed.
 */

/** The live verdict and alert summary. Replace with the generated type in wave L. */
export interface ExampleLiveVerdict {
    /** The verdict state word, such as "On track". */
    state: string;
    /** Why the verdict has that state. */
    reason: string;
    /** The confidence label. The live verdict is always Tentative. */
    confidence: 'design_estimate';
    /** One sentence that sums up the alerts. */
    alertSentence: string;
    /** How many alerts the sentence covers. */
    alertCount: number;
}

/** Example live verdict. */
export const exampleLiveVerdict: ExampleLiveVerdict = {
    state: 'On track',
    reason: 'Star width and guiding error are inside the limits for the last 20 frames.',
    confidence: 'design_estimate',
    alertSentence: '2 frames flagged for a high guiding error.',
    alertCount: 2,
};
