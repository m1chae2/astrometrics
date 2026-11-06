/**
 * @file TelescopeControlPanel.tsx
 * @description The Telescope Control panel. It holds everything that starts
 * alignment, tracking, guiding or parking, together with the limits that can
 * stop them, on five tabs: "Controls" (the alignment, tracking and park
 * buttons), "Alignment" (its
 * settings), "Guiding" (its settings and the Start and Stop buttons),
 * "Altitude" and "Hour angle" (the limits, one shared form and Apply button). One "Why unavailable?"
 * button in the header explains why the controls are greyed, and every tab
 * greys its live controls the same way. The tabs follow the WAI-ARIA tab
 * pattern. The settings values live in the parent, so they survive a tab switch.
 */

import React, { useState } from 'react';
import { SectionPanel } from '../../common/components/SectionPanel';
import { TabPanel, Tabs, type TabDefinition } from '../../common/components/Tabs';
import { CommandGate, WhyUnavailableButton } from '../CommandGate';
import { TelescopeControl } from './TelescopeControl';
import { TrackingSettings } from './TrackingSettings';
import { LimitSettings, useSafetyLimitsForm } from './LimitSettings';

/** The five tabs of the panel, in order. */
const TELESCOPE_CONTROL_TABS: TabDefinition[] = [
    { id: 'controls', label: 'Controls' },
    { id: 'alignment', label: 'Alignment' },
    { id: 'guiding', label: 'Guiding' },
    { id: 'altitude', label: 'Altitude' },
    { id: 'hour-angle', label: 'Hour angle' },
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
    onGuideStart?: (exposure: number, gain: number) => void;
    onGuideStop?: () => void;

    /** True when the app may not command the equipment. The live controls are then greyed out. */
    commandsDisabled?: boolean;
    /** Why commands are off; shown by the header button and on hover. */
    commandsDisabledReason?: string;
}

/**
 * The Telescope Control panel with its five tabs.
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
    accuracy,
    settleTime,
    onAccuracyChange,
    onSettleTimeChange,
    guidingExposure,
    guidingGain,
    onGuidingExposureChange,
    onGuidingGainChange,
    onGuideStart,
    onGuideStop,
    commandsDisabled = false,
    commandsDisabledReason = '',
}) => {
    const [selectedTab, setSelectedTab] = useState('controls');
    // One form for both limit tabs, so an unsaved edit survives a tab switch.
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
                    onGuideStart={onGuideStart}
                    onGuideStop={onGuideStop}
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
                size="compact"
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
            <TabPanel idPrefix={TELESCOPE_CONTROL_TABS_ID_PREFIX} tabId="alignment" selected={selectedTab === 'alignment'}>
                {renderSettings('alignment')}
            </TabPanel>
            <TabPanel idPrefix={TELESCOPE_CONTROL_TABS_ID_PREFIX} tabId="guiding" selected={selectedTab === 'guiding'}>
                {renderSettings('guiding')}
            </TabPanel>
            <TabPanel idPrefix={TELESCOPE_CONTROL_TABS_ID_PREFIX} tabId="altitude" selected={selectedTab === 'altitude'}>
                <LimitSettings form={limitsForm} group="altitude" disabled={commandsDisabled} reason={commandsDisabledReason} />
            </TabPanel>
            <TabPanel idPrefix={TELESCOPE_CONTROL_TABS_ID_PREFIX} tabId="hour-angle" selected={selectedTab === 'hour-angle'}>
                <LimitSettings form={limitsForm} group="hour-angle" disabled={commandsDisabled} reason={commandsDisabledReason} />
            </TabPanel>
        </SectionPanel>
    );
};
