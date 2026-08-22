import React, { useState } from 'react';
import { ControlColumn, ParameterRow } from '../common/components/ControlLayoutComponents';
import '../common/styles/panels.css';

export interface DitherConfig {
    enabled: boolean;
    everyN: number;
    scale: number;
}

interface DitherSettingsProps {
    enabled?: boolean;
    everyN?: number;
    scale?: number;
    onChange?: (enabled: boolean, everyN: number, scale: number) => void;
    // Optional: could accept 'values' prop of type DitherConfig too, but flat props are fine for now.
}

export const DitherSettings: React.FC<DitherSettingsProps> = ({
    enabled: propEnabled,
    everyN: propEveryN,
    scale: propScale,
    onChange
}) => {
    const [localEnabled, setLocalEnabled] = useState(false);
    const [localEveryN, setLocalEveryN] = useState(1);
    const [localPixels, setLocalPixels] = useState(2.0);

    const isControlled = onChange !== undefined;

    const enabled = isControlled ? (propEnabled ?? false) : localEnabled;
    const everyN = isControlled ? (propEveryN ?? 1) : localEveryN;
    const pixels = isControlled ? (propScale ?? 2.0) : localPixels;

    const update = (newEnabled: boolean, newEveryN: number, newPixels: number) => {
        if (isControlled && onChange) {
            onChange(newEnabled, newEveryN, newPixels);
        } else {
            setLocalEnabled(newEnabled);
            setLocalEveryN(newEveryN);
            setLocalPixels(newPixels);
        }
    };

    return (
        <div className="dither-settings__container">
            <ControlColumn>
                <ParameterRow label="Dither">
                    <div className="dither-settings__toggle">
                        <input
                            type="checkbox"
                            checked={enabled}
                            onChange={(e) => update(e.target.checked, everyN, pixels)}
                        />
                        <span className="checkbox-label">{enabled ? 'Enabled' : 'Disabled'}</span>
                    </div>
                </ParameterRow>

                <ParameterRow label="Every">
                    <input
                        type="number"
                        min="1"
                        value={everyN}
                        onChange={(e) => update(enabled, parseInt(e.target.value) || 1, pixels)}
                        disabled={!enabled}
                        className="entry entry--fill"
                    />
                </ParameterRow>

                <ParameterRow label="Scale">
                    <input
                        type="number"
                        min="0.1"
                        step="0.1"
                        value={pixels}
                        onChange={(e) => update(enabled, everyN, parseFloat(e.target.value) || 0)}
                        disabled={!enabled}
                        className="entry entry--fill"
                    />
                </ParameterRow>
            </ControlColumn>
        </div>
    );
};
