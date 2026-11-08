/**
 * @fileoverview The shared loading, empty and error states of the quality panels.
 */

import React from 'react';
import { Button } from '../Button';
import { Skeleton, SkeletonVariant } from '../Skeleton';

export interface QualityPanelStateProps {
    /** Which state to show. */
    state: 'loading' | 'noTarget' | 'empty' | 'error';
    /** What to say for the empty state. */
    emptyMessage?: string;
    /** Called when the user presses "Try again" in the error state. */
    onRetry?: () => void;
    /** The name of the thing that failed, such as "quality report". */
    subject?: string;
    /** Shape of the loading placeholder. Default is key/value lines. */
    loadingShape?: SkeletonVariant;
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
    loadingShape = 'keyValue',
}) => {
    if (state === 'loading') {
        return (
            <Skeleton variant={loadingShape} count={3} label={`Loading the ${subject}`} />
        );
    }
    if (state === 'noTarget') return <div className="quality-state">Choose a target</div>;
    if (state === 'error') {
        return (
            <div className="quality-state quality-state--error" role="alert">
                <span>Could not load the {subject}</span>
                <Button size="small" onClick={onRetry}>
                    Try again
                </Button>
            </div>
        );
    }
    return <div className="quality-state">{emptyMessage}</div>;
};
