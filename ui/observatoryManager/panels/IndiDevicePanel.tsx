import React from 'react';
import { Button } from '../../common/components/Button';
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
                    message={isLoading ? 'Loading devices' : 'No INDI devices connected.'}
                />
            </div>
        );
    }

    return (
        <div id="indi-device-list" className="indi-devices">
            {devices.map((device) => (
                <Button
                    key={device}
                    id={`btn-indi-device-${device.replace(/\s+/g, '-')}`}
                    variant="secondary"
                    truncate
                    pressed={selectedDevice === device}
                    onClick={() => onSelectDevice(device)}
                >
                    {device}
                </Button>
            ))}
        </div>
    );
};
