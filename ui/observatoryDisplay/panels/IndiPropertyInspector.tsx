import React from 'react';
import { IndiPropertyData } from '../../common/types/indiTypes';
import { IndiPropertyItem } from './IndiPropertyItem';
import '../../common/styles/scrollbars.css';
import './IndiPropertyInspector.css';

export interface IndiPropertyInspectorProps {
    properties: Record<string, IndiPropertyData>;
    selectedDevice: string;
    onApply: (device: string, prop: string, val: string, element?: string) => void;
}

/**
 * IndiPropertyInspector Component
 *
 * Renders a list of INDI properties for a selected device.
 * Dynamically renders components based on property type (Text, Number, Switch, Light).
 */
export const IndiPropertyInspector: React.FC<IndiPropertyInspectorProps> = ({
    properties,
    selectedDevice,
    onApply,
}) => {
    const propKeys = Object.keys(properties);

    if (propKeys.length === 0) {
        return (
            <div id="indi-no-properties" className="indi-properties__empty">
                No properties found for this device.
            </div>
        );
    }

    return (
        <div id="indi-property-list" className="indi-properties">
            {propKeys.map((key) => {
                const p = properties[key];
                return (
                    <IndiPropertyItem
                        key={key}
                        name={key}
                        type={p.type || 'Text'}
                        label={p.label || key}
                        value={p.value}
                        elements={p.elements}
                        perm={p.perm}
                        onApply={(val, element) => onApply(selectedDevice, key, val, element)}
                    />
                );
            })}
        </div>
    );
};
