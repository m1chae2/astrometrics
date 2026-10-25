/**
 * @fileoverview The shared loading, empty and error states of the quality panels.
 */

import React from 'react';

export interface QualityPanelStateProps {
    /** Which state to show. */
    state: 'loading' | 'noTarget' | 'empty' | 'error';
    /** What to say for the empty state. */
    emptyMessage?: string;
    /** Called when the user presses "Try again" in the error state. */
    onRetry?: () => void;
    /** The name of the thing that failed, such as "quality report". */
    subject?: string;
}

/**
 * Shows one of the four non-data states.
 * @param props The state and its texts.
 * @return The state view.
 */
export const QualityPanelState: React.FC<QualityPanelStateProps> = ({
    state,
    emptyMessage = 'No frames yet. Sync to fetch new frames.',
    onRetry,
    subject = 'quality report',
}) => {
    if (state === 'loading') {
        return (
            <div className="quality-state" aria-busy="true">
                Loading…
            </div>
        );
    }
    if (state === 'noTarget') return <div className="quality-state">Choose a target</div>;
    if (state === 'error') {
        return (
            <div className="quality-state quality-state--error" role="alert">
                <span>Could not load the {subject}</span>
                <button type="button" className="btn btn--tiny quality-button" onClick={onRetry}>
                    Try again
                </button>
            </div>
        );
    }
    return <div className="quality-state">{emptyMessage}</div>;
};
