/**
 * @fileoverview Tests of the Planetarium context menu: "Slew Telescope Here"
 * is greyed, guarded and explained while the app may not command the mount,
 * and "Open in Astronomy Manager" hands the star over through the hand-off store.
 */

import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const slewTelescopeMock = vi.fn();
vi.mock('../../common/services/telescope/motionService', () => ({
  slewTelescope: (...args: unknown[]) => slewTelescopeMock(...args),
}));
vi.mock('../../common/services/backendApi', () => ({
  callBackend: vi.fn(async () => ({})),
}));

import { PlanetariumContextMenu } from '../components/PlanetariumContextMenu';
import { peekHandoffs } from '../../common/utils/displayHandoffStore';

const source = { id: 's1', name: 'Vega', ra: 279.2, dec: 38.8, hasSpectra: false, hasPhotometry: false };

describe('PlanetariumContextMenu', () => {
  /** The slew item is disabled, aria-disabled and does nothing; the reason is reachable. */
  it('greys out Slew Telescope Here and gives the reason', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <PlanetariumContextMenu
          source={source as never}
          x={0}
          y={0}
          telescopeConnected={true}
          onClose={() => undefined}
        />
      </QueryClientProvider>,
    );
    const slew = screen.getByRole('button', { name: 'Slew Telescope Here' }) as HTMLButtonElement;
    expect(slew.disabled).toBe(true);
    expect(slew.getAttribute('aria-disabled')).toBe('true');
    fireEvent.click(slew);
    expect(slewTelescopeMock).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Why unavailable?' })).toBeTruthy();
  });

  /** The star travels in the hand-off store, and no raw storage key is written. */
  it('opens the star in the Astronomy Manager through the hand-off', async () => {
    const onClose = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <PlanetariumContextMenu
          source={{ ...source, hasSpectra: true } as never}
          x={0}
          y={0}
          telescopeConnected={false}
          onClose={onClose}
        />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Open in Astronomy Manager' }));
    await waitFor(() => expect(peekHandoffs('Astronomy Manager', 'astronomySelectStar')).toHaveLength(1));
    expect(peekHandoffs('Astronomy Manager', 'astronomySelectStar')[0].payload).toBe('s1');
    expect(onClose).toHaveBeenCalled();
    expect(window.localStorage.getItem('planetariumSelectedStar')).toBeNull();
    expect(window.localStorage.getItem('appMode')).toBeNull();
  });
});
