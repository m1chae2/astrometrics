/**
 * @fileoverview The Night panel of Observation Execution: the night
 * selector, the current target, "Working at Now", how stale the data is and
 * "Sync now".
 *
 * The selector lists `history:nights` in the order returned. The first choice,
 * "Live session", is a way to look at the screen (the running session), not a
 * night of the library. The sync times are example data until wave L1, and
 * "Sync now" stays off until wave L2.
 */

import React from 'react';
import type { NightListEntry } from '../common/types/backendTypes';
import { exampleSyncStatus } from '../common/examples';
import { ExampleDataBadge } from '../common/components/quality/ExampleDataBadge';
import { ExecutionPanel } from './ExecutionPanel';
import { useNow } from './hooks/useNow';
import { formatNightLabel, formatSyncLine } from './utils/formatDisplay';

/** The value of the selector's first choice, which shows the running session. */
export const LIVE_SESSION_VALUE = '';

/** The visible reason why "Sync now" is off. */
export const SYNC_UNAVAILABLE_TEXT = 'Sync arrives with the library sync run';

export interface ExecutionHeaderProps {
    /** The nights, in the order the library returned them. */
    nights: NightListEntry[];
    /** True while the nights are loading. */
    nightsLoading: boolean;
    /** True when reading the nights failed. */
    nightsFailed: boolean;
    /** The chosen night id, or `LIVE_SESSION_VALUE`. */
    selectedNightId: string;
    /** Called when the user picks a night. */
    onSelectNight: (nightId: string) => void;
    /** The current target's id, or an empty string. */
    currentTarget: string;
}

/**
 * The Night panel.
 * @param props The nights, the selection and the current target.
 * @return The Night panel.
 */
export const ExecutionHeader: React.FC<ExecutionHeaderProps> = ({
    nights,
    nightsLoading,
    nightsFailed,
    selectedNightId,
    onSelectNight,
    currentTarget,
}) => {
    const now = useNow();
    return (
        <ExecutionPanel title="Night">
            <div className="execution-night">
                <label className="execution-header__field">
                    <span>Night</span>
                    <select
                        className="dropdown"
                        value={selectedNightId}
                        onChange={(event) => onSelectNight(event.target.value)}
                        aria-busy={nightsLoading}
                    >
                        <option value={LIVE_SESSION_VALUE}>Live session</option>
                        {nights.map((night) => (
                            <option key={night.nightId} value={night.nightId}>
                                {formatNightLabel(night.nightDate, night.targetsImaged?.length ?? 0)}
                            </option>
                        ))}
                    </select>
                </label>
                {nightsLoading && <span className="execution-header__note">Loading nights…</span>}
                {nightsFailed && <span className="execution-header__note" role="alert">Could not load the night list</span>}
                <span className="execution-header__field">
                    <span>Current target</span>
                    <strong>{currentTarget || 'No target chosen'}</strong>
                </span>
                <span className="execution-header__field">
                    <strong>Working at Now</strong>
                </span>
                <span className="execution-header__sync" data-example="true">
                    <ExampleDataBadge />
                    <span>{formatSyncLine('Logs', exampleSyncStatus.lastLogSync, now)}</span>
                    <span>{formatSyncLine('Frames', exampleSyncStatus.lastFrameSync, now)}</span>
                </span>
                <span className="execution-header__sync-action">
                    <button
                        type="button"
                        className="btn btn--tiny"
                        disabled
                        aria-disabled="true"
                        aria-describedby="execution-sync-reason"
                    >
                        Sync now
                    </button>
                    <span id="execution-sync-reason" className="execution-header__note">
                        {SYNC_UNAVAILABLE_TEXT}
                    </span>
                </span>
            </div>
        </ExecutionPanel>
    );
};
