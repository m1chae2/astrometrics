/**
 * @fileoverview Example framing panel corners, shown until wave L3 adds the real read.
 *
 * The overlay that shows this value must carry the "Example data" badge. The UI
 * does no framing math; it draws the corners as given.
 */

/** One corner in sky coordinates. */
export interface ExampleSkyCorner {
    /** Right ascension in degrees. */
    raDeg: number;
    /** Declination in degrees. */
    decDeg: number;
}

/** One panel's four corners. Replace with the generated type in wave L. */
export interface ExampleFramingPanel {
    /** The panel's name. */
    name: string;
    /** The four corners, in the order returned. */
    corners: ExampleSkyCorner[];
}

/** Example framing panels. */
export const exampleFramingCorners: ExampleFramingPanel[] = [
    {
        name: 'Panel 1',
        corners: [
            { raDeg: 148.8, decDeg: 69.0 },
            { raDeg: 149.6, decDeg: 69.0 },
            { raDeg: 149.6, decDeg: 69.8 },
            { raDeg: 148.8, decDeg: 69.8 },
        ],
    },
];
