/**
 * @file PackageLibraryPanel.tsx
 * @description The observation package library, in the left column of the Observation Manager.
 * Tabs Suggested, Planned and Complete (with counts); each card shows only the target name on one
 * line and, for a suggestion, a small confidence chip. Picking a card loads the editor. "New package" opens a
 * target search; "Add Target" adds a target to the library. A presentational component: the lists,
 * the loading and error state and every action come in as props.
 */
import React, { useState } from 'react';
import { SectionPanel } from '../common/components/SectionPanel';
import { Card } from '../common/components/Card';
import { Tabs, TabPanel } from '../common/components/Tabs';
import { StatusChip } from '../common/components/StatusChip';
import { EmptyState } from '../common/components/EmptyState';
import { ObservationPackage } from '../common/types/backendTypes';
import { PackageLibraryLists } from './hooks/usePackageLibrary';
import { NewPackageSearch } from './NewPackageSearch';
import { confidenceLabel } from './packageModel';
import './packageLibrary.css';

/** The three tabs, in display order, with the words shown when a tab has no packages. */
const TAB_DEFINITIONS = [
    { id: 'suggested', label: 'Suggested', emptyText: 'No suggestions yet. Suggestions appear when the library finds a target that needs more frames.' },
    { id: 'planned', label: 'Planned', emptyText: 'No planned packages. Choose New package to start one.' },
    { id: 'complete', label: 'Complete', emptyText: 'No complete packages yet.' },
] as const;

type TabId = typeof TAB_DEFINITIONS[number]['id'];

const ID_PREFIX = 'package-library';

export interface PackageLibraryPanelProps {
    lists: PackageLibraryLists;
    isLoading: boolean;
    /** Error text, or null when the load worked. */
    error: string | null;
    onRetry: () => void;
    /** Id of the package picked for editing, if any. */
    selectedPackageId: string | null;
    onSelectPackage: (observationPackage: ObservationPackage) => void;
    /** Called with a library target id when the user picks a target in the New package search. */
    onNewPackage: (targetId: string) => void;
    /** Called when the user chooses Add Target. */
    onAddTarget: () => void;
    /** Text of a refused create (for example an unknown target), or null. Stays until dismissed. */
    actionError?: string | null;
    onDismissActionError?: () => void;
}

/**
 * Shows the package library.
 * @param props See {@link PackageLibraryPanelProps}.
 * @return The panel element.
 */
export const PackageLibraryPanel: React.FC<PackageLibraryPanelProps> = ({
    lists, isLoading, error, onRetry, selectedPackageId, onSelectPackage, onNewPackage, onAddTarget,
    actionError = null, onDismissActionError,
}) => {
    const [selectedTab, setSelectedTab] = useState<TabId>('planned');
    const [isSearchOpen, setIsSearchOpen] = useState(false);

    const tabs = TAB_DEFINITIONS.map((definition) => ({
        id: definition.id,
        label: definition.label,
        count: lists[definition.id].length,
    }));

    /** Starts a package for the picked target and closes the search. */
    const handlePick = (targetId: string) => {
        setIsSearchOpen(false);
        onNewPackage(targetId);
    };

    return (
        <SectionPanel
            title="Observation Packages"
            className="package-library"
            headerContent={
                <span className="package-library__actions">
                    <button type="button" className="btn btn--tiny btn--primary" onClick={() => setIsSearchOpen(true)}>
                        New package
                    </button>
                    <button type="button" className="btn btn--tiny" onClick={onAddTarget} title="Add a new target to the library">
                        Add Target
                    </button>
                </span>
            }
        >
            <div className="package-library__body" aria-busy={isLoading}>
                {actionError && (
                    <div className="package-library__error" role="alert">
                        <span>{actionError}</span>
                        <button type="button" className="btn btn--tiny" onClick={onDismissActionError}>Dismiss</button>
                    </div>
                )}
                {isSearchOpen && <NewPackageSearch onPick={handlePick} onClose={() => setIsSearchOpen(false)} />}
                <Tabs
                    tabs={tabs}
                    selectedId={selectedTab}
                    onChange={(tabId) => setSelectedTab(tabId as TabId)}
                    ariaLabel="Package status"
                    idPrefix={ID_PREFIX}
                />
                {TAB_DEFINITIONS.map((definition) => (
                    <TabPanel key={definition.id} idPrefix={ID_PREFIX} tabId={definition.id} selected={selectedTab === definition.id}>
                        <div className="package-library__list">
                            {isLoading && <EmptyState variant="loading" message="Loading…" />}
                            {!isLoading && error && (
                                <div className="package-library__error" role="alert">
                                    <span>Could not load the package library.</span>
                                    <button type="button" className="btn btn--tiny" onClick={onRetry}>Try again</button>
                                </div>
                            )}
                            {!isLoading && !error && lists[definition.id].length === 0 && (
                                <EmptyState message={definition.emptyText} />
                            )}
                            {!isLoading && !error && lists[definition.id].map((observationPackage) => (
                                <PackageRow
                                    key={observationPackage.id}
                                    observationPackage={observationPackage}
                                    statusWord={definition.id}
                                    selected={observationPackage.id === selectedPackageId}
                                    onSelect={() => onSelectPackage(observationPackage)}
                                />
                            ))}
                        </div>
                    </TabPanel>
                ))}
            </div>
        </SectionPanel>
    );
};

interface PackageRowProps {
    observationPackage: ObservationPackage;
    /** The tab the package is listed under, used in the accessible name. */
    statusWord: TabId;
    selected: boolean;
    onSelect: () => void;
}

/**
 * One package card: the target name on a single line, and for a suggestion a confidence chip.
 * @param props See {@link PackageRowProps}.
 * @return The card element.
 */
const PackageRow: React.FC<PackageRowProps> = ({ observationPackage, statusWord, selected, onSelect }) => {
    const suggestion = observationPackage.suggestion;
    const confidenceWord = suggestion ? confidenceLabel(suggestion.confidence) : null;
    const accessibleName = confidenceWord
        ? `${observationPackage.target_id}, ${statusWord} package, ${confidenceWord} confidence`
        : `${observationPackage.target_id}, ${statusWord} package`;
    return (
        <Card
            title={observationPackage.target_id}
            headerContent={confidenceWord ? <StatusChip status="info" label={confidenceWord} /> : undefined}
            selected={selected}
            onClick={onSelect}
            ariaLabel={accessibleName}
            className="package-row"
        />
    );
};
