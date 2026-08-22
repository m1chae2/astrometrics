/**
 * FocuserControl.tsx
 *
 * Provides manual control for the telescope focuser, including step size
 * selection and inward/outward movement buttons.
 */

import React, { useState } from 'react';
// Styles handled by panels.css

export interface FocuserControlProps {
    position: number;
    onMoveFocuser: (steps: number) => void;
}

/**
 * FocuserControl Component
 *
 * Provides a UI for manual control of the telescope's focus motor, allowing for
 * incremental movements in/out with variable step sizes.
 * REQ: OBS-5: Focuser Control
 */
export const FocuserControl: React.FC<FocuserControlProps> = ({ position, onMoveFocuser }) => {
    const [stepSize, setStepSize] = useState<number>(100);

    const stepSizes = [10, 50, 100, 500, 1000];

    return (
        <div id="focuser-control-group" className="focuser">
            <div id="focuser-position-display" className="focuser__status">
                <span id="label-focuser-position" className="focuser__label">Position:</span>
                {/* REQ: OBS-5.1: The display SHALL show current focuser position in steps. */}
                <span id="val-focuser-position" className="focuser__value">{position}</span>
            </div>

            <div id="focuser-movement-buttons" className="focuser__actions">
                <button
                    id="btn-focus-in"
                    className="btn btn--control btn--success"
                    onClick={() => onMoveFocuser(-stepSize)}
                    type="button"
                >
                    Focus IN (-)
                </button>
                <button
                    id="btn-focus-out"
                    className="btn btn--control btn--success"
                    onClick={() => onMoveFocuser(stepSize)}
                    type="button"
                >
                    Focus OUT (+)
                </button>
            </div>

            <div className="horizontal-divider" />

            <div id="focuser-step-selector" className="focuser__steps">
                {/* REQ: OBS-5.4: The display SHALL provide selection of focus step sizes (e.g., 10, 50, 100, 500, 1000). */}
                <span id="label-focus-steps" className="focuser__label">Steps:</span>
                {stepSizes.map(size => (
                    <button
                        key={size}
                        id={`btn-focus-step-${size}`}
                        onClick={() => setStepSize(size)}
                        className={`focuser__step ${stepSize === size ? 'focuser__step--active' : ''}`}
                        type="button"
                    >
                        {size}
                    </button>
                ))}
            </div>
        </div>
    );
};
