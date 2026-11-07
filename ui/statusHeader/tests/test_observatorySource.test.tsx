/**
 * @fileoverview Tests for the "Observatory source" setting and the header chip: the chip shows only for a
 * simulator source, the control is two buttons, REAL and SIMULATED, and switching saves the
 * choice and waits for the backend.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { ObservatorySourceChip } from '../ObservatorySourceChip';
import { ObservatorySourceControl } from '../settings/components/ObservatorySourceControl';

let telescopeValue: Record<string, unknown> = {};
vi.mock('../../common/context/AstrometricsContext', () => ({
  useAstrometrics: () => ({ telescope: telescopeValue }),
}));
const setBackendRestartingMock = vi.fn();
vi.mock('../../common/services/backendApi', () => ({
  setBackendRestarting: (...args: unknown[]) => setBackendRestartingMock(...args),
}));

vi.mock('../../common/services/observatoryService', () => ({
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
function report(source: 'real' | 'simulated' | 'offline', overrides: Record<string, unknown> = {}) {
  const label = source === 'real' ? '' : 'SIMULATOR';
  return {
    source, chosen: source, label, restart_pending: false,
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

  it('shows no chip for the real telescope', async () => {
    vi.mocked(getObservatorySource).mockResolvedValue(report('real') as never);
    const { container } = render(<ObservatorySourceChip />);
    await waitFor(() => expect(getObservatorySource).toHaveBeenCalled());
    expect(container.textContent).toBe('');
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
    fireEvent.click(await screen.findByRole('button', { name: 'REAL' }));
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
    const failed = report('real', { failed: true, reason: 'indiserver is not installed', error: 'raw backend text' });
    vi.mocked(getObservatorySource).mockResolvedValue(failed as never);
    const { container, unmount } = render(<ObservatorySourceChip />);
    expect(await screen.findByText('SIMULATOR FAILED')).toBeTruthy();
    expect(container.querySelector('[title="indiserver is not installed"]')).not.toBeNull();
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
    expect((simulated as HTMLButtonElement).disabled).toBe(true);
    expect(simulated.getAttribute('title')).toContain('indiserver');
  });
});
