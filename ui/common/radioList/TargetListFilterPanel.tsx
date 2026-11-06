import React, { useState } from 'react';
import { SelectableList, SelectableItem } from '../components/SelectableList';
import type { TargetCameraSummary } from '../services/backendApi';
import {
    CAMERA_ALL,
    SORT_ALPHABETICAL,
    SORT_NEWEST,
    SortChoice,
} from '../hooks/targetListFiltering';
import { Tabs } from '../components/Tabs';
import '../styles/targetListFilterPanel.css';

export interface TargetListFilterPanelProps {
    /** Catalog choices, such as "All Targets" and "Messier Catalog". */
    catalogOptions: string[];
    selectedCatalog: string;
    onCatalogChange: (catalog: string) => void;
    /** Configured cameras with how many targets each imaged. */
    cameras: TargetCameraSummary[];
    selectedCamera: string;
    onCameraChange: (camera: string) => void;
    sort: SortChoice;
    onSortChange: (sort: SortChoice) => void;
}

type FilterTab = 'catalog' | 'camera';

/**
 * Two-tab filter for the target list: pick a catalog ("By catalog"), then
 * optionally a camera ("By camera"). Both choices apply together. A toggle
 * sorts the list by name or by the most recently imaged.
 *
 * @param {TargetListFilterPanelProps} props - Options, selections and change handlers.
 * @return {React.ReactElement} The filter panel body.
 */
export const TargetListFilterPanel: React.FC<TargetListFilterPanelProps> = ({
    catalogOptions,
    selectedCatalog,
    onCatalogChange,
    cameras,
    selectedCamera,
    onCameraChange,
    sort,
    onSortChange,
}) => {
    const [activeTab, setActiveTab] = useState<FilterTab>('catalog');

    const catalogItems: SelectableItem[] = catalogOptions.map((option) => ({
        id: option,
        value: option,
        label: option,
    }));
    const cameraItems: SelectableItem[] = [
        { id: CAMERA_ALL, value: CAMERA_ALL, label: CAMERA_ALL },
        ...cameras.map((camera) => ({
            id: camera.name,
            value: camera.name,
            label: camera.name,
            subtitle: camera.targetCount === 0
                ? 'No images yet'
                : `${camera.targetCount} ${camera.targetCount === 1 ? 'target' : 'targets'}`,
        })),
    ];

    return (
        <div className="target-list-filter-panel">
            <div className="astronomy-display__mode-toggle">
                <Tabs
                    idPrefix="target-list-filter"
                    ariaLabel="Filter targets"
                    selectedId={activeTab}
                    onChange={(tabId) => setActiveTab(tabId as typeof activeTab)}
                    tabs={[
                        { id: 'catalog', label: 'By catalog' },
                        { id: 'camera', label: 'By camera' },
                    ]}
                />
            </div>

            <SelectableList
                className="target-list-filter-panel__options"
                items={activeTab === 'catalog' ? catalogItems : cameraItems}
                selectedId={activeTab === 'catalog' ? selectedCatalog : selectedCamera}
                pendingId={activeTab === 'catalog' ? selectedCatalog : selectedCamera}
                onSelect={activeTab === 'catalog' ? onCatalogChange : onCameraChange}
            />

            <div className="target-list-filter-panel__summary">
                <div className="astronomy-display__mode-toggle">
                    <Tabs
                        idPrefix="target-list-sort"
                        ariaLabel="Sort order"
                        size="compact"
                        selectedId={sort}
                        onChange={(tabId) => onSortChange(tabId as SortChoice)}
                        tabs={[SORT_ALPHABETICAL, SORT_NEWEST].map((choice) => ({ id: choice, label: choice }))}
                    />
                </div>
            </div>
        </div>
    );
};
