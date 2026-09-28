import React, { useState, useEffect } from 'react';
import { IndiDevicePanel } from './panels/IndiDevicePanel';
import { IndiPropertyInspector } from './panels/IndiPropertyInspector';
import { fetchIndiDevices, fetchIndiProperties, setIndiProperty } from '../common/services/telescopeService';
import { SectionPanel } from '../common/components/SectionPanel';
import '../common/styles/manager.css';
import '../common/styles/panels.css';

import { IndiPropertyData } from '../common/types/indiTypes';

/**
 * IndiStatusPanel Component
 *
 * Manages the INDI device selection and property inspection interface.
 * Manages the INDI device selection and property inspection interface.
 * Consists of a device list and a dynamic property inspector.
 * REQ: OBS-9: INDI Device Inspection
 * REQ: OBS-9.1: The display SHALL allow viewing of all connected INDI devices.
 * REQ: OBS-9.2: The display SHALL allow viewing and editing of raw INDI properties.
 */
export const IndiStatusPanel: React.FC = () => {
    const [devices, setDevices] = useState<string[]>([]);
    const [selectedDevice, setSelectedDevice] = useState<string>('');
    const [properties, setProperties] = useState<Record<string, IndiPropertyData>>({});

    useEffect(() => {
        let mounted = true;
        const loadDevices = async () => {
            const devs = await fetchIndiDevices();
            if (mounted) setDevices(devs);
        };
        loadDevices();
        const interval = setInterval(loadDevices, 5000);
        return () => { mounted = false; clearInterval(interval); };
    }, []);

    useEffect(() => {
        if (!selectedDevice) {
            setProperties({});
            return;
        }
        let mounted = true;
        const loadProps = async () => {
            const props = await fetchIndiProperties(selectedDevice);
            if (mounted) {
                setProperties(props as Record<string, IndiPropertyData>);
            }
        };
        loadProps();
        const interval = setInterval(loadProps, 2000);
        return () => { mounted = false; clearInterval(interval); };
    }, [selectedDevice]);

    const handleApply = async (device: string, prop: string, val: string, element?: string) => {
        await setIndiProperty(device, prop, val, element);
        const props = await fetchIndiProperties(device);
        setProperties(props as Record<string, IndiPropertyData>);
    };

    return (
        <div className="panel-group indi-management-wrapper">
            {/* Device Selection Panel */}
            {/* REQ: OBS-9.1: The display SHALL list all connected INDI devices. */}
            <SectionPanel title="Devices" className="flex-auto">
                <IndiDevicePanel
                    devices={devices}
                    selectedDevice={selectedDevice}
                    onSelectDevice={setSelectedDevice}
                />
            </SectionPanel>

            {/* Property Inspector Panel */}
            {/* REQ: OBS-9.2: The display SHALL allow viewing and editing of raw INDI properties. */}
            <SectionPanel title="Device Properties" className="flex-fill">
                {selectedDevice ? (
                    <IndiPropertyInspector
                        properties={properties}
                        onApply={handleApply}
                        selectedDevice={selectedDevice}
                    />
                ) : (
                    <div className="indi-status__no-selection">
                        Select a device to view properties
                    </div>
                )}
            </SectionPanel>
        </div>
    );
};
