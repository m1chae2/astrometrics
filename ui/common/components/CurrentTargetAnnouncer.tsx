/**
 * @fileoverview A hidden polite live region that says "Current target: M 81"
 * when the current target changes (after "Switch to", "Open in Planner", or a
 * change made in another window). It stays silent at start.
 */

import React, { useEffect, useRef, useState } from 'react';
import { useOptionalTargetContext } from '../context/TargetContext';

const VISUALLY_HIDDEN: React.CSSProperties = {
    position: 'absolute',
    width: 1,
    height: 1,
    overflow: 'hidden',
    clip: 'rect(0 0 0 0)',
    whiteSpace: 'nowrap',
};

/**
 * Announces target changes to screen readers.
 * @return The live region.
 */
export const CurrentTargetAnnouncer: React.FC = () => {
    const selectedTarget = useOptionalTargetContext()?.selectedTarget ?? '';
    const previousTarget = useRef(selectedTarget);
    const [message, setMessage] = useState('');
    useEffect(() => {
        if (selectedTarget && selectedTarget !== previousTarget.current) {
            setMessage(`Current target: ${selectedTarget}`);
        }
        previousTarget.current = selectedTarget;
    }, [selectedTarget]);
    return (
        <div aria-live="polite" role="status" style={VISUALLY_HIDDEN} data-testid="current-target-announcer">
            {message}
        </div>
    );
};
