/**
 * @fileoverview Tests of the Planetarium framing panel: states, panel numbers
 * with their unit, the rotation convention the library returns, the corners
 * the library returns handed to the map, and Send framing to package.
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const callBackendMock = vi.fn();
vi.mock('../../common/services/backendApi', () => ({
  callBackend: (...args: unknown[]) => callBackendMock(...args),
}));

import { ToastContext } from '../../common/hooks/useToast';
import { FramingPanel, FRAMING_REVEAL_MS } from '../components/FramingPanel';

const CONVENTION = 'Degrees east of north, as the library states it.';

const panelReply = [
  {
    row: 0, col: 0, ra_str: '', dec_str: '', ra_deg: 148.8, dec_deg: 69.0, panel_id: 'P0_0', rotation_deg: 30,
    rotation_convention: CONVENTION,
    corners: [
      { ra_deg: 148.5, dec_deg: 68.8 }, { ra_deg: 149.1, dec_deg: 68.8 },
      { ra_deg: 149.1, dec_deg: 69.2 }, { ra_deg: 148.5, dec_deg: 69.2 },
    ],
  },
  {
    row: 0, col: 1, ra_str: '', dec_str: '', ra_deg: 149.6, dec_deg: 69.0, panel_id: 'P0_1', rotation_deg: 30,
    rotation_convention: CONVENTION,
    corners: [
      { ra_deg: 149.3, dec_deg: 68.8 }, { ra_deg: 149.9, dec_deg: 68.8 },
      { ra_deg: 149.9, dec_deg: 69.2 }, { ra_deg: 149.3, dec_deg: 69.2 },
    ],
  },
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
function renderPanel(center: { ra: number; dec: number } | null, onChange = vi.fn(), onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ToastContext.Provider value={{ show: vi.fn() }}>
        <FramingPanel targetName="M 81" center={center} onFramingPanelsChange={onChange} isOpen onClose={onClose} />
      </ToastContext.Provider>
    </QueryClientProvider>,
  );
  return userEvent.setup();
}

beforeEach(() => callBackendMock.mockReset());

describe('FramingPanel', () => {
  /** Opening puts focus on Close; Close asks the display to close the panel. */
  it('focuses Close when it opens and reports a close', async () => {
    setBackend();
    const onClose = vi.fn();
    const user = renderPanel(null, vi.fn(), onClose);
    const closeButton = screen.getByRole('button', { name: 'Close' });
    expect(document.activeElement).toBe(closeButton);
    await user.click(closeButton);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  /** A closed panel shows nothing and draws no outlines; there is no floating Framing button. */
  it('shows nothing while closed', () => {
    setBackend();
    const onChange = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ToastContext.Provider value={{ show: vi.fn() }}>
          <FramingPanel targetName="M 81" center={{ ra: 1, dec: 2 }} onFramingPanelsChange={onChange} isOpen={false} onClose={vi.fn()} />
        </ToastContext.Provider>
      </QueryClientProvider>,
    );
    expect(screen.queryByRole('button', { name: 'Framing' })).toBeNull();
    expect(screen.queryByTestId('framing-panel')).toBeNull();
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  /**
   * Renders the panel open or closed in one query client.
   * @returns A function that renders the tree for an open state.
   */
  function panelTree() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return (isOpen: boolean) => (
      <QueryClientProvider client={client}>
        <ToastContext.Provider value={{ show: vi.fn() }}>
          <FramingPanel targetName="M 81" center={null} onFramingPanelsChange={vi.fn()} isOpen={isOpen} onClose={vi.fn()} />
        </ToastContext.Provider>
      </QueryClientProvider>
    );
  }

  /** Ends the fold transition on the wrapper, as the browser would. */
  function endFold(): void {
    const event = new Event('transitionend', { bubbles: true });
    Object.defineProperty(event, 'propertyName', { value: 'grid-template-rows' });
    act(() => { screen.getByTestId('framing-reveal').dispatchEvent(event); });
  }

  /** Opening appears folded, then extends; closing folds, and the panel goes when the fold ends. */
  it('opens and closes through its phases', async () => {
    setBackend();
    const tree = panelTree();
    const view = render(tree(false));
    expect(screen.queryByTestId('framing-reveal')).toBeNull();
    view.rerender(tree(true));
    expect(['opening', 'open']).toContain(screen.getByTestId('framing-reveal').getAttribute('data-phase'));
    await waitFor(() => expect(screen.getByTestId('framing-reveal').getAttribute('data-phase')).toBe('open'));
    expect(screen.getByTestId('framing-reveal').className).toContain('framing-reveal--open');

    view.rerender(tree(false));
    // Still on screen while it folds, with its content kept, so nothing jumps or flashes.
    expect(screen.getByTestId('framing-reveal').getAttribute('data-phase')).toBe('closing');
    expect(screen.getByTestId('framing-panel')).toBeTruthy();
    endFold();
    expect(screen.queryByTestId('framing-reveal')).toBeNull();
  });

  /** Opening again while it folds extends it again, without leaving the screen. */
  it('reverses cleanly when reopened while closing', async () => {
    setBackend();
    const tree = panelTree();
    const view = render(tree(true));
    view.rerender(tree(false));
    expect(screen.getByTestId('framing-reveal').getAttribute('data-phase')).toBe('closing');
    view.rerender(tree(true));
    expect(screen.getByTestId('framing-reveal').getAttribute('data-phase')).toBe('open');
  });

  /** With no transition end, the close still finishes after the fold's time. */
  it('finishes the close without a transition end', () => {
    vi.useFakeTimers();
    try {
      setBackend();
      const tree = panelTree();
      const view = render(tree(true));
      view.rerender(tree(false));
      expect(screen.getByTestId('framing-reveal').getAttribute('data-phase')).toBe('closing');
      act(() => { vi.advanceTimersByTime(FRAMING_REVEAL_MS + 100); });
      expect(screen.queryByTestId('framing-reveal')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  /** With reduced motion asked for, Close removes the panel at once. */
  it('closes at once with reduced motion', () => {
    const originalMatchMedia = window.matchMedia;
    window.matchMedia = ((query: string) =>
      ({ matches: query.includes('reduce'), media: query }) as unknown as MediaQueryList) as typeof window.matchMedia;
    try {
      setBackend();
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      const tree = (isOpen: boolean) => (
        <QueryClientProvider client={client}>
          <ToastContext.Provider value={{ show: vi.fn() }}>
            <FramingPanel targetName="M 81" center={null} onFramingPanelsChange={vi.fn()} isOpen={isOpen} onClose={vi.fn()} />
          </ToastContext.Provider>
        </QueryClientProvider>
      );
      const view = render(tree(true));
      view.rerender(tree(false));
      expect(screen.queryByTestId('framing-panel')).toBeNull();
    } finally {
      window.matchMedia = originalMatchMedia;
    }
  });

  /** No target: asks for one and does not call the preview. */
  it('says "Choose a target" and does not fetch', async () => {
    setBackend();
    renderPanel(null);
    expect(screen.getByText('Choose a target')).toBeTruthy();
    expect(callBackendMock.mock.calls.some(([method]) => method === 'mosaic:preview')).toBe(false);
  });

  /** Panels show with their unit; the map gets the returned corners; the convention is the library's. */
  it('shows panel numbers, the library convention and hands the returned corners to the map', async () => {
    setBackend();
    const onChange = vi.fn();
    renderPanel({ ra: 148.9, dec: 69.1 }, onChange);
    expect(await screen.findByText('P0_0')).toBeTruthy();
    expect(screen.getByText('148.8000')).toBeTruthy();
    // Each panel's rotation (30) differs from the Rotation box (0), so the Rotation column shows.
    expect(screen.getAllByText('30.0').length).toBe(2);
    expect(screen.getByText('Panel centers (degrees)')).toBeTruthy();
    // The convention sits behind the ⓘ button beside Rotation, not in the panel body.
    expect(screen.queryByText(CONVENTION)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'About the rotation' }));
    expect(screen.getByRole('note').textContent).toBe(CONVENTION);
    expect(screen.getByLabelText('Rotation (degrees)')).toBeTruthy();
    expect(screen.queryByTestId('example-data-badge')).toBeNull();
    expect(screen.getByTestId('framing-panel').getAttribute('data-example')).toBeNull();
    expect(onChange).toHaveBeenLastCalledWith([
      {
        name: 'P0_0',
        corners: [
          { raDeg: 148.5, decDeg: 68.8 }, { raDeg: 149.1, decDeg: 68.8 },
          { raDeg: 149.1, decDeg: 69.2 }, { raDeg: 148.5, decDeg: 69.2 },
        ],
      },
      {
        name: 'P0_1',
        corners: [
          { raDeg: 149.3, decDeg: 68.8 }, { raDeg: 149.9, decDeg: 68.8 },
          { raDeg: 149.9, decDeg: 69.2 }, { raDeg: 149.3, decDeg: 69.2 },
        ],
      },
    ]);
    const previewCall = callBackendMock.mock.calls.find(([method]) => method === 'mosaic:preview');
    expect(previewCall?.[1]).toMatchObject({ center_ra: 148.9, center_dec: 69.1, rotation_deg: 0 });
  });

  /** The Rotation column is left out when every panel has the Rotation box's angle. */
  it('drops the Rotation column when it only repeats the Rotation box', async () => {
    setBackend({ preview: panelReply.map((panel) => ({ ...panel, rotation_deg: 0 })) });
    renderPanel({ ra: 148.9, dec: 69.1 });
    expect(await screen.findByText('P0_0')).toBeTruthy();
    expect(screen.queryByRole('columnheader', { name: 'Rotation' })).toBeNull();
    expect(screen.getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual(['Panel', 'RA', 'Dec']);
  });

  /** A failed read shows the error state with Try again. */
  it('shows the error state', async () => {
    setBackend({ preview: new Error('boom') });
    renderPanel({ ra: 1, dec: 2 });
    expect(await screen.findByText(/Could not load the framing/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });

  /** An empty real reply shows the empty state and no outlines. */
  it('shows the empty state for no panels', async () => {
    setBackend({ preview: [] });
    const onChange = vi.fn();
    renderPanel({ ra: 1, dec: 2 }, onChange);
    expect(await screen.findByText(/returned no panels/)).toBeTruthy();
    expect(onChange).toHaveBeenLastCalledWith([]);
  });
});
