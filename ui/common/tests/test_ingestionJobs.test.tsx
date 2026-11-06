/**
 * @file test_ingestionJobs.test.tsx
 * @description Tests that a frame download keeps being followed and is finished by
 * the app-wide `IngestionJobsProvider` with no display mounted (the Observatory
 * Manager may be hidden and paused), and that a finished download is finished once.
 */

import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const fetchIngestStatusMock = vi.fn();
const fetchTargetObjectMock = vi.fn();
const createTargetMock = vi.fn();
const forceReloadMock = vi.fn();
const setSelectedTargetMock = vi.fn();
const setPendingTargetMock = vi.fn();
const emitToastMock = vi.fn();
let selectedTargetValue = '';

vi.mock('../services/imaging/ingestionService', () => ({
  fetchIngestStatus: (...args: unknown[]) => fetchIngestStatusMock(...args),
}));
vi.mock('../services/targetService', () => ({
  fetchTargetObject: (...args: unknown[]) => fetchTargetObjectMock(...args),
  createTarget: (...args: unknown[]) => createTargetMock(...args),
}));
vi.mock('../utils/emitToast', () => ({ emitToast: (...args: unknown[]) => emitToastMock(...args) }));
vi.mock('../context/TargetContext', () => ({
  useTargetContext: () => ({
    selectedTarget: selectedTargetValue,
    forceReload: forceReloadMock,
    setSelectedTarget: setSelectedTargetMock,
    setPendingTarget: setPendingTargetMock,
  }),
}));

import {
  CALIBRATION_INGESTION_CHANNEL,
  IngestionJobsProvider,
  OBSERVATORY_INGESTION_CHANNEL,
  useIngestionJobs,
} from '../context/IngestionJobsContext';
import { on as onEvent } from '../utils/eventBus';

const latest: { controls: ReturnType<typeof useIngestionJobs> | null } = { controls: null };
/** The newest controls the provider gave. */
const current = (): ReturnType<typeof useIngestionJobs> => latest.controls as ReturnType<typeof useIngestionJobs>;

/** Captures the provider's controls so a test can start a download without any display. */
const Capture: React.FC = () => {
  const controls = useIngestionJobs();
  React.useEffect(() => {
    latest.controls = controls;
  });
  return null;
};

/** Renders only the provider, with no display below it. */
function renderProviderOnly(queryClient = new QueryClient()) {
  return render(
    <QueryClientProvider client={queryClient}>
      <IngestionJobsProvider>
        <Capture />
      </IngestionJobsProvider>
    </QueryClientProvider>
  );
}

describe('IngestionJobsProvider', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    fetchTargetObjectMock.mockResolvedValue(null);
    createTargetMock.mockResolvedValue(undefined);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  /** The download of a target finishes with no Observatory Manager mounted. */
  it('finishes a target download with no display mounted', async () => {
    fetchIngestStatusMock
      .mockResolvedValueOnce({ status: 'running', progress: '1/2', logs: ['one'] })
      .mockResolvedValue({ status: 'completed', progress: '2/2', logs: ['one', 'two'] });
    const targetsUpdated = vi.fn();
    const detach = onEvent('targetsUpdated', targetsUpdated);
    renderProviderOnly();

    act(() => current().trackJob(OBSERVATORY_INGESTION_CHANNEL, 'job-1', 'M 13'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(current().jobs[OBSERVATORY_INGESTION_CHANNEL].status).toBe('running');
    expect(current().jobs[OBSERVATORY_INGESTION_CHANNEL].progress).toBe('1/2');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(current().jobs[OBSERVATORY_INGESTION_CHANNEL].status).toBe('completed');
    expect(createTargetMock).toHaveBeenCalledWith('M 13');
    expect(forceReloadMock).toHaveBeenCalledTimes(1);
    expect(targetsUpdated).toHaveBeenCalledTimes(1);
    expect(setSelectedTargetMock).toHaveBeenCalledWith('M 13');
    expect(setPendingTargetMock).toHaveBeenCalledWith('M 13');

    // Polling stopped and the finish step does not run again.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(forceReloadMock).toHaveBeenCalledTimes(1);
    detach();
  });

  /** A calibration download refreshes the calibration queries and creates no target. */
  it('refreshes the calibration queries when a calibration download ends', async () => {
    fetchIngestStatusMock.mockResolvedValue({ status: 'completed', progress: '', logs: [] });
    const queryClient = new QueryClient();
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    renderProviderOnly(queryClient);

    act(() => current().trackJob(CALIBRATION_INGESTION_CHANNEL, 'job-2', 'Calibration'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['calibrationLibrary'] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['calibrationHealth'] });
    expect(createTargetMock).not.toHaveBeenCalled();
  });

  /** A reset forgets the download. */
  it('forgets a download when it is reset', () => {
    renderProviderOnly();
    act(() => current().trackJob(OBSERVATORY_INGESTION_CHANNEL, 'job-3', 'M 81'));
    expect(current().jobs[OBSERVATORY_INGESTION_CHANNEL].jobId).toBe('job-3');
    act(() => current().resetJob(OBSERVATORY_INGESTION_CHANNEL));
    expect(current().jobs[OBSERVATORY_INGESTION_CHANNEL]).toBeUndefined();
  });
});

describe('IngestionJobsProvider alerts and selection', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    emitToastMock.mockReset();
    selectedTargetValue = 'M 31';
    fetchTargetObjectMock.mockResolvedValue({});
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  /** A failed download is reported once, as an error that asks for a notification. */
  it('shows one error toast with a notification when a download fails', async () => {
    fetchIngestStatusMock.mockResolvedValue({ status: 'failed', progress: '', logs: [] });
    renderProviderOnly();
    act(() => current().trackJob(OBSERVATORY_INGESTION_CHANNEL, 'job-f', 'M 13'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(emitToastMock).toHaveBeenCalledTimes(1);
    expect(emitToastMock).toHaveBeenCalledWith('Frame download for M 13 failed', 'error', 'download', { notify: true });
  });

  /** If the user picked another target while the download ran, the selection stays and a toast offers Open. */
  it('does not move the selection if the user moved on, and offers an Open action', async () => {
    fetchIngestStatusMock.mockResolvedValue({ status: 'completed', progress: '', logs: [] });
    renderProviderOnly();
    act(() => current().trackJob(OBSERVATORY_INGESTION_CHANNEL, 'job-m', 'M 13'));
    selectedTargetValue = 'M 42';
    // Any state change re-renders the provider, which reads the new selection.
    act(() => current().appendLog(OBSERVATORY_INGESTION_CHANNEL, 'user picked another target'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(setSelectedTargetMock).not.toHaveBeenCalled();
    const call = emitToastMock.mock.calls.find((args) => String(args[0]).includes('M 13'));
    expect(call).toBeTruthy();
    act(() => call?.[3].action.run());
    expect(setSelectedTargetMock).toHaveBeenCalledWith('M 13');
  });

  /** If the selection did not change, the finished target is selected as before. */
  it('selects the target when the selection is unchanged', async () => {
    fetchIngestStatusMock.mockResolvedValue({ status: 'completed', progress: '', logs: [] });
    renderProviderOnly();
    act(() => current().trackJob(OBSERVATORY_INGESTION_CHANNEL, 'job-s', 'M 13'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(setSelectedTargetMock).toHaveBeenCalledWith('M 13');
  });

  /** A status check that keeps failing logs one warning per job, never an error. */
  it('warns once per job when the status check fails', async () => {
    fetchIngestStatusMock.mockRejectedValue(new Error('blip'));
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    renderProviderOnly();
    act(() => current().trackJob(OBSERVATORY_INGESTION_CHANNEL, 'job-w', 'M 13'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(warn).toHaveBeenCalledTimes(1);
    expect(error).not.toHaveBeenCalled();
    warn.mockRestore();
    error.mockRestore();
  });
});
