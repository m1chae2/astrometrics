/**
 * @fileoverview Example whole-night visibility, shown until wave L3 adds the real read.
 *
 * The chart that shows this value must carry the "Example data" badge. The UI
 * draws these numbers as given; it computes no altitude or span itself.
 */

/** One altitude sample. */
export interface ExampleVisibilitySample {
    /** The sample time, ISO 8601 text. */
    time: string;
    /** The target's altitude in degrees. */
    altitudeDeg: number;
    /** The Moon's altitude in degrees. */
    moonAltitudeDeg: number;
}

/** A span of time as start and end text. */
export interface ExampleTimeSpan {
    /** Start, ISO 8601 text. */
    start: string;
    /** End, ISO 8601 text. */
    end: string;
}

/** Whole-night visibility. Replace with the generated type in wave L. */
export interface ExampleNightVisibility {
    /** The target's altitude samples. */
    samples: ExampleVisibilitySample[];
    /** The span in which the target can be imaged. */
    usable: ExampleTimeSpan;
    /** The span in which the target is clear of the horizon limit. */
    clearOfHorizon: ExampleTimeSpan;
    /** Astronomical twilight end and start. */
    twilight: ExampleTimeSpan;
    /** The Moon's illumination as a fraction from 0 to 1. */
    moonIlluminationFraction: number;
    /** The altitude limit the library used, in degrees. */
    altitudeLimitDeg: number;
    /** The highest altitude of the target, in degrees. */
    highestAltitudeDeg: number;
    /** The time of the highest point, ISO 8601 text. */
    highestTime: string;
}

/** Example whole-night visibility. */
export const exampleNightVisibility: ExampleNightVisibility = {
    samples: [
        { time: '2026-10-23T20:00:00-06:00', altitudeDeg: 35, moonAltitudeDeg: -12 },
        { time: '2026-10-23T23:00:00-06:00', altitudeDeg: 58, moonAltitudeDeg: 10 },
        { time: '2026-10-24T02:00:00-06:00', altitudeDeg: 72, moonAltitudeDeg: 35 },
        { time: '2026-10-24T05:00:00-06:00', altitudeDeg: 50, moonAltitudeDeg: 20 },
    ],
    usable: { start: '2026-10-23T20:30:00-06:00', end: '2026-10-24T05:00:00-06:00' },
    clearOfHorizon: { start: '2026-10-23T19:50:00-06:00', end: '2026-10-24T05:30:00-06:00' },
    twilight: { start: '2026-10-23T20:00:00-06:00', end: '2026-10-24T05:45:00-06:00' },
    moonIlluminationFraction: 0.35,
    altitudeLimitDeg: 30,
    highestAltitudeDeg: 72,
    highestTime: '2026-10-24T02:00:00-06:00',
};
