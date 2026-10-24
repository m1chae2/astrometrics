/**
 * @fileoverview Tests of the Planetarium framing panel: states, the example
 * badge, panel numbers with their unit and convention, the corners handed to
 * the map, and Send to plan.
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BackendError } from '../../common/services/backendError';

const callBackendMock = vi.fn();
vi.mock('../../common/services/backendApi', () => ({
  callBackend: (...args: unknown[]) => callBackendMock(...args),
}));

import { FramingPanel } from '../components/FramingPanel';
import { exampleFramingCorners } from '../../common/examples/exampleFramingCorners';

const panelReply = [
  { row: 0, col: 0, ra_str: '', dec_str: '', ra_deg: 148.8, dec_deg: 69.0, panel_id: 'P0_0', rotation_deg: 30 },
  { row: 0, col: 1, ra_str: '', dec_str: '', ra_deg: 149.6, dec_deg: 69.0, panel_id: 'P0_1', rotation_deg: 30 },
];

/**
 * Sets what the fake backend answers.
 * @param options Overrides: the preview reply (or an error), the plan list, the plan editing outcome.
 */
function setBackend(options: { preview?: unknown; plans?: unknown[]; editError?: Error } = {}): void {
  callBackendMock.mockImplementation(async (method: string, params: Record<string, unknown>) => {
    if (method === 'mosaic:preview') {
      if (options.preview instanceof Error) throw options.preview;
      return options.preview ?? panelReply;
    }
    if (method === 'plan:get') {
      return params.session_id ? { id: params.session_id, revision: 4 } : (options.plans ?? []);
    }
    if (method === 'plan:edit') {
      if (options.editError) throw options.editError;
      return { revision: 5 };
    }
    if (method === 'plan:create') return { id: 'new-plan' };
    return undefined;
  });
}

/**
 * Renders the panel inside a fresh query client.
 * @param center The target center, or null.
 * @param onChange The corners callback.
 * @returns The user-event handle.
 */
function renderPanel(center: { ra: number; dec: number } | null, onChange = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <FramingPanel targetName="M 81" center={center} onFramingPanelsChange={onChange} />
    </QueryClientProvider>,
  );
  return userEvent.setup();
}

beforeEach(() => callBackendMock.mockReset());

describe('FramingPanel', () => {
  /** No target: asks for one and does not call the preview. */
  it('says "Choose a target" and does not fetch', async () => {
    setBackend();
    const user = renderPanel(null);
    await user.click(screen.getByRole('button', { name: 'Framing' }));
    expect(screen.getByText('Choose a target')).toBeTruthy();
    expect(callBackendMock.mock.calls.some(([method]) => method === 'mosaic:preview')).toBe(false);
  });

  /** Panels show with unit and convention, the badge, data-example, and the example corners go to the map. */
  it('shows panel numbers, the example badge and hands corners to the map', async () => {
    setBackend();
    const onChange = vi.fn();
    const user = renderPanel({ ra: 148.9, dec: 69.1 }, onChange);
    await user.click(screen.getByRole('button', { name: 'Framing' }));
    expect(await screen.findByText('P0_0')).toBeTruthy();
    expect(screen.getByText('148.8000')).toBeTruthy();
    expect(screen.getAllByText('30.0').length).toBe(2);
    expect(screen.getAllByText(/degrees, east of north/).length).toBeGreaterThan(0);
    expect(screen.getByTestId('example-data-badge')).toBeTruthy();
    expect(screen.getByTestId('framing-panel').getAttribute('data-example')).toBe('true');
    expect(onChange).toHaveBeenLastCalledWith(exampleFramingCorners);
    const previewCall = callBackendMock.mock.calls.find(([method]) => method === 'mosaic:preview');
    expect(previewCall?.[1]).toMatchObject({ center_ra: 148.9, center_dec: 69.1, rotation_deg: 0 });
  });

  /** A failed read shows the error state with Try again. */
  it('shows the error state', async () => {
    setBackend({ preview: new Error('boom') });
    const user = renderPanel({ ra: 1, dec: 2 });
    await user.click(screen.getByRole('button', { name: 'Framing' }));
    expect(await screen.findByText(/Could not load the framing/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });

  /** An empty real reply shows the empty state, not the example. */
  it('shows the empty state for no panels', async () => {
    setBackend({ preview: [] });
    const onChange = vi.fn();
    const user = renderPanel({ ra: 1, dec: 2 }, onChange);
    await user.click(screen.getByRole('button', { name: 'Framing' }));
    expect(await screen.findByText(/returned no panels/)).toBeTruthy();
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  /** Send to plan is disabled with a visible reason until a plan is picked. */
  it('disables Send to plan with a visible reason', async () => {
    setBackend({ plans: [{ id: 'plan-1', status: 'planned', nightDate: '2026-10-24', entryCount: 2 }] });
    const user = renderPanel({ ra: 1, dec: 2 });
    await user.click(screen.getByRole('button', { name: 'Framing' }));
    await screen.findByText('P0_0');
    const send = screen.getByRole('button', { name: 'Send to plan' });
    expect((send as HTMLButtonElement).disabled).toBe(true);
    const reason = screen.getByText('Pick a plan to send to.');
    expect(send.getAttribute('aria-describedby')).toBe(reason.id);
  });

  /** Send to plan writes the framing with the plan's revision. */
  it('writes the framing through plan:edit with expected_revision', async () => {
    setBackend({ plans: [{ id: 'plan-1', status: 'planned', nightDate: '2026-10-24', entryCount: 2 }] });
    const user = renderPanel({ ra: 148.9, dec: 69.1 });
    await user.click(screen.getByRole('button', { name: 'Framing' }));
    await screen.findByText('P0_0');
    await user.selectOptions(await screen.findByRole('combobox', { name: /Plan/ }), 'plan-1');
    await user.click(screen.getByRole('button', { name: 'Send to plan' }));
    await waitFor(() => expect(screen.getByText('Framing sent to the plan.')).toBeTruthy());
    const editCall = callBackendMock.mock.calls.find(([method]) => method === 'plan:edit');
    expect(editCall?.[1]).toMatchObject({
      session_id: 'plan-1',
      expected_revision: 4,
      framing: { centerRaDeg: 148.9, centerDecDeg: 69.1, rotationDeg: 0, panels: panelReply },
    });
  });

  /** A rejection stays until dismissed and is an alert. */
  it('keeps a rejection until dismissed', async () => {
    setBackend({
      plans: [{ id: 'plan-1', status: 'planned', nightDate: '2026-10-24', entryCount: 2 }],
      editError: new BackendError({ code: 'invalid_argument', message: 'Framing is outside the sky.' }),
    });
    const user = renderPanel({ ra: 1, dec: 2 });
    await user.click(screen.getByRole('button', { name: 'Framing' }));
    await screen.findByText('P0_0');
    await user.selectOptions(await screen.findByRole('combobox', { name: /Plan/ }), 'plan-1');
    await user.click(screen.getByRole('button', { name: 'Send to plan' }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Framing is outside the sky.');
    await user.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByRole('alert')).toBeNull();
  });

  /** With no plans the panel offers to create one. */
  it('offers to create a plan when there is none', async () => {
    setBackend({ plans: [] });
    const user = renderPanel({ ra: 1, dec: 2 });
    await user.click(screen.getByRole('button', { name: 'Framing' }));
    await user.click(await screen.findByRole('button', { name: 'Create a plan' }));
    await waitFor(() => expect(callBackendMock.mock.calls.some(([method]) => method === 'plan:create')).toBe(true));
  });
});
