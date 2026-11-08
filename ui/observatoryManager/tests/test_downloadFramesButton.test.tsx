/**
 * @fileoverview Tests of the Download Frames button in the Observatory Manager
 * target list actions: it opens the download window for the picked target, is
 * off with a reason when no target is picked, and shows "Downloading…" (and is
 * off) while a download is running.
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import userEvent from '@testing-library/user-event';
import { render, screen } from '@testing-library/react';

const openIngestModalMock = vi.fn();
const state = { selectedTarget: 'M 31' as string | null, isActive: false };

vi.mock('../../common/services/backendApi', () => ({ callBackend: vi.fn() }));
vi.mock('../../common/components/IngestFramesModal', () => ({ IngestFramesModal: () => null }));
vi.mock('../../common/hooks/useIngestionManager', () => ({
    useIngestionManager: () => ({
        isActive: state.isActive,
        isIngestModalOpen: false,
        openIngestModal: openIngestModalMock,
        closeIngestModal: () => undefined,
    }),
}));
vi.mock('../ControlPanel', () => ({ ControlPanel: () => <div>controls</div> }));
vi.mock('../IndiStatusPanel', () => ({ IndiStatusPanel: () => <div>Devices</div> }));
vi.mock('../../common/hooks/useTelescopeStatus', () => ({
    useTelescopeStatus: () => ({ trackingStatus: 'Idle', telemetry: { alignmentAttempts: [], guidingHistory: [] } }),
}));
vi.mock('../../common/context/AstrometricsContext', () => ({ useAstrometrics: () => ({ connected: true }) }));
vi.mock('../../common/utils/appBootReadiness', () => ({ useReportModeReady: () => undefined }));
vi.mock('../../common/context/TargetContext', () => ({
    useTargetContext: () => ({
        selectedTarget: state.selectedTarget, pendingTarget: null, setPendingTarget: () => undefined,
        setSelectedTarget: () => undefined, reloadKey: 0, forceReload: () => undefined, raShared: '1', decShared: '2',
    }),
}));
vi.mock('../../common/context/RemoteStatusContext', () => ({ useRemoteStatusContext: () => ({ remoteTargets: [] }) }));
vi.mock('../../common/hooks/useTargetListLogic', () => ({
    useTargetListLogic: () => ({ items: [], filterPanel: {}, filterText: '', setFilterText: () => undefined, isLoading: false }),
}));
vi.mock('../../common/components/SelectableList', () => ({ SelectableList: () => <div>target list</div> }));
vi.mock('../../common/radioList/TargetListFilterPanel', () => ({ TargetListFilterPanel: () => null }));
vi.mock('../../common/components/AddTargetModal', () => ({ AddTargetModal: () => null }));
vi.mock('../../common/hooks/useCommandAvailability', () => ({
    useCommandAvailability: () => ({ canCommand: true, reason: '', canCommandCapability: () => true, reasonForCapability: () => '' }),
}));
vi.mock('../../common/hooks/useEquipmentConfiguration', () => ({
    useEquipmentConfiguration: () => ({ availableCameras: [], loading: false, error: null, reload: () => undefined }),
}));

import { ObservatoryManager } from '../ObservatoryManager';

/**
 * Renders the Observatory Manager inside a fresh query client (the night picker reads the night list).
 * @return The render result.
 */
function renderDisplay() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(<QueryClientProvider client={client}><ObservatoryManager /></QueryClientProvider>);
}

beforeEach(() => {
    openIngestModalMock.mockReset();
    state.selectedTarget = 'M 31';
    state.isActive = false;
});

describe('Observatory Manager Download Frames button', () => {
    it('opens the download window when a target is picked', async () => {
        renderDisplay();
        await userEvent.click(screen.getByRole('button', { name: 'Download Frames' }));
        expect(openIngestModalMock).toHaveBeenCalledTimes(1);
    });

    it('is off with a reason when no target is picked', () => {
        state.selectedTarget = null;
        renderDisplay();
        const button = screen.getByRole('button', { name: 'Download Frames' });
        expect(button).toBeDisabled();
        expect(button).toHaveAttribute('title', 'Pick a target in the list first.');
    });

    it('shows Downloading and is off while a download runs', () => {
        state.isActive = true;
        renderDisplay();
        expect(screen.getByRole('button', { name: 'Downloading…' })).toBeDisabled();
    });
});
