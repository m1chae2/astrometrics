/**
 * @fileoverview The frame list of a night: one row per frame with its time,
 * target, capture setting, star width, sky rate and status.
 *
 * The rows are example data until wave L1 adds the per-frame list to the
 * night review read. The list is a scroll region and shows the first rows
 * with "Show all N". A frame is "flagged", never "ruined", before stacking.
 */

import React, { useRef, useState } from 'react';
import { exampleNightFrames } from '../common/examples';
import type { ExampleNightFrame } from '../common/examples';
import { ExampleDataBadge } from '../common/components/quality/ExampleDataBadge';
import { QualityPanelState } from '../common/components/quality/QualityPanelState';
import { formatValue } from '../common/components/quality/statementLabels';
import { formatMoment } from './utils/formatDisplay';

/** How many rows show before "Show all N". */
const TOP_FRAME_COUNT = 10;

/**
 * Gives the word for a frame status.
 * @param status The status.
 * @return The word.
 */
function frameStatusWord(status: ExampleNightFrame['status']): string {
    switch (status) {
        case 'not_yet_judged':
            return 'Not yet judged';
        case 'flagged_by_live_status':
            return 'Flagged';
        case 'set_aside':
            return 'Set aside';
        default:
            return 'In stack';
    }
}

/**
 * Draws the icon of a frame status. The status word is shown beside it.
 * @param props The status.
 * @return The icon.
 */
const FrameStatusIcon: React.FC<{ status: ExampleNightFrame['status'] }> = ({ status }) => {
    let shape: React.ReactNode;
    switch (status) {
        case 'not_yet_judged':
            shape = <circle cx="12" cy="12" r="8" strokeDasharray="3 3" />;
            break;
        case 'flagged_by_live_status':
            shape = <path d="M6 3 V21 M6 4 H18 L15 8.5 L18 13 H6" />;
            break;
        case 'set_aside':
            shape = <path d="M5 5 L19 19 M19 5 L5 19" />;
            break;
        default:
            shape = <path d="M5 12.5 L10 17.5 L19 7" />;
    }
    return (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden="true" focusable="false">
            {shape}
        </svg>
    );
};

export interface NightFrameListProps {
    /** A label that says which night the frames are of. */
    label?: string;
}

/**
 * The frame list.
 * @param props The optional label.
 * @return The panel.
 */
export const NightFrameList: React.FC<NightFrameListProps> = ({ label = 'Frames of the night' }) => {
    const [showAll, setShowAll] = useState(false);
    const toggleReference = useRef<HTMLButtonElement>(null);
    const frames = exampleNightFrames;
    const shown = showAll ? frames : frames.slice(0, TOP_FRAME_COUNT);

    return (
        <section className="execution-section" aria-label={label} data-example="true">
            <h3 className="execution-section__title">
                Frames <ExampleDataBadge />
            </h3>
            {frames.length === 0 ? (
                <QualityPanelState state="empty" />
            ) : (
                <>
                    <div className="frame-list__scroll" role="region" aria-label="Frame rows" tabIndex={0}>
                        <table className="frame-list">
                            <thead>
                                <tr>
                                    <th scope="col">Time</th>
                                    <th scope="col">Target</th>
                                    <th scope="col">Setting</th>
                                    <th scope="col">Star width (arcsec)</th>
                                    <th scope="col">Sky rate (e/s/px)</th>
                                    <th scope="col">Status</th>
                                </tr>
                            </thead>
                            <tbody>
                                {shown.map((frame) => (
                                    <tr key={frame.frameId} data-status={frame.status}>
                                        <td>{formatMoment(frame.time)}</td>
                                        <td>{frame.target}</td>
                                        <td>{frame.captureSetting}</td>
                                        <td>{frame.starWidthArcsec === null ? 'not measured' : formatValue(frame.starWidthArcsec)}</td>
                                        <td>{frame.skyRate === null ? 'not measured' : formatValue(frame.skyRate)}</td>
                                        <td>
                                            <FrameStatusIcon status={frame.status} /> {frameStatusWord(frame.status)}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    {frames.length > TOP_FRAME_COUNT && (
                        <button
                            ref={toggleReference}
                            type="button"
                            className="btn btn--tiny"
                            aria-expanded={showAll}
                            onClick={() => {
                                setShowAll((value) => !value);
                                toggleReference.current?.focus();
                            }}
                        >
                            {showAll ? 'Show fewer' : `Show all ${frames.length}`}
                        </button>
                    )}
                </>
            )}
        </section>
    );
};
