/**
 * @fileoverview Hook for the "Observatory source" setting: reads which source the backend runs with
 * and switches the source (which restarts the backend).
 */

import { useCallback, useEffect, useState } from 'react';
import { setBackendRestarting } from '../../common/services/backendApi';
import {
    getObservatorySource,
    setObservatorySource,
    waitForBackendRestart,
    ObservatorySource,
    ObservatorySourceState,
} from '../../common/services/observatoryService';

/** What the hook offers the screens. */
export interface ObservatorySourceControl {
    /** The backend's report, or null until it answers. */
    state: ObservatorySourceState | null;
    /** True while the backend restarts to switch the source. */
    switching: boolean;
    /** The text of the last failed switch, or null. */
    error: string | null;
    /** Saves the source, waits for the backend to restart, then reloads the page. */
    switchSource: (source: ObservatorySource) => Promise<void>;
}

/**
 * Reads and changes the observatory source.
 *
 * @param active False to skip the first read (for a hidden panel).
 * @return The state and the actions.
 */
export function useObservatorySource(active = true): ObservatorySourceControl {
    const [state, setState] = useState<ObservatorySourceState | null>(null);
    const [switching, setSwitching] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!active) return;
        let cancelled = false;
        getObservatorySource()
            .then((value) => { if (!cancelled) setState(value ?? null); })
            .catch(() => { /* The backend may be busy; the header chip and the setting simply stay empty. */ });
        return () => { cancelled = true; };
    }, [active]);

    const switchSource = useCallback(async (source: ObservatorySource) => {
        setError(null);
        setSwitching(true);
        // The backend is about to go away. Calls that fail while it restarts are expected, not errors.
        setBackendRestarting(true);
        try {
            const result = await setObservatorySource(source);
            const back = await waitForBackendRestart(result.restarting);
            if (!back) {
                setBackendRestarting(false);
                setError('The backend did not come back in time. Check the backend log.');
                setSwitching(false);
                return;
            }
            // Everything on screen came from the other library, so start the page again.
            window.location.reload();
        } catch (failure) {
            setBackendRestarting(false);
            setError(failure instanceof Error ? failure.message : String(failure));
            setSwitching(false);
        }
    }, []);

    return { state, switching, error, switchSource };
}
