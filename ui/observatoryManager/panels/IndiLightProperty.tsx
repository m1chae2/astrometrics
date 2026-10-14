import React from 'react';
import './IndiPropertyItem.css';

export interface IndiLightPropertyProps {
    name: string;
    label?: string;
    elements?: Record<string, unknown>;
}

/**
 * IndiLightProperty Component
 *
 * Handles rendering of INDI Light status indicators.
 */
export const IndiLightProperty: React.FC<IndiLightPropertyProps> = ({
    name,
    label,
    elements,
}) => {
    const safeName = name.replace(/\s+/g, '-');
    const elKeys = elements ? Object.keys(elements) : [];

    return (
        <div id={`indi-prop-${safeName}`} className="indi-property">
            <div id={`label-prop-${safeName}`} className="indi-property__label">{label || name}</div>
            <div id={`indi-light-grid-${safeName}`} className="indi-property__lights">
                {elKeys.map(el => {
                    const state = elements![el];
                    const safeEl = el.replace(/\s+/g, '-');
                    // Helper for modifier class name based on state
                    const stateModifier = (state === 'Ok') ? '--ok' : (state === 'Busy') ? '--busy' : (state === 'Alert') ? '--alert' : '';

                    return (
                        <div key={el} id={`indi-light-row-${safeName}-${safeEl}`} className="indi-property__light-item">
                            <div id={`indi-light-dot-${safeName}-${safeEl}`} className={`indi-property__light-dot indi-property__light-dot${stateModifier}`} />
                            <span id={`indi-light-label-${safeName}-${safeEl}`} className="indi-property__light-label">{el}</span>
                        </div>
                    );
                })}
            </div>
        </div>
    );
};
