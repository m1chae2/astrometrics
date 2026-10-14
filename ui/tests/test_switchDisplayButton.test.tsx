/**
 * @fileoverview Tests for the button that switches a target between the
 * Image Viewer and Image Processing displays. It must use the shared
 * displayCoordinator and do nothing without a selected target.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('../common/utils/displayCoordinator', () => ({
  navigateToElement: vi.fn(async () => ({ handledRemotely: false })),
}));

import { navigateToElement } from '../common/utils/displayCoordinator';
import { SwitchDisplayButton } from '../common/components/SwitchDisplayButton';

describe('SwitchDisplayButton', () => {
  beforeEach(() => {
    vi.mocked(navigateToElement).mockClear();
  });

  it('sends the selected target to the other display through the coordinator', () => {
    render(<SwitchDisplayButton targetDisplay="Image Processing" selectedTarget="M_81" />);

    fireEvent.click(screen.getByRole('button', { name: 'Image Processing' }));

    expect(navigateToElement).toHaveBeenCalledWith(
      expect.objectContaining({
        targetDisplay: 'Image Processing',
        targetElement: 'fitsViewer',
        action: 'targetSelected',
        payload: { targetId: 'M_81' },
      }),
    );
  });

  it('is disabled and does nothing when no target is selected', () => {
    render(<SwitchDisplayButton targetDisplay="Image Viewer" selectedTarget="" />);

    const button = screen.getByRole('button', { name: 'Image Viewer' });
    fireEvent.click(button);

    expect(button).toBeDisabled();
    expect(navigateToElement).not.toHaveBeenCalled();
  });
});
