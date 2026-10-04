import React, { useState } from 'react';
import { SelectableList, SelectableItem } from '../components/SelectableList';
import type { TargetCameraSummary } from '../services/backendApi';
import {
    CAMERA_ALL,
    SORT_ALPHABETICAL,
    SORT_NEWEST,
    SortChoice,
} from '../hooks/targetListFiltering';
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
 * optionally a camera ("By camera"). Both choices apply together. A summary
 * line shows what is active, and a toggle sorts the list by name or by the
 * most recently imaged.
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

    const activeFilters = [selectedCatalog, selectedCamera].filter(
        (choice, index) => index === 0 || choice !== CAMERA_ALL
    );

    return (
        <div className="target-list-filter-panel">
            <div className="astronomy-display__mode-toggle" role="tablist">
                <button
                    type="button"
                    role="tab"
                    aria-selected={activeTab === 'catalog'}
                    className={`segmented-btn ${activeTab === 'catalog' ? 'active' : ''}`}
                    onClick={() => setActiveTab('catalog')}
                >
                    By catalog
                </button>
                <button
                    type="button"
                    role="tab"
                    aria-selected={activeTab === 'camera'}
                    className={`segmented-btn ${activeTab === 'camera' ? 'active' : ''}`}
                    onClick={() => setActiveTab('camera')}
                >
                    By camera
                </button>
            </div>

            <SelectableList
                className="target-list-filter-panel__options"
                items={activeTab === 'catalog' ? catalogItems : cameraItems}
                selectedId={activeTab === 'catalog' ? selectedCatalog : selectedCamera}
                pendingId={activeTab === 'catalog' ? selectedCatalog : selectedCamera}
                onSelect={activeTab === 'catalog' ? onCatalogChange : onCameraChange}
            />

            <div className="target-list-filter-panel__summary">
                <span className="target-list-filter-panel__chips" title="Active filters">
                    {activeFilters.join(' · ')}
                </span>
                <div className="astronomy-display__mode-toggle" role="group" aria-label="Sort order">
                    {([SORT_ALPHABETICAL, SORT_NEWEST] as SortChoice[]).map((choice) => (
                        <button
                            key={choice}
                            type="button"
                            aria-pressed={sort === choice}
                            className={`segmented-btn ${sort === choice ? 'active' : ''}`}
                            onClick={() => onSortChange(choice)}
                        >
                            {choice}
                        </button>
                    ))}
                </div>
            </div>
        </div>
    );
};
