/**
 * @fileoverview Observation Execution: the display for watching the night
 * while Ekos runs it.
 *
 * Layout (UI_DISPLAY_PLAN 3.2): a pinned header (night selector, current
 * target, "Working at Now", staleness and "Sync now"); for the live session
 * the quality strip, the live verdict, the re-ranked candidates and the frame
 * list; for an earlier night the same data from storage; then the tabs
 * "Guiding" and "Control". The display always works at Now: it does not use
 * the shared planning time. The control panels are disabled while Ekos is in
 * charge.
 */

import React, { useId, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { callBackend } from '../common/services/backendApi';
import { qualityQueryKeys } from '../common/queries/qualityQueryKeys';
import { useTargetContext } from '../common/context/TargetContext';
import { useReportModeReady } from '../common/utils/appBootReadiness';
import { QualityStrip } from '../common/components/quality/QualityStrip';
import { EarlierNightPanels } from './EarlierNightPanels';
import { ExecutionHeader, LIVE_SESSION_VALUE } from './ExecutionHeader';
import { ControlTab } from './ControlTab';
import { GuidingTab } from './GuidingTab';
import { LiveVerdictPanel } from './LiveVerdictPanel';
import { NightFrameList } from './NightFrameList';
import { TonightCandidatesPanel } from './TonightCandidatesPanel';
import { useEquipmentCommandPolicy } from './hooks/useEquipmentCommandPolicy';
import './observationExecution.css';

/** The two tabs, in order. */
const TABS = ['guiding', 'control'] as const;
type TabName = (typeof TABS)[number];

/**
 * The main component of Observation Execution.
 * @return The display.
 */
export const ObservationExecution: React.FC = () => {
    const { selectedTarget } = useTargetContext();
    const [selectedNightId, setSelectedNightId] = useState<string>(LIVE_SESSION_VALUE);
    const [activeTab, setActiveTab] = useState<TabName>('guiding');
    const tabReferences = useRef<Record<TabName, HTMLButtonElement | null>>({ guiding: null, control: null });
    const idPrefix = useId();
    const policy = useEquipmentCommandPolicy();

    const nights = useQuery({
        queryKey: qualityQueryKeys.nights(),
        queryFn: () => callBackend('history:nights', {}),
    });
    useReportModeReady('Observation Execution', !nights.isLoading);

    const isLive = selectedNightId === LIVE_SESSION_VALUE;
    const controlLabel = policy.canCommand ? 'Control' : 'Control (Ekos in charge)';

    /** Moves between tabs with the arrow keys, Home and End (WAI-ARIA tab pattern). */
    const onTabKeyDown = (event: React.KeyboardEvent, current: TabName) => {
        const index = TABS.indexOf(current);
        let next: TabName | null = null;
        if (event.key === 'ArrowRight') next = TABS[(index + 1) % TABS.length];
        else if (event.key === 'ArrowLeft') next = TABS[(index + TABS.length - 1) % TABS.length];
        else if (event.key === 'Home') next = TABS[0];
        else if (event.key === 'End') next = TABS[TABS.length - 1];
        if (next) {
            event.preventDefault();
            setActiveTab(next);
            tabReferences.current[next]?.focus();
        }
    };

    const tabLabels: Record<TabName, string> = { guiding: 'Guiding', control: controlLabel };

    return (
        <div className="observation-execution">
            <ExecutionHeader
                nights={nights.data ?? []}
                nightsLoading={nights.isLoading}
                nightsFailed={nights.isError}
                selectedNightId={selectedNightId}
                onSelectNight={setSelectedNightId}
                currentTarget={selectedTarget}
            />
            <div className="observation-execution__body">
                <QualityStrip targetId={selectedTarget} nightId={isLive ? undefined : selectedNightId} />
                {isLive ? (
                    <>
                        <LiveVerdictPanel />
                        <TonightCandidatesPanel />
                    </>
                ) : (
                    <EarlierNightPanels nightId={selectedNightId} />
                )}
                <NightFrameList />
                <div className="execution-tabs">
                    <div role="tablist" aria-label="Equipment" className="execution-tabs__list">
                        {TABS.map((tab) => (
                            <button
                                key={tab}
                                ref={(element) => {
                                    tabReferences.current[tab] = element;
                                }}
                                type="button"
                                role="tab"
                                id={`${idPrefix}-tab-${tab}`}
                                aria-selected={activeTab === tab}
                                aria-controls={`${idPrefix}-panel-${tab}`}
                                tabIndex={activeTab === tab ? 0 : -1}
                                className="execution-tabs__tab"
                                onClick={() => setActiveTab(tab)}
                                onKeyDown={(event) => onTabKeyDown(event, tab)}
                            >
                                {tabLabels[tab]}
                            </button>
                        ))}
                    </div>
                    <div
                        role="tabpanel"
                        id={`${idPrefix}-panel-${activeTab}`}
                        aria-labelledby={`${idPrefix}-tab-${activeTab}`}
                    >
                        {activeTab === 'guiding' ? <GuidingTab /> : <ControlTab policy={policy} />}
                    </div>
                </div>
            </div>
        </div>
    );
};

export default ObservationExecution;
