/**
 * @file CitedFramesPanel.tsx
 * @description The Image Viewer's Evidence view: a banner that says which
 * statement cited the rows, with "Back to <mode>" and "Clear", and a list of
 * the cited quality rows exactly as the library returned them. A row whose
 * picture file the library found has "Open image"; a row without one shows the
 * library's reason as text.
 */

import React, { useRef } from 'react';
import { goBackToPreviousMode } from './citedFramesStore';
import { useCitedFramesQuery, useCitedFramesRequest } from './useCitedFrames';
import './citedFrames.css';

/**
 * Formats a measured value for display, with its unit when known.
 *
 * @param value The number to show.
 * @param unit The unit text, or undefined.
 * @returns Text such as "1.42 arcsec".
 */
function formatValue(value: number, unit: string | undefined): string {
  const text = String(Number(value.toPrecision(4)));
  return unit ? `${text} ${unit}` : text;
}

/** Props of the cited-frames panel. */
interface CitedFramesPanelProps {
  /** Called with a frame's file path when the user presses "Open image". */
  onOpenFrame?: (path: string) => void;
  /** Called when the request is cleared, so the viewer can drop an opened frame. */
  onClosed?: () => void;
}

/**
 * Shows the cited rows when a `showFrames` request is active; renders nothing
 * otherwise.
 *
 * @param props The open and close callbacks.
 * @returns The banner and list, or null.
 */
export const CitedFramesPanel: React.FC<CitedFramesPanelProps> = ({ onOpenFrame, onClosed }) => {
  const { request, clear } = useCitedFramesRequest();
  const rowIds = request?.rowIds ?? [];
  const query = useCitedFramesQuery(rowIds);
  const sectionRef = useRef<HTMLElement>(null);

  /**
   * Clears the request. The panel disappears, so focus first moves to the
   * first control of the Image Viewer outside the panel (its frame area),
   * so keyboard users do not lose their place.
   *
   * @returns Nothing.
   */
  const handleClear = () => {
    const viewer = sectionRef.current?.parentElement;
    const nextControl = Array.from(
      viewer?.querySelectorAll<HTMLElement>('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])') ?? [],
    ).find((element) => !sectionRef.current?.contains(element) && !(element as HTMLButtonElement).disabled);
    nextControl?.focus();
    onClosed?.();
    clear();
  };

  if (!request) {
    return null;
  }

  const total = rowIds.length;
  const missing = query.data?.missingRowIds.length ?? 0;
  const frameWord = total === 1 ? 'frame' : 'frames';

  return (
    <section className="cited-frames" aria-label="Cited frames" ref={sectionRef}>
      <div className="cited-frames__banner">
        <p className="cited-frames__banner-text" role="status" aria-live="polite">
          Showing {total} {frameWord} cited by {request.statementText}
        </p>
        <div className="cited-frames__banner-actions">
          {request.previousMode ? (
            <button type="button" className="cited-frames__button" onClick={goBackToPreviousMode}>
              Back to {request.previousMode}
            </button>
          ) : null}
          <button type="button" className="cited-frames__button" onClick={handleClear}>
            Clear
          </button>
        </div>
      </div>

      {total === 0 ? (
        <p className="cited-frames__message">This statement cites no stored rows.</p>
      ) : query.isLoading ? (
        <p className="cited-frames__message" aria-busy="true">Loading…</p>
      ) : query.isError ? (
        <div className="cited-frames__message" role="alert">
          <span>Could not load the cited frames.</span>{' '}
          <button type="button" className="cited-frames__button" onClick={() => void query.refetch()}>
            Try again
          </button>
        </div>
      ) : (
        <>
          {missing > 0 ? (
            <p className="cited-frames__missing">
              {missing} of {total} cited {frameWord} {missing === 1 ? 'is' : 'are'} no longer in the library.
            </p>
          ) : null}
          <ul className="cited-frames__list">
            {query.data?.rows.map((row) => {
              const file = row.rowId === null || row.rowId === undefined
                ? undefined
                : query.data?.filesByRowId[row.rowId];
              const reasonId = `cited-frames-reason-${row.rowId}`;
              return (
                <li key={row.rowId ?? `${row.subject.subjectId}-${row.metric}`} className="cited-frames__row">
                  <span className="cited-frames__subject">
                    {row.subject.level}: {row.subject.subjectId}
                  </span>
                  <span className="cited-frames__metric">{row.metric}</span>
                  <span className="cited-frames__value">
                    {row.value === null || row.value === undefined
                      ? `${row.status.replace('_', ' ')}${row.reason ? `: ${row.reason}` : ''}`
                      : formatValue(row.value, row.unit ?? undefined)}
                  </span>
                  {file?.path ? (
                    <button
                      type="button"
                      className="cited-frames__button"
                      onClick={() => onOpenFrame?.(file.path as string)}
                    >
                      Open image
                    </button>
                  ) : (
                    <span className="cited-frames__reason" id={reasonId}>
                      {file?.reason || 'No picture for this row.'}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
};
