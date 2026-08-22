import React from 'react';
import { useTargetListLogic } from '../common/hooks/useTargetListLogic';
import { RadioListManager } from '../common/radioList/RadioListManager';
import { ListActions } from '../common/components/ListActions'; // Import ListActions
import '../common/styles/button.css'; // Ensure button styles are loaded

interface Props {
    className?: string;
    onAddTarget?: () => void;
    selectedTarget: string;
    setSelectedTarget: (id: string) => void;
    pendingTarget: string;
    setPendingTarget: (id: string) => void;
    reloadKey: number;
}

/**
 * Panel displaying the list of available targets.
 * Reuses the common RadioListManager logic.
 * REQ: SR-4.1: The system SHALL maintain a catalog of celestial targets.
 */
export const TargetListPanel: React.FC<Props> = ({
    className = '',
    onAddTarget,
    selectedTarget,
    setSelectedTarget,
    pendingTarget,
    setPendingTarget,
    reloadKey
}) => {
    // Hook only used for list logic now, passing in the state from props
    const {
        items,
        filterOptions,
        selectedFilterOption,
        setFilterOption,
        filterText,
        setFilterText,
        highlightedIds,
    } = useTargetListLogic(reloadKey, pendingTarget, selectedTarget, setPendingTarget);

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
                title="Target List"
                items={items}
                selectedId={selectedTarget}
                pendingId={pendingTarget}
                onSelect={(id) => {
                    setPendingTarget(id);
                    setSelectedTarget(id);
                }}
                filterOptions={filterOptions}
                selectedFilterOption={selectedFilterOption}
                onFilterOptionChange={setFilterOption}
                filterText={filterText}
                onFilterTextChange={setFilterText}
                highlightedIds={highlightedIds}
                className="target-list-manager"
                actions={
                    <ListActions>
                        <button
                            className="btn"
                            onClick={onAddTarget}
                            title="Add a new target"
                        >
                            Add Target
                        </button>
                    </ListActions>
                }
            />
        </div>
    );
};
