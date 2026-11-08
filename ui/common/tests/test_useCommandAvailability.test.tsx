/**
 * @fileoverview Tests for the command rules: commands are on only for a simulator that is ready, and stay off
 * (with the existing texts) for the real telescope and while the source is unknown or starting.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../services/backendApi', () => ({
  callBackend: vi.fn(),
}));
vi.mock('../services/observatoryService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../services/observatoryService')>()),
  getObservatorySource: vi.fn(),
}));

import { ObservatoryCapability } from '../types/backendTypes';
import { callBackend } from '../services/backendApi';
import { getObservatorySource } from '../services/observatoryService';
import {
  COMMANDS_OFF_TEXT,
  SIMULATOR_COMMANDS_TEXT,
  useCommandAvailability,
} from '../hooks/useCommandAvailability';

/**
 * Builds a backend report for a source.
 *
 * @param source The source in force.
 * @param overrides Fields to change.
 * @return The report.
 */
function report(source: 'real' | 'simulated' | 'no_telescope', overrides: Record<string, unknown> = {}) {
  return {
    source, chosen: source, label: '',
    launch: source === 'simulated' ? { label: 'SIMULATOR', live_hardware: false, disposable_library: true, offline: true, locked_keys: [] } : null, restart_pending: false, starting: false, ready: true,
    running: source !== 'real', error: null, missing_programs: [],
    commands: source === 'simulated' ? { allowed: ['OBSERVATORY_SAFETY', 'MOUNT_CONTROL'], off: { AUTOFOCUS: 'needs a focus model' }, error: null } : null,
    ...overrides,
  };
}

/**
 * Sets the mount's delegation state that the backend's safety status reports.
 *
 * @param state The state, or null for an empty policy.
 */
function mountIs(state: string | null) {
  vi.mocked(callBackend).mockResolvedValue({
    delegation_policy: { capability_delegations: state ? [{ capability: 'MOUNT_CONTROL', state }] : [] },
  } as never);
}

/**
 * Renders the hook inside a fresh query client.
 *
 * @return The hook result.
 */
function renderAvailability() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(() => useCommandAvailability(), { wrapper });
}

describe('useCommandAvailability per capability', () => {
  /** An authoritative mount does not turn on a delegated focuser, and the reason names the focuser. */
  it('decides each capability from its own delegation state', async () => {
    vi.mocked(getObservatorySource).mockResolvedValue(report('simulated') as never);
    vi.mocked(callBackend).mockResolvedValue({
      delegation_policy: { capability_delegations: [
        { capability: 'MOUNT_CONTROL', state: 'AUTHORITATIVE' },
        { capability: 'AUTOFOCUS', state: 'DELEGATED' },
        { capability: 'AUTOGUIDING', state: 'AUTHORITATIVE' },
      ] },
    } as never);
    const { result } = renderAvailability();
    await waitFor(() => expect(result.current.canCommandCapability(ObservatoryCapability.MOUNT_CONTROL)).toBe(true));
    expect(result.current.canCommandCapability(ObservatoryCapability.AUTOGUIDING)).toBe(true);
    expect(result.current.canCommandCapability(ObservatoryCapability.AUTOFOCUS)).toBe(false);
    expect(result.current.canCommandCapability(ObservatoryCapability.CAPTURE_ORCHESTRATION)).toBe(false);
    expect(result.current.reasonForCapability(ObservatoryCapability.MOUNT_CONTROL)).toBe('');
    expect(result.current.reasonForCapability(ObservatoryCapability.AUTOFOCUS)).toContain('the focuser');
  });

  /** A failed source keeps every capability off, even when the policy says AUTHORITATIVE. */
  it('keeps every capability off for a failed source', async () => {
    mountIs('AUTHORITATIVE');
    vi.mocked(getObservatorySource).mockResolvedValue(report('no_telescope', { failed: true }) as never);
    const { result } = renderAvailability();
    await waitFor(() => expect(getObservatorySource).toHaveBeenCalled());
    expect(result.current.canCommandCapability(ObservatoryCapability.MOUNT_CONTROL)).toBe(false);
  });
});

describe('useCommandAvailability', () => {
  beforeEach(() => { vi.clearAllMocks(); mountIs('AUTHORITATIVE'); });

  /** A ready simulator is commandable, and says where the commands go. */
  it('allows commands for a ready simulator', async () => {
    vi.mocked(getObservatorySource).mockResolvedValue(report('simulated') as never);
    const { result } = renderAvailability();
    await waitFor(() => expect(result.current.canCommand).toBe(true));
    expect(result.current.reason).toContain(SIMULATOR_COMMANDS_TEXT);
    expect(result.current.reason).toContain('autofocus (needs a focus model)');
  });

  /** The real telescope keeps today's fail-safe answer and text. */
  it('keeps commands off for the real telescope', async () => {
    mountIs('DELEGATED');
    vi.mocked(getObservatorySource).mockResolvedValue(report('real') as never);
    const { result } = renderAvailability();
    await waitFor(() => expect(result.current.isChecking).toBe(false));
    await waitFor(() => expect(getObservatorySource).toHaveBeenCalled());
    expect(result.current.canCommand).toBe(false);
    expect(result.current.reason).toBe(COMMANDS_OFF_TEXT);
  });

  /** The answer follows the policy, not the source: a delegated mount stays off even for a simulator, and an authoritative one is on. */
  it('decides from the delegation policy', async () => {
    mountIs('DELEGATED');
    vi.mocked(getObservatorySource).mockResolvedValue(report('simulated') as never);
    const off = renderAvailability();
    await waitFor(() => expect(off.result.current.isChecking).toBe(false));
    expect(off.result.current.canCommand).toBe(false);
    off.unmount();
    mountIs('AUTHORITATIVE');
    vi.mocked(getObservatorySource).mockResolvedValue(report('real') as never);
    const on = renderAvailability();
    await waitFor(() => expect(on.result.current.canCommand).toBe(true));
    // Unknown policy (no mount entry) is off with the existing text.
    on.unmount();
    mountIs(null);
    const unknown = renderAvailability();
    await waitFor(() => expect(unknown.result.current.isChecking).toBe(false));
    expect(unknown.result.current.canCommand).toBe(false);
    expect(unknown.result.current.reason).toBe(COMMANDS_OFF_TEXT);
  });

  /** A simulator that is still starting, or a source not yet known, is not commandable. */
  it('keeps commands off while the source is starting or unknown', async () => {
    vi.mocked(getObservatorySource).mockResolvedValue(report('simulated', { starting: true, ready: false }) as never);
    const { result, unmount } = renderAvailability();
    await waitFor(() => expect(getObservatorySource).toHaveBeenCalled());
    expect(result.current.canCommand).toBe(false);
    unmount();
    vi.mocked(getObservatorySource).mockReturnValue(new Promise(() => {}) as never);
    const unknown = renderAvailability();
    expect(unknown.result.current.canCommand).toBe(false);
  });

  /** A simulator that is up but where the app got no command of the mount stays off, with the reason. */
  it('keeps commands off when the app could not take command of the simulator', async () => {
    vi.mocked(getObservatorySource).mockResolvedValue(
      report('simulated', { commands: { allowed: [], off: {}, error: 'RuntimeError: boom' } }) as never);
    mountIs('DELEGATED');
    const { result } = renderAvailability();
    await waitFor(() => expect(getObservatorySource).toHaveBeenCalled());
    await waitFor(() => expect(result.current.reason).toContain('boom'));
    expect(result.current.canCommand).toBe(false);
  });

  /** A simulator that failed to start leaves the app on the offline fallback: no commands. */
  it('keeps commands off for a failed simulator', async () => {
    vi.mocked(getObservatorySource).mockResolvedValue(report('no_telescope', { failed: true }) as never);
    const { result } = renderAvailability();
    await waitFor(() => expect(getObservatorySource).toHaveBeenCalled());
    expect(result.current.canCommand).toBe(false);
  });
});
