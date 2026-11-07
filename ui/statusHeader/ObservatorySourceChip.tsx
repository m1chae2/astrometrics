/**
 * @fileoverview ObservatorySourceChip: the status header chip that says the app is not on the real telescope
 * ("SIMULATOR"), so a simulated night is never mistaken for a real one. It shows nothing for the
 * real telescope.
 */

import React from 'react';
import { StatusChip } from '../common/components/StatusChip';
import { describeCommandsStillOff } from '../common/services/observatoryService';
import { useObservatorySource } from './hooks/useObservatorySource';

/**
 * Shows the chip while the backend runs a simulator source ("SIMULATOR STARTING" until it is ready), or "SIMULATOR FAILED" when it could not start.
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
    // The simulator is still starting (copying the library, starting the telescope). That is a status, not an error.
    if (state.starting || state.ready === false) {
        return (
            <span className="header__source-chip" title={`The simulated observatory is starting${state.step ? `: ${state.step}` : ''}. The app uses its own separate library, not your telescope.`}>
                <StatusChip status="warning" label="SIMULATOR STARTING" />
            </span>
        );
    }
    return (
        <span className="header__source-chip" title={`The app is using a simulated observatory and its own separate library, not your telescope. ${describeCommandsStillOff(state)}`.trim()}>
            <StatusChip status="warning" label="SIMULATOR" />
        </span>
    );
};
