import React from 'react';
import { IndiTextProperty } from './IndiTextProperty';
import { IndiSwitchProperty } from './IndiSwitchProperty';
import { IndiLightProperty } from './IndiLightProperty';
// CSS imported by sub-components or common styles
import './IndiPropertyItem.css';

export interface IndiPropertyItemProps {
    name: string;
    type: string; // 'Text', 'Number', 'Switch', 'Light'
    value: unknown;
    label?: string;
    elements?: Record<string, unknown>; // For vectors
    perm?: string; // ro, rw, wo
    onApply: (newValue: string, element?: string) => void;
}

/**
 * IndiPropertyItem component.
 *
 * Dispatcher component that renders the appropriate specific property component
 * (Text, Switch, Light) based on the INDI property type.
 */
export const IndiPropertyItem: React.FC<IndiPropertyItemProps> = ({
    name,
    type,
    value,
    label,
    elements,
    perm,
    onApply,
}) => {
    const handleApply = (elName: string, val: string) => {
        onApply(val, elName);
    };

    if (type === 'Number' || type === 'Text') {
        return (
            <IndiTextProperty
                name={name}
                value={value}
                label={label}
                elements={elements}
                perm={perm}
                onApply={handleApply}
            />
        );
    }

    if (type === 'Switch') {
        return (
            <IndiSwitchProperty
                name={name}
                label={label}
                elements={elements}
                perm={perm}
                onApply={handleApply}
            />
        );
    }

    if (type === 'Light') {
        return (
            <IndiLightProperty
                name={name}
                label={label}
                elements={elements}
            />
        );
    }

    return null;
};
