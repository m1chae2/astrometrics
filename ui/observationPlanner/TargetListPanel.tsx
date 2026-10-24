/**
 * @fileoverview The target list of the Observation Planner: filters, search
 * and the list, with "Add Target" and "Reindex Target".
 *
 * It reuses the shared list logic (`useTargetListLogic`) and the shared radio
 * list. When no target is chosen yet, the first target of the list becomes the
 * current one.
 */

import React from 'react';
import { useTargetListLogic } from '../common/hooks/useTargetListLogic';
import { RadioListManager } from '../common/radioList/RadioListManager';
import { TargetListFilterPanel } from '../common/radioList/TargetListFilterPanel';
import { ListActions } from '../common/components/ListActions';
import { refreshTarget } from '../common/services/targetService';
import '../common/styles/button.css';

interface Props {
    className?: string;
    onAddTarget?: () => void;
    selectedTarget: string;
    setSelectedTarget: (id: string) => void;
    pendingTarget: string;
    setPendingTarget: (id: string) => void;
    reloadKey: number;
    /** Called after a target was reindexed, so the lists read again. */
    onReindexed?: () => void;
}

/**
 * Panel displaying the list of available targets.
 * @param props The selection state and callbacks.
 * @return The panel.
 */
export const TargetListPanel: React.FC<Props> = ({
    className = '',
    onAddTarget,
    selectedTarget,
    setSelectedTarget,
    pendingTarget,
    setPendingTarget,
    reloadKey,
    onReindexed,
}) => {
    const { items, filterPanel, filterText, setFilterText, highlightedIds, isLoading } = useTargetListLogic(
        reloadKey,
        pendingTarget,
        selectedTarget,
        setPendingTarget,
    );

    React.useEffect(() => {
        if (!selectedTarget && items.length > 0) {
            const firstId = items[0].id;
            setPendingTarget(firstId);
            setSelectedTarget(firstId);
        }
    }, [items, selectedTarget, setPendingTarget, setSelectedTarget]);

    return (
        <div className={`target-list-panel ${className}`}>
            <RadioListManager
                title="Targets"
                items={items}
                selectedId={selectedTarget}
                pendingId={pendingTarget}
                onSelect={(id) => {
                    setPendingTarget(id);
                    setSelectedTarget(id);
                }}
                topPanel={<TargetListFilterPanel {...filterPanel} />}
                topPanelTitle="Filter Targets"
                filterText={filterText}
                onFilterTextChange={setFilterText}
                highlightedIds={highlightedIds}
                isLoading={isLoading}
                loadingMessage="Loading targets…"
                emptyMessage="No targets in the catalog."
                className="target-list-manager"
                actions={
                    <ListActions>
                        <button className="btn" onClick={onAddTarget}>
                            Add Target
                        </button>
                        <button
                            className="btn btn--secondary"
                            disabled={!selectedTarget}
                            onClick={async () => {
                                await refreshTarget(selectedTarget);
                                onReindexed?.();
                            }}
                        >
                            Reindex Target
                        </button>
                    </ListActions>
                }
            />
        </div>
    );
};
