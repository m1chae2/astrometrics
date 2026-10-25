/**
 * @file NewPackageSearch.tsx
 * @description The search box of the "New package" button: type part of a name and pick one of the
 * library's targets. Reuses the shared target list hook, so it searches the same list as the
 * other displays.
 */
import React from 'react';
import { useTargetListLogic } from '../common/hooks/useTargetListLogic';
import { EmptyState } from '../common/components/EmptyState';
import './packageLibrary.css';

/** The most matching targets shown at once; the rest appear as the user types more. */
const MAX_SEARCH_RESULTS = 50;

export interface NewPackageSearchProps {
    /** Called with the target id the user picked. */
    onPick: (targetId: string) => void;
    /** Called when the user closes the search without picking. */
    onClose: () => void;
}

/**
 * Shows a search box and the matching library targets.
 * @param props See {@link NewPackageSearchProps}.
 * @return The search element.
 */
export const NewPackageSearch: React.FC<NewPackageSearchProps> = ({ onPick, onClose }) => {
    const noop = React.useCallback(() => undefined, []);
    const { items, filterText, setFilterText, isLoading, isError, refetchTargets } = useTargetListLogic(
        undefined, '', '', noop, undefined, undefined, false, true
    );
    const shown = items.slice(0, MAX_SEARCH_RESULTS);

    return (
        <div className="package-search" role="search" aria-busy={isLoading}>
            <div className="package-search__bar">
                <input
                    type="search"
                    className="entry entry--fill"
                    aria-label="Search library targets"
                    placeholder="Search library targets"
                    value={filterText}
                    autoFocus
                    onChange={(event) => setFilterText(event.target.value)}
                    onKeyDown={(event) => { if (event.key === 'Escape') onClose(); }}
                />
                <button type="button" className="btn btn--tiny" onClick={onClose}>Cancel</button>
            </div>
            {isLoading && <EmptyState variant="loading" message="Loading targets…" />}
            {!isLoading && isError && (
                <div className="package-library__error" role="alert">
                    <span>Could not load the target list.</span>
                    <button type="button" className="btn btn--tiny" onClick={refetchTargets}>Try again</button>
                </div>
            )}
            {!isLoading && !isError && shown.length === 0 && (
                <EmptyState message="No library target matches. Use Add Target to add one." />
            )}
            <ul className="package-search__results">
                {shown.map((item) => (
                    <li key={item.id}>
                        <button type="button" className="package-search__result" onClick={() => onPick(item.id)}>
                            {item.label}
                        </button>
                    </li>
                ))}
            </ul>
        </div>
    );
};
