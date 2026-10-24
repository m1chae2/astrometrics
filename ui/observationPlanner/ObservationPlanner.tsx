/**
 * @fileoverview The Observation Planner: where the user plans the night.
 *
 * Layout (UI_DISPLAY_PLAN 3.1): a header with the current target and the
 * planning time; on the left the targets (filters, search, list); in the
 * center the Data Quality panel of the current target; on the right "Tonight"
 * (the altitude chart and the candidates), the stored night plan and the Ekos
 * summary. The display draws what the library returns and ranks nothing.
 *
 * The display also answers `openInPlanner`: it makes the target current and
 * moves focus to the Data Quality panel.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { GenericDisplayLayout } from '../common/components/GenericDisplayLayout';
import { AddTargetModal } from '../common/components/AddTargetModal';
import { PlanningTimeIndicator } from '../common/components/PlanningTimeIndicator';
import { DataQualityPanel } from '../common/components/quality/DataQualityPanel';
import { useTargetContext } from '../common/context/TargetContext';
import { createTarget } from '../common/services/targetService';
import { useReportModeReady } from '../common/utils/appBootReadiness';
import { EkosSummaryPanel } from './EkosSummaryPanel';
import { NightPlanPanel } from './NightPlanPanel';
import { TargetListPanel } from './TargetListPanel';
import { TonightPanel } from './TonightPanel';
import { useNightPlan } from './hooks/useNightPlan';
import './observationPlanner.css';

/** The id of the Data Quality panel's element. */
export const DATA_QUALITY_PANEL_ID = 'data-quality-panel';

/**
 * Moves keyboard focus to the Data Quality panel.
 * @return `true` when the panel was found.
 */
function focusDataQualityPanel(): boolean {
    const panel = document.getElementById(DATA_QUALITY_PANEL_ID);
    if (!panel) return false;
    if (!panel.hasAttribute('tabindex')) panel.setAttribute('tabindex', '-1');
    panel.focus();
    panel.scrollIntoView?.({ block: 'nearest' });
    return true;
}

/**
 * The main component of the Observation Planner.
 * @return The display.
 */
export const ObservationPlanner: React.FC = () => {
    const { selectedTarget, setSelectedTarget, pendingTarget, setPendingTarget, reloadKey, forceReload } =
        useTargetContext();
    const plan = useNightPlan();
    useReportModeReady('Observation Planner', !plan.plansLoading);

    const [isAddModalOpen, setIsAddModalOpen] = useState(false);
    const [focusRequest, setFocusRequest] = useState(0);

    /** Persists a newly created target and reads the list again. */
    const handleAddTargetSubmit = async (name: string, ra: string, dec: string) => {
        await createTarget(name, ra, dec);
        forceReload();
    };

    /** Makes the target current and asks for focus on the Data Quality panel. */
    const handleOpenInPlanner = useCallback(
        (event: Event) => {
            const detail = (event as CustomEvent).detail;
            const targetId = typeof detail === 'string' ? detail : detail?.targetId;
            if (targetId) {
                setSelectedTarget(targetId);
                setPendingTarget(targetId);
            }
            setFocusRequest((count) => count + 1);
        },
        [setSelectedTarget, setPendingTarget],
    );

    useEffect(() => {
        window.addEventListener('astrometrics:openInPlanner', handleOpenInPlanner);
        return () => window.removeEventListener('astrometrics:openInPlanner', handleOpenInPlanner);
    }, [handleOpenInPlanner]);

    // Focus after the display is visible and the panel has drawn.
    useEffect(() => {
        if (focusRequest === 0) return undefined;
        const frame = window.requestAnimationFrame(() => {
            focusDataQualityPanel();
        });
        return () => window.cancelAnimationFrame(frame);
    }, [focusRequest]);

    const leftPanel = (
        <TargetListPanel
            selectedTarget={selectedTarget}
            setSelectedTarget={setSelectedTarget}
            pendingTarget={pendingTarget}
            setPendingTarget={setPendingTarget}
            reloadKey={reloadKey}
            onAddTarget={() => setIsAddModalOpen(true)}
            onReindexed={forceReload}
        />
    );

    const centerPanel = <DataQualityPanel targetId={selectedTarget} />;

    const rightPanel = (
        <div className="observation-planner__right">
            <TonightPanel />
            <NightPlanPanel plan={plan} />
            <EkosSummaryPanel />
        </div>
    );

    return (
        <div className="observation-planner">
            <AddTargetModal
                isOpen={isAddModalOpen}
                onClose={() => setIsAddModalOpen(false)}
                onAdd={handleAddTargetSubmit}
            />
            <header className="planner-header" aria-label="Observation Planner header">
                <span className="planner-header__field">
                    <span>Current target</span>
                    <strong>{selectedTarget || 'No target chosen'}</strong>
                </span>
                <PlanningTimeIndicator />
            </header>
            <div className="observation-planner__layout">
                <GenericDisplayLayout leftPanel={leftPanel} centerPanel={centerPanel} rightPanel={rightPanel} />
            </div>
        </div>
    );
};

export default ObservationPlanner;
