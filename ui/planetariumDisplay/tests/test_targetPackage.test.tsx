/**
 * @fileoverview Tests of the Planetarium package actions: "Add to observation package" on the info
 * card and "Send framing to package" in the framing panel, with `callBackend` mocked.
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BackendError } from '../../common/services/backendError';
import { ToastContext } from '../../common/hooks/useToast';

const callBackendMock = vi.fn();
vi.mock('../../common/services/backendApi', () => ({
  callBackend: (...args: unknown[]) => callBackendMock(...args),
}));

import { PlanetariumInfoCard } from '../components/PlanetariumInfoCard';
import { FramingPanel } from '../components/FramingPanel';
import type { PlanetariumSource } from '../../common/types/planetariumTypes';

const toastShow = vi.fn();

const source = { id: 'M31', name: 'M 31', type: 'target', ra: 10.68, dec: 41.27 } as unknown as PlanetariumSource;

const panelReply = [
  { row: 0, col: 0, ra_str: '', dec_str: '', ra_deg: 10.6, dec_deg: 41.2, panel_id: 'P0_0', rotation_deg: 0 },
];

/**
 * Sets what the fake backend answers.
 * @param options The packages listed for the target, the create outcome and the edit outcome.
 */
function setBackend(options: { packages?: unknown[]; createError?: Error; editError?: Error } = {}): void {
  callBackendMock.mockImplementation(async (method: string) => {
    if (method === 'mosaic:preview') return panelReply;
    if (method === 'plan:list_packages') return options.packages ?? [];
    if (method === 'plan:create_package') {
      if (options.createError) throw options.createError;
      return { id: 'new-package', status: 'PLANNED', revision: 0 };
    }
    if (method === 'plan:edit_package') {
      if (options.editError) throw options.editError;
      return { id: 'p1', revision: 8 };
    }
    return undefined;
  });
}

/**
 * Wraps a component in the query client and a fake toast context.
 * @param element The component to render.
 * @returns The user-event handle.
 */
function renderWithProviders(element: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ToastContext.Provider value={{ show: toastShow }}>{element}</ToastContext.Provider>
    </QueryClientProvider>,
  );
  return userEvent.setup();
}

/**
 * Names the calls made to one backend method.
 * @param method The method name.
 * @returns The argument lists of those calls.
 */
function callsTo(method: string): unknown[][] {
  return callBackendMock.mock.calls.filter(([name]) => name === method);
}

beforeEach(() => {
  callBackendMock.mockReset();
  toastShow.mockReset();
});

describe('Add to observation package', () => {
  /** A target with a planned package gets a toast and no second package. */
  it('does not create a duplicate when a planned package exists', async () => {
    setBackend({ packages: [{ id: 'p1', status: 'SUGGESTED' }, { id: 'p2', status: 'PLANNED' }] });
    const user = renderWithProviders(
      <PlanetariumInfoCard source={source} observerLocation={null} onClose={vi.fn()} isLibraryTarget />,
    );
    await user.click(screen.getByRole('button', { name: 'Add to observation package' }));
    await waitFor(() => expect(toastShow).toHaveBeenCalledWith('M 31 already has a planned package', 'info'));
    expect(callsTo('plan:create_package')).toHaveLength(0);
  });

  /** A target with only a suggested package is also not duplicated. */
  it('treats a suggested package as existing', async () => {
    setBackend({ packages: [{ id: 'p1', status: 'SUGGESTED' }] });
    const user = renderWithProviders(
      <PlanetariumInfoCard source={source} observerLocation={null} onClose={vi.fn()} isLibraryTarget />,
    );
    await user.click(screen.getByRole('button', { name: 'Add to observation package' }));
    await waitFor(() => expect(toastShow).toHaveBeenCalledWith('M 31 already has a suggested package', 'info'));
    expect(callsTo('plan:create_package')).toHaveLength(0);
  });

  /** No package: one is created for the target and the toast says so. */
  it('creates a package when there is none', async () => {
    setBackend();
    const user = renderWithProviders(
      <PlanetariumInfoCard source={source} observerLocation={null} onClose={vi.fn()} isLibraryTarget />,
    );
    await user.click(screen.getByRole('button', { name: 'Add to observation package' }));
    await waitFor(() => expect(toastShow).toHaveBeenCalledWith('Added M 31 to the observation packages', 'success'));
    expect(callsTo('plan:create_package')[0][1]).toEqual({ target: 'M 31' });
  });

  /** A target outside the library is added from the catalog and the toast says so. */
  it('says the target became a library target', async () => {
    setBackend();
    const user = renderWithProviders(
      <PlanetariumInfoCard source={source} observerLocation={null} onClose={vi.fn()} />,
    );
    await user.click(screen.getByRole('button', { name: 'Add to observation package' }));
    await waitFor(() =>
      expect(toastShow).toHaveBeenCalledWith('M 31 is now a library target and has a package', 'success'));
  });

  /** A target the catalog does not know gives a visible alert. */
  it('shows an alert when the catalog does not know the target', async () => {
    setBackend({ createError: new BackendError({ code: 'not_found', message: 'No catalog entry for M 31.' }) });
    const user = renderWithProviders(
      <PlanetariumInfoCard source={source} observerLocation={null} onClose={vi.fn()} />,
    );
    await user.click(screen.getByRole('button', { name: 'Add to observation package' }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('No catalog entry for M 31.');
    expect(toastShow).not.toHaveBeenCalled();
  });
});

describe('Send framing to package', () => {
  /**
   * Opens the framing panel for M 81 and waits for its panels.
   * @returns The user-event handle.
   */
  async function openPanel() {
    const user = renderWithProviders(
      <FramingPanel targetName="M 81" center={{ ra: 148.9, dec: 69.1 }} onFramingPanelsChange={vi.fn()} />,
    );
    await user.click(screen.getByRole('button', { name: 'Framing' }));
    await screen.findByText('P0_0');
    return user;
  }

  /** The framing goes to the package with its revision. */
  it('writes the framing with expected_revision', async () => {
    setBackend({ packages: [{ id: 'p1', status: 'PLANNED', revision: 7 }] });
    const user = await openPanel();
    await user.click(screen.getByRole('button', { name: 'Send framing to package' }));
    await waitFor(() => expect(toastShow).toHaveBeenCalled());
    expect(callsTo('plan:edit_package')[0][1]).toMatchObject({
      package_id: 'p1',
      expected_revision: 7,
      framing: { centerRaDeg: 148.9, centerDecDeg: 69.1, rotationDeg: 0, panels: panelReply },
    });
    expect(callsTo('plan:create_package')).toHaveLength(0);
  });

  /** A target with no package gets one first, then the framing. */
  it('creates the package first when there is none', async () => {
    setBackend();
    const user = await openPanel();
    await user.click(screen.getByRole('button', { name: 'Send framing to package' }));
    await waitFor(() => expect(callsTo('plan:edit_package')).toHaveLength(1));
    expect(callsTo('plan:create_package')).toHaveLength(1);
    expect(callsTo('plan:edit_package')[0][1]).toMatchObject({ package_id: 'new-package', expected_revision: 0 });
  });

  /** A revision conflict is a visible alert that can be dismissed. */
  it('shows an alert on a revision conflict', async () => {
    setBackend({
      packages: [{ id: 'p1', status: 'PLANNED', revision: 7 }],
      editError: new BackendError({ code: 'conflict', message: 'Stale revision.' }),
    });
    const user = await openPanel();
    await user.click(screen.getByRole('button', { name: 'Send framing to package' }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('changed in another window');
    await user.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
