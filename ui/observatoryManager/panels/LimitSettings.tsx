/**
 * @file LimitSettings.tsx
 * @description The bodies of the Altitude and Hour angle tabs of
 * the Telescope Control panel. It shows the active telescope's saved limits and saves changes
 * through the library. The limits sit on two tabs: "Altitude" (the
 * altitude window) and "Hour angle" (the hour angle limit and the
 * meridian flip point). Both tabs share one form and one Apply button. The form fills in when the read arrives. "Apply safety limits"
 * is at the bottom of the tab. It is enabled only when something changed, and,
 * like every equipment control, it is greyed while the app may not command the
 * equipment. The inputs stay readable when greyed.
 */

import React, { useEffect, useState } from 'react';
import { Checkbox } from '../../common/components/Checkbox';
import { NumberInput } from '../../common/components/NumberInput';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { callBackend } from '../../common/services/backendApi';
import { emitToast } from '../../common/utils/emitToast';
import { FormRow } from '../../common/components/FormRow';
import { CommandGate } from '../CommandGate';
import type { Telescope } from '../../common/types/backendTypes';
import { Button } from '../../common/components/Button';

/** The React Query key of the active telescope read. */
export const ACTIVE_TELESCOPE_QUERY_KEY = ['observatory', 'activeTelescope'] as const;

/** Said in the success message, because Ekos keeps its own copy of the session. */
export const EKOS_COPY_WARNING =
    "Ekos's copy of the session may be out of date. Copy the session into Ekos again to use the new limits there.";

/** The form fields, kept as text so a half-typed number is not lost. */
export interface LimitFormValues {
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

/** The id of the hidden text that says why Apply is greyed. */
const APPLY_REASON_ID = 'limit-settings-apply-reason';

export type LimitGroup = 'altitude' | 'hour-angle';

/** Everything the two limit tabs share: the read, the form, the save and its messages. */
export interface SafetyLimitsForm {
    telescopeQuery: ReturnType<typeof useQuery<Telescope | null>>;
    telescope: Telescope | null;
    values: LimitFormValues | null;
    refusal: string | null;
    showEkosCopyNote: boolean;
    hasChanged: boolean;
    numbersValid: boolean;
    applyDisabled: boolean;
    clearRefusal: () => void;
    update: (changes: Partial<LimitFormValues>) => void;
    handleApply: () => void;
}

/**
 * Holds the limits form for both limit tabs, so a half-typed edit survives a
 * switch between tabs. Call it once, in the panel that shows the tabs.
 * @param disabled True when the app may not command the equipment.
 * @return The shared form state. See {@link SafetyLimitsForm}.
 */
export function useSafetyLimitsForm(disabled: boolean): SafetyLimitsForm {
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

    const saved = telescope ? formValuesFromTelescope(telescope) : null;
    const hasChanged = !!saved && !!values
        && (Object.keys(saved) as (keyof LimitFormValues)[]).some((key) => saved[key] !== values[key]);
    const numbersValid = !values || [values.minimumAltitude, values.maximumAltitude, values.maximumHourAngle, values.flipHourAngle]
        .every((text) => text.trim() !== '' && Number.isFinite(Number(text)));
    const applyDisabled = disabled || !hasChanged || !numbersValid || saveMutation.isPending;

    return {
        telescopeQuery,
        telescope,
        values,
        refusal,
        showEkosCopyNote,
        hasChanged,
        numbersValid,
        applyDisabled,
        clearRefusal: () => setRefusal(null),
        update: (changes) => setValues((current) => (current ? { ...current, ...changes } : current)),
        handleApply: () => {
            if (disabled || !hasChanged || !numbersValid || !values) {
                return;
            }
            setRefusal(null);
            saveMutation.mutate(limitsFromFormValues(values));
        },
    };
}

export interface LimitSettingsProps {
    /** The shared form, from {@link useSafetyLimitsForm}. */
    form: SafetyLimitsForm;
    /** Which limits this tab shows. */
    group: LimitGroup;
    /** True when the app may not command the equipment. Apply and the checkboxes are then greyed. */
    disabled?: boolean;
    /** Why the controls are unavailable; shown on hover and read by screen readers. */
    reason?: string;
}

/**
 * The body of the Altitude limits tab or the Hour angle limits tab. Both show
 * the telescope name and the same Apply button, which saves the whole form.
 * @param props See {@link LimitSettingsProps}.
 * @return The tab body.
 */
export const LimitSettings: React.FC<LimitSettingsProps> = ({ form, group, disabled = false, reason = '' }) => {
    const { telescopeQuery, telescope, values, refusal, showEkosCopyNote, hasChanged, numbersValid, applyDisabled, update, handleApply } = form;

    if (telescopeQuery.isPending) {
        return <p aria-busy="true">Loading…</p>;
    }
    if (telescopeQuery.isError) {
        return (
            <div role="alert">
                <p>Could not load the safety limits.</p>
                <Button onClick={() => void telescopeQuery.refetch()}>Try again</Button>
            </div>
        );
    }
    if (!telescope || !values) {
        return <p>No active telescope. Choose a telescope in the equipment settings to set its limits.</p>;
    }

    const applyButton = (
        <div className="limit-settings__footer">
            <Button
                size="small"
                className="limit-settings__apply"
                onClick={handleApply}
                disabled={applyDisabled}
                aria-disabled={applyDisabled}
                title={disabled ? reason : undefined}
                aria-describedby={refusal ? 'limit-settings-refusal' : (disabled ? APPLY_REASON_ID : undefined)}
            >
                Apply safety limits
            </Button>
            {(hasChanged || !numbersValid) && (
                <p className="limit-settings__hint">
                    {hasChanged && <span>Unsaved changes</span>}
                    {hasChanged && !numbersValid && ' '}
                    {!numbersValid && <span>Enter a number in every field.</span>}
                </p>
            )}
            {disabled && <span id={APPLY_REASON_ID} className="command-gate__reason">{reason}</span>}
        </div>
    );

    return (
        <CommandGate
            disabled={disabled}
            reason={reason}
            renderChildren={(reasonId) => {
                const unavailable = { 'aria-describedby': disabled ? reasonId : undefined };
                return (
                    <div id="safety-limits-group" className="controls__group limit-settings">
                        <p className="limit-settings__telescope">Telescope: {telescope.name}</p>
                        {group === 'altitude' && (
                    <div className="limit-settings__rows">
                        <Checkbox
                            className="limit-settings__checkbox"
                            id="checkbox-enable-alt-limits"
                            label="Enable altitude limits"
                            checked={values.altitudeEnabled}
                            onChange={(event) => update({ altitudeEnabled: event.target.checked })}
                            disabled={disabled}
                            {...unavailable}
                        />
                        <div className="limit-settings__fields">
                            <FormRow label="Minimum altitude" htmlFor="input-limit-min-alt" unit="°">
                                <NumberInput
                                    id="input-limit-min-alt"
                                    step="0.01"
                                    width="short"
                                    centered
                                    value={values.minimumAltitude}
                                    onChange={(event) => update({ minimumAltitude: event.target.value })}
                                    disabled={disabled || !values.altitudeEnabled}
                                    {...unavailable}
                                />
                            </FormRow>
                            <FormRow label="Maximum altitude" htmlFor="input-limit-max-alt" unit="°">
                                <NumberInput
                                    id="input-limit-max-alt"
                                    step="0.01"
                                    width="short"
                                    centered
                                    value={values.maximumAltitude}
                                    onChange={(event) => update({ maximumAltitude: event.target.value })}
                                    disabled={disabled || !values.altitudeEnabled}
                                    {...unavailable}
                                />
                            </FormRow>
                        </div>
                    </div>
                        )}
                        {group === 'hour-angle' && (
                    <div className="limit-settings__rows">
                        <Checkbox
                            className="limit-settings__checkbox"
                            id="checkbox-enable-ha-limits"
                            label="Enable hour angle limit"
                            checked={values.hourAngleEnabled}
                            onChange={(event) => update({ hourAngleEnabled: event.target.checked })}
                            disabled={disabled}
                            {...unavailable}
                        />
                        <div className="limit-settings__fields">
                            <FormRow label="Maximum hour angle" htmlFor="input-limit-max-ha" unit="hours">
                                <NumberInput
                                    id="input-limit-max-ha"
                                    step="0.01"
                                    width="short"
                                    centered
                                    value={values.maximumHourAngle}
                                    onChange={(event) => update({ maximumHourAngle: event.target.value })}
                                    disabled={disabled || !values.hourAngleEnabled}
                                    {...unavailable}
                                />
                            </FormRow>
                            <FormRow label="Meridian flip after transit" htmlFor="input-meridian-flip-ha" unit="°">
                                <NumberInput
                                    id="input-meridian-flip-ha"
                                    step="0.01"
                                    width="short"
                                    centered
                                    value={values.flipHourAngle}
                                    onChange={(event) => update({ flipHourAngle: event.target.value })}
                                    disabled={disabled}
                                    {...unavailable}
                                />
                            </FormRow>
                        </div>
                    </div>
                        )}
                        {refusal && (
                            <div role="alert" id="limit-settings-refusal" className="limit-settings__refusal">
                                <span>{refusal}</span>
                                <Button onClick={form.clearRefusal}>Dismiss</Button>
                            </div>
                        )}
                        {showEkosCopyNote && <p id="limit-settings-ekos-note" className="limit-settings__hint">{EKOS_COPY_WARNING}</p>}
                        {applyButton}
                    </div>
                );
            }}
        />
    );
};
