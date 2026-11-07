/**
 * @fileoverview ObservatorySourceChip: the status header chip that says the app is not on the real telescope
 * ("SIMULATOR"), so a simulated night is never mistaken for a real one. It shows nothing for the
 * real telescope.
 */

import React from 'react';
import { StatusChip } from '../common/components/StatusChip';
import { useObservatorySource } from './hooks/useObservatorySource';

/**
 * Shows the chip while the backend runs a simulator source, or "SIMULATOR FAILED" when it could not start.
 *
 * @return The chip, or nothing for the real telescope.
 */
export const ObservatorySourceChip: React.FC = () => {
    const { state } = useObservatorySource();
    // The simulator was chosen but could not start. Say so in red, with the reason on hover.
    if (state?.failed) {
        return (
            <span className="header__source-chip" title={state.reason || 'The simulator did not start.'}>
                <StatusChip status="error" label="SIMULATOR FAILED" />
            </span>
        );
    }
    if (!state || state.source === 'real') return null;
    return (
        <span className="header__source-chip" title="The app is using a simulated observatory and its own separate library, not your telescope.">
            <StatusChip status="warning" label="SIMULATOR" />
        </span>
    );
};
