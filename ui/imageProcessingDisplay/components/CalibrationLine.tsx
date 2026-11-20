/**
 * @file CalibrationLine.tsx
 * @description The one "Calibration" line of the Data tab, shown below the stack summary line.
 * It says what the stacker would use for each group of the target's light frames: darks, flats
 * and bias frames. A missing set, or one that is only a near match, turns the line amber.
 * Hovering shows the detail for each group.
 */
import React from 'react';
import type { GroupedFrameStat } from '../../common/types/backendTypes';
import { buildCalibrationLine } from '../utils/calibrationLine';

interface CalibrationLineProps {
    /** The target's light-frame groups, each with the stacker's calibration match. */
    lightFrames: GroupedFrameStat[];
}

/**
 * Shows the calibration status of the target as one line, or nothing when there are no frames.
 */
export const CalibrationLine: React.FC<CalibrationLineProps> = ({ lightFrames }) => {
    const line = buildCalibrationLine(lightFrames);
    if (!line) return null;
    return (
        <div className="stack-summary calibration-line" title={line.detail} data-testid="calibration-line">
            <span className="stacked-image-panel__label">Calibration: </span>
            <span className={line.hasWarning ? 'stack-summary__flag' : undefined}>{line.text}</span>
        </div>
    );
};
