import React from 'react';
import './IndiDevicePanel.css';
import '../../common/styles/theme.css';

export interface IndiDevicePanelProps {
    devices: string[];
    selectedDevice: string;
    onSelectDevice: (device: string) => void;
}

/**
 * IndiDevicePanel Component
 *
 * Renders a grid of buttons for selecting an INDI device.
 */
export const IndiDevicePanel: React.FC<IndiDevicePanelProps> = ({
    devices,
    selectedDevice,
    onSelectDevice,
}) => {
    return (
        <div id="indi-device-list" className="indi-devices">
            {devices.map((device) => (
                <button
                    key={device}
                    id={`btn-indi-device-${device.replace(/\s+/g, '-')}`}
                    className={`indi-devices__btn ${selectedDevice === device ? 'indi-devices__btn--active' : ''}`}
                    onClick={() => onSelectDevice(device)}
                    type="button"
                >
                    {device}
                </button>
            ))}
        </div>
    );
};
