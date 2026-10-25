/**
 * @fileoverview ProgressBar: a bar with `role="progressbar"` and visible text such as "12 of 20 frames".
 */

import React from 'react';
import './sharedComponents.css';

export interface ProgressBarProps {
    /** Amount done. */
    value: number;
    /** Amount that means finished. */
    max: number;
    /** Name of the counted thing, for example "frames". */
    unit: string;
    /** Accessible name of the bar, for example "Capture progress". */
    label: string;
}

/**
 * Shows a progress bar and its text. The text is also the bar's `aria-valuetext`.
 *
 * @param props See {@link ProgressBarProps}.
 * @return The progress bar element.
 */
export const ProgressBar: React.FC<ProgressBarProps> = ({ value, max, unit, label }) => {
    const safeMax = max > 0 ? max : 0;
    const clampedValue = Math.min(Math.max(value, 0), safeMax);
    const percent = safeMax > 0 ? (clampedValue / safeMax) * 100 : 0;
    const text = `${clampedValue} of ${safeMax} ${unit}`;

    return (
        <div className="progress-bar">
            <div
                className="progress-bar__track"
                role="progressbar"
                aria-label={label}
                aria-valuemin={0}
                aria-valuemax={safeMax}
                aria-valuenow={clampedValue}
                aria-valuetext={text}
            >
                <div className="progress-bar__fill" style={{ width: `${percent}%` }} />
            </div>
            <span className="progress-bar__text">{text}</span>
        </div>
    );
};
