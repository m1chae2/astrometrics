import React from 'react';
import '../../common/styles/button.css';
import './IndiPropertyItem.css';

export interface IndiSwitchPropertyProps {
    name: string;
    label?: string;
    elements?: Record<string, unknown>;
    perm?: string;
    onApply: (elName: string, val: string) => void;
}

/**
 * IndiSwitchProperty Component
 *
 * Handles rendering and toggling of INDI Switch properties.
 */
export const IndiSwitchProperty: React.FC<IndiSwitchPropertyProps> = ({
    name,
    label,
    elements,
    perm,
    onApply,
}) => {
    const canEdit = perm !== 'ro';
    const safeName = name.replace(/\s+/g, '-');
    const elKeys = elements ? Object.keys(elements) : [];

    return (
        <div id={`indi-prop-${safeName}`} className="indi-property">
            <div id={`label-prop-${safeName}`} className="indi-property__label">{label || name}</div>
            <div id={`indi-switch-grid-${safeName}`} className="indi-property__switches">
                {elKeys.map(el => {
                    const isOn = elements![el] === 'On' || elements![el] === true;
                    const safeEl = el.replace(/\s+/g, '-');
                    return (
                        <button
                            key={el}
                            id={`btn-switch-${safeName}-${safeEl}`}
                            className={`btn indi-property__switch ${isOn ? 'indi-property__switch--active' : ''}`}
                            onClick={() => canEdit && onApply(el, 'On')}
                            type="button"
                        >
                            {el}
                        </button>
                    );
                })}
            </div>
        </div>
    );
};
