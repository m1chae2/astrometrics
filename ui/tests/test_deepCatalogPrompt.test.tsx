/**
 * @fileoverview Tests for the first-launch prompt to download the deep-star catalog.
 *
 * The prompt only shows the command; it must appear when the catalog is missing
 * or partial, stay away once it is complete, and respect "Not now" (this launch
 * only) and "Don't remind me" (remembered).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act, renderHook, waitFor } from '@testing-library/react';

vi.mock('../common/services/backendApi', () => ({
  callBackend: vi.fn(),
}));

import { callBackend, DeepCatalogStatus } from '../common/services/backendApi';
import {
  DeepCatalogPrompt,
  DEEP_CATALOG_PROMPT_DISMISSED_KEY,
} from '../planetariumDisplay/components/DeepCatalogPrompt';
import { useDeepCatalogStatus } from '../planetariumDisplay/hooks/useDeepCatalogStatus';

const INSTALL_COMMAND = 'python -m astrometricslib.scripts.build_deep_star_catalog';

const statusOf = (overrides: Partial<DeepCatalogStatus> = {}): DeepCatalogStatus => ({
  installed: false,
  complete: false,
  star_count: 0,
  pixels_downloaded: 0,
  pixels_total: null,
  healpix_level: null,
  magnitude_limit: null,
  size_megabytes: 0,
  installCommand: INSTALL_COMMAND,
  ...overrides,
});

describe('DeepCatalogPrompt', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('shows the command when the catalog has not been downloaded', () => {
    render(<DeepCatalogPrompt status={statusOf()} />);

    expect(screen.getByRole('dialog')).toBeTruthy();
    expect(screen.getByText(INSTALL_COMMAND)).toBeTruthy();
    expect(screen.getByText(/has not been downloaded yet/)).toBeTruthy();
  });

  it('says how far a partial download got and how to finish it', () => {
    render(
      <DeepCatalogPrompt
        status={statusOf({ installed: true, pixels_downloaded: 1200, pixels_total: 3072, healpix_level: 4 })}
      />,
    );

    expect(screen.getByText(/partly downloaded \(1,200 of 3,072 chunks\)/)).toBeTruthy();
    expect(screen.getByText(/To finish it/)).toBeTruthy();
  });

  it('shows nothing once the catalog is complete', () => {
    render(
      <DeepCatalogPrompt status={statusOf({ installed: true, complete: true, pixels_downloaded: 3072, pixels_total: 3072 })} />,
    );

    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('shows nothing until the status is known', () => {
    render(<DeepCatalogPrompt status={null} />);

    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('"Not now" hides it for this launch only', () => {
    const { unmount } = render(<DeepCatalogPrompt status={statusOf()} />);

    fireEvent.click(screen.getByText('Not now'));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(window.localStorage.getItem(DEEP_CATALOG_PROMPT_DISMISSED_KEY)).toBeNull();

    unmount();
    render(<DeepCatalogPrompt status={statusOf()} />);
    expect(screen.getByRole('dialog')).toBeTruthy();
  });

  it('"Don\'t remind me" hides it now and on later launches', () => {
    const { unmount } = render(<DeepCatalogPrompt status={statusOf()} />);

    fireEvent.click(screen.getByText("Don't remind me"));
    expect(screen.queryByRole('dialog')).toBeNull();

    unmount();
    render(<DeepCatalogPrompt status={statusOf()} />);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('Escape closes it like "Not now"', () => {
    render(<DeepCatalogPrompt status={statusOf()} />);

    fireEvent.keyDown(window, { key: 'Escape' });

    expect(screen.queryByRole('dialog')).toBeNull();
    expect(window.localStorage.getItem(DEEP_CATALOG_PROMPT_DISMISSED_KEY)).toBeNull();
  });

  it('copies the command to the clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    render(<DeepCatalogPrompt status={statusOf()} />);

    await act(async () => {
      fireEvent.click(screen.getByText('Copy'));
    });

    expect(writeText).toHaveBeenCalledWith(INSTALL_COMMAND);
    expect(screen.getByText('Copied')).toBeTruthy();
  });

  it('still works when the clipboard is unavailable', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
      configurable: true,
    });
    render(<DeepCatalogPrompt status={statusOf()} />);

    await act(async () => {
      fireEvent.click(screen.getByText('Copy'));
    });

    expect(screen.getByText(INSTALL_COMMAND)).toBeTruthy();
    expect(screen.queryByText('Copied')).toBeNull();
  });
});

describe('useDeepCatalogStatus', () => {
  beforeEach(() => {
    vi.mocked(callBackend).mockReset();
  });

  it('loads the status once', async () => {
    vi.mocked(callBackend).mockResolvedValue(statusOf({ installed: true }) as never);

    const { result } = renderHook(() => useDeepCatalogStatus());

    expect(result.current.status).toBeNull();
    await waitFor(() => expect(result.current.status?.installed).toBe(true));
    expect(callBackend).toHaveBeenCalledTimes(1);
    expect(callBackend).toHaveBeenCalledWith('planetarium:get_deep_catalog_status', {});
  });

  it('leaves the status null, without throwing, when the request fails', async () => {
    vi.mocked(callBackend).mockRejectedValue(new Error('backend too old'));

    const { result } = renderHook(() => useDeepCatalogStatus());

    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.status).toBeNull();
  });
});
