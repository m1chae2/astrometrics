/**
 * @module FramingPanel
 * @fileoverview The Planetarium's framing panel. It replaces the old Mosaic
 * Planner.
 *
 * It asks the library (`mosaic:preview`) for the panel centers and rotation of
 * a grid around the chosen target, lists them as numbers with their units, and
 * hands the panel corners to the sky map to outline. The corners are example
 * values (`exampleFramingCorners`) until the library returns real ones, so the
 * panel shows the "Example data" badge. The UI does no framing math. "Send to
 * plan" writes the framing into a plan the user picks.
 */

import React, { useEffect, useId, useState } from 'react';
import { ExampleDataBadge } from '../../common/components/quality/ExampleDataBadge';
import { exampleFramingCorners } from '../../common/examples/exampleFramingCorners';
import type { FramingPanelCorners } from '../layers/FramingOverlay';
import { useMosaicPreview } from '../hooks/useMosaicPreview';
import { useSendFramingToPlan } from '../hooks/useSendFramingToPlan';

/** The words that name the rotation everywhere it is shown. */
export const ROTATION_CONVENTION = 'degrees, east of north';

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
 * The framing panel: grid inputs, panel numbers, and Send to plan.
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
  const [planId, setPlanId] = useState('');

  const preview = useMosaicPreview(center, { rows, cols, overlap }, rotationDeg, isOpen);
  const { plansQuery, outcome, sendToPlan, createPlan, dismissOutcome } = useSendFramingToPlan();

  const panels = preview.data ?? [];
  const hasPanels = isOpen && center !== null && panels.length > 0;

  // The sky map outlines the example corners while there are panels to show.
  useEffect(() => {
    onFramingPanelsChange(hasPanels ? exampleFramingCorners : []);
  }, [hasPanels, onFramingPanelsChange]);

  const plans = plansQuery.data ?? [];
  const sendBlockedReason = !center
    ? 'Choose a target first.'
    : !hasPanels
      ? 'There are no panels to send yet.'
      : planId === ''
        ? 'Pick a plan to send to.'
        : null;
  const reasonId = `${idPrefix}-send-reason`;

  /** Writes the framing into the picked plan. */
  const handleSend = (): void => {
    if (sendBlockedReason !== null || center === null) return;
    void sendToPlan(planId, {
      centerRaDeg: center.ra,
      centerDecDeg: center.dec,
      rotationDeg,
      panels,
    });
  };

  /** Makes a plan and picks it. */
  const handleCreatePlan = async (): Promise<void> => {
    const createdId = await createPlan();
    if (createdId) setPlanId(createdId);
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
      data-example="true"
      data-testid="framing-panel"
    >
      <header className="framing-panel__header">
        <h3 className="framing-panel__title">Framing{targetName ? `: ${targetName}` : ''}</h3>
        <ExampleDataBadge />
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
          Rotation ({ROTATION_CONVENTION})
          <input type="number" min={-360} max={360} value={rotationDeg}
            onChange={(event) => setRotationDeg(parseNumber(event.target.value, 0))} />
        </label>
      </div>

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
          <caption>Panel centers (RA and Dec in degrees); rotation in {ROTATION_CONVENTION}</caption>
          <thead>
            <tr>
              <th scope="col">Panel</th>
              <th scope="col">RA (degrees)</th>
              <th scope="col">Dec (degrees)</th>
              <th scope="col">Rotation ({ROTATION_CONVENTION})</th>
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
        {plansQuery.isError && <p className="framing-panel__state" role="alert">Could not load the plans.</p>}
        {!plansQuery.isError && plans.length === 0 && !plansQuery.isLoading && (
          <p className="framing-panel__state">
            No plans yet.
            <button type="button" onClick={() => void handleCreatePlan()}>Create a plan</button>
          </p>
        )}
        {plans.length > 0 && (
          <label>
            Plan
            <select value={planId} onChange={(event) => setPlanId(event.target.value)}>
              <option value="">Pick a plan</option>
              {plans.map((plan) => (
                <option key={plan.id} value={plan.id}>{plan.nightDate} ({plan.entryCount} entries)</option>
              ))}
            </select>
          </label>
        )}
        <button
          type="button"
          onClick={handleSend}
          disabled={sendBlockedReason !== null}
          aria-disabled={sendBlockedReason !== null}
          aria-describedby={sendBlockedReason !== null ? reasonId : undefined}
        >
          Send to plan
        </button>
        {sendBlockedReason !== null && <p id={reasonId} className="framing-panel__reason">{sendBlockedReason}</p>}

        <div aria-live="polite">
          {outcome?.kind === 'sending' && <p>Sending…</p>}
          {outcome?.kind === 'sent' && <p>Framing sent to the plan.</p>}
        </div>
        {outcome?.kind === 'conflict' && (
          <div role="alert" className="framing-panel__rejection">
            The plan changed in another window. Nothing was sent.
            <button type="button" onClick={dismissOutcome}>Dismiss</button>
          </div>
        )}
        {outcome?.kind === 'rejected' && (
          <div role="alert" className="framing-panel__rejection">
            The plan did not accept the framing: {outcome.reason}
            <button type="button" onClick={dismissOutcome}>Dismiss</button>
          </div>
        )}
      </div>
    </section>
  );
};
