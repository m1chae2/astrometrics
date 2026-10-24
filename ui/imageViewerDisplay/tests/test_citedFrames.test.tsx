/**
 * @file test_citedFrames.test.tsx
 * @description Tests for the Image Viewer's cited-frames list: the
 * `showFrames` listener, the banner, the row list, rows missing from the
 * library, and the Back and Clear buttons.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../common/services/backendApi', () => ({ callBackend: vi.fn() }));

import { callBackend } from '../../common/services/backendApi';
import { CitedFramesPanel } from '../citedFrames/CitedFramesPanel';
import { clearCitedFramesRequest } from '../citedFrames/citedFramesStore';
import { showFrames } from '../../common/utils/displayCoordinator';

const mockedCallBackend = vi.mocked(callBackend) as unknown as ReturnType<typeof vi.fn>;

/** Builds one stored row in the shape the library returns. */
function makeRow(rowId: number, subjectId: string, value: number | null) {
  return {
    metric: 'frame.star_width_moffat_arcsec',
    definitionVersion: 1,
    subject: { level: 'frame', subjectId },
    status: value === null ? 'not_measured' : 'measured',
    value,
    reason: value === null ? 'no stars' : undefined,
    origin: 'measured',
    runId: 'run',
    measuredAt: '2026-10-24T00:00:00Z',
    rowId,
  };
}

/** Renders the panel inside a fresh query client. */
function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <CitedFramesPanel />
    </QueryClientProvider>,
  );
}

/** Sends the local showFrames action the way the display coordinator does. */
async function sendShowFrames(rowIds: number[], statementText = 'Stars are wide') {
  await act(async () => {
    window.dispatchEvent(
      new CustomEvent('astrometrics:showFrames', {
        detail: { rowIds, targetId: 'M 81', statementText },
      }),
    );
  });
}

describe('CitedFramesPanel', () => {
  beforeEach(() => {
    window.localStorage.clear();
    delete (window as any).astrometrics;
    mockedCallBackend.mockReset();
    act(() => clearCitedFramesRequest());
    mockedCallBackend.mockImplementation(async (action: string) => {
      if (action === 'quality:metrics') {
        return [{ name: 'frame.star_width_moffat_arcsec', unit: 'arcsec' }];
      }
      return [makeRow(1, 'f1', 1.5), makeRow(2, 'f2', null)];
    });
  });

  /** Nothing shows before a request arrives, and nothing is fetched. */
  it('renders nothing without a request', () => {
    const { container } = renderPanel();
    expect(container).toBeEmptyDOMElement();
    expect(mockedCallBackend).not.toHaveBeenCalled();
  });

  /** The listener reads the rows with row_ids and shows banner and list. */
  it('shows the banner and the cited rows', async () => {
    renderPanel();
    await sendShowFrames([1, 2]);
    expect(screen.getByRole('status')).toHaveTextContent('Showing 2 frames cited by Stars are wide');
    await screen.findByText('1.5 arcsec');
    expect(mockedCallBackend).toHaveBeenCalledWith('quality:query', { row_ids: [1, 2] });
    expect(screen.getByText('not measured: no stars')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Open image' })[0]).toBeDisabled();
    expect(screen.getAllByText('Opening frames arrives later').length).toBe(1);
  });

  /** Rows the library no longer has are counted. */
  it('counts cited rows that are no longer in the library', async () => {
    mockedCallBackend.mockImplementation(async (action: string) =>
      action === 'quality:metrics' ? [] : [makeRow(1, 'f1', 2)],
    );
    renderPanel();
    await sendShowFrames([1, 2, 3]);
    await screen.findByText('2 of 3 cited frames are no longer in the library.');
  });

  /** A failed read shows the error and a retry. */
  it('shows an error with Try again', async () => {
    mockedCallBackend.mockRejectedValue(new Error('down'));
    renderPanel();
    await sendShowFrames([1]);
    expect(await screen.findByText('Could not load the cited frames.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  /** Clear hides the banner and the list. */
  it('clears the request', async () => {
    renderPanel();
    await sendShowFrames([1, 2]);
    await screen.findByText('1.5 arcsec');
    await userEvent.click(screen.getByRole('button', { name: 'Clear' }));
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
  });

  /** Back returns to the mode shown before the Image Viewer. */
  it('goes back to the previous mode', async () => {
    renderPanel();
    const modeChange = vi.fn();
    window.dispatchEvent(new CustomEvent('astrometrics:modeChange', { detail: 'Planetarium' }));
    window.addEventListener('astrometrics:modeChange', modeChange);
    // The coordinator's local fallback switches the mode, then sends the action.
    await act(async () => {
      await showFrames({ rowIds: [1], targetId: 'M 81', statementText: 'Stars are wide' });
    });
    modeChange.mockClear();
    const back = await screen.findByRole('button', { name: 'Back to Planetarium' });
    await userEvent.click(back);
    expect(modeChange).toHaveBeenCalledTimes(1);
    expect((modeChange.mock.calls[0][0] as CustomEvent).detail).toBe('Planetarium');
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
    window.removeEventListener('astrometrics:modeChange', modeChange);
  });
});
