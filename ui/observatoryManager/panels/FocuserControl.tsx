/**
 * @file FocuserControl.tsx
 * @description The Focuser section of the Manual tab. It shows where the focuser
 * is and moves it in or out by a chosen number of steps. The position is read
 * while the tab is visible and again after each move. With no focuser the
 * position shows a dash and the buttons are off.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '../../common/components/Button';
import { PopoverSelect } from '../../common/components/PopoverSelect';
import { callBackend } from '../../common/services/backendApi';
import { emitToast } from '../../common/utils/emitToast';

/** The step sizes to offer, in focuser steps. A fixed list is always a valid whole number. */
export const FOCUSER_STEP_SIZES = [10, 50, 100, 500] as const;

/** The step size picked until the user picks another. */
export const DEFAULT_FOCUSER_STEP = 100;

/** Milliseconds between position reads while the section is visible. */
export const FOCUSER_POLL_INTERVAL_MS = 3000;

/** The tooltip and text for a focuser that cannot be read. */
const NO_FOCUSER_REASON = 'No focuser is connected.';

export interface FocuserControlProps {
    /** True while the Manual tab is on screen. The position is read only then. */
    active: boolean;
    /** True when the app may not command the equipment. Reads stop and the buttons are off. */
    disabled?: boolean;
}

/**
 * Shows the focuser position with In and Out buttons and a step size picker.
 * Sign convention: a positive step count moves the focuser OUT (the drawtube
 * extends, which is the INDI FOCUS_OUT direction) and a negative count moves it IN.
 * @param props See {@link FocuserControlProps}.
 * @return The section body.
 */
export const FocuserControl: React.FC<FocuserControlProps> = ({ active, disabled = false }) => {
    const [position, setPosition] = useState<number | null>(null);
    const [step, setStep] = useState<number>(DEFAULT_FOCUSER_STEP);
    const [moving, setMoving] = useState(false);
    const mountedRef = useRef(true);
    useEffect(() => {
        mountedRef.current = true;
        return () => { mountedRef.current = false; };
    }, []);

    /** Reads the position; a failed read means there is no focuser to show. */
    const refreshPosition = useCallback(async (): Promise<void> => {
        try {
            const value = await callBackend('telescope:get_focuser_position', {});
            if (mountedRef.current) setPosition(typeof value === 'number' ? value : null);
        } catch {
            if (mountedRef.current) setPosition(null);
        }
    }, []);

    // Read now and then on a timer, but only while the tab is visible and commands are allowed.
    useEffect(() => {
        if (!active || disabled) return undefined;
        void refreshPosition();
        const timer = window.setInterval(() => { void refreshPosition(); }, FOCUSER_POLL_INTERVAL_MS);
        return () => window.clearInterval(timer);
    }, [active, disabled, refreshPosition]);

    /** Moves the focuser by the signed step count, then reads the new position. */
    const move = async (signedSteps: number): Promise<void> => {
        if (disabled || moving) return;
        setMoving(true);
        try {
            const accepted = await callBackend('telescope:focus_move', { steps: signedSteps });
            if (!accepted) emitToast('Focuser move failed', 'error');
        } catch {
            emitToast('Focuser move failed', 'error');
        } finally {
            if (mountedRef.current) setMoving(false);
            await refreshPosition();
        }
    };

    const noFocuser = position === null;
    const buttonsOff = disabled || noFocuser;
    const offReason = !disabled && noFocuser ? NO_FOCUSER_REASON : undefined;

    return (
        <div className="focuser-control">
            <div className="focuser-control__position">
                <span id="label-focuser-position">Position</span>
                <output aria-labelledby="label-focuser-position" className="focuser-control__value">
                    {disabled || noFocuser ? '—' : position}
                </output>
            </div>
            <div className="focuser-control__moves">
                <Button
                    id="btn-focus-in"
                    size="small"
                    aria-label="Focus in"
                    title={offReason}
                    pending={moving}
                    disabled={buttonsOff}
                    onClick={() => { void move(-step); }}
                >
                    In
                </Button>
                <PopoverSelect
                    label="Step size"
                    options={FOCUSER_STEP_SIZES.map((size) => ({ id: String(size), label: `${size} steps` }))}
                    selectedId={String(step)}
                    onSelect={(id) => { if (id) setStep(Number(id)); }}
                    disabled={disabled}
                    size="compact"
                    fill
                />
                <Button
                    id="btn-focus-out"
                    size="small"
                    aria-label="Focus out"
                    title={offReason}
                    pending={moving}
                    disabled={buttonsOff}
                    onClick={() => { void move(step); }}
                >
                    Out
                </Button>
            </div>
        </div>
    );
};
