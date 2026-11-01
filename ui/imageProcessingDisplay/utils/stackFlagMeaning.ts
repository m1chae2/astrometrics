/**
 * @file stackFlagMeaning.ts
 * @description Explains the flags a stacking run raises in plain words. The stacking step
 * writes each flag as one line of text. This file recognizes the known kinds of line and
 * gives each a short meaning for a reader who is not a stacking expert. A line it does not
 * recognize is returned as it is, so no flag is ever hidden or reworded wrongly.
 */

/** One flag, explained. */
export interface StackFlagMeaning {
    /** A short name for the kind of flag, or `null` when the kind is not known. */
    title: string | null;
    /** What the flag means in plain words. For an unknown flag this is the raw text. */
    explanation: string;
    /** True when the kind was recognized, so the raw text may sit behind a disclosure. */
    known: boolean;
}

/** One known kind of flag: a test on the raw text and the words to show. */
interface FlagKind {
    matches: RegExp;
    title: string;
    explanation: string;
}

/** The known kinds, tested in order. The first match wins. */
const FLAG_KINDS: FlagKind[] = [
    {
        matches: /^background split/i,
        title: 'Background split',
        explanation: 'The sky brightness of the frames falls into separate groups. This often means '
            + 'some frames were taken under different conditions, such as moonlight or haze, so '
            + 'they may not blend well.',
    },
    {
        matches: /calibration metadata mismatch|cannot match the lights/i,
        title: 'Calibration mismatch',
        explanation: 'Some calibration frames were taken with different camera settings than the '
            + 'light frames, or could not be used because they cannot match. Calibration '
            + 'then removes less of the camera noise than it should.',
    },
    {
        matches: /calibration frame count too low|unique frame/i,
        title: 'Too few calibration frames',
        explanation: 'A master calibration frame was built from too few different frames, so it '
            + 'adds some of its own noise to the stack.',
    },
    {
        matches: /^flat calibration/i,
        title: 'Flat frame problem',
        explanation: 'The flat frames, which correct uneven lighting and dust, did not pass a check. '
            + 'The corrected image may still show vignetting or dust spots.',
    },
    {
        matches: /had no dark at their exposure/i,
        title: 'Dark frames missing',
        explanation: 'Some light frames had no dark frame of the same exposure length, so heat '
            + 'noise and hot pixels may remain in them.',
    },
    {
        matches: /sensor temperature .* spans/i,
        title: 'Camera temperature drifted',
        explanation: 'The camera sensor changed temperature during the night by more than a dark '
            + 'frame can match, so the dark frame removes the heat noise only partly.',
    },
    {
        matches: /rejected pixel fraction|rejected pixels/i,
        title: 'Many rejected pixels',
        explanation: 'More pixels were thrown out than usual. The stacker drops pixels that differ '
            + 'a lot between frames, such as satellite trails, so a high share can mean '
            + 'problem frames.',
    },
    {
        matches: /degraded vs|wider than expected|star width/i,
        title: 'Wider stars',
        explanation: 'The stars in the stack are wider than the input frames predict. Poor '
            + 'alignment between frames can blur the stack.',
    },
    {
        matches: /exactly zero/i,
        title: 'Pixels at exactly zero',
        explanation: 'Many pixels in the stack are exactly zero, which is not real sky. Some '
            + 'frames were probably cut off at zero before stacking.',
    },
    {
        matches: /negative pixels/i,
        title: 'Negative pixels',
        explanation: 'Stacking produced many negative pixel values. This usually means the '
            + 'calibration frames do not match the light frames.',
    },
    {
        matches: /saturated pixel/i,
        title: 'Saturated pixels',
        explanation: 'A large share of pixels reached the brightest level the camera can record, '
            + 'so bright stars lose their detail.',
    },
    {
        matches: /spectral registration/i,
        title: 'Spectra drifted',
        explanation: 'Some frames did not line up well on the star that anchors the spectrum.',
    },
    {
        matches: /clouds or trailed stars/i,
        title: 'Frames set aside',
        explanation: 'Some frames showed clouds or trailed stars and were moved to the '
            + '_excluded folder so they would not harm the stack.',
    },
    {
        matches: /exposure group/i,
        title: 'Exposure group left out',
        explanation: 'Frames with one exposure length were left out of the final image, for the '
            + 'reason given in the raw text.',
    },
    {
        matches: /single-frame stack/i,
        title: 'Single frame',
        explanation: 'The stack holds one frame, so it had no rejection or alignment checks.',
    },
];

/**
 * Explains one raw flag in plain words.
 * @param rawFlag The flag text the stacking step saved.
 * @return The meaning for a known kind, or the raw text unchanged for an unknown kind.
 */
export const describeStackFlag = (rawFlag: string): StackFlagMeaning => {
    const kind = FLAG_KINDS.find((candidate) => candidate.matches.test(rawFlag));
    if (!kind) return { title: null, explanation: rawFlag, known: false };
    return { title: kind.title, explanation: kind.explanation, known: true };
};

/**
 * Counts a stack's flags for the one-line summary.
 * @param flagged True when the stacking step flagged the stack.
 * @param flagReasons The raw flag lines.
 * @return Text such as `no flags`, `1 flag` or `2 flags`.
 */
export const describeFlagCount = (flagged: boolean | undefined, flagReasons: string[]): string => {
    const count = flagReasons.length > 0 ? flagReasons.length : flagged ? 1 : 0;
    if (count === 0) return 'no flags';
    return count === 1 ? '1 flag' : `${count} flags`;
};
