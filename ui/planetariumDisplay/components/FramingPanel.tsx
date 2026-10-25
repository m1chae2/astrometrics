/**
 * @module FramingPanel
 * @fileoverview The Planetarium's framing panel. It replaces the old Mosaic
 * Planner.
 *
 * It asks the library (`mosaic:preview`) for the panel centers and rotation of
 * a grid around the chosen target, lists them as numbers with their units, and
 * hands the panel corners the library returns to the sky map to outline. The
 * words that say what the rotation means also come from the library. The UI
 * does no framing math. "Send framing to package" writes the framing into the
 * target's observation package.
 */

import React, { useEffect, useId, useMemo, useState } from 'react';
import type { FramingPanelCorners } from '../layers/FramingOverlay';
import { useMosaicPreview } from '../hooks/useMosaicPreview';
import { useTargetPackage } from '../hooks/useTargetPackage';

/** Props of the framing panel. */
interface FramingPanelProps {
  /** The chosen target's name, or null when none is chosen. */
  targetName: string | null;
  /** The chosen target's center in degrees, or null when none is chosen. */
  center: { ra: number; dec: number } | null;
  /** Called with the corners to outline on the sky map (empty when the panel is closed). */
  onFramingPanelsChange: (panels: FramingPanelCorners[]) => void;
}

/**
 * Turns the text of a number box into a number, or the fallback when it is empty.
 *
 * @param text The text typed by the user.
 * @param fallback The value to use when the text is not a number.
 * @returns The number.
 */
function parseNumber(text: string, fallback: number): number {
  const value = Number.parseFloat(text);
  return Number.isFinite(value) ? value : fallback;
}

/**
 * The framing panel: grid inputs, panel numbers, and Send framing to package.
 *
 * @param props The target, its center, and the callback for the corners to draw.
 * @returns The panel.
 */
export const FramingPanel: React.FC<FramingPanelProps> = ({ targetName, center, onFramingPanelsChange }) => {
  const idPrefix = useId();
  const [isOpen, setIsOpen] = useState(false);
  const [rows, setRows] = useState(2);
  const [cols, setCols] = useState(2);
  const [overlap, setOverlap] = useState(20);
  const [rotationDeg, setRotationDeg] = useState(0);

  const preview = useMosaicPreview(center, { rows, cols, overlap }, rotationDeg, isOpen);
  const { isWorking, refusal, sendFramingToPackage, dismissRefusal } = useTargetPackage(targetName, true);

  const panels = useMemo(() => preview.data ?? [], [preview.data]);
  const hasPanels = isOpen && center !== null && panels.length > 0;

  // The words that say what the rotation means, as the library gives them.
  const rotationConvention = panels.find((panel) => panel.rotation_convention)?.rotation_convention ?? null;

  // The corners the library returned, renamed to the names the sky map draws.
  const outlines = useMemo<FramingPanelCorners[]>(
    () => panels.map((panel) => ({
      name: panel.panel_id,
      corners: (panel.corners ?? []).map((corner) => ({ raDeg: corner.ra_deg, decDeg: corner.dec_deg })),
    })),
    [panels],
  );

  // The sky map outlines the returned corners while there are panels to show.
  useEffect(() => {
    onFramingPanelsChange(hasPanels ? outlines : []);
  }, [hasPanels, outlines, onFramingPanelsChange]);

  const sendBlockedReason = !center || !targetName
    ? 'Choose a target first.'
    : !hasPanels
      ? 'There are no panels to send yet.'
      : null;
  const reasonId = `${idPrefix}-send-reason`;
  const refusalId = `${idPrefix}-send-refusal`;

  /** Writes the framing into the target's package. */
  const handleSend = (): void => {
    if (sendBlockedReason !== null || center === null) return;
    void sendFramingToPackage({
      centerRaDeg: center.ra,
      centerDecDeg: center.dec,
      rotationDeg,
      panels,
    });
  };

  if (!isOpen) {
    return (
      <div className="framing-panel framing-panel--closed">
        <button type="button" className="framing-panel__toggle" onClick={() => setIsOpen(true)}>
          Framing
        </button>
      </div>
    );
  }

  return (
    <section
      className="framing-panel"
      aria-label="Framing"
      data-testid="framing-panel"
    >
      <header className="framing-panel__header">
        <h3 className="framing-panel__title">Framing{targetName ? `: ${targetName}` : ''}</h3>
        <button type="button" className="framing-panel__toggle" onClick={() => setIsOpen(false)}>
          Close
        </button>
      </header>

      <div className="framing-panel__inputs">
        <label>
          Rows
          <input type="number" min={1} max={10} value={rows}
            onChange={(event) => setRows(Math.max(1, Math.round(parseNumber(event.target.value, 1))))} />
        </label>
        <label>
          Columns
          <input type="number" min={1} max={10} value={cols}
            onChange={(event) => setCols(Math.max(1, Math.round(parseNumber(event.target.value, 1))))} />
        </label>
        <label>
          Overlap (%)
          <input type="number" min={0} max={80} value={overlap}
            onChange={(event) => setOverlap(parseNumber(event.target.value, 0))} />
        </label>
        <label>
          Rotation (degrees)
          <input type="number" min={-360} max={360} value={rotationDeg}
            aria-describedby={rotationConvention !== null ? `${idPrefix}-convention` : undefined}
            onChange={(event) => setRotationDeg(parseNumber(event.target.value, 0))} />
        </label>
      </div>
      {rotationConvention !== null && (
        <p id={`${idPrefix}-convention`} className="framing-panel__state">{rotationConvention}</p>
      )}

      {center === null && <p className="framing-panel__state">Choose a target</p>}
      {center !== null && preview.isLoading && (
        <p className="framing-panel__state" aria-busy="true">Loading…</p>
      )}
      {center !== null && preview.isError && (
        <div className="framing-panel__state" role="alert">
          Could not load the framing
          <button type="button" onClick={() => void preview.refetch()}>Try again</button>
        </div>
      )}
      {center !== null && preview.isSuccess && panels.length === 0 && (
        <p className="framing-panel__state">The library returned no panels. Change the grid size or overlap.</p>
      )}

      {hasPanels && (
        <table className="framing-panel__table">
          <caption>Panel centers (RA and Dec in degrees); rotation in degrees</caption>
          <thead>
            <tr>
              <th scope="col">Panel</th>
              <th scope="col">RA (degrees)</th>
              <th scope="col">Dec (degrees)</th>
              <th scope="col">Rotation (degrees)</th>
            </tr>
          </thead>
          <tbody>
            {panels.map((panel) => (
              <tr key={panel.panel_id}>
                <th scope="row">{panel.panel_id}</th>
                <td>{panel.ra_deg.toFixed(4)}</td>
                <td>{panel.dec_deg.toFixed(4)}</td>
                <td>{(panel.rotation_deg ?? 0).toFixed(1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="framing-panel__send">
        <button
          type="button"
          onClick={handleSend}
          disabled={sendBlockedReason !== null || isWorking}
          aria-disabled={sendBlockedReason !== null || isWorking}
          aria-describedby={sendBlockedReason !== null ? reasonId : undefined}
        >
          Send framing to package
        </button>
        {sendBlockedReason !== null && <p id={reasonId} className="framing-panel__reason">{sendBlockedReason}</p>}
        {isWorking && <p aria-busy="true">Sending…</p>}
        {refusal !== null && (
          <div role="alert" id={refusalId} className="framing-panel__rejection">
            {refusal}
            <button type="button" onClick={dismissRefusal}>Dismiss</button>
          </div>
        )}
      </div>
    </section>
  );
};
