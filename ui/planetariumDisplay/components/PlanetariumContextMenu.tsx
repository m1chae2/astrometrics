/**
 * @module PlanetariumContextMenu
 * @fileoverview Right-click context menu for celestial objects on the sky map.
 *
 * Displays coordinates and object name, with optional actions to open the
 * object in Astronomy Manager or slew the telescope to its position.
 *
 */

import React, { useEffect, useRef } from 'react';
import { PlanetariumSource } from '../../common/types/planetariumTypes';
import { slewTelescope } from '../../common/services/telescopeService';
import { emitToast } from '../../common/utils/emitToast';

/**
 * Props for PlanetariumContextMenu.
 */
interface Props {
  /** The source object that was right-clicked. */
  source: PlanetariumSource;
  /** Canvas-local X pixel position for the menu anchor. */
  x: number;
  /** Canvas-local Y pixel position for the menu anchor. */
  y: number;
  /** Whether a telescope mount is connected and slew is available. */
  telescopeConnected: boolean;
  /** Callback to close and dismiss the menu. */
  onClose: () => void;
}

/**
 * Floating right-click context popup for a selected celestial source.
 *
 * Dismisses itself when the user clicks outside (via document-level pointerdown
 * listener) or triggers another contextmenu event.
 *
 * @func PlanetariumContextMenu
 * @param {Props} props - Component props.
 * @returns {React.ReactElement} The rendered context menu.
 */
export const PlanetariumContextMenu: React.FC<Props> = ({ source, x, y, telescopeConnected, onClose }) => {
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleOutsideClick = (e: MouseEvent | PointerEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    };

    document.addEventListener('pointerdown', handleOutsideClick);
    document.addEventListener('contextmenu', handleOutsideClick);

    return () => {
      document.removeEventListener('pointerdown', handleOutsideClick);
      document.removeEventListener('contextmenu', handleOutsideClick);
    };
  }, [onClose]);

  /**
   * Opens a duplicate browser tab pre-navigated to the Astronomy Manager view
   * with the current source pre-selected via URL query parameter.
   *
   * @returns {void}
   */
  const handleOpenDuplicateWindow = () => {
    const url = `${window.location.origin}${window.location.pathname}?mode=Astronomy Manager&star=${encodeURIComponent(source.id)}`;
    window.open(url, '_blank');
    onClose();
  };

  /**
   * Commands the telescope mount to slew to the coordinates of the selected object.
   *
   * @returns {Promise<void>}
   */
  const handleSlewToPosition = async () => {
    // Slew API expects RA in decimal hours (1h = 15°), not decimal degrees
    const raHours = source.ra / 15.0;
    const decDegrees = source.dec;

    emitToast(`Slewing telescope to ${source.name || 'selected target'}...`, 'info');
    const success = await slewTelescope(raHours, decDegrees);
    if (success) {
      emitToast(`Slew initiated successfully`, 'success');
    } else {
      emitToast(`Slew failed. Check Safe Mode or hardware connection.`, 'error');
    }
    onClose();
  };

  const hasAnyData = source.hasSpectra || source.hasPhotometry;

  return (
    <div
      ref={menuRef}
      className="planetarium-context-menu"
      style={{ top: y, left: x }}
    >
      <div className="planetarium-context-menu__section">
        <div className="planetarium-context-menu__label">Star ID</div>
        <div className="planetarium-context-menu__value">{source.name}</div>
      </div>

      <div className="planetarium-context-menu__section">
        <div className="planetarium-context-menu__label">Coordinates</div>
        <div className="planetarium-context-menu__coords">
          <div>RA: {source.ra.toFixed(6)}&deg;</div>
          <div>DEC: {source.dec.toFixed(6)}&deg;</div>
        </div>
      </div>

      {hasAnyData && (
        <button
          className="planetarium-context-menu__action"
          onClick={handleOpenDuplicateWindow}
        >
          Open in Astronomy Manager ↗
        </button>
      )}

      {telescopeConnected && (
        <button
          className="planetarium-context-menu__action"
          onClick={handleSlewToPosition}
        >
          Slew Telescope Here
        </button>
      )}
    </div>
  );
};
