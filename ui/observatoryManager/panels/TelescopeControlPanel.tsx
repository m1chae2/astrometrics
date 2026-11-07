/**
 * @file TelescopeControlPanel.tsx
 * @description The Telescope Control panel. It holds everything that starts
 * alignment, tracking, guiding or parking, together with the limits that can
 * stop them, on three tabs: "Mount" (the alignment, tracking and park
 * buttons), "Manual" (the Telescope direction pad and slew speed, and the Focuser) and "Limits" (four
 * short sections: Alignment and Guiding settings, then the Altitude and Hour
 * angle limits with one shared form and Apply button). One "Why unavailable?"
 * button in the header explains why the controls are greyed, and every tab
 * greys its live controls the same way. The tabs follow the WAI-ARIA tab
 * pattern. The settings values live in the parent, so they survive a tab switch.
 */

import React, { useState } from 'react';
import { SectionPanel } from '../../common/components/SectionPanel';
import { TabPanel, Tabs, type TabDefinition } from '../../common/components/Tabs';
import { CommandGate, WhyUnavailableButton } from '../CommandGate';
import { TelescopeControl } from './TelescopeControl';
import { DirectionalControl, DEFAULT_SLEW_RATE } from './DirectionalControl';
import { FocuserControl } from './FocuserControl';
import { TrackingSettings } from './TrackingSettings';
import { LimitSettings, useSafetyLimitsForm } from './LimitSettings';

/** The three tabs of the panel, in order. */
const TELESCOPE_CONTROL_TABS: TabDefinition[] = [
    { id: 'controls', label: 'Mount' },
    { id: 'manual', label: 'Manual' },
    { id: 'limits', label: 'Limits' },
];

/** Prefix that keeps the tab element ids unique on the page. */
const TELESCOPE_CONTROL_TABS_ID_PREFIX = 'telescope-control';

export interface TelescopeControlPanelProps {
    isTracking: boolean;
    isParked: boolean;
    onSetTracking: (enabled: boolean) => void;
    onPark: () => void;
    onUnpark: () => void;
    onStartAlignment?: () => void;
    onStopAlignment?: () => void;
    isAligningActive?: boolean;

    // Manual slew (the direction pad on the Manual tab)
    onStartMove: (direction: string) => void;
    onStopMove: (direction: string) => void;
    onSetSlewRate: (rate: number) => void;
    onStop: () => void;

    // Alignment settings
    accuracy?: number;
    settleTime?: number;
    onAccuracyChange?: (value: number) => void;
    onSettleTimeChange?: (value: number) => void;

    // Guiding settings
    guidingExposure?: number;
    guidingGain?: number;
    onGuidingExposureChange?: (value: number) => void;
    onGuidingGainChange?: (value: number) => void;

    /** True when the app may not command the equipment. The live controls are then greyed out. */
    commandsDisabled?: boolean;
    /** Why commands are off; shown by the header button and on hover. */
    commandsDisabledReason?: string;
}

/**
 * The Telescope Control panel with its three tabs.
 * @param props See {@link TelescopeControlPanelProps}.
 * @return The panel.
 */
export const TelescopeControlPanel: React.FC<TelescopeControlPanelProps> = ({
    isTracking,
    isParked,
    onSetTracking,
    onPark,
    onUnpark,
    onStartAlignment,
    onStopAlignment,
    isAligningActive = false,
    onStartMove,
    onStopMove,
    onSetSlewRate,
    onStop,
    accuracy,
    settleTime,
    onAccuracyChange,
    onSettleTimeChange,
    guidingExposure,
    guidingGain,
    onGuidingExposureChange,
    onGuidingGainChange,
    commandsDisabled = false,
    commandsDisabledReason = '',
}) => {
    const [selectedTab, setSelectedTab] = useState('controls');
    // The slew rate lives here, so the slider keeps it when the Manual tab is closed and opened.
    const [slewRate, setSlewRate] = useState<number>(DEFAULT_SLEW_RATE);
    /** Remembers the chosen rate and passes it on to the mount. */
    const handleSlewRateChange = (rate: number): void => {
        setSlewRate(rate);
        onSetSlewRate(rate);
    };
    // One form for both limit sections, so an unsaved edit survives a tab switch.
    const limitsForm = useSafetyLimitsForm(commandsDisabled);

    /**
     * Draws the settings of one group inside a command gate.
     * @param section Which settings to show.
     * @return The gated settings.
     */
    const renderSettings = (section: 'alignment' | 'guiding') => (
        <CommandGate
            disabled={commandsDisabled}
            reason={commandsDisabledReason}
            renderChildren={(reasonId) => (
                <TrackingSettings
                    section={section}
                    disabled={commandsDisabled}
                    disabledReasonId={reasonId}
                    accuracy={accuracy}
                    settleTime={settleTime}
                    onAccuracyChange={onAccuracyChange}
                    onSettleTimeChange={onSettleTimeChange}
                    guidingExposure={guidingExposure}
                    guidingGain={guidingGain}
                    onGuidingExposureChange={onGuidingExposureChange}
                    onGuidingGainChange={onGuidingGainChange}
                />
            )}
        />
    );

    return (
        <SectionPanel
            title="Telescope Control"
            className="observatory-top-row__fit telescope-control-panel"
            headerContent={<WhyUnavailableButton disabled={commandsDisabled} reason={commandsDisabledReason} />}
        >
            <Tabs
                tabs={TELESCOPE_CONTROL_TABS}
                selectedId={selectedTab}
                onChange={setSelectedTab}
                ariaLabel="Telescope control groups"
                idPrefix={TELESCOPE_CONTROL_TABS_ID_PREFIX}
                className="telescope-control-tabs"
            />
            <TabPanel idPrefix={TELESCOPE_CONTROL_TABS_ID_PREFIX} tabId="controls" selected={selectedTab === 'controls'}>
                <CommandGate disabled={commandsDisabled} reason={commandsDisabledReason}>
                    <TelescopeControl
                        disabled={commandsDisabled}
                        isTracking={isTracking}
                        isParked={isParked}
                        onSetTracking={onSetTracking}
                        onPark={onPark}
                        onUnpark={onUnpark}
                        onStartAlignment={onStartAlignment}
                        onStopAlignment={onStopAlignment}
                        isAligningActive={isAligningActive}
                    />
                </CommandGate>
            </TabPanel>
            <TabPanel idPrefix={TELESCOPE_CONTROL_TABS_ID_PREFIX} tabId="manual" selected={selectedTab === 'manual'}>
                <CommandGate disabled={commandsDisabled} reason={commandsDisabledReason}>
                    <div className="manual-tab">
                        <section className="manual-tab__section" aria-labelledby="manual-telescope-heading">
                            <h4 id="manual-telescope-heading" className="limits-tab__heading">Telescope</h4>
                            <DirectionalControl
                                disabled={commandsDisabled}
                                onStartMove={onStartMove}
                                onStopMove={onStopMove}
                                slewRate={slewRate}
                                onSetSlewRate={handleSlewRateChange}
                                onStop={onStop}
                            />
                        </section>
                        <section className="manual-tab__section" aria-labelledby="manual-focuser-heading">
                            <h4 id="manual-focuser-heading" className="limits-tab__heading">Focuser</h4>
                            <FocuserControl active={selectedTab === 'manual'} disabled={commandsDisabled} />
                        </section>
                    </div>
                </CommandGate>
            </TabPanel>
            <TabPanel idPrefix={TELESCOPE_CONTROL_TABS_ID_PREFIX} tabId="limits" selected={selectedTab === 'limits'}>
                <div className="limits-tab">
                    <section className="limits-tab__section" aria-labelledby="limits-alignment-heading">
                        <h4 id="limits-alignment-heading" className="limits-tab__heading">Alignment</h4>
                        {renderSettings('alignment')}
                    </section>
                    <section className="limits-tab__section" aria-labelledby="limits-guiding-heading">
                        <h4 id="limits-guiding-heading" className="limits-tab__heading">Guiding</h4>
                        {renderSettings('guiding')}
                    </section>
                    <section className="limits-tab__section limits-tab__section--wide">
                        <LimitSettings form={limitsForm} group="all" disabled={commandsDisabled} reason={commandsDisabledReason} />
                    </section>
                </div>
            </TabPanel>
        </SectionPanel>
    );
};
