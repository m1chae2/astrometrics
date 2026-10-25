/**
 * @fileoverview Tests of the camera profile and calibration library panels:
 * the noise measurement asks before saving, and the "Show in calibration
 * library" listener.
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MeasurementStatus } from '../../common/types/backendTypes';

const callBackendMock = vi.fn();

vi.mock('../../common/services/backendApi', () => ({
    callBackend: (...args: unknown[]) => callBackendMock(...args),
}));
vi.mock('../../common/components/IngestFramesModal', () => ({ IngestFramesModal: () => null }));
vi.mock('../../common/hooks/useIngestionManager', () => ({ useIngestionManager: () => ({ isActive: false }) }));
vi.mock('../IndiStatusPanel', () => ({ IndiStatusPanel: () => <div>Devices</div> }));
vi.mock('../../common/hooks/useTelescopeStatus', () => ({
    useTelescopeStatus: () => ({ trackingStatus: 'Idle', telemetry: { alignmentAttempts: [] } }),
}));
vi.mock('../../common/context/AstrometricsContext', () => ({ useAstrometrics: () => ({ connected: true }) }));
vi.mock('../../common/utils/appBootReadiness', () => ({ useReportModeReady: () => undefined }));

import { CameraProfilesPanel } from '../equipment/CameraProfilesPanel';
import { CalibrationLibraryPanel } from '../calibration/CalibrationLibraryPanel';
import { matchCalibrationRows, toLibraryRows } from '../calibration/calibrationRowMatch';

const cameras = [{ name: 'ZWO ASI533MM Pro', pixelSizeUm: 3.76, sensorWidthPx: 3008, sensorHeightPx: 3008 }];
const libraryReply = {
    detail: 'counts',
    darks: [
        { camera: 'ZWO ASI533MM Pro', iso: '100', offset: 10, binning: '1x1', exposure: 60, count: 20 },
        { camera: 'ZWO ASI533MM Pro', iso: '0', offset: 10, binning: '1x1', exposure: 60, count: 5 },
    ],
    biases: [{ camera: 'ZWO ASI533MM Pro', iso: '100', offset: 10, binning: '1x1', count: 30 }],
    flats: [],
};

/**
 * Renders a component inside a fresh query client.
 * @param ui The component.
 * @return The render result.
 */
function renderWithQuery(ui: React.ReactElement) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

/**
 * Answers each backend method with a canned reply.
 * @param method The RPC method.
 * @return The reply.
 */
async function answer(method: string): Promise<unknown> {
    if (method === 'observatory:get_equipment_configuration') {
        return {
            telescope: { name: 'Scope', focalLengthMm: 400, focalRatio: 5 },
            camera: cameras[0],
            plateScaleArcsecPerPx: 1.94,
            fovWidthDeg: 1.6,
            fovHeightDeg: 1.6,
        };
    }
    if (method === 'observatory:list_cameras') return cameras;
    if (method === 'calibration:query') return libraryReply;
    if (method === 'observatory:safety_status') return { delegation_policy: { id: 'p', capability_delegations: [] } };
    throw new Error(`unexpected ${method}`);
}

beforeEach(() => {
    callBackendMock.mockReset();
    callBackendMock.mockImplementation((method: string) => answer(method));
});

describe('CameraProfilesPanel', () => {
    /** The example badge marks the noise table. */
    it('labels the noise table as example data', () => {
        render(<CameraProfilesPanel cameras={cameras} loading={false} error={null} onRetry={() => undefined} />);
        const example = screen.getByTestId('camera-noise-example');
        expect(example.getAttribute('data-example')).toBe('true');
        expect(screen.getByTestId('example-data-badge')).toBeTruthy();
    });

    /** Loading, error and empty states of the profile list. */
    it('shows loading, error and empty states', async () => {
        const retry = vi.fn();
        const { rerender } = render(<CameraProfilesPanel cameras={[]} loading error={null} onRetry={retry} />);
        expect(screen.getByText('Loading…').getAttribute('aria-busy')).toBe('true');
        rerender(<CameraProfilesPanel cameras={[]} loading={false} error="boom" onRetry={retry} />);
        await userEvent.click(screen.getByText('Try again'));
        expect(retry).toHaveBeenCalled();
        rerender(<CameraProfilesPanel cameras={[]} loading={false} error={null} onRetry={retry} />);
        expect(screen.getByText(/No camera profiles/)).toBeTruthy();
    });

    /** Measuring never saves; saving asks first and passes save=true only after yes. */
    it('asks before saving a measurement', async () => {
        const user = userEvent.setup();
        callBackendMock.mockImplementation(async (_method: string, params: { save: boolean }) => ({
            status: MeasurementStatus.MEASURED,
            cameraId: 'ZWO ASI533MM Pro',
            gainSetting: '100',
            offset: '10',
            binning: '1',
            gainEPerAdu: 0.9,
            readNoiseE: 1.6,
            saved: params.save,
        }));
        render(<CameraProfilesPanel cameras={cameras} loading={false} error={null} onRetry={() => undefined} />);
        const measureButton = screen.getByRole('button', { name: 'Measure noise' }) as HTMLButtonElement;
        expect(measureButton.disabled).toBe(true);
        await user.type(screen.getByLabelText('Gain'), '100');
        await user.type(screen.getByLabelText('Offset'), '10');
        await user.click(measureButton);
        await screen.findByTestId('noise-measurement');
        expect(callBackendMock).toHaveBeenCalledTimes(1);
        expect(callBackendMock.mock.calls[0][1].save).toBe(false);

        await user.click(screen.getByRole('button', { name: 'Save measurement' }));
        expect(callBackendMock).toHaveBeenCalledTimes(1);
        await user.click(screen.getByRole('button', { name: 'Cancel' }));
        expect(callBackendMock).toHaveBeenCalledTimes(1);

        await user.click(screen.getByRole('button', { name: 'Save measurement' }));
        await user.click(screen.getByRole('button', { name: 'Yes, save' }));
        await waitFor(() => expect(callBackendMock).toHaveBeenCalledTimes(2));
        expect(callBackendMock.mock.calls[1][1].save).toBe(true);
        expect(await screen.findByText('Saved to the calibration library.')).toBeTruthy();
    });
});

describe('CalibrationLibraryPanel', () => {
    /** An empty library says what to do. */
    it('shows the empty state', async () => {
        callBackendMock.mockResolvedValue({ detail: 'counts', darks: [], biases: [], flats: [] });
        renderWithQuery(<CalibrationLibraryPanel />);
        expect(await screen.findByText(/No calibration frames yet/)).toBeTruthy();
    });

    /** A failed read shows the error state. */
    it('shows the error state', async () => {
        callBackendMock.mockRejectedValue(new Error('down'));
        renderWithQuery(<CalibrationLibraryPanel />);
        expect(await screen.findByText('Could not load the calibration library')).toBeTruthy();
    });

    /** A matching request highlights exactly the matching rows. */
    it('highlights the rows a request matches', async () => {
        callBackendMock.mockResolvedValue(libraryReply);
        renderWithQuery(<CalibrationLibraryPanel />);
        await screen.findAllByText('Dark');
        act(() => {
            window.dispatchEvent(new CustomEvent('astrometrics:showCalibrationRow', {
                detail: { calibrationKey: 'dark', captureSetting: { camera: 'ZWO ASI533MM Pro', gainSetting: 100, offset: 10, binning: 1 }, rule: 'r' },
            }));
        });
        await waitFor(() => expect(document.querySelectorAll('[data-highlighted="true"]').length).toBe(1));
        expect(document.querySelector('[data-highlighted="true"]')?.textContent).toContain('20');
    });

    /** A request nothing matches says so. */
    it('says "No calibration row matches" when nothing matches', async () => {
        callBackendMock.mockResolvedValue(libraryReply);
        renderWithQuery(<CalibrationLibraryPanel />);
        await screen.findAllByText('Dark');
        act(() => {
            window.dispatchEvent(new CustomEvent('astrometrics:showCalibrationRow', {
                detail: { calibrationKey: 'flat', captureSetting: { camera: 'ZWO ASI533MM Pro', gainSetting: 100 }, rule: 'r' },
            }));
        });
        expect(await screen.findByText('No calibration row matches')).toBeTruthy();
    });
});

describe('matchCalibrationRows', () => {
    const rows = [...toLibraryRows(libraryReply.darks, 'dark'), ...toLibraryRows(libraryReply.biases, 'bias')];

    /** A value the statement leaves out is not compared. */
    it('does not compare values the statement leaves out', () => {
        const matches = matchCalibrationRows(rows, { calibrationKey: 'dark', captureSetting: { camera: 'ZWO ASI533MM Pro' }, rule: 'r' });
        expect(matches).toHaveLength(2);
    });

    /** A different gain does not match, and neither does a statement with nothing to match on. */
    it('is exact and refuses empty requests', () => {
        expect(matchCalibrationRows(rows, { calibrationKey: 'dark', captureSetting: { camera: 'ZWO ASI533MM Pro', gainSetting: 50 }, rule: 'r' })).toHaveLength(0);
        expect(matchCalibrationRows(rows, { calibrationKey: null, captureSetting: null, rule: 'r' })).toHaveLength(0);
    });
});
