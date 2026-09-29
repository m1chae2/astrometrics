import React from 'react';
import { EmptyState } from '../../common/components/EmptyState';
import './IndiDevicePanel.css';
import '../../common/styles/theme.css';

export interface IndiDevicePanelProps {
    devices: string[];
    selectedDevice: string;
    onSelectDevice: (device: string) => void;
    /** Whether the initial device list fetch is still in flight. */
    isLoading?: boolean;
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
    isLoading = false,
}) => {
    if (devices.length === 0) {
        return (
            <div id="indi-device-list" className="indi-devices">
                <EmptyState
                    variant={isLoading ? 'loading' : 'empty'}
                    message={isLoading ? 'Loading devices…' : 'No INDI devices connected.'}
                />
            </div>
        );
    }

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
