/**
 * @fileoverview ObservatorySourceChip: the status header chip that says the app is not on the real telescope
 * ("SIMULATOR"), so a simulated night is never mistaken for a real one. It shows nothing for the
 * real telescope.
 */

import React from 'react';
import { StatusChip } from '../common/components/StatusChip';
import { describeCommandsStillOff, isNotLiveLaunch } from '../common/services/observatoryService';
import { useObservatorySource } from './hooks/useObservatorySource';

/**
 * Shows the chip while the backend runs a world that is not on live hardware. The text is the launch label
 * ("SIMULATOR"; "SIMULATOR STARTING" until it is ready), or an error chip when it could not start.
 *
 * @return The chip, or nothing for the real telescope.
 */
export const ObservatorySourceChip: React.FC = () => {
    const { state } = useObservatorySource();
    const label = state?.launch?.label || 'SIMULATOR';
    // The simulator was chosen but could not start. Say so in red, with the reason on hover.
    if (state?.failed || (isNotLiveLaunch(state) && /fail/i.test(label))) {
        return (
            <span className="header__source-chip" title={state?.reason || 'The simulator did not start.'}>
                <StatusChip status="error" label={/fail/i.test(label) ? label : 'SIMULATOR FAILED'} />
            </span>
        );
    }
    if (!state || !isNotLiveLaunch(state)) return null;
    // The simulator is still starting (copying the library, starting the telescope). That is a status, not an error.
    if (state.starting || state.ready === false) {
        return (
            <span className="header__source-chip" title={`The simulated observatory is starting${state.step ? `: ${state.step}` : ''}. The app uses its own separate library, not your telescope.`}>
                <StatusChip status="warning" label={`${label} STARTING`} />
            </span>
        );
    }
    const offline = state.launch?.offline ? 'Nothing leaves this computer.' : '';
    return (
        <span className="header__source-chip" title={`${label}: the app is not using your telescope or your real library. ${offline} ${describeCommandsStillOff(state)}`.replace(/\s+/g, ' ').trim()}>
            <StatusChip status="warning" label={label} />
        </span>
    );
};
