/**
 * @file test_imageSurvivesPause.test.tsx
 * @description Tests that the Image Viewer's picture still shows after the display
 * is paused (hidden by React `Activity`) and shown again: its object URL must not
 * be freed by the pause, and a library change must still drop the old picture.
 */

import React, { Activity } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';

const fetchProcessedImageMock = vi.fn();
vi.mock('../../common/services/imaging/imageService', () => ({
  fetchProcessedImage: (...args: unknown[]) => fetchProcessedImageMock(...args),
}));

import { useTargetImage } from '../hooks/useTargetImage';
import { clearTargetImageCache } from '../hooks/targetImageCache';
import { emit as emitEvent } from '../../common/utils/eventBus';

/** Shows the picture URL the hook returns. */
const Viewer: React.FC = () => {
  const { imageUrl } = useTargetImage('M 13');
  return <div data-testid="url">{imageUrl ?? 'none'}</div>;
};

describe('Image Viewer picture across a pause', () => {
  let revoked: string[];
  let created: number;

  beforeEach(() => {
    revoked = [];
    created = 0;
    fetchProcessedImageMock.mockReset();
    fetchProcessedImageMock.mockImplementation(async () => new Blob(['x'], { type: 'image/jpeg' }));
    URL.createObjectURL = vi.fn(() => `blob:picture-${(created += 1)}`);
    URL.revokeObjectURL = vi.fn((url: string) => {
      revoked.push(url);
    });
  });
  afterEach(() => {
    // Unmount first, so clearing the cache does not start a new read.
    cleanup();
    clearTargetImageCache();
  });

  /** Hiding and showing the display keeps the same picture URL and does not free it. */
  it('keeps the picture after the display is hidden and shown again', async () => {
    const { rerender } = render(
      <Activity mode="visible">
        <Viewer />
      </Activity>
    );
    await waitFor(() => expect(screen.getByTestId('url').textContent).toBe('blob:picture-1'));

    rerender(
      <Activity mode="hidden">
        <Viewer />
      </Activity>
    );
    expect(revoked).toEqual([]);

    rerender(
      <Activity mode="visible">
        <Viewer />
      </Activity>
    );
    await waitFor(() => expect(screen.getByTestId('url').textContent).toBe('blob:picture-1'));
    expect(revoked).toEqual([]);
    expect(fetchProcessedImageMock).toHaveBeenCalledTimes(1);
  });

  /** A library change while the display is paused makes it read a fresh picture when shown. */
  it('reads a fresh picture when the library changed while it was paused', async () => {
    const { rerender } = render(
      <Activity mode="visible">
        <Viewer />
      </Activity>
    );
    await waitFor(() => expect(screen.getByTestId('url').textContent).toBe('blob:picture-1'));
    rerender(
      <Activity mode="hidden">
        <Viewer />
      </Activity>
    );
    act(() => emitEvent('targetsUpdated'));
    expect(revoked).toEqual(['blob:picture-1']);
    rerender(
      <Activity mode="visible">
        <Viewer />
      </Activity>
    );
    await waitFor(() => expect(screen.getByTestId('url').textContent).toBe('blob:picture-2'));
    expect(fetchProcessedImageMock).toHaveBeenCalledTimes(2);
  });
});
