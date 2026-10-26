/**
 * @file LimitSettings.tsx
 * @description The Safety Limits panel. It shows the active telescope's saved
 * limits and saves changes through the library. The limits sit on two tabs:
 * "Altitude" (the altitude window) and "Hour angle" (the hour angle limit and
 * the meridian flip point). The form fills in when the read arrives. "Apply
 * safety limits" sits at the bottom of the panel body, in view without
 * scrolling. It is enabled only when something changed, and, like every equipment control, it
 * is greyed while the app may not command the equipment. The inputs stay
 * readable when greyed.
 */

import React, { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { callBackend } from '../../common/services/backendApi';
import { emitToast } from '../../common/utils/emitToast';
import { SectionPanel } from '../../common/components/SectionPanel';
import { FormRow } from '../../common/components/FormRow';
import { TabPanel, Tabs, type TabDefinition } from '../../common/components/Tabs';
import { CommandGate, WhyUnavailableButton } from '../CommandGate';
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

/** The two tabs of the panel. */
const LIMIT_TABS: TabDefinition[] = [
    { id: 'altitude', label: 'Altitude' },
    { id: 'hour-angle', label: 'Hour angle' },
];

/** Prefix that keeps the tab element ids unique on the page. */
const LIMIT_TABS_ID_PREFIX = 'safety-limits';

/** The id of the hidden text that says why Apply is greyed. */
const APPLY_REASON_ID = 'limit-settings-apply-reason';

export interface LimitSettingsProps {
    /** True when the app may not command the equipment. Apply and the checkboxes are then greyed. */
    disabled?: boolean;
    /** Why the controls are unavailable; shown by the "Why unavailable?" button and on hover. */
    reason?: string;
}

/**
 * The Safety Limits panel, with its header (the "Why unavailable?" button and
 * "Apply safety limits") and its two tabs.
 * @param props See {@link LimitSettingsProps}.
 * @return The panel.
 */
export const LimitSettings: React.FC<LimitSettingsProps> = ({ disabled = false, reason = '' }) => {
    const queryClient = useQueryClient();
    const telescopeQuery = useQuery({
        queryKey: ACTIVE_TELESCOPE_QUERY_KEY,
        queryFn: () => callBackend('observatory:get_active_telescope', {}),
        retry: false,
    });
    const telescope = telescopeQuery.data ?? null;
    const [values, setValues] = useState<LimitFormValues | null>(null);
    const [refusal, setRefusal] = useState<string | null>(null);
    const [selectedTab, setSelectedTab] = useState('altitude');
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

    const whyButton = <WhyUnavailableButton disabled={disabled} reason={reason} />;

    if (telescopeQuery.isPending) {
        return <SectionPanel title="Safety Limits" headerContent={whyButton}><p aria-busy="true">Loading…</p></SectionPanel>;
    }
    if (telescopeQuery.isError) {
        return (
            <SectionPanel title="Safety Limits" headerContent={whyButton}>
                <div role="alert">
                    <p>Could not load the safety limits.</p>
                    <button type="button" className="btn" onClick={() => void telescopeQuery.refetch()}>Try again</button>
                </div>
            </SectionPanel>
        );
    }
    if (!telescope || !values) {
        return (
            <SectionPanel title="Safety Limits" headerContent={whyButton}>
                <p>No active telescope. Choose a telescope in the equipment settings to set its limits.</p>
            </SectionPanel>
        );
    }

    const saved = formValuesFromTelescope(telescope);
    const hasChanged = (Object.keys(saved) as (keyof LimitFormValues)[]).some((key) => saved[key] !== values[key]);
    const numbersValid = [values.minimumAltitude, values.maximumAltitude, values.maximumHourAngle, values.flipHourAngle]
        .every((text) => text.trim() !== '' && Number.isFinite(Number(text)));
    const applyDisabled = disabled || !hasChanged || !numbersValid || saveMutation.isPending;

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

    // Apply sits in the panel header, where Camera Profiles keeps its Measure noise button.
    const applyHeaderButton = (
        <button
            type="button"
            className="btn btn--tiny quality-button limit-settings__apply"
            onClick={handleApply}
            disabled={applyDisabled}
            aria-disabled={applyDisabled}
            title={disabled ? reason : undefined}
            aria-describedby={refusal ? 'limit-settings-refusal' : (disabled ? APPLY_REASON_ID : undefined)}
        >
            Apply safety limits
        </button>
    );

    const applyButton = (
        <div className="limit-settings__footer">
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
        <SectionPanel
            title="Safety Limits"
            headerContent={<span className="limit-settings__header-actions">{whyButton}{applyHeaderButton}</span>}
        >
            <CommandGate
                disabled={disabled}
                reason={reason}
                renderChildren={(reasonId) => {
                    const unavailable = { 'aria-describedby': disabled ? reasonId : undefined };
                    return (
                        <div id="safety-limits-group" className="controls__group limit-settings">
                            <p className="limit-settings__telescope">Telescope: {telescope.name}</p>
                            <Tabs
                                tabs={LIMIT_TABS}
                                selectedId={selectedTab}
                                onChange={setSelectedTab}
                                ariaLabel="Safety limit groups"
                                idPrefix={LIMIT_TABS_ID_PREFIX}
                            />
                            <TabPanel idPrefix={LIMIT_TABS_ID_PREFIX} tabId="altitude" selected={selectedTab === 'altitude'}>
                                <div className="limit-settings__rows">
                                    <div className="limit-settings__checkbox">
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
                                    <div className="limit-settings__fields">
                                        <FormRow label="Minimum altitude" htmlFor="input-limit-min-alt" unit="°">
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
                                        </FormRow>
                                        <FormRow label="Maximum altitude" htmlFor="input-limit-max-alt" unit="°">
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
                                        </FormRow>
                                    </div>
                                </div>
                            </TabPanel>
                            <TabPanel idPrefix={LIMIT_TABS_ID_PREFIX} tabId="hour-angle" selected={selectedTab === 'hour-angle'}>
                                <div className="limit-settings__rows">
                                    <div className="limit-settings__checkbox">
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
                                    <div className="limit-settings__fields">
                                        <FormRow label="Maximum hour angle" htmlFor="input-limit-max-ha" unit="hours">
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
                                        </FormRow>
                                        <FormRow label="Meridian flip after (hour angle)" htmlFor="input-meridian-flip-ha" unit="°">
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
                                        </FormRow>
                                    </div>
                                </div>
                            </TabPanel>
                            {refusal && (
                                <div role="alert" id="limit-settings-refusal" className="limit-settings__refusal">
                                    <span>{refusal}</span>
                                    <button type="button" className="btn" onClick={() => setRefusal(null)}>Dismiss</button>
                                </div>
                            )}
                            {showEkosCopyNote && <p id="limit-settings-ekos-note" className="limit-settings__hint">{EKOS_COPY_WARNING}</p>}
                            {applyButton}
                        </div>
                    );
                }}
            />
        </SectionPanel>
    );
};
