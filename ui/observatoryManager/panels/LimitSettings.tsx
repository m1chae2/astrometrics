/**
 * @file LimitSettings.tsx
 * @description The Safety Limits panel. It shows the active telescope's saved
 * limits (the altitude window, the hour angle limit and the meridian flip
 * point) and saves changes through the library. The form fills in when the
 * read arrives. "Apply safety limits" is enabled only when something changed,
 * and, like every equipment control, it is greyed while the app may not
 * command the equipment. The inputs stay readable when greyed.
 */

import React, { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { callBackend } from '../../common/services/backendApi';
import { emitToast } from '../../common/utils/emitToast';
import type { Telescope } from '../../common/types/backendTypes';

/** The React Query key of the active telescope read. */
export const ACTIVE_TELESCOPE_QUERY_KEY = ['observatory', 'activeTelescope'] as const;

/** Said in the success message, because Ekos keeps its own copy of the session. */
export const EKOS_COPY_WARNING =
    "Ekos's copy of the session may be out of date. Copy the session into Ekos again to use the new limits there.";

/** The form fields, kept as text so a half-typed number is not lost. */
interface LimitFormValues {
    altitudeEnabled: boolean;
    minimumAltitude: string;
    maximumAltitude: string;
    hourAngleEnabled: boolean;
    maximumHourAngle: string;
    flipHourAngle: string;
}

/**
 * Turns the saved limits into form values.
 * @param telescope The active telescope.
 * @return The form values.
 */
function formValuesFromTelescope(telescope: Telescope): LimitFormValues {
    return {
        altitudeEnabled: telescope.altitude_limits_enabled ?? false,
        minimumAltitude: String(telescope.min_altitude_deg ?? 0),
        maximumAltitude: String(telescope.max_altitude_deg ?? 90),
        hourAngleEnabled: telescope.hour_angle_limits_enabled ?? false,
        maximumHourAngle: String(telescope.max_hour_angle_hours ?? 0),
        flipHourAngle: String(telescope.flip_hour_angle_deg ?? 0),
    };
}

/**
 * Turns form values into the limits the save call takes.
 * @param values The form values.
 * @return The limits payload.
 */
function limitsFromFormValues(values: LimitFormValues): Record<string, number | boolean> {
    return {
        altitude_limits_enabled: values.altitudeEnabled,
        min_altitude_deg: Number(values.minimumAltitude),
        max_altitude_deg: Number(values.maximumAltitude),
        hour_angle_limits_enabled: values.hourAngleEnabled,
        max_hour_angle_hours: Number(values.maximumHourAngle),
        flip_hour_angle_deg: Number(values.flipHourAngle),
    };
}

export interface LimitSettingsProps {
    /** True when the app may not command the equipment. Apply and the checkboxes are then greyed. */
    disabled?: boolean;
    /** The id of the hidden text that says why the controls are unavailable. */
    disabledReasonId?: string;
}

/**
 * The Safety Limits panel body.
 * @param props See {@link LimitSettingsProps}.
 * @return The limits form.
 */
export const LimitSettings: React.FC<LimitSettingsProps> = ({ disabled = false, disabledReasonId }) => {
    const queryClient = useQueryClient();
    const telescopeQuery = useQuery({
        queryKey: ACTIVE_TELESCOPE_QUERY_KEY,
        queryFn: () => callBackend('observatory:get_active_telescope', {}),
        retry: false,
    });
    const telescope = telescopeQuery.data ?? null;
    const [values, setValues] = useState<LimitFormValues | null>(null);
    const [refusal, setRefusal] = useState<string | null>(null);
    // Stays on screen after a save, because a toast fades and Ekos's copy stays old.
    const [showEkosCopyNote, setShowEkosCopyNote] = useState(false);

    // Reset the form whenever a new read arrives.
    useEffect(() => {
        if (telescope) {
            setValues(formValuesFromTelescope(telescope));
        }
    }, [telescope]);

    const saveMutation = useMutation({
        mutationFn: (limits: Record<string, number | boolean>) =>
            callBackend('observatory:set_active_telescope', { telescope_id: (telescope as Telescope).id, limits }),
        onSuccess: () => {
            setRefusal(null);
            setShowEkosCopyNote(true);
            emitToast(`Safety limits saved. ${EKOS_COPY_WARNING}`, 'success');
            void queryClient.invalidateQueries({ queryKey: ACTIVE_TELESCOPE_QUERY_KEY });
        },
        onError: (error: unknown) => {
            setRefusal(error instanceof Error ? error.message : String(error));
        },
    });

    if (telescopeQuery.isPending) {
        return <p aria-busy="true">Loading…</p>;
    }
    if (telescopeQuery.isError) {
        return (
            <div role="alert">
                <p>Could not load the safety limits.</p>
                <button type="button" className="btn" onClick={() => void telescopeQuery.refetch()}>Try again</button>
            </div>
        );
    }
    if (!telescope || !values) {
        return <p>No active telescope. Choose a telescope in the equipment settings to set its limits.</p>;
    }

    const saved = formValuesFromTelescope(telescope);
    const hasChanged = (Object.keys(saved) as (keyof LimitFormValues)[]).some((key) => saved[key] !== values[key]);
    const numbersValid = [values.minimumAltitude, values.maximumAltitude, values.maximumHourAngle, values.flipHourAngle]
        .every((text) => text.trim() !== '' && Number.isFinite(Number(text)));
    const unavailable = { 'aria-describedby': disabled ? disabledReasonId : undefined };

    /**
     * Saves the limits, unless the app may not command the equipment.
     * @return Nothing.
     */
    const handleApply = () => {
        if (disabled || !hasChanged || !numbersValid) {
            return;
        }
        setRefusal(null);
        saveMutation.mutate(limitsFromFormValues(values));
    };

    /**
     * Updates one form field.
     * @param changes The fields to change.
     * @return Nothing.
     */
    const update = (changes: Partial<LimitFormValues>) => setValues({ ...values, ...changes });

    return (
        <div id="safety-limits-group" className="controls__group">
            <p className="limit-settings__telescope">Telescope: {telescope.name}</p>
            <div className="controls__group">
                <div className="checkbox-container">
                    <input
                        id="checkbox-enable-alt-limits"
                        type="checkbox"
                        checked={values.altitudeEnabled}
                        onChange={(event) => update({ altitudeEnabled: event.target.checked })}
                        disabled={disabled}
                        {...unavailable}
                        className="checkbox"
                    />
                    <label htmlFor="checkbox-enable-alt-limits">Enable altitude limits</label>
                </div>
                <div id="row-limit-min-alt" className="parameter">
                    <label htmlFor="input-limit-min-alt" className="parameter__label limit-settings__label">Minimum altitude</label>
                    <div className="controls__group-row limit-settings__group-row--auto">
                        <input
                            id="input-limit-min-alt"
                            type="number"
                            step="0.01"
                            className="input input--short"
                            value={values.minimumAltitude}
                            onChange={(event) => update({ minimumAltitude: event.target.value })}
                            disabled={disabled || !values.altitudeEnabled}
                            {...unavailable}
                        />
                        <span className="limit-settings__unit">°</span>
                    </div>
                </div>
                <div id="row-limit-max-alt" className="parameter">
                    <label htmlFor="input-limit-max-alt" className="parameter__label limit-settings__label">Maximum altitude</label>
                    <div className="controls__group-row limit-settings__group-row--auto">
                        <input
                            id="input-limit-max-alt"
                            type="number"
                            step="0.01"
                            className="input input--short"
                            value={values.maximumAltitude}
                            onChange={(event) => update({ maximumAltitude: event.target.value })}
                            disabled={disabled || !values.altitudeEnabled}
                            {...unavailable}
                        />
                        <span className="limit-settings__unit">°</span>
                    </div>
                </div>
            </div>

            <div className="controls__group limit-settings__group--margined">
                <div className="checkbox-container">
                    <input
                        id="checkbox-enable-ha-limits"
                        type="checkbox"
                        checked={values.hourAngleEnabled}
                        onChange={(event) => update({ hourAngleEnabled: event.target.checked })}
                        disabled={disabled}
                        {...unavailable}
                        className="checkbox"
                    />
                    <label htmlFor="checkbox-enable-ha-limits">Enable hour angle limit</label>
                </div>
                <div id="row-limit-max-ha" className="parameter">
                    <label htmlFor="input-limit-max-ha" className="parameter__label limit-settings__label">Maximum hour angle</label>
                    <div className="controls__group-row limit-settings__group-row--auto">
                        <input
                            id="input-limit-max-ha"
                            type="number"
                            step="0.01"
                            className="input input--short"
                            value={values.maximumHourAngle}
                            onChange={(event) => update({ maximumHourAngle: event.target.value })}
                            disabled={disabled || !values.hourAngleEnabled}
                            {...unavailable}
                        />
                        <span className="limit-settings__unit">hours</span>
                    </div>
                </div>
                <div id="row-meridian-flip-ha" className="parameter">
                    <label htmlFor="input-meridian-flip-ha" className="parameter__label limit-settings__label">
                        Meridian flip after (hour angle)
                    </label>
                    <div className="controls__group-row limit-settings__group-row--auto">
                        <input
                            id="input-meridian-flip-ha"
                            type="number"
                            step="0.01"
                            className="input input--short"
                            value={values.flipHourAngle}
                            onChange={(event) => update({ flipHourAngle: event.target.value })}
                            disabled={disabled}
                            {...unavailable}
                        />
                        <span className="limit-settings__unit">°</span>
                    </div>
                </div>
            </div>

            {refusal && (
                <div role="alert" id="limit-settings-refusal" className="limit-settings__refusal">
                    <span>{refusal}</span>
                    <button type="button" className="btn" onClick={() => setRefusal(null)}>Dismiss</button>
                </div>
            )}
            {showEkosCopyNote && <p id="limit-settings-ekos-note" className="limit-settings__hint">{EKOS_COPY_WARNING}</p>}
            <div className="controls__group-row">
                <button
                    type="button"
                    className="btn btn--primary"
                    onClick={handleApply}
                    disabled={disabled || !hasChanged || !numbersValid || saveMutation.isPending}
                    aria-disabled={disabled || !hasChanged || !numbersValid || saveMutation.isPending}
                    aria-describedby={refusal ? 'limit-settings-refusal' : unavailable['aria-describedby']}
                >
                    Apply safety limits
                </button>
                {hasChanged && <span className="limit-settings__hint">Unsaved changes</span>}
                {!numbersValid && <span className="limit-settings__hint">Enter a number in every field.</span>}
            </div>
        </div>
    );
};
