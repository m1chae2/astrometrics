/**
 * @fileoverview Tests of the Planetarium framing panel: states, the example
 * badge, panel numbers with their unit and convention, the corners handed to
 * the map, and Send framing to package.
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const callBackendMock = vi.fn();
vi.mock('../../common/services/backendApi', () => ({
  callBackend: (...args: unknown[]) => callBackendMock(...args),
}));

import { ToastContext } from '../../common/hooks/useToast';
import { FramingPanel } from '../components/FramingPanel';
import { exampleFramingCorners } from '../../common/examples/exampleFramingCorners';

const panelReply = [
  { row: 0, col: 0, ra_str: '', dec_str: '', ra_deg: 148.8, dec_deg: 69.0, panel_id: 'P0_0', rotation_deg: 30 },
  { row: 0, col: 1, ra_str: '', dec_str: '', ra_deg: 149.6, dec_deg: 69.0, panel_id: 'P0_1', rotation_deg: 30 },
];

/**
 * Sets what the fake backend answers.
 * @param options Overrides: the preview reply (or an error).
 */
function setBackend(options: { preview?: unknown } = {}): void {
  callBackendMock.mockImplementation(async (method: string) => {
    if (method === 'mosaic:preview') {
      if (options.preview instanceof Error) throw options.preview;
      return options.preview ?? panelReply;
    }
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
      <ToastContext.Provider value={{ show: vi.fn() }}>
        <FramingPanel targetName="M 81" center={center} onFramingPanelsChange={onChange} />
      </ToastContext.Provider>
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
});
