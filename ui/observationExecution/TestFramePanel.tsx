/**
 * @fileoverview The test frame panel: takes one frame with the chosen
 * exposure, type and filter. It moved here from the Observation Planner,
 * because taking a frame commands the camera.
 *
 * The button is disabled, and its click handler is guarded too, whenever the
 * app may not command the equipment.
 */

import React, { useState } from 'react';
import { captureTestFrame } from '../common/services/imaging/captureService';
import { emitToast } from '../common/utils/emitToast';

/** The frame types the camera can take. */
const FRAME_TYPES = ['LIGHT', 'DARK', 'FLAT', 'BIAS'] as const;

/** The filters offered, the same list as the filter wheel panel. */
const FILTER_NAMES = ['L', 'R', 'G', 'B', 'Ha', 'OIII', 'SII', 'Spect'] as const;

export interface TestFramePanelProps {
    /** The current target's id, or an empty string. */
    targetId: string;
    /** True when the app may not command the equipment. */
    disabled: boolean;
    /** The id of the visible text that says why the button is disabled. */
    disabledReasonId?: string;
}

/**
 * The test frame panel.
 * @param props The target, whether commands are disabled, and the reason's id.
 * @return The panel.
 */
export const TestFramePanel: React.FC<TestFramePanelProps> = ({ targetId, disabled, disabledReasonId }) => {
    const [exposureSeconds, setExposureSeconds] = useState(10);
    const [imageType, setImageType] = useState<string>('LIGHT');
    const [filterName, setFilterName] = useState<string>('L');

    const takeFrame = async () => {
        if (disabled) return;
        if (!targetId) {
            emitToast('Select a target before capturing a test frame', 'error');
            return;
        }
        emitToast(`Capturing test frame: ${exposureSeconds} s ${filterName} ${imageType}`, 'info');
        const jobId = await captureTestFrame({
            targetId,
            exposureSeconds,
            imageType,
            filterName: filterName || undefined,
        });
        emitToast(jobId ? 'Test frame capture started' : 'Test frame capture failed', jobId ? 'success' : 'error');
    };

    return (
        <div className="test-frame">
            <label>
                Exposure (s)
                <input
                    type="number"
                    min={0.001}
                    step="any"
                    value={exposureSeconds}
                    disabled={disabled}
                    aria-disabled={disabled}
                    onChange={(event) => setExposureSeconds(Number(event.target.value))}
                />
            </label>
            <label>
                Type
                <select
                    className="dropdown"
                    value={imageType}
                    disabled={disabled}
                    aria-disabled={disabled}
                    onChange={(event) => setImageType(event.target.value)}
                >
                    {FRAME_TYPES.map((type) => (
                        <option key={type} value={type}>
                            {type}
                        </option>
                    ))}
                </select>
            </label>
            <label>
                Filter
                <select
                    className="dropdown"
                    value={filterName}
                    disabled={disabled}
                    aria-disabled={disabled}
                    onChange={(event) => setFilterName(event.target.value)}
                >
                    {FILTER_NAMES.map((name) => (
                        <option key={name} value={name}>
                            {name}
                        </option>
                    ))}
                </select>
            </label>
            <button
                type="button"
                className="btn btn--secondary"
                disabled={disabled}
                aria-disabled={disabled}
                aria-describedby={disabled ? disabledReasonId : undefined}
                onClick={() => void takeFrame()}
            >
                Take test frame
            </button>
        </div>
    );
};
