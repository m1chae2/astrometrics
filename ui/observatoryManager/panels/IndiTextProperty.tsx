import React, { useState, useEffect } from 'react';
import { useObservatoryNight } from '../ObservatoryNightContext';
import './IndiPropertyItem.css';
import { TextInput } from '../../common/components/TextInput';
import { Button } from '../../common/components/Button';

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
 * While the Observatory Manager shows a past night, the input and Apply button are greyed
 * with the reason, because an edit would change the live device.
 */
export const IndiTextProperty: React.FC<IndiTextPropertyProps> = ({
    name,
    value,
    label,
    elements,
    perm,
    onApply,
}) => {
    const { isLive, liveOnlyReason } = useObservatoryNight();
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
                                <TextInput
                                    id={`input-indi-${safeName}-${safeEl}`}
                                    width="fill"
                                    value={inputVal}
                                    placeholder="New Value"
                                    disabled={!isLive}
                                    title={isLive ? undefined : liveOnlyReason}
                                    onChange={(e) => setInputs(prev => ({ ...prev, [el]: e.target.value }))}
                                />
                                <Button
                                    control
                                    id={`btn-apply-indi-${safeName}-${safeEl}`}
                                    onClick={() => isLive && onApply(el === 'value' ? name : el, inputVal)}
                                    disabled={!isLive}
                                    title={isLive ? undefined : liveOnlyReason}
                                >
                                    Apply
                                </Button>
                            </div>
                        )}
                    </div>
                );
            })}
        </div>
    );
};
