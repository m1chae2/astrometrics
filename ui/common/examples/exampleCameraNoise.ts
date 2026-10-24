/**
 * @fileoverview Example measured camera noise, shown until wave L3 adds the real read.
 *
 * The panel that shows this value must carry the "Example data" badge.
 */

/** Measured gain and read noise for one camera setting. Replace with the generated type in wave L. */
export interface ExampleCameraNoiseRow {
    /** The camera's id. */
    cameraId: string;
    /** The camera gain setting. */
    gainSetting: number;
    /** The gain in electrons per count. */
    gainElectronsPerCount: number;
    /** The read noise in electrons. */
    readNoiseElectrons: number;
}

/** Example camera noise rows. */
export const exampleCameraNoise: ExampleCameraNoiseRow[] = [
    { cameraId: 'ZWO ASI533MM Pro', gainSetting: 100, gainElectronsPerCount: 0.9, readNoiseElectrons: 1.6 },
    { cameraId: 'ZWO ASI533MM Pro', gainSetting: 0, gainElectronsPerCount: 4.2, readNoiseElectrons: 3.1 },
];
