/**
 * @fileoverview Tests of the camera profile and calibration library panels:
 * the noise measurement asks before saving, and the "Show in calibration
 * library" listener.
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor, within } from '@testing-library/react';
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
    /** The noise table shows the recorded values with their evidence words and no example badge. */
    it('shows the recorded gain and read noise with their evidence words', () => {
        const withNoise = [{
            ...cameras[0],
            sensorNoise: [{
                cameraName: 'ZWO ASI533MM Pro',
                gainSetting: '0',
                offset: '10',
                binning: '1x1',
                temperatureC: -10,
                gain: { value: 0.783, status: 'measured', kind: 'measured', source: 'flat pairs' },
                readNoise: { value: 3.8, status: 'design_estimate', kind: 'datasheet', source: 'datasheet' },
                status: 'partly_measured',
            }],
        }] as unknown as typeof cameras;
        render(<CameraProfilesPanel cameras={withNoise} loading={false} error={null} onRetry={() => undefined} />);
        const table = screen.getByRole('table', { name: 'Recorded camera noise' });
        expect(table.textContent).toContain('0.783');
        expect(table.textContent).toContain('3.80');
        expect(table.textContent).toContain('Tentative');
        expect(table.textContent).toContain('Partly measured');
        expect(screen.queryByTestId('example-data-badge')).toBeNull();
        expect(screen.queryByText('Example data')).toBeNull();
        expect(screen.queryByTestId('camera-noise-not-measured')).toBeNull();
        expect(document.querySelector('[data-example="true"]')).toBeNull();
    });

    /** A camera with no entry says it is not measured. */
    it('says "Not measured" for a camera with no recorded noise', () => {
        render(<CameraProfilesPanel cameras={cameras} loading={false} error={null} onRetry={() => undefined} />);
        expect(screen.getByTestId('camera-noise-not-measured').textContent).toContain('Not measured');
        expect(screen.queryByRole('table', { name: 'Recorded camera noise' })).toBeNull();
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
        // Focus returns to the button that opened the confirm step.
        await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Save measurement' })));

        await user.click(screen.getByRole('button', { name: 'Save measurement' }));
        await user.click(screen.getByRole('button', { name: 'Yes, save' }));
        await waitFor(() => expect(callBackendMock).toHaveBeenCalledTimes(2));
        expect(callBackendMock.mock.calls[1][1].save).toBe(true);
        const savedText = await screen.findByText('Saved to the calibration library.');
        expect(savedText.getAttribute('role')).toBeNull();
        expect(document.activeElement).toBe(screen.getByTestId('noise-measurement'));
    });
});

describe('CalibrationLibraryPanel', () => {
    /** The old calibration panel look: camera picker, Darks table, totals. */
    it('shows the camera picker, the Darks table and the totals', async () => {
        callBackendMock.mockResolvedValue(libraryReply);
        renderWithQuery(<CalibrationLibraryPanel />);
        await screen.findByText('Darks');
        const picker = screen.getByLabelText('Camera:') as HTMLSelectElement;
        expect(picker.value).toBe('ZWO ASI533MM Pro');
        const table = screen.getByRole('table', { name: 'Darks' });
        const headers = within(table).getAllByRole('columnheader').map((header) => header.textContent);
        expect(headers).toEqual(['Exposure', 'Offset', 'Count']);
        expect(within(table).getAllByRole('row')).toHaveLength(3);
        expect(within(table).getAllByText('60s')).toHaveLength(2);
        expect(screen.getByText('Biases:').nextSibling?.textContent).toBe('30');
        expect(screen.getByText('Flat Frames:').nextSibling?.textContent).toBe('0');
    });

    /** Choosing another camera shows only that camera's rows. */
    it('filters by the chosen camera', async () => {
        callBackendMock.mockResolvedValue({
            ...libraryReply,
            darks: [...libraryReply.darks, { camera: 'Nikon D5300', iso: '800', offset: null, binning: '1x1', exposure: 30, count: 7 }],
            flats: [{ camera: 'Nikon D5300', iso: '800', filter: 'L', count: 4 }],
        });
        renderWithQuery(<CalibrationLibraryPanel />);
        await screen.findByText('Darks');
        await userEvent.selectOptions(screen.getByLabelText('Camera:'), 'Nikon D5300');
        const table = screen.getByRole('table', { name: 'Darks' });
        expect(within(table).getAllByRole('row')).toHaveLength(2);
        expect(within(table).getByText('30s')).toBeTruthy();
        expect(screen.getByText('Flat Frames:').nextSibling?.textContent).toBe('4');
        expect(screen.getByText('Biases:').nextSibling?.textContent).toBe('0');
    });

    /** Refresh reads the library again. */
    it('refreshes the library from the refresh button', async () => {
        callBackendMock.mockResolvedValue(libraryReply);
        renderWithQuery(<CalibrationLibraryPanel />);
        await screen.findByText('Darks');
        const before = callBackendMock.mock.calls.length;
        await userEvent.click(screen.getByRole('button', { name: 'Refresh calibration library' }));
        await waitFor(() => expect(callBackendMock.mock.calls.length).toBeGreaterThan(before));
    });

    /** Rows that differ only by gain or binning still get unique keys. */
    it('does not warn about duplicate React keys', async () => {
        const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
        callBackendMock.mockResolvedValue({
            ...libraryReply,
            darks: [
                { camera: 'ZWO ASI533MM Pro', iso: '100', offset: 10, binning: '1x1', exposure: 60, count: 20 },
                { camera: 'ZWO ASI533MM Pro', iso: '100', offset: 10, binning: '2x2', exposure: 60, count: 5 },
            ],
        });
        renderWithQuery(<CalibrationLibraryPanel />);
        await screen.findByText('Darks');
        const keyWarnings = errorSpy.mock.calls.filter((call) => String(call[0]).includes('same key'));
        errorSpy.mockRestore();
        expect(keyWarnings).toHaveLength(0);
    });

    /** A bias request highlights the Biases total. */
    it('highlights the Biases total for a bias request', async () => {
        callBackendMock.mockResolvedValue(libraryReply);
        renderWithQuery(<CalibrationLibraryPanel />);
        await screen.findByText('Darks');
        act(() => {
            window.dispatchEvent(new CustomEvent('astrometrics:showCalibrationRow', {
                detail: { calibrationKey: 'bias', captureSetting: { camera: 'ZWO ASI533MM Pro' }, rule: 'r' },
            }));
        });
        await waitFor(() => expect(document.querySelectorAll('[data-highlighted="true"]').length).toBe(1));
        expect(document.querySelector('[data-highlighted="true"]')?.textContent).toContain('Biases:');
    });

    /** The loading state shows while the first read is pending. */
    it('shows the loading state', () => {
        callBackendMock.mockReturnValue(new Promise(() => undefined));
        renderWithQuery(<CalibrationLibraryPanel />);
        expect(screen.getByText('Loading…').getAttribute('aria-busy')).toBe('true');
    });

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
        await screen.findByText('Darks');
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
        await screen.findByText('Darks');
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
