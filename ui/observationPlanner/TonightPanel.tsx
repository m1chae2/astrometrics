/**
 * @fileoverview "Tonight": the altitude chart of the current target and the
 * candidates of `advisory:tonight` at the shared planning time.
 *
 * The candidates are drawn in the order the library returned them, each use in
 * its own list (top three, "Show all N"), with "Show on sky" and "Switch to"
 * on each candidate and a "Refresh" button. The ranking is read at the
 * planning time, so changing the planning time reads it again.
 */

import React from 'react';
import { SectionPanel } from '../common/components/SectionPanel';
import { usePlanningTime } from '../common/context/PlanningTimeContext';
import { useTargetContext } from '../common/context/TargetContext';
import { useTonightRanking } from '../common/queries/qualityAdvisoryQueries';
import { TonightRankingBody } from '../common/components/quality/RankedCandidates';
import { navigateToElement } from '../common/utils/displayCoordinator';
import { NightVisibilityChart } from './NightVisibilityChart';

/**
 * Asks the Planetarium to center on a target.
 * @param targetId The target.
 */
export function showTargetOnSky(targetId: string): void {
    void navigateToElement({
        targetDisplay: 'Planetarium',
        targetElement: 'sky',
        action: 'showOnSky',
        payload: { targetId },
    });
}

/**
 * The Tonight panel.
 * @return The panel.
 */
export const TonightPanel: React.FC = () => {
    const { planningTime } = usePlanningTime();
    const { selectedTarget, setSelectedTarget } = useTargetContext();
    const ranking = useTonightRanking(planningTime);

    return (
        <SectionPanel title="Tonight" id="tonight-panel" className="tonight">
            {selectedTarget ? (
                <NightVisibilityChart targetId={selectedTarget} />
            ) : (
                <p className="quality-state">Choose a target</p>
            )}
            <section className="tonight__candidates" aria-label="Candidates for the planning time">
                <h4 className="tonight__candidates-title">
                    Candidates
                    <button type="button" className="btn btn--tiny" onClick={() => void ranking.refetch()}>
                        Refresh
                    </button>
                </h4>
                <TonightRankingBody query={ranking} onShowOnSky={showTargetOnSky} onSwitch={setSelectedTarget} />
            </section>
        </SectionPanel>
    );
};
