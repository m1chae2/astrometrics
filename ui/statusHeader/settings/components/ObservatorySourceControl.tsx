/**
 * @fileoverview ObservatorySourceControl: the "Observatory source" setting. Two buttons, REAL and SIMULATED, pick
 * the real telescope or a simulated sky, and the control switches by restarting the backend.
 */

import React, { useState } from 'react';
import { Button } from '../../../common/components/Button';
import { ConfirmDialog } from '../../../common/components/ConfirmDialog';
import { useAstrometrics } from '../../../common/context/AstrometricsContext';
import { isTrackingStatus } from '../../../common/utils/isTrackingStatus';
import { useGuidingRms } from '../../hooks/useGuidingRms';
import { useObservatorySource } from '../../hooks/useObservatorySource';

/** The two sources the user can pick, with the word shown in the question. */
const SOURCE_WORDS = { real: 'Real', simulated: 'Simulated' } as const;
type PickableSource = keyof typeof SOURCE_WORDS;

export interface ObservatorySourceControlProps {
    /** True while the Settings form has changes that are not saved. A restart would lose them, so the buttons wait. */
    hasUnsavedChanges?: boolean;
}

/**
 * Shows the "Observatory source" control: two buttons, REAL and SIMULATED. Pressing the one that is not active asks
 * "Restart on ...?" in a dialog; confirming restarts the backend on that source. Pressing the active one does nothing.
 *
 * @param props See {@link ObservatorySourceControlProps}.
 * @return The control.
 */
export const ObservatorySourceControl: React.FC<ObservatorySourceControlProps> = ({ hasUnsavedChanges = false }) => {
    const { state, switching, error, switchSource } = useObservatorySource();
    const { telescope } = useAstrometrics();
    const guidingRms = useGuidingRms(telescope?.guidingHistory);
    const [asking, setAsking] = useState<PickableSource | null>(null);

    // A saved replay (from an older setting) counts as simulated.
    const active: PickableSource = !state || state.source === 'real' ? 'real' : 'simulated';
    const missing = state?.missing_programs ?? [];

    // Work that a restart would cut short: tracking, guiding or an exposure (a sequence) under way.
    const running: string[] = [];
    if (isTrackingStatus(telescope?.trackingStatus)) running.push('tracking');
    if (guidingRms !== null) running.push('guiding');
    if (/expos/i.test(telescope?.cameraStatus ?? '')) running.push('a sequence');
    const confirmLines: string[] = [];
    if (running.length > 0) confirmLines.push(`The telescope is ${running.join(', ')} now. Restarting stops it.`);
    if (asking === 'simulated' && state?.failed) confirmLines.push(`The simulator failed to start last time: ${state.reason || 'no reason given'}.`);
    confirmLines.push('The backend restarts and the page reloads when it is back.');

    /**
     * Why a button cannot be pressed, shown as its tooltip, or undefined when it can.
     * @param source The button's source.
     * @return The reason, or undefined.
     */
    const disabledReason = (source: PickableSource): string | undefined => {
        if (source === active) return undefined;
        if (hasUnsavedChanges) return 'Save or revert your changes first';
        if (source === 'simulated' && missing.length > 0) return `The simulator needs these programs: ${missing.join(', ')}`;
        return undefined;
    };

    /** Draws one source button. */
    const sourceButton = (source: PickableSource) => {
        const isActive = source === active;
        const reason = disabledReason(source);
        return (
            <Button
                fit
                variant={isActive ? undefined : 'secondary'}
                pressed={isActive}
                pending={switching && !isActive}
                disabled={switching || reason !== undefined}
                title={reason}
                onClick={() => { if (!isActive) setAsking(source); }}
            >
                {source === 'real' ? 'REAL' : 'SIMULATED'}
            </Button>
        );
    };

    return (
        <div className="settings__divider">
            <h4>Observatory source</h4>
            <div className="settings__field settings__field--row" role="group" aria-label="Observatory source">
                {sourceButton('real')}
                {sourceButton('simulated')}
            </div>
            {error && <div className="settings__help" role="alert">{error}</div>}
            <ConfirmDialog
                open={asking !== null}
                title={`Restart on ${asking ? SOURCE_WORDS[asking] : ''}?`}
                message={confirmLines.join(' ')}
                confirmLabel="Restart"
                onCancel={() => setAsking(null)}
                onConfirm={() => {
                    const target = asking;
                    setAsking(null);
                    if (target) void switchSource(target);
                }}
            />
        </div>
    );
};
