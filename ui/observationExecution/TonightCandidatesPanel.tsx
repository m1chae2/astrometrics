/**
 * @fileoverview The re-ranked candidates at Now, drawn with the shared
 * candidate lists: for each use the library's list, the top three and
 * "Show all N", with "Switch to" and "Open in Planner" on each candidate.
 *
 * The panel does no ranking. It draws the lists in the order the library
 * returned them, with the ranking's own `at` and `generated_at` times,
 * because Execution always works at Now.
 */

import React from 'react';
import { useTonightRanking } from '../common/queries/qualityAdvisoryQueries';
import { useTargetContext } from '../common/context/TargetContext';
import { navigateToElement } from '../common/utils/displayCoordinator';
import { ExecutionPanel } from './ExecutionPanel';
import { TonightRankingBody } from '../common/components/quality/RankedCandidates';
import { OBSERVATION_PLANNER_MODE } from '../common/components/quality/useStatementActions';

/**
 * The tonight candidates panel.
 * @return The panel.
 */
export const TonightCandidatesPanel: React.FC = () => {
    const ranking = useTonightRanking(null);
    const { setSelectedTarget } = useTargetContext();

    const openInPlanner = (targetId: string) => {
        void navigateToElement({
            targetDisplay: OBSERVATION_PLANNER_MODE,
            targetElement: 'dataQuality',
            action: 'openInPlanner',
            payload: { targetId },
        });
    };

    return (
        <ExecutionPanel
            title="Candidates now"
            grow
            headerContent={
                <button type="button" className="btn btn--tiny" onClick={() => void ranking.refetch()}>
                    Refresh
                </button>
            }
        >
            <TonightRankingBody query={ranking} onSwitch={setSelectedTarget} onOpenInPlanner={openInPlanner} />
        </ExecutionPanel>
    );
};
