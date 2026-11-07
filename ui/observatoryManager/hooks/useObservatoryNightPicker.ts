/**
 * Description: State of the Observatory Manager's night picker.
 * Reads the list of observing nights (`history:nights`), keeps the chosen night, and builds the
 * value the panels read (see ObservatoryNightContext). The list is read again when the backend
 * sends `library:guiding_updated`. Every night the library lists is offered, because each one
 * has some data (frames, guiding or an Ekos record).
 */

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { callBackend } from '../../common/services/backendApi';
import { qualityQueryKeys } from '../../common/queries/qualityQueryKeys';
import type { SessionPickerOption } from '../../common/components/SessionPicker';
import { useGuidingUpdatedRefresh } from '../panels/useGuidingUpdatedRefresh';
import {
    LIVE_NIGHT_VALUE_OBJECT,
    liveOnlyReasonForNight,
    type ObservatoryNightValue,
} from '../ObservatoryNightContext';

/** What the picker hook gives the view. */
export interface ObservatoryNightPicker {
    /** The nights to offer, newest first as the library lists them. */
    options: SessionPickerOption[];
    /** The chosen night id, or null for Live. */
    selectedId: string | null;
    /** Chooses a night, or Live when given null. */
    select: (nightId: string | null) => void;
    /** The value to put in the context for the panels. */
    value: ObservatoryNightValue;
}

/**
 * Turns a target id into text for people ("M_13" becomes "M 13").
 *
 * @param targetId The target id.
 * @returns The text.
 */
const targetText = (targetId: string): string => targetId.replace(/_/g, ' ');

/**
 * Keeps the night picker's list and choice.
 *
 * @returns The options, the choice, the setter and the context value.
 */
export function useObservatoryNightPicker(): ObservatoryNightPicker {
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const nightsQuery = useQuery({
        queryKey: qualityQueryKeys.nights(),
        queryFn: () => callBackend('history:nights', {}),
        retry: false,
    });
    useGuidingUpdatedRefresh([qualityQueryKeys.nights()]);
    const nights = nightsQuery.data;

    const options = useMemo<SessionPickerOption[]>(
        () => (nights ?? []).map((night) => ({
            id: night.nightId,
            label: night.nightDate,
            detail: (night.targetsImaged ?? []).map(targetText).join(', ') || undefined,
        })),
        [nights],
    );

    const value = useMemo<ObservatoryNightValue>(() => {
        if (selectedId === null) return LIVE_NIGHT_VALUE_OBJECT;
        const night = (nights ?? []).find((entry) => entry.nightId === selectedId) ?? null;
        const nightDate = night?.nightDate ?? selectedId;
        return {
            isLive: false,
            nightId: selectedId,
            nightDate,
            night,
            liveOnlyReason: liveOnlyReasonForNight(nightDate),
        };
    }, [selectedId, nights]);

    return { options, selectedId, select: setSelectedId, value };
}
