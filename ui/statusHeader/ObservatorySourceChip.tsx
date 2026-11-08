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
 * ("SIMULATOR"; "SIMULATOR STARTING" until it is ready), or an error chip when the backend reports the simulator failed. In narrow windows the chip shows a short word
 * ("SIM"); the full text stays in the tooltip and the accessible name.
 *
 * @return The chip, or nothing for the real telescope.
 */
export const ObservatorySourceChip: React.FC = () => {
    const { state } = useObservatorySource();
    const label = state?.launch?.label || 'SIMULATOR';
    // The simulator was chosen but could not start. The backend's `failed` flag says so; show it in red.
    if (state?.failed) {
        const reason = state.reason || 'The simulator did not start.';
        const title = `${reason} Open Settings, Observatory source, and pick Real to switch back to the real telescope.`;
        return (
            <span className="header__source-chip" title={title}>
                <StatusChip status="error" label={state.label || 'Simulator did not start'} shortLabel="SIM FAILED" title={title} />
            </span>
        );
    }
    if (!state || !isNotLiveLaunch(state)) return null;
    // The simulator is still starting (copying the library, starting the telescope). That is a status, not an error.
    if (state.starting || state.ready === false) {
        const title = `The simulated observatory is starting${state.step ? `: ${state.step}` : ''}. The app uses its own separate library, not your telescope.`;
        return (
            <span className="header__source-chip" title={title}>
                <StatusChip status="warning" label={`${label} STARTING`} shortLabel="SIM" title={title} />
            </span>
        );
    }
    const offline = state.launch?.offline ? 'Nothing leaves this computer.' : '';
    const title = `${label}: the app is not using your telescope or your real library. ${offline} ${describeCommandsStillOff(state)}`.replace(/\s+/g, ' ').trim();
    return (
        <span className="header__source-chip" title={title}>
            <StatusChip status="warning" label={label} shortLabel="SIM" title={title} />
        </span>
    );
};
