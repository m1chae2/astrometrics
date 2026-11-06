/**
 * @file test_trackingGuiding.test.tsx
 * @description Tests of the Alignment and Guiding sections of the Limits tab: the exposure and gain
 * fields take typed values, a greyed panel greys them, there are no Start or Stop Guiding buttons,
 * and the Alignment section has no exposure field.
 */
import React, { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TrackingSettings } from '../panels/TrackingSettings';

/** Holds the exposure and gain in state, as the Observatory Manager does. */
const Harness: React.FC<{
    section?: 'alignment' | 'guiding';
    disabled?: boolean;
}> = ({ section = 'guiding', disabled = false }) => {
    const [exposure, setExposure] = useState(1.0);
    const [gain, setGain] = useState(100);
    return (
        <TrackingSettings
            section={section}
            guidingExposure={exposure}
            guidingGain={gain}
            onGuidingExposureChange={setExposure}
            onGuidingGainChange={setGain}
            disabled={disabled}
        />
    );
};

describe('Telescope Control guiding controls', () => {
    beforeEach(() => vi.clearAllMocks());

    it('takes the typed exposure and gain', async () => {
        render(<Harness />);
        const exposure = screen.getByLabelText('Guiding Exposure Seconds') as HTMLInputElement;
        const gain = screen.getByLabelText('Guiding Gain') as HTMLInputElement;
        await userEvent.clear(exposure);
        await userEvent.type(exposure, '2.5');
        await userEvent.clear(gain);
        await userEvent.type(gain, '150');
        expect(exposure.value).toBe('2.5');
        expect(gain.value).toBe('150');
    });

    it('has no Start Guiding or Stop Guiding button', () => {
        render(<Harness />);
        expect(screen.queryByRole('button', { name: 'Start Guiding' })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Stop Guiding' })).not.toBeInTheDocument();
    });

    it('greys the fields when commands are off', () => {
        render(<Harness disabled />);
        expect(screen.getByLabelText('Guiding Exposure Seconds')).toBeDisabled();
        expect(screen.getByLabelText('Guiding Gain')).toBeDisabled();
    });

    it('has no alignment exposure field, because alignment start takes only the target', () => {
        render(<Harness section="alignment" />);
        expect(screen.queryByLabelText('Alignment Exposure Seconds')).not.toBeInTheDocument();
        expect(screen.getByLabelText('Alignment Accuracy')).toBeInTheDocument();
    });
});
