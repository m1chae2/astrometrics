/**
 * @fileoverview Tests of target selection in the Observatory Manager: a click selects the target
 * (one highlighted row, and Download Frames, Slew to Target and Start Alignment all use it), and
 * the buttons are off with a reason when no target is picked.
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import userEvent from '@testing-library/user-event';
import { render, screen } from '@testing-library/react';

const { slewToTargetMock, startAlignmentMock, fetchTargetObjectMock, ingestionTarget, targetState } = vi.hoisted(() => ({
    slewToTargetMock: vi.fn(async (_id: string) => true),
    startAlignmentMock: vi.fn(async (_ra: string, _dec: string) => true),
    fetchTargetObjectMock: vi.fn(async (_id: string) => ({ ra: '16h 41m 42s', dec: '+36d 27m 36s' })),
    ingestionTarget: { current: undefined as string | undefined },
    targetState: { selected: 'Alnath' as string | undefined, pending: 'Alnath' as string | undefined },
}));

vi.mock('../../common/services/backendApi', () => ({ callBackend: vi.fn() }));
vi.mock('../../common/services/telescope/motionService', () => ({
    slewToTarget: slewToTargetMock,
    parkTelescope: vi.fn(), unparkTelescope: vi.fn(), abortTelescopeMotion: vi.fn(),
    setTelescopeTracking: vi.fn(), moveTelescope: vi.fn(), setTelescopeSlewRate: vi.fn(),
}));
vi.mock('../../common/services/alignmentApi', () => ({ startAlignment: startAlignmentMock, stopAlignment: vi.fn() }));
vi.mock('../../common/services/targetService', () => ({ createTarget: vi.fn(), fetchTargetObject: fetchTargetObjectMock }));
vi.mock('../../common/components/IngestFramesModal', () => ({ IngestFramesModal: () => null }));
vi.mock('../../common/hooks/useIngestionManager', () => ({
    useIngestionManager: (target: string | undefined) => {
        ingestionTarget.current = target;
        return { isActive: false, isIngestModalOpen: false, openIngestModal: () => undefined, closeIngestModal: () => undefined };
    },
}));
vi.mock('../ControlPanel', () => ({
    ControlPanel: (props: { onStartAlignment?: () => void; startAlignmentBlockedReason?: string | null }) => (
        <button disabled={Boolean(props.startAlignmentBlockedReason)} title={props.startAlignmentBlockedReason ?? ''} onClick={() => props.onStartAlignment?.()}>
            Start Alignment
        </button>
    ),
}));
vi.mock('../IndiStatusPanel', () => ({ IndiStatusPanel: () => <div>Devices</div> }));
vi.mock('../../common/hooks/useTelescopeStatus', () => ({
    useTelescopeStatus: () => ({ trackingStatus: 'Idle', telemetry: { alignmentAttempts: [], guidingHistory: [] } }),
}));
vi.mock('../../common/context/AstrometricsContext', () => ({ useAstrometrics: () => ({ connected: true, activeJobs: [] }) }));
vi.mock('../../common/utils/appBootReadiness', () => ({ useReportModeReady: () => undefined }));
vi.mock('../../common/context/TargetContext', () => ({
    useTargetContext: () => {
        const [selected, setSelected] = React.useState(targetState.selected);
        const [pending, setPending] = React.useState(targetState.pending);
        return {
            selectedTarget: selected, pendingTarget: pending, setPendingTarget: setPending,
            setSelectedTarget: setSelected, reloadKey: 0, forceReload: () => undefined,
        };
    },
}));
vi.mock('../../common/context/RemoteStatusContext', () => ({ useRemoteStatusContext: () => ({ remoteTargets: [] }) }));
vi.mock('../../common/hooks/useTargetListLogic', () => ({
    useTargetListLogic: () => ({
        items: [
            { id: 'Alnath', value: 'Alnath', label: 'Alnath' },
            { id: 'M 13', value: 'M 13', label: 'M 13' },
        ],
        filterPanel: {}, filterText: '', setFilterText: () => undefined, isLoading: false,
    }),
}));
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
 * Renders the Observatory Manager inside a fresh query client.
 * @return The render result.
 */
function renderDisplay() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(<QueryClientProvider client={client}><ObservatoryManager /></QueryClientProvider>);
}

beforeEach(() => {
    slewToTargetMock.mockClear();
    startAlignmentMock.mockClear();
    fetchTargetObjectMock.mockClear();
    targetState.selected = 'Alnath';
    targetState.pending = 'Alnath';
});

describe('Observatory Manager target selection', () => {
    /** One click leaves exactly one row selected and the buttons use that target. */
    it('selects the clicked target and uses it for the buttons', async () => {
        renderDisplay();
        await userEvent.click(screen.getByText('M 13'));
        const selectedRows = document.querySelectorAll('.selectable-list__item--selected');
        expect(selectedRows).toHaveLength(1);
        expect(selectedRows[0]?.textContent).toContain('M 13');
        expect(ingestionTarget.current).toBe('M 13');
        await userEvent.click(screen.getByRole('button', { name: 'Slew to Target' }));
        expect(slewToTargetMock).toHaveBeenCalledWith('M 13');
        await userEvent.click(screen.getByRole('button', { name: 'Start Alignment' }));
        expect(fetchTargetObjectMock).toHaveBeenCalledWith('M 13');
        expect(startAlignmentMock).toHaveBeenCalledWith('16h 41m 42s', '+36d 27m 36s');
    });

    /** With no target the buttons are off and say why. */
    it('turns the buttons off with a reason when no target is picked', () => {
        targetState.selected = undefined;
        targetState.pending = undefined;
        renderDisplay();
        expect(screen.getByRole('button', { name: 'Slew to Target' })).toBeDisabled();
        expect(screen.getByRole('button', { name: 'Start Alignment' })).toBeDisabled();
        expect(screen.getByRole('button', { name: 'Start Alignment' })).toHaveAttribute('title', 'Pick a target in the list first.');
    });
});
