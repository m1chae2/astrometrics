/**
 * @file test_colorPreview.test.tsx
 * @description Tests the Image Viewer's black-and-white or color switch: it shows only for a
 * target whose record names a color picture, and the color picture is read only when chosen.
 */

import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

const fetchColorPreviewPathMock = vi.fn();
const fetchColorPreviewPictureMock = vi.fn();
vi.mock('../../common/services/imaging/imageService', () => ({
  fetchColorPreviewPath: (...args: unknown[]) => fetchColorPreviewPathMock(...args),
  fetchColorPreviewPicture: (...args: unknown[]) => fetchColorPreviewPictureMock(...args),
}));
vi.mock('../../common/services/imaging/measurementPath', () => ({ resolveProcessedImagePath: vi.fn() }));
vi.mock('../../common/fitsViewer/FitsViewerManager', () => ({ FitsViewerManager: () => <div data-testid="viewer" /> }));
vi.mock('../../common/components/SwitchDisplayButton', () => ({ SwitchDisplayButton: () => null }));

import { useColorPreview } from '../hooks/useColorPreview';
import { clearTargetImageCache } from '../hooks/targetImageCache';
import { TargetViewerManager } from '../targetViewerManager/TargetViewerManager';

const COLOR_PATH = '/stacks/M 81/M_81_L_Stacked_color_preview.jpg';

/** Shows what the hook returns. */
const Probe: React.FC<{ showColor: boolean }> = ({ showColor }) => {
  const { colorPreviewPath, imageUrl } = useColorPreview('M 81', showColor);
  return (
    <>
      <div data-testid="path">{colorPreviewPath ?? 'none'}</div>
      <div data-testid="url">{imageUrl ?? 'none'}</div>
    </>
  );
};

describe('color picture switch', () => {
  beforeEach(() => {
    fetchColorPreviewPathMock.mockReset();
    fetchColorPreviewPictureMock.mockReset();
    fetchColorPreviewPictureMock.mockResolvedValue(new Blob(['c'], { type: 'image/jpeg' }));
    URL.createObjectURL = vi.fn(() => 'blob:color-1');
    URL.revokeObjectURL = vi.fn();
  });
  afterEach(() => {
    cleanup();
    clearTargetImageCache();
  });

  /** Without a picture choice (no color picture) the switch is hidden. */
  it('hides the switch when the target has no color picture', () => {
    render(<TargetViewerManager selectedTarget="M 81" />);
    expect(screen.queryByRole('tab', { name: 'Color' })).toBeNull();
  });

  /** With a picture choice the switch shows, and choosing Color reports it. */
  it('shows the switch when the target has a color picture', () => {
    const onChange = vi.fn();
    render(<TargetViewerManager selectedTarget="M 81" pictureChoice="mono" onPictureChoiceChange={onChange} />);
    expect(screen.getByRole('tab', { name: 'Black and white' }).getAttribute('aria-selected')).toBe('true');
    fireEvent.click(screen.getByRole('tab', { name: 'Color' }));
    expect(onChange).toHaveBeenCalledWith('color');
  });

  /** A target whose record names no color picture asks for no file. */
  it('reads no file when the record names no color picture', async () => {
    fetchColorPreviewPathMock.mockResolvedValue(null);
    render(<Probe showColor />);
    await waitFor(() => expect(fetchColorPreviewPathMock).toHaveBeenCalledWith('M 81', expect.anything()));
    expect(screen.getByTestId('path').textContent).toBe('none');
    expect(fetchColorPreviewPictureMock).not.toHaveBeenCalled();
  });

  /** The color picture is read only once color is chosen, from the path the record names. */
  it('loads the color picture from the record path only when color is chosen', async () => {
    fetchColorPreviewPathMock.mockResolvedValue(COLOR_PATH);
    const { rerender } = render(<Probe showColor={false} />);
    await waitFor(() => expect(screen.getByTestId('path').textContent).toBe(COLOR_PATH));
    expect(fetchColorPreviewPictureMock).not.toHaveBeenCalled();

    rerender(<Probe showColor />);
    await waitFor(() => expect(screen.getByTestId('url').textContent).toBe('blob:color-1'));
    expect(fetchColorPreviewPictureMock).toHaveBeenCalledWith(COLOR_PATH, expect.anything());
  });
});
