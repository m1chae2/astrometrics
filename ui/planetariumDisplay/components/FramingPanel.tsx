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

import React, { useEffect, useId, useMemo, useState, useRef } from 'react';
import type { FramingPanelCorners } from '../layers/FramingOverlay';
import { useMosaicPreview } from '../hooks/useMosaicPreview';
import { useTargetPackage } from '../hooks/useTargetPackage';
import { Button } from '../../common/components/Button';
import { NumberInput } from '../../common/components/NumberInput';
import { InfoButton } from '../../common/components/InfoButton';

import { Skeleton } from '../../common/components/Skeleton';
import { prefersReducedMotion } from '../../common/utils/reducedMotion';
/**
 * How long the panel takes to extend down or fold back up, in milliseconds. Matches
 * `--motion-duration-panel` in theme.css, which times the transition itself. A close also
 * ends after this long (plus a little) if the browser sends no transition end.
 */
export const FRAMING_REVEAL_MS = 240;

/**
 * Where the panel is in its open and close movement.
 * - "closed": not on screen.
 * - "opening": on screen, folded, about to extend (one frame).
 * - "open": extended.
 * - "closing": folding back up; it leaves the screen when the fold ends.
 */
export type RevealPhase = 'closed' | 'opening' | 'open' | 'closing';

/**
 * Runs the panel's open and close movement.
 *
 * Opening puts the panel on screen folded, then extends it on the next frame, so the
 * transition has a start. Closing keeps it on screen while it folds and takes it away when
 * the fold ends (or after `FRAMING_REVEAL_MS` if no transition end comes). Opening again
 * while it folds simply extends it again. With reduced motion asked for, both are instant.
 *
 * @param isOpen Whether the panel should be open.
 * @returns The phase and the handler for the wrapper's transition end.
 */
export function useRevealPhase(isOpen: boolean): { phase: RevealPhase; onTransitionEnd: (event: React.TransitionEvent) => void } {
  const [phase, setPhase] = useState<RevealPhase>(isOpen ? 'open' : 'closed');

  useEffect(() => {
    const reduced = prefersReducedMotion();
    if (isOpen) {
      setPhase((current) => {
        if (current === 'open') return current;
        // From closed, start folded so the extend has somewhere to start; while closing, reverse.
        return current === 'closed' && !reduced ? 'opening' : 'open';
      });
      return undefined;
    }
    if (reduced) {
      setPhase('closed');
      return undefined;
    }
    setPhase((current) => (current === 'closed' ? current : 'closing'));
    const fallback = setTimeout(() => {
      setPhase((current) => (current === 'closing' ? 'closed' : current));
    }, FRAMING_REVEAL_MS + 60);
    return () => clearTimeout(fallback);
  }, [isOpen]);

  // One frame after it appears folded, extend it.
  useEffect(() => {
    if (phase !== 'opening') return undefined;
    const frame = requestAnimationFrame(() => {
      setPhase((current) => (current === 'opening' ? 'open' : current));
    });
    return () => cancelAnimationFrame(frame);
  }, [phase]);

  const onTransitionEnd = (event: React.TransitionEvent) => {
    if (event.target !== event.currentTarget || event.propertyName !== 'grid-template-rows') return;
    setPhase((current) => (current === 'closing' ? 'closed' : current));
  };

  return { phase, onTransitionEnd };
}

/** Props of the framing panel. */
interface FramingPanelProps {
  /** The chosen target's name, or null when none is chosen. */
  targetName: string | null;
  /** The chosen target's center in degrees, or null when none is chosen. */
  center: { ra: number; dec: number } | null;
  /** Called with the corners to outline on the sky map (empty when the panel is closed). */
  onFramingPanelsChange: (panels: FramingPanelCorners[]) => void;
  /** Whether the panel is open. It is opened by the Framing button on the selected object's info card. */
  isOpen: boolean;
  /** Called when the panel's Close button is pressed. */
  onClose: () => void;
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
 * Nothing is shown while it is closed.
 *
 * @param props The target, its center, whether it is open, and the callbacks for the corners to draw and for closing.
 * @returns The panel.
 */
export const FramingPanel: React.FC<FramingPanelProps> = ({ targetName, center, onFramingPanelsChange, isOpen, onClose }) => {
  const idPrefix = useId();
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  // Focus moves to the Close button when the panel opens, so a keyboard user lands in it.
  useEffect(() => {
    if (isOpen) closeButtonRef.current?.focus();
  }, [isOpen]);
  // The panel extends down when it opens and folds back up when it closes (see useRevealPhase).
  const { phase, onTransitionEnd } = useRevealPhase(isOpen);
  const isOnScreen = phase !== 'closed';
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
  // A panel's rotation is shown only when one differs from the Rotation box; otherwise the column repeats it.
  const showRotationColumn = panels.some((panel) => Math.abs((panel.rotation_deg ?? 0) - rotationDeg) > 0.05);

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

  if (!isOnScreen) return null;

  return (
    <div
      className={`framing-reveal framing-reveal--${phase}`}
      data-testid="framing-reveal"
      data-phase={phase}
      onTransitionEnd={onTransitionEnd}
    >
    <div className="framing-reveal__inner">
    <section
      className="framing-panel"
      aria-label="Framing"
      data-testid="framing-panel"
      inert={!isOpen}
    >
      <header className="framing-panel__header">
        <h3 className="framing-panel__title">Framing{targetName ? `: ${targetName}` : ''}</h3>
        <Button variant="secondary" size="small" ref={closeButtonRef} onClick={onClose}>
          Close
        </Button>
      </header>

      <div className="framing-panel__inputs">
        <label>
          Rows
          <NumberInput min={1} max={10} value={rows}
            onChange={(event) => setRows(Math.max(1, Math.round(parseNumber(event.target.value, 1))))} />
        </label>
        <label>
          Columns
          <NumberInput min={1} max={10} value={cols}
            onChange={(event) => setCols(Math.max(1, Math.round(parseNumber(event.target.value, 1))))} />
        </label>
        <label>
          Overlap (%)
          <NumberInput min={0} max={80} value={overlap}
            onChange={(event) => setOverlap(parseNumber(event.target.value, 0))} />
        </label>
        <div className="framing-panel__field">
          <span className="framing-panel__field-label">
            <label htmlFor={`${idPrefix}-rotation`}>Rotation (degrees)</label>
            {/* What the angle means, in the library's words, behind the shared ⓘ button. */}
            {rotationConvention !== null && (
              <InfoButton text={rotationConvention} label="About the rotation" />
            )}
          </span>
          <NumberInput id={`${idPrefix}-rotation`} min={-360} max={360} value={rotationDeg}
            onChange={(event) => setRotationDeg(parseNumber(event.target.value, 0))} />
        </div>
      </div>

      {center === null && <p className="framing-panel__state">Choose a target</p>}
      {center !== null && preview.isLoading && (
        <Skeleton variant="keyValue" count={3} label="Loading the framing" />
      )}
      {center !== null && preview.isError && (
        <div className="framing-panel__state" role="alert">
          Could not load the framing
          <Button variant="secondary" size="small" onClick={() => void preview.refetch()}>Try again</Button>
        </div>
      )}
      {center !== null && preview.isSuccess && panels.length === 0 && (
        <p className="framing-panel__state">The library returned no panels. Change the grid size or overlap.</p>
      )}

      {isOnScreen && center !== null && panels.length > 0 && (
        <div className="framing-panel__table-scroll">
          <table className="framing-panel__table">
            <caption>Panel centers (degrees)</caption>
            <thead>
              <tr>
                <th scope="col">Panel</th>
                <th scope="col">RA</th>
                <th scope="col">Dec</th>
                {showRotationColumn && <th scope="col">Rotation</th>}
              </tr>
            </thead>
            <tbody>
              {panels.map((panel) => (
                <tr key={panel.panel_id}>
                  <th scope="row">{panel.panel_id}</th>
                  <td>{panel.ra_deg.toFixed(4)}</td>
                  <td>{panel.dec_deg.toFixed(4)}</td>
                  {showRotationColumn && <td>{(panel.rotation_deg ?? 0).toFixed(1)}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="framing-panel__send">
        <Button
          variant="secondary"
          size="small"
          onClick={handleSend}
          disabled={sendBlockedReason !== null || isWorking}
          aria-disabled={sendBlockedReason !== null || isWorking}
          aria-describedby={sendBlockedReason !== null ? reasonId : undefined}
        >
          Send framing to package
        </Button>
        {sendBlockedReason !== null && <p id={reasonId} className="framing-panel__reason">{sendBlockedReason}</p>}
        {isWorking && <p aria-busy="true">Sending…</p>}
        {refusal !== null && (
          <div role="alert" id={refusalId} className="framing-panel__rejection">
            {refusal}
            <Button variant="secondary" size="small" onClick={dismissRefusal}>Dismiss</Button>
          </div>
        )}
      </div>
    </section>
    </div>
    </div>
  );
};
