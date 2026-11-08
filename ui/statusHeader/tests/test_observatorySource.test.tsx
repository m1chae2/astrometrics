/**
 * @fileoverview Tests for the "Observatory source" setting and the header chip: the chip shows only for a
 * simulator source, the control is two buttons, REAL and SIMULATED, and switching saves the
 * choice and waits for the backend.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render as plainRender, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { socketClient } from '../../common/utils/socketClient';
import { OBSERVATORY_SOURCE_RETRY_MS } from '../hooks/useObservatorySource';
import { ObservatorySourceChip } from '../ObservatorySourceChip';
import { ObservatorySourceControl } from '../settings/components/ObservatorySourceControl';

/**
 * Renders with a fresh query client, as the app does.
 *
 * @param ui The element.
 * @return The render result.
 */
function render(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false } } });
  return plainRender(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

let telescopeValue: Record<string, unknown> = {};
vi.mock('../../common/context/AstrometricsContext', () => ({
  useAstrometrics: () => ({ telescope: telescopeValue }),
}));
const setBackendRestartingMock = vi.fn();
vi.mock('../../common/services/backendApi', () => ({
  setBackendRestarting: (...args: unknown[]) => setBackendRestartingMock(...args),
}));

vi.mock('../../common/services/observatoryService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../common/services/observatoryService')>()),
  getObservatorySource: vi.fn(),
  setObservatorySource: vi.fn(),
  waitForBackendRestart: vi.fn(),
}));

import {
  getObservatorySource,
  setObservatorySource,
  waitForBackendRestart,
} from '../../common/services/observatoryService';

/**
 * Builds a backend report for a source.
 *
 * @param source The source in force.
 * @param overrides Fields to change.
 * @return The report.
 */
function report(source: 'real' | 'simulated' | 'no_telescope', overrides: Record<string, unknown> = {}) {
  const label = source === 'real' ? '' : 'SIMULATOR';
  const launch = source === 'real' ? null
    : { label, live_hardware: false, disposable_library: true, offline: true, locked_keys: [] };
  return {
    source, chosen: source, label, launch, restart_pending: false,
    starting: false, running: source !== 'real', error: null, missing_programs: [], ...overrides,
  };
}

describe('Observatory source', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    telescopeValue = { trackingStatus: 'Not Tracking', guidingHistory: [], cameraStatus: 'Idle' };
    vi.mocked(setObservatorySource).mockResolvedValue({ restarting: true, source: 'simulated' });
    vi.mocked(waitForBackendRestart).mockResolvedValue(false);
  });

  /** A simulator chip is never hidden: it carries a short word for narrow windows and the full text for hover and screen readers. */
  it('gives the simulator chip a short label and the full text as its name', async () => {
    vi.mocked(getObservatorySource).mockResolvedValue(report('simulated') as never);
    const { container } = render(<ObservatorySourceChip />);
    const chip = await screen.findByRole('img', { name: 'SIMULATOR' });
    expect(chip.getAttribute('title')).toContain('not using your telescope');
    expect(container.querySelector('.status-chip__word--short')?.textContent).toBe('SIM');
  });

  it('shows no chip for the real telescope', async () => {
    vi.mocked(getObservatorySource).mockResolvedValue(report('real') as never);
    const { container } = render(<ObservatorySourceChip />);
    await waitFor(() => expect(getObservatorySource).toHaveBeenCalled());
    expect(container.textContent).toBe('');
  });

  /** The chip follows the launch report: a failed world, and a TPM run on a "real" source, both show. */
  it('drives the chip from the launch report', async () => {
    const cases: Array<[Record<string, unknown>, string | null]> = [
      [{ launch: null }, null],
      [{ launch: { label: 'LIVE', live_hardware: true, disposable_library: false, offline: false, locked_keys: [] } }, null],
      [{ failed: true, label: 'SIMULATOR FAILED', launch: { label: 'SIMULATOR', live_hardware: false, disposable_library: true, offline: true, locked_keys: [] } }, 'SIMULATOR FAILED'],
      [{ launch: { label: 'TPM RUN', live_hardware: false, disposable_library: true, offline: true, locked_keys: [] } }, 'TPM RUN'],
    ];
    for (const [overrides, expected] of cases) {
      vi.mocked(getObservatorySource).mockResolvedValue(report('real', overrides) as never);
      const { container, unmount } = render(<ObservatorySourceChip />);
      await waitFor(() => expect(getObservatorySource).toHaveBeenCalled());
      if (expected) {
        expect(await screen.findByText(expected)).toBeTruthy();
        if (expected.includes('FAILED')) expect(container.querySelector('[data-status="error"]')).not.toBeNull();
        else {
          expect(container.querySelector('[data-status="warning"]')).not.toBeNull();
          expect(container.querySelector('.header__source-chip')?.getAttribute('title')).toContain('Nothing leaves this computer');
        }
      } else {
        expect(container.textContent).toBe('');
      }
      unmount();
      vi.clearAllMocks();
    }
  });

  it('shows only a SIMULATOR chip for simulator sources, never a replay date', async () => {
    vi.mocked(getObservatorySource).mockResolvedValue(report('simulated') as never);
    const { unmount } = render(<ObservatorySourceChip />);
    expect(await screen.findByText('SIMULATOR')).toBeTruthy();
    unmount();
    vi.mocked(getObservatorySource).mockResolvedValue(report('simulated') as never);
    render(<ObservatorySourceChip />);
    expect(await screen.findByText('SIMULATOR')).toBeTruthy();
    expect(screen.queryByText(/REPLAY/)).toBeNull();
  });

  it('offers REAL and SIMULATED and asks before switching to the simulator', async () => {
    vi.mocked(getObservatorySource).mockResolvedValue(report('real') as never);
    render(<ObservatorySourceControl />);
    const simulated = await screen.findByRole('button', { name: 'SIMULATED' });
    const real = screen.getByRole('button', { name: 'REAL' });
    expect(real.getAttribute('aria-pressed')).toBe('true');
    expect(simulated.getAttribute('aria-pressed')).toBe('false');
    // The active button looks selected; the other one looks like a normal secondary button.
    expect(simulated.className).toContain('btn--secondary');
    expect(real.className).toContain('btn--active');
    // No Switch button and no help text.
    expect(screen.queryByRole('button', { name: 'Switch' })).toBeNull();
    expect(screen.queryByText(/separate library/)).toBeNull();
    // Pressing the active button does nothing.
    fireEvent.click(real);
    expect(screen.queryByRole('dialog')).toBeNull();
    // Pressing the other one asks first.
    fireEvent.click(simulated);
    expect(setObservatorySource).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog', { name: 'Restart on Simulated?' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Restart' }));
    await waitFor(() => expect(setObservatorySource).toHaveBeenCalledWith('simulated'));
    expect(setBackendRestartingMock).toHaveBeenCalledWith(true);
    expect(await screen.findByRole('alert')).toBeTruthy();
  });

  /** Switching back to the real telescope asks the same way. */
  it('asks "Restart on Real?" when the simulator is active', async () => {
    vi.mocked(getObservatorySource).mockResolvedValue(report('simulated') as never);
    render(<ObservatorySourceControl />);
    const simulated = await screen.findByRole('button', { name: 'SIMULATED' });
    await waitFor(() => expect(simulated.getAttribute('aria-pressed')).toBe('true'));
    fireEvent.click(screen.getByRole('button', { name: 'REAL' }));
    expect(screen.getByRole('dialog', { name: 'Restart on Real?' })).toBeTruthy();
  });

  /** Cancelling the question changes nothing. */
  it('does not restart when the question is cancelled', async () => {
    vi.mocked(getObservatorySource).mockResolvedValue(report('real') as never);
    render(<ObservatorySourceControl />);
    fireEvent.click(await screen.findByRole('button', { name: 'SIMULATED' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(setObservatorySource).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  /** A stronger line warns when the telescope is working. */
  it('warns in the question when tracking or an exposure is running', async () => {
    telescopeValue = { trackingStatus: 'Tracking', guidingHistory: [], cameraStatus: 'Exposing 30s' };
    vi.mocked(getObservatorySource).mockResolvedValue(report('real') as never);
    render(<ObservatorySourceControl />);
    fireEvent.click(await screen.findByRole('button', { name: 'SIMULATED' }));
    expect(screen.getByText(/The telescope is tracking, a sequence now/)).toBeTruthy();
  });

  /** Unsaved Settings changes would be lost, so the inactive button is disabled with its reason as a tooltip. */
  it('disables the inactive button while the Settings form has unsaved changes', async () => {
    vi.mocked(getObservatorySource).mockResolvedValue(report('real') as never);
    render(<ObservatorySourceControl hasUnsavedChanges />);
    const simulated = await screen.findByRole('button', { name: 'SIMULATED' });
    await waitFor(() => expect(getObservatorySource).toHaveBeenCalled());
    expect((simulated as HTMLButtonElement).disabled).toBe(true);
    expect(simulated.getAttribute('title')).toBe('Save or revert your changes first');
    expect(screen.queryByText('Save or revert your changes first.')).toBeNull();
  });

  /** The choice is two toggle buttons, not tabs or radios. */
  it('uses two toggle buttons for the source choice', async () => {
    vi.mocked(getObservatorySource).mockResolvedValue(report('real') as never);
    render(<ObservatorySourceControl />);
    await screen.findByRole('button', { name: 'SIMULATED' });
    expect(screen.getByRole('group', { name: 'Observatory source' })).toBeTruthy();
    expect(screen.queryAllByRole('tab')).toHaveLength(0);
    expect(screen.queryAllByRole('radio')).toHaveLength(0);
  });

  /** A failed simulator shows as a red chip with the reason on hover, and in the dialog, but no extra line in Settings. */
  it('shows the failure only in the chip and the dialog', async () => {
    const failed = report('real', { failed: true, label: 'SIMULATOR FAILED', reason: 'indiserver is not installed', error: 'raw backend text' });
    vi.mocked(getObservatorySource).mockResolvedValue(failed as never);
    const { container, unmount } = render(<ObservatorySourceChip />);
    expect(await screen.findByText('SIMULATOR FAILED')).toBeTruthy();
    // The tooltip gives the reason and says how to switch back.
    const title = container.querySelector('.header__source-chip')?.getAttribute('title') ?? '';
    expect(title).toContain('indiserver is not installed');
    expect(title).toContain('Open Settings, Observatory source');
    expect(container.querySelector('[data-status="error"]')).not.toBeNull();
    unmount();
    render(<ObservatorySourceControl />);
    fireEvent.click(await screen.findByRole('button', { name: 'SIMULATED' }));
    expect(screen.getByText(/failed to start last time: indiserver is not installed/)).toBeTruthy();
    expect(screen.queryByText(/raw backend text/)).toBeNull();
  });

  /** A missing simulator program disables SIMULATED with the reason as its tooltip. */
  it('explains missing simulator programs in the tooltip', async () => {
    vi.mocked(getObservatorySource).mockResolvedValue(report('real', { missing_programs: ['indiserver'] }) as never);
    render(<ObservatorySourceControl />);
    const simulated = await screen.findByRole('button', { name: 'SIMULATED' });
    await waitFor(() => expect((simulated as HTMLButtonElement).disabled).toBe(true));
    expect(simulated.getAttribute('title')).toContain('indiserver');
  });

  /** A launcher note, such as a missing star catalog, shows as a status line while the simulator runs. */
  it('shows the launcher notes only for the simulator', async () => {
    const note = 'gsc (star catalog): stars are not drawn';
    vi.mocked(getObservatorySource).mockResolvedValue(report('simulated', { notes: [note] }) as never);
    const { unmount } = render(<ObservatorySourceControl />);
    expect(await screen.findByText(note)).toBeTruthy();
    unmount();
    vi.mocked(getObservatorySource).mockResolvedValue(report('real', { notes: [note] }) as never);
    render(<ObservatorySourceControl />);
    await screen.findByRole('button', { name: 'SIMULATED' });
    expect(screen.queryByText(note)).toBeNull();
  });

  /** The backend is still starting when the app opens: the first question fails, a later one works. */
  it('shows the chip after the first question fails and a later one succeeds', async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(getObservatorySource)
        .mockRejectedValueOnce(new Error('Failed to fetch'))
        .mockResolvedValue(report('simulated') as never);
      render(<ObservatorySourceChip />);
      await act(async () => { await vi.advanceTimersByTimeAsync(10); });
      expect(screen.queryByText('SIMULATOR')).toBeNull();
      await act(async () => { await vi.advanceTimersByTimeAsync(OBSERVATORY_SOURCE_RETRY_MS + 10); });
      expect(screen.getByText('SIMULATOR')).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  /** While the simulator starts, the chip says so (a status, not an error), then becomes SIMULATOR. */
  it('shows SIMULATOR STARTING until the backend is ready, then SIMULATOR', async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(getObservatorySource)
        .mockResolvedValueOnce(report('simulated', { starting: true, ready: false, step: 'Seeding targets' }) as never)
        .mockResolvedValue(report('simulated', { ready: true }) as never);
      const { container } = render(<ObservatorySourceChip />);
      await act(async () => { await vi.advanceTimersByTimeAsync(10); });
      expect(screen.getByText('SIMULATOR STARTING')).toBeTruthy();
      expect(container.querySelector('[data-status="error"]')).toBeNull();
      expect(container.querySelector('.header__source-chip')?.getAttribute('title')).toContain('Seeding targets');
      await act(async () => { await vi.advanceTimersByTimeAsync(OBSERVATORY_SOURCE_RETRY_MS + 10); });
      expect(screen.getByText('SIMULATOR')).toBeTruthy();
      expect(screen.queryByText('SIMULATOR STARTING')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  /** Once the answer is final, the header stops asking, so an idle app makes no repeat calls. */
  it('does not ask again once the backend is ready', async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(getObservatorySource).mockResolvedValue(report('simulated', { ready: true }) as never);
      render(<ObservatorySourceChip />);
      await act(async () => { await vi.advanceTimersByTimeAsync(10); });
      expect(screen.getByText('SIMULATOR')).toBeTruthy();
      await act(async () => { await vi.advanceTimersByTimeAsync(OBSERVATORY_SOURCE_RETRY_MS * 10); });
      expect(getObservatorySource).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  /** The real telescope needs no chip and no polling either. */
  it('does not ask again for the real telescope', async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(getObservatorySource).mockResolvedValue(report('real', { ready: true }) as never);
      render(<ObservatorySourceChip />);
      await act(async () => { await vi.advanceTimersByTimeAsync(OBSERVATORY_SOURCE_RETRY_MS * 10); });
      expect(getObservatorySource).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  /** After the event socket drops and comes back, the backend may have restarted on another source: ask once more. */
  it('asks again when the event socket reconnects after a break', async () => {
    vi.mocked(getObservatorySource).mockResolvedValue(report('simulated', { ready: true }) as never);
    render(<ObservatorySourceChip />);
    expect(await screen.findByText('SIMULATOR')).toBeTruthy();
    expect(getObservatorySource).toHaveBeenCalledTimes(1);
    // A first connection is not a reconnect.
    act(() => { socketClient.emit('connected'); });
    expect(getObservatorySource).toHaveBeenCalledTimes(1);
    act(() => { socketClient.emit('disconnected'); });
    act(() => { socketClient.emit('connected'); });
    await waitFor(() => expect(getObservatorySource).toHaveBeenCalledTimes(2));
  });
});
