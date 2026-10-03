/**
 * @file SwitchDisplayButton.tsx
 * @description Header button that opens the current target in another display.
 * It sends a navigation intent through the shared displayCoordinator, the same
 * way the Astronomy Manager and Planetarium hand a target to other displays,
 * so an already-open window is reused and the target selection carries over.
 */

import React from 'react';
import { navigateToElement } from '../utils/displayCoordinator';

/** Props for the SwitchDisplayButton component. */
interface SwitchDisplayButtonProps {
  /** The display to open, for example 'Image Processing' or 'Image Viewer'. */
  targetDisplay: 'Image Processing' | 'Image Viewer';
  /** The target to show in that display. The button is disabled without one. */
  selectedTarget?: string;
}

/**
 * Button that switches between the Image Viewer and Image Processing
 * displays for the current target.
 *
 * @param props The destination display and the target to carry over.
 * @return A small header button.
 */
export const SwitchDisplayButton: React.FC<SwitchDisplayButtonProps> = ({ targetDisplay, selectedTarget }) => {
  const handleClick = (): void => {
    if (!selectedTarget) return;
    try {
      window.localStorage.setItem('selectedTarget', selectedTarget);
    } catch {
      // Ignore localStorage access failures (e.g. in private browsing)
    }
    void navigateToElement({
      targetDisplay,
      targetElement: 'fitsViewer',
      action: 'targetSelected',
      payload: { targetId: selectedTarget },
    });
  };

  return (
    <button
      className="btn btn--tiny"
      onClick={handleClick}
      disabled={!selectedTarget}
      title={
        selectedTarget
          ? `Open ${selectedTarget.replace(/_/g, ' ')} in ${targetDisplay}`
          : `Select a target to open it in ${targetDisplay}`
      }
    >
      {targetDisplay}
    </button>
  );
};
