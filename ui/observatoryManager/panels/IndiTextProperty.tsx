import React, { useState, useEffect } from 'react';
import '../../common/styles/button.css';
import './IndiPropertyItem.css';

export interface IndiTextPropertyProps {
    name: string;
    value: unknown;
    label?: string;
    elements?: Record<string, unknown>;
    perm?: string;
    onApply: (elName: string, val: string) => void;
}

/**
 * IndiTextProperty Component
 *
 * Handles rendering and editing of INDI Text and Number properties.
 */
export const IndiTextProperty: React.FC<IndiTextPropertyProps> = ({
    name,
    value,
    label,
    elements,
    perm,
    onApply,
}) => {
    // Local state for input
    const [inputs, setInputs] = useState<Record<string, string>>({});

    const canEdit = perm !== 'ro';
    const safeName = name.replace(/\s+/g, '-');
    const elKeys = elements ? Object.keys(elements) : ['value'];

    return (
        <div id={`indi-prop-${safeName}`} className="indi-property">
            <div id={`label-prop-${safeName}`} className="indi-property__label">{label || name}</div>
            {elKeys.map((el) => {
                const currentVal = elements ? elements[el] : value;
                const inputVal = inputs[el] ?? '';
                const safeEl = el.replace(/\s+/g, '-');

                return (
                    <div key={el} id={`indi-el-row-${safeName}-${safeEl}`} className="indi-property__element">
                        <div id={`indi-val-display-${safeName}-${safeEl}`} className="indi-property__value">
                            {el}: {String(currentVal)}
                        </div>
                        {canEdit && (
                            <div id={`indi-input-row-${safeName}-${safeEl}`} className="indi-property__input-group">
                                <input
                                    id={`input-indi-${safeName}-${safeEl}`}
                                    className="input input--fill"
                                    value={inputVal}
                                    placeholder="New Value"
                                    onChange={(e) => setInputs(prev => ({ ...prev, [el]: e.target.value }))}
                                />
                                <button
                                    id={`btn-apply-indi-${safeName}-${safeEl}`}
                                    className="btn btn--control indi-property__apply"
                                    onClick={() => onApply(el === 'value' ? name : el, inputVal)}
                                    type="button"
                                >
                                    Apply
                                </button>
                            </div>
                        )}
                    </div>
                );
            })}
        </div>
    );
};
