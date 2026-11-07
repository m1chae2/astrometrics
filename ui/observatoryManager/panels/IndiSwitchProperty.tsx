import React from 'react';
import { Button } from '../../common/components/Button';
import { useObservatoryNight } from '../ObservatoryNightContext';
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
 * While the Observatory Manager shows a past night, the switches are greyed with the reason,
 * because a toggle would change the live device.
 */
export const IndiSwitchProperty: React.FC<IndiSwitchPropertyProps> = ({
    name,
    label,
    elements,
    perm,
    onApply,
}) => {
    const { isLive, liveOnlyReason } = useObservatoryNight();
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
                        <Button
                            key={el}
                            id={`btn-switch-${safeName}-${safeEl}`}
                            variant={isOn ? 'primary' : 'secondary'}
                            size="small"
                            onClick={() => canEdit && isLive && onApply(el, 'On')}
                            disabled={!isLive}
                            title={isLive ? undefined : liveOnlyReason}
                        >
                            {el}
                        </Button>
                    );
                })}
            </div>
        </div>
    );
};
