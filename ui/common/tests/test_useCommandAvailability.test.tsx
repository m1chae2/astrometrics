/**
 * @fileoverview Tests for the command rules: commands are on only for a simulator that is ready, and stay off
 * (with the existing texts) for the real telescope and while the source is unknown or starting.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../services/backendApi', () => ({
  callBackend: vi.fn().mockResolvedValue({ delegation_policy: { capability_delegations: [] } }),
}));
vi.mock('../services/observatoryService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../services/observatoryService')>()),
  getObservatorySource: vi.fn(),
}));

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
function report(source: 'real' | 'simulated' | 'offline', overrides: Record<string, unknown> = {}) {
  return {
    source, chosen: source, label: '', restart_pending: false, starting: false, ready: true,
    running: source !== 'real', error: null, missing_programs: [],
    commands: source === 'simulated' ? { allowed: ['OBSERVATORY_SAFETY', 'MOUNT_CONTROL'], off: { AUTOFOCUS: 'needs a focus model' }, error: null } : null,
    ...overrides,
  };
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

describe('useCommandAvailability', () => {
  beforeEach(() => vi.clearAllMocks());

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
    vi.mocked(getObservatorySource).mockResolvedValue(report('real') as never);
    const { result } = renderAvailability();
    await waitFor(() => expect(result.current.isChecking).toBe(false));
    await waitFor(() => expect(getObservatorySource).toHaveBeenCalled());
    expect(result.current.canCommand).toBe(false);
    expect(result.current.reason).toBe(COMMANDS_OFF_TEXT);
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
    const { result } = renderAvailability();
    await waitFor(() => expect(getObservatorySource).toHaveBeenCalled());
    await waitFor(() => expect(result.current.reason).toContain('boom'));
    expect(result.current.canCommand).toBe(false);
  });

  /** A simulator that failed to start leaves the app on the offline fallback: no commands. */
  it('keeps commands off for a failed simulator', async () => {
    vi.mocked(getObservatorySource).mockResolvedValue(report('offline', { failed: true }) as never);
    const { result } = renderAvailability();
    await waitFor(() => expect(getObservatorySource).toHaveBeenCalled());
    expect(result.current.canCommand).toBe(false);
  });
});
