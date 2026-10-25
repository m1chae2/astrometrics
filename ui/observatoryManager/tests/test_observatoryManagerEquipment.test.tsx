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
import { CalibrationLibraryPanel, formatExposure } from '../calibration/CalibrationLibraryPanel';

const cameras = [{ name: 'ZWO ASI533MM Pro', pixelSizeUm: 3.76, sensorWidthPx: 3008, sensorHeightPx: 3008 }];
const libraryReply = {
    detail: 'counts',
    darks: [
        { row_key: 'dark-a', camera: 'ZWO ASI533MM Pro', iso: '100', offset: 10, binning: '1x1', exposure: 60, count: 20 },
        { row_key: 'dark-b', camera: 'ZWO ASI533MM Pro', iso: '0', offset: 10, binning: '1x1', exposure: 60, count: 5 },
    ],
    biases: [{ row_key: 'bias-a', camera: 'ZWO ASI533MM Pro', iso: '100', offset: 10, binning: '1x1', count: 30 }],
    flats: [],
    camera_totals: [{ camera: 'ZWO ASI533MM Pro', dark_count: 25, bias_count: 30, flat_count: 0 }],
};

/** Answers `calibration:query`: the counts reply, or the matched keys when asked to match. */
function answerCalibration(matchedRowKeys: string[], counts: Record<string, unknown> = libraryReply) {
    return async (_method: string, params: { detail?: string }) =>
        params?.detail === 'match' ? { detail: 'match', matched_row_keys: matchedRowKeys, camera_totals: [] } : counts;
}

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
        expect(table.textContent).toContain('Gain 0 · Offset 10 · 1x1 · −10 °C');
        expect(table.textContent).toContain('Measured');
        expect(table.textContent).toContain('Tentative');
        expect(table.textContent).not.toContain('MEASURED');
        expect(screen.queryByText('Recorded noise')).toBeNull();
        expect(table.querySelectorAll('.status-chip').length).toBe(2);
        expect(screen.queryByTestId('example-data-badge')).toBeNull();
        expect(screen.queryByText('Example data')).toBeNull();
        expect(screen.queryByTestId('camera-noise-not-measured')).toBeNull();
        expect(document.querySelector('[data-example="true"]')).toBeNull();
    });

    /** A camera with no entry says it is not measured. */
    it('says "Not measured" for a camera with no recorded noise', () => {
        render(<CameraProfilesPanel cameras={cameras} loading={false} error={null} onRetry={() => undefined} />);
        expect(screen.getByTestId('camera-noise-not-measured').textContent).toMatch(/^Not measured: /);
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
        await user.click(screen.getByRole('button', { name: 'Measure noise' }));
        const measureButton = screen.getByRole('button', { name: 'Measure' }) as HTMLButtonElement;
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

    /** The measurement form is closed until the header button opens it, and closing returns focus. */
    it('opens the measurement form on demand and returns focus to the button on close', async () => {
        const user = userEvent.setup();
        render(<CameraProfilesPanel cameras={cameras} loading={false} error={null} onRetry={() => undefined} />);
        const opener = screen.getByRole('button', { name: 'Measure noise' });
        expect(opener.getAttribute('aria-expanded')).toBe('false');
        expect(screen.queryByLabelText('Gain')).toBeNull();
        await user.click(opener);
        expect(opener.getAttribute('aria-expanded')).toBe('true');
        expect(document.activeElement).toBe(screen.getByLabelText(/^Camera/));
        await user.click(screen.getByRole('button', { name: 'Close' }));
        expect(opener.getAttribute('aria-expanded')).toBe('false');
        expect(screen.queryByLabelText('Gain')).toBeNull();
        expect(document.activeElement).toBe(opener);
        // Escape closes it too, and focus returns again.
        await user.click(opener);
        await user.keyboard('{Escape}');
        expect(screen.queryByLabelText('Gain')).toBeNull();
        expect(document.activeElement).toBe(opener);
    });

    /** The opener is reachable with the keyboard alone. */
    it('opens the measurement form from the keyboard', async () => {
        const user = userEvent.setup();
        render(<CameraProfilesPanel cameras={cameras} loading={false} error={null} onRetry={() => undefined} />);
        await user.tab();
        expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Measure noise' }));
        await user.keyboard('{Enter}');
        expect(screen.getByLabelText('Gain')).toBeTruthy();
    });
});

describe('formatExposure', () => {
    /** Sub-second exposures read in milliseconds and longer ones in seconds. */
    it('picks milliseconds or seconds', () => {
        expect(formatExposure(0.01)).toBe('10 ms');
        expect(formatExposure(0.00025)).toBe('0.25 ms');
        expect(formatExposure(30)).toBe('30 s');
        expect(formatExposure(null)).toBe('—');
    });
});

describe('CalibrationLibraryPanel', () => {
    /** The old calibration panel look: camera picker, Darks table, totals. */
    it('shows the camera picker, the Darks table and the totals', async () => {
        callBackendMock.mockResolvedValue(libraryReply);
        renderWithQuery(<CalibrationLibraryPanel />);
        await screen.findByText('Darks:');
        const picker = screen.getByLabelText('Camera:') as HTMLSelectElement;
        expect(picker.value).toBe('ZWO ASI533MM Pro');
        // The Darks table is always shown; there is no Show darks toggle.
        expect(screen.queryByRole('button', { name: 'Show darks' })).toBeNull();
        const table = screen.getByRole('table', { name: 'Darks' });
        const headers = within(table).getAllByRole('columnheader').map((header) => header.textContent);
        expect(headers).toEqual(['Exposure', 'Offset', 'Count']);
        expect(within(table).getAllByRole('row')).toHaveLength(3);
        expect(within(table).getAllByText('60 s')).toHaveLength(2);
        expect(screen.getByText('Darks:').nextSibling?.textContent).toBe('25');
        expect(screen.getByText('Biases:').nextSibling?.textContent).toBe('30');
        expect(screen.getByText('Flats:').nextSibling?.textContent).toBe('0');
    });

    /** Choosing another camera shows only that camera's rows. */
    it('filters by the chosen camera', async () => {
        callBackendMock.mockResolvedValue({
            ...libraryReply,
            darks: [...libraryReply.darks, { row_key: 'dark-n', camera: 'Nikon D5300', iso: '800', offset: null, binning: '1x1', exposure: 30, count: 7 }],
            flats: [{ row_key: 'flat-n', camera: 'Nikon D5300', iso: '800', filter: 'L', count: 4 }],
            camera_totals: [
                ...libraryReply.camera_totals,
                { camera: 'Nikon D5300', dark_count: 7, bias_count: 0, flat_count: 4 },
            ],
        });
        renderWithQuery(<CalibrationLibraryPanel />);
        await screen.findByText('Darks:');
        await userEvent.selectOptions(screen.getByLabelText('Camera:'), 'Nikon D5300');
        const table = screen.getByRole('table', { name: 'Darks' });
        expect(within(table).getAllByRole('row')).toHaveLength(2);
        expect(within(table).getByText('30 s')).toBeTruthy();
        expect(screen.getByText('Flats:').nextSibling?.textContent).toBe('4');
        expect(screen.getByText('Biases:').nextSibling?.textContent).toBe('0');
    });

    /** Refresh reads the library again. */
    it('refreshes the library from the refresh button', async () => {
        callBackendMock.mockResolvedValue(libraryReply);
        renderWithQuery(<CalibrationLibraryPanel />);
        await screen.findByText('Darks:');
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
                { row_key: 'k1', camera: 'ZWO ASI533MM Pro', iso: '100', offset: 10, binning: '1x1', exposure: 60, count: 20 },
                { row_key: 'k2', camera: 'ZWO ASI533MM Pro', iso: '100', offset: 10, binning: '2x2', exposure: 60, count: 5 },
            ],
        });
        renderWithQuery(<CalibrationLibraryPanel />);
        await screen.findByText('Darks:');
        const keyWarnings = errorSpy.mock.calls.filter((call) => String(call[0]).includes('same key'));
        errorSpy.mockRestore();
        expect(keyWarnings).toHaveLength(0);
    });

    /** A bias request highlights the Biases total. */
    it('highlights the Biases total for a bias request', async () => {
        callBackendMock.mockImplementation(answerCalibration(['bias-a']));
        renderWithQuery(<CalibrationLibraryPanel />);
        await screen.findByText('Darks:');
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
        callBackendMock.mockResolvedValue({ detail: 'counts', darks: [], biases: [], flats: [], camera_totals: [] });
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
        callBackendMock.mockImplementation(answerCalibration(['dark-a']));
        renderWithQuery(<CalibrationLibraryPanel />);
        await screen.findByText('Darks:');
        act(() => {
            window.dispatchEvent(new CustomEvent('astrometrics:showCalibrationRow', {
                detail: { calibrationKey: 'dark', captureSetting: { camera: 'ZWO ASI533MM Pro', gainSetting: 100, offset: 10, binning: 1 }, rule: 'r' },
            }));
        });
        await waitFor(() => expect(document.querySelectorAll('[data-highlighted="true"]').length).toBe(1));
        expect(document.querySelector('[data-highlighted="true"]')?.textContent).toContain('20');
        expect(callBackendMock).toHaveBeenCalledWith('calibration:query', {
            detail: 'match', camera_id: 'ZWO ASI533MM Pro', kind: 'dark', gain: 100, offset: 10, binning: 1,
        });
    });

    /** A request nothing matches says so. */
    it('says "No calibration row matches" when nothing matches', async () => {
        callBackendMock.mockImplementation(answerCalibration([]));
        renderWithQuery(<CalibrationLibraryPanel />);
        await screen.findByText('Darks:');
        act(() => {
            window.dispatchEvent(new CustomEvent('astrometrics:showCalibrationRow', {
                detail: { calibrationKey: 'flat', captureSetting: { camera: 'ZWO ASI533MM Pro', gainSetting: 100 }, rule: 'r' },
            }));
        });
        expect(await screen.findByText('No calibration row matches')).toBeTruthy();
    });
});
