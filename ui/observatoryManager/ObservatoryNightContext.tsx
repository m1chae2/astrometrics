/**
 * Description: Which night the Observatory Manager shows.
 * The view is either "Live" (what is happening now) or one past observing night. The choice
 * lives at the top of the view, and every panel reads it from here. Without a provider the
 * context says "Live", so a panel used alone behaves as it always did.
 */

import React from 'react';
import type { NightListEntry } from '../common/types/backendTypes';

/** What the panels need to know about the chosen night. */
export interface ObservatoryNightValue {
    /** True when the view shows live data. False when it shows a past night. */
    isLive: boolean;
    /** The night id, such as "2026-11-04", or null while Live. */
    nightId: string | null;
    /** The night's date as text, or an empty string while Live. */
    nightDate: string;
    /** The night's list entry (targets imaged and so on), or null while Live or while the list loads. */
    night: NightListEntry | null;
    /** Why the live-only controls are off, or an empty string while Live. */
    liveOnlyReason: string;
}

/** The value while the view is Live. */
export const LIVE_NIGHT_VALUE_OBJECT: ObservatoryNightValue = {
    isLive: true,
    nightId: null,
    nightDate: '',
    night: null,
    liveOnlyReason: '',
};

/**
 * Builds the sentence that says why the live-only controls are off.
 *
 * @param nightDate The date of the night being viewed.
 * @returns The sentence, such as "Viewing the night of 2026-11-04. Controls are live-only; pick Live to use them."
 */
export const liveOnlyReasonForNight = (nightDate: string): string =>
    `Viewing the night of ${nightDate}. Controls are live-only; pick Live to use them.`;

/** The context. Its default is the Live value. */
export const ObservatoryNightContext = React.createContext<ObservatoryNightValue>(LIVE_NIGHT_VALUE_OBJECT);

/**
 * Reads which night the Observatory Manager shows.
 *
 * @returns The night value; Live when no provider is above the caller.
 */
export const useObservatoryNight = (): ObservatoryNightValue => React.useContext(ObservatoryNightContext);
