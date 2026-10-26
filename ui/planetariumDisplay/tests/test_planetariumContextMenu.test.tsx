/**
 * @fileoverview Tests of the Planetarium context menu: "Slew Telescope Here"
 * is greyed, guarded and explained while the app may not command the mount.
 */

import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';

const slewTelescopeMock = vi.fn();
vi.mock('../../common/services/telescope/motionService', () => ({
  slewTelescope: (...args: unknown[]) => slewTelescopeMock(...args),
}));
vi.mock('../../common/services/backendApi', () => ({
  callBackend: vi.fn(async () => ({})),
}));

import { PlanetariumContextMenu } from '../components/PlanetariumContextMenu';

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
});
