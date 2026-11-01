/**
 * @file test_trackingGuiding.test.tsx
 * @description Tests of the Guiding tab in Tracking Settings: Start Guiding sends the exposure and
 * gain that were typed, Stop Guiding calls the stop handler, bad numbers keep Start greyed, a
 * greyed panel sends nothing, and the Alignment tab has no exposure field. Also tests that the
 * guiding handlers show a toast when the backend call fails.
 */
import React, { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook, act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TrackingSettings } from '../panels/TrackingSettings';

const guiding = vi.hoisted(() => ({ startGuiding: vi.fn(), stopGuiding: vi.fn(), captureGuideFrame: vi.fn() }));
vi.mock('../../common/services/telescope/guidingService', () => guiding);
const toast = vi.hoisted(() => ({ emitToast: vi.fn() }));
vi.mock('../../common/utils/emitToast', () => toast);

import { useObservatoryControl } from '../hooks/useObservatoryControl';

/** Holds the exposure and gain in state, as the Observatory Manager does. */
const Harness: React.FC<{
    onGuideStart: (exposure: number, gain: number) => void;
    onGuideStop: () => void;
    disabled?: boolean;
}> = ({ onGuideStart, onGuideStop, disabled = false }) => {
    const [exposure, setExposure] = useState(1.0);
    const [gain, setGain] = useState(100);
    return (
        <TrackingSettings
            guidingExposure={exposure}
            guidingGain={gain}
            onGuidingExposureChange={setExposure}
            onGuidingGainChange={setGain}
            onGuideStart={onGuideStart}
            onGuideStop={onGuideStop}
            disabled={disabled}
        />
    );
};

describe('Tracking Settings guiding controls', () => {
    beforeEach(() => vi.clearAllMocks());

    it('sends the typed exposure and gain when Start Guiding is clicked', async () => {
        const onGuideStart = vi.fn();
        render(<Harness onGuideStart={onGuideStart} onGuideStop={vi.fn()} />);
        await userEvent.click(screen.getByRole('tab', { name: 'Guiding' }));
        const exposure = screen.getByLabelText('Guiding Exposure Seconds');
        const gain = screen.getByLabelText('Guiding Gain');
        await userEvent.clear(exposure);
        await userEvent.type(exposure, '2.5');
        await userEvent.clear(gain);
        await userEvent.type(gain, '150');
        await userEvent.click(screen.getByRole('button', { name: 'Start Guiding' }));
        expect(onGuideStart).toHaveBeenCalledWith(2.5, 150);
    });

    it('calls the stop handler when Stop Guiding is clicked', async () => {
        const onGuideStop = vi.fn();
        render(<Harness onGuideStart={vi.fn()} onGuideStop={onGuideStop} />);
        await userEvent.click(screen.getByRole('tab', { name: 'Guiding' }));
        await userEvent.click(screen.getByRole('button', { name: 'Stop Guiding' }));
        expect(onGuideStop).toHaveBeenCalledTimes(1);
    });

    it('keeps Start Guiding greyed while the exposure is empty', async () => {
        const onGuideStart = vi.fn();
        render(<Harness onGuideStart={onGuideStart} onGuideStop={vi.fn()} />);
        await userEvent.click(screen.getByRole('tab', { name: 'Guiding' }));
        await userEvent.clear(screen.getByLabelText('Guiding Exposure Seconds'));
        const start = screen.getByRole('button', { name: 'Start Guiding' });
        expect(start).toBeDisabled();
        expect(screen.getByText(/Enter an exposure above 0/)).toBeInTheDocument();
        await userEvent.click(start);
        expect(onGuideStart).not.toHaveBeenCalled();
    });

    it('greys both buttons when commands are off', async () => {
        render(<Harness onGuideStart={vi.fn()} onGuideStop={vi.fn()} disabled />);
        await userEvent.click(screen.getByRole('tab', { name: 'Guiding' }));
        expect(screen.getByRole('button', { name: 'Start Guiding' })).toBeDisabled();
        expect(screen.getByRole('button', { name: 'Stop Guiding' })).toBeDisabled();
    });

    it('has no alignment exposure field, because alignment start takes only the target', () => {
        render(<Harness onGuideStart={vi.fn()} onGuideStop={vi.fn()} />);
        expect(screen.queryByLabelText('Alignment Exposure Seconds')).not.toBeInTheDocument();
        expect(screen.getByLabelText('Alignment Accuracy')).toBeInTheDocument();
    });
});

describe('guiding handlers', () => {
    beforeEach(() => vi.clearAllMocks());

    it('pass the exposure and gain to the backend call', async () => {
        guiding.startGuiding.mockResolvedValue(true);
        const { result } = renderHook(() => useObservatoryControl(() => undefined));
        await act(async () => { await result.current.handlers.onGuideStart(2.5, 150); });
        expect(guiding.startGuiding).toHaveBeenCalledWith(2.5, 150);
        expect(toast.emitToast).not.toHaveBeenCalledWith(expect.any(String), 'error');
    });

    it('show an error toast when guiding does not start or stop', async () => {
        guiding.startGuiding.mockResolvedValue(false);
        guiding.stopGuiding.mockResolvedValue(false);
        const { result } = renderHook(() => useObservatoryControl(() => undefined));
        await act(async () => { await result.current.handlers.onGuideStart(1, 100); });
        expect(toast.emitToast).toHaveBeenCalledWith(expect.stringContaining('did not start'), 'error');
        await act(async () => { await result.current.handlers.onGuideStop(); });
        expect(toast.emitToast).toHaveBeenCalledWith(expect.stringContaining('did not stop'), 'error');
    });
});
