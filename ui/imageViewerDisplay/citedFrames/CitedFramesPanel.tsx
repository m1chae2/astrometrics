/**
 * @file CitedFramesPanel.tsx
 * @description The Image Viewer's Evidence view: a banner that says which
 * statement cited the rows, with "Back to <mode>" and "Clear", and a list of
 * the cited quality rows exactly as the library returned them.
 */

import React from 'react';
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

/**
 * Shows the cited rows when a `showFrames` request is active; renders nothing
 * otherwise.
 *
 * @returns The banner and list, or null.
 */
export const CitedFramesPanel: React.FC = () => {
  const { request, clear } = useCitedFramesRequest();
  const rowIds = request?.rowIds ?? [];
  const query = useCitedFramesQuery(rowIds);

  if (!request) {
    return null;
  }

  const total = rowIds.length;
  const missing = query.data?.missingRowIds.length ?? 0;
  const frameWord = total === 1 ? 'frame' : 'frames';

  return (
    <section className="cited-frames" aria-label="Cited frames">
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
          <button type="button" className="cited-frames__button" onClick={clear}>
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
          <p className="cited-frames__open-note" id="cited-frames-open-note">
            Opening frames arrives later
          </p>
          <ul className="cited-frames__list">
            {query.data?.rows.map((row) => (
              <li key={row.rowId ?? `${row.subject.subjectId}-${row.metric}`} className="cited-frames__row">
                <span className="cited-frames__subject">
                  {row.subject.level}: {row.subject.subjectId}
                </span>
                <span className="cited-frames__metric">{row.metric}</span>
                <span className="cited-frames__value">
                  {row.value === null || row.value === undefined
                    ? `${row.status.replace('_', ' ')}${row.reason ? `: ${row.reason}` : ''}`
                    : formatValue(row.value, query.data?.unitByMetric[row.metric])}
                </span>
                <button
                  type="button"
                  className="cited-frames__button"
                  disabled
                  aria-disabled="true"
                  aria-describedby="cited-frames-open-note"
                >
                  Open image
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
};
