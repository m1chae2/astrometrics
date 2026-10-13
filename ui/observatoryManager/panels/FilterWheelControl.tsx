/**
 * FilterWheelControl.tsx
 *
 * Component for selecting the active filter in the observation train.
 * Displays a grid of common filters with active state highlighting.
 */

import React from 'react';
import '../../common/styles/button.css';
// Styles handled by panels.css

export interface FilterWheelControlProps {
    activeFilter: string;
    onSelectFilter: (filter: string) => void;
}

const FILTERS = ['L', 'R', 'G', 'B', 'Ha', 'OIII', 'SII', 'Spect'];

/**
 * FilterWheelControl Component
 *
 * Renders a grid of filter selection buttons. Highlights the currently active filter
 * based on feedback from the device.
 * REQ: OBS-6: Filter Wheel Control
 */
export const FilterWheelControl: React.FC<FilterWheelControlProps> = ({ activeFilter, onSelectFilter }) => {
    const [pendingFilter, setPendingFilter] = React.useState<string | null>(null);

    React.useEffect(() => {
        // Clear pending when active filter matches target or changes
        if (activeFilter === pendingFilter) {
            setPendingFilter(null);
        }
    }, [activeFilter, pendingFilter]);

    const handleSelect = (filter: string) => {
        setPendingFilter(filter);
        onSelectFilter(filter);
    };

    /**
     * Normalizes the active filter name returned from the hardware
     * to match the predefined UI button labels.
     */
    const normalize = (name: string) => {
        const normalizedName = name.toLowerCase();
        if (normalizedName.startsWith('lum')) return 'L';
        if (normalizedName === 'red') return 'R';
        if (normalizedName === 'green') return 'G';
        if (normalizedName === 'blue') return 'B';
        if (normalizedName.startsWith('spec')) return 'Spect';
        return name;
    };

    return (
        <div id="filter-wheel-grid" className="filter-wheel">
            {FILTERS.map(filter => {
                // Compare normalized values
                const isActive = activeFilter === filter || normalize(activeFilter) === filter;
                const isPending = pendingFilter === filter;

                const safeFilterName = filter.replace(/\s+/g, '-').replace(/[()]/g, '');
                return (
                    <button
                        key={filter}
                        id={`btn-filter-${safeFilterName}`}
                        // REQ: OBS-6.1: The display SHALL show the currently selected filter.
                        // REQ: OBS-6.3: The display SHALL indicate when a filter change is in progress.
                        className={`btn filter-wheel__btn ${isActive ? 'filter-wheel__btn--active' : ''} ${isPending && !isActive ? 'filter-wheel__btn--pending' : ''}`}
                        // REQ: OBS-6.2: The display SHALL allow selection of a new filter from a predefined list.
                        onClick={() => handleSelect(filter)}
                        type="button"
                    >
                        {filter}
                    </button>
                );
            })}
        </div>
    );
};
