/**
 * @fileoverview Observation Execution: the display for watching the night
 * while Ekos runs it.
 *
 * Layout (UI_DISPLAY_PLAN 3.2), three columns like the other displays, and the
 * page never scrolls. Left: the Night panel (selector, "Working at Now", sync
 * lines, "Sync now"), the Quality panel, then the live verdict (or, for an
 * earlier night, the night review from storage). Center: the frame list.
 * Right: the re-ranked candidates (live session only) and a panel with the
 * "Guiding" and "Control" tabs. A panel with long content scrolls inside
 * itself. The display always works at Now: it does not use the shared
 * planning time. The control panels are disabled while Ekos is in charge.
 */

import React, { useId, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { callBackend } from '../common/services/backendApi';
import { qualityQueryKeys } from '../common/queries/qualityQueryKeys';
import { useTargetContext } from '../common/context/TargetContext';
import { useReportModeReady } from '../common/utils/appBootReadiness';
import { GenericDisplayLayout } from '../common/components/GenericDisplayLayout';
import '../common/components/tabView.css';
import { QualityStrip } from '../common/components/quality/QualityStrip';
import { ExecutionPanel } from './ExecutionPanel';
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

    const leftPanel = (
        <div className="panel-group">
            <ExecutionHeader
                nights={nights.data ?? []}
                nightsLoading={nights.isLoading}
                nightsFailed={nights.isError}
                selectedNightId={selectedNightId}
                onSelectNight={setSelectedNightId}
                currentTarget={selectedTarget}
            />
            <ExecutionPanel title="Quality" grow>
                <QualityStrip targetId={selectedTarget} nightId={isLive ? undefined : selectedNightId} />
            </ExecutionPanel>
            {isLive ? <LiveVerdictPanel /> : <EarlierNightPanels nightId={selectedNightId} />}
        </div>
    );

    const centerPanel = (
        <div className="panel-group">
            <NightFrameList />
        </div>
    );

    const rightPanel = (
        <div className="panel-group">
            {isLive && <TonightCandidatesPanel />}
            <ExecutionPanel label="Equipment" grow>
                <div className="execution-tabs">
                    <div role="tablist" aria-label="Equipment" className="tab-view__header execution-tabs__list">
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
                                className={`tab-view__tab ${activeTab === tab ? 'tab-view__tab--active' : ''}`}
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
                        className="execution-tabs__panel"
                    >
                        {activeTab === 'guiding' ? <GuidingTab /> : <ControlTab policy={policy} />}
                    </div>
                </div>
            </ExecutionPanel>
        </div>
    );

    return (
        <div className="observation-execution">
            <GenericDisplayLayout className="execution-layout" leftPanel={leftPanel} centerPanel={centerPanel} rightPanel={rightPanel} />
        </div>
    );
};

export default ObservationExecution;
