/**
 * @file test_citedFrames.test.tsx
 * @description Tests for the Image Viewer's cited-frames list: the
 * `showFrames` listener, the banner, the row list, rows missing from the
 * library, and the Back and Clear buttons.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, renderHook, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../common/services/backendApi', () => ({ callBackend: vi.fn() }));

import { callBackend } from '../../common/services/backendApi';
import { CitedFramesPanel } from '../citedFrames/CitedFramesPanel';
import { useOpenedFrame } from '../citedFrames/useOpenedFrame';
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
    unit: 'arcsec',
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
      if (action === 'quality:resolve_rows') {
        return {
          rows: [
            { rowId: 1, level: 'frame', targetId: 'M 81', subjectId: 'f1', path: '/frames/f1.fits', reason: '' },
            { rowId: 2, level: 'frame', targetId: 'M 81', subjectId: 'f2', path: null, reason: 'frame file was moved' },
          ],
          notFoundRowIds: [],
        };
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
    expect(mockedCallBackend).toHaveBeenCalledWith('quality:resolve_rows', { row_ids: [1, 2] });
    expect(mockedCallBackend).not.toHaveBeenCalledWith('quality:metrics', expect.anything());
  });

  /** A row with a file opens it; a row without one shows the library's reason. */
  it('enables Open image only for rows with a path and shows the reason for the others', async () => {
    const onOpenFrame = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <CitedFramesPanel onOpenFrame={onOpenFrame} />
      </QueryClientProvider>,
    );
    await sendShowFrames([1, 2]);
    await screen.findByText('1.5 arcsec');
    const buttons = screen.getAllByRole('button', { name: 'Open image' });
    expect(buttons).toHaveLength(1);
    expect(buttons[0]).toBeEnabled();
    expect(screen.getByText('frame file was moved')).toBeVisible();
    await userEvent.click(buttons[0]);
    expect(onOpenFrame).toHaveBeenCalledWith('/frames/f1.fits');
  });

  /** Rows the library no longer has are counted. */
  it('counts cited rows that are no longer in the library', async () => {
    mockedCallBackend.mockImplementation(async (action: string) =>
      action === 'quality:resolve_rows'
        ? { rows: [{ rowId: 1, level: 'frame', subjectId: 'f1', path: null, reason: '' }], notFoundRowIds: [2, 3] }
        : [makeRow(1, 'f1', 2)],
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

describe('useOpenedFrame', () => {
  /** Opening a path draws it with the viewer's FITS call and keeps the picture. */
  it('opens a frame through images:convert_fits', async () => {
    mockedCallBackend.mockResolvedValue({ min: 0, max: 1, imageData: 'data:image/png;base64,iVBORw0KGgo=' });
    URL.createObjectURL = vi.fn(() => 'blob:frame');
    URL.revokeObjectURL = vi.fn();
    const { result } = renderHook(() => useOpenedFrame());
    act(() => result.current.openFrame('/frames/f1.fits'));
    expect(result.current.openedFrame?.loading).toBe(true);
    await waitFor(() => expect(result.current.openedFrame?.imageUrl).toBe('blob:frame'));
    expect(mockedCallBackend).toHaveBeenCalledWith('images:convert_fits', { path: '/frames/f1.fits', stretch: true });
    act(() => result.current.closeFrame());
    expect(result.current.openedFrame).toBeNull();
  });
});
