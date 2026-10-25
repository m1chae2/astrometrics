/**
 * @file PackageLibraryPanel.tsx
 * @description The observation package library, in the left column of the Observation Manager.
 * Tabs Suggested, Planned and Complete (with counts); each card shows only the target name on one
 * line and, for a suggestion, a small confidence chip. Picking a card loads the editor. "New package" opens a
 * target search; "Add Target" adds a target to the library; "Refresh suggestions" asks the library to
 * refresh the suggestions. A suggestion card has Dismiss (with an Undo notice); a complete card has
 * Reopen. A polite status line reports "Suggestions updated: ..." after a refresh. When the focused
 * card goes away, focus moves to the card that takes its place. A presentational component: the lists,
 * the loading and error state and every action come in as props.
 */
import React, { useEffect, useRef, useState } from 'react';
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
    /** Called when the user chooses Dismiss on a suggestion card. */
    onDismissPackage?: (observationPackage: ObservationPackage) => void;
    /** Called when the user chooses Reopen on a complete card. */
    onReopenPackage?: (observationPackage: ObservationPackage) => void;
    /** Called when the user chooses Refresh suggestions. */
    onRefreshSuggestions?: () => void;
    /** Plain text shown after Refresh suggestions started (not announced), or null. */
    refreshStartedText?: string | null;
    /** The line after a refresh, such as "Suggestions updated: 1 added" (announced politely), or null. */
    refreshNotice?: string | null;
    /** The notice after a dismissal, with the Undo and Close actions, or null. */
    undoNotice?: { text: string; onUndo: () => void; onClose: () => void } | null;
}

/**
 * Shows the package library.
 * @param props See {@link PackageLibraryPanelProps}.
 * @return The panel element.
 */
export const PackageLibraryPanel: React.FC<PackageLibraryPanelProps> = ({
    lists, isLoading, error, onRetry, selectedPackageId, onSelectPackage, onNewPackage, onAddTarget,
    actionError = null, onDismissActionError, onDismissPackage, onReopenPackage, onRefreshSuggestions,
    refreshStartedText = null, refreshNotice = null, undoNotice = null,
}) => {
    const [selectedTab, setSelectedTab] = useState<TabId>('planned');
    const [isSearchOpen, setIsSearchOpen] = useState(false);
    const listRegionRef = useRef<HTMLDivElement>(null);
    /** The New package button, so focus can return to it when the search closes. */
    const newPackageButtonRef = useRef<HTMLButtonElement>(null);
    /** The card that last had focus: its id and its place in its list. Cleared when focus moves on. */
    const focusedCardRef = useRef<{ id: string; index: number } | null>(null);
    const visibleIds = lists[selectedTab].map((candidate) => candidate.id);
    const visibleIdsKey = visibleIds.join('|');

    // When the focused card leaves the list (dismissed, reopened, removed by a refresh) and focus
    // fell to the page, move focus to the card now at its place, else the last card, else the tab.
    useEffect(() => {
        const focused = focusedCardRef.current;
        if (!focused || visibleIds.includes(focused.id)) return;
        focusedCardRef.current = null;
        const activeElement = document.activeElement;
        // Focus is somewhere real: leave it there.
        if (activeElement && activeElement !== document.body) return;
        const cards = listRegionRef.current?.querySelectorAll<HTMLElement>(`[data-package-card="${selectedTab}"] .card`);
        const next = cards && cards.length > 0 ? cards[Math.min(focused.index, cards.length - 1)] : null;
        if (next) {
            next.focus();
        } else {
            document.getElementById(`${ID_PREFIX}-tab-${selectedTab}`)?.focus();
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [visibleIdsKey, selectedTab]);

    const tabs = TAB_DEFINITIONS.map((definition) => ({
        id: definition.id,
        label: definition.label,
        count: lists[definition.id].length,
    }));

    /** Starts a package for the picked target and closes the search. */
    const handlePick = (targetId: string) => {
        closeSearch();
        onNewPackage(targetId);
    };

    /** Closes the search and puts focus back on the New package button. */
    function closeSearch() {
        setIsSearchOpen(false);
        window.setTimeout(() => newPackageButtonRef.current?.focus(), 0);
    }

    return (
        <SectionPanel
            title="Observation Packages"
            className="package-library"
            headerContent={
                <span className="package-library__actions">
                    <button type="button" ref={newPackageButtonRef} className="btn btn--tiny btn--primary" onClick={() => setIsSearchOpen(true)}>
                        New package
                    </button>
                    <button type="button" className="btn btn--tiny" onClick={onAddTarget} title="Add a new target to the library">
                        Add Target
                    </button>
                    <button type="button" className="btn btn--tiny" onClick={onRefreshSuggestions}>
                        Refresh suggestions
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
                {undoNotice && (
                    <div className="package-library__undo">
                        <span>{undoNotice.text}</span>
                        <button type="button" className="btn btn--tiny" onClick={undoNotice.onUndo}>Undo</button>
                        <button type="button" className="btn btn--tiny" onClick={undoNotice.onClose} aria-label="Close notice">Close</button>
                    </div>
                )}
                {refreshStartedText && <div className="package-library__status">{refreshStartedText}</div>}
                <div className="package-library__status" role="status" aria-live="polite">{refreshNotice}</div>
                {isSearchOpen && <NewPackageSearch onPick={handlePick} onClose={closeSearch} />}
                <Tabs
                    tabs={tabs}
                    selectedId={selectedTab}
                    onChange={(tabId) => setSelectedTab(tabId as TabId)}
                    ariaLabel="Package status"
                    idPrefix={ID_PREFIX}
                />
                {TAB_DEFINITIONS.map((definition) => (
                    <TabPanel key={definition.id} idPrefix={ID_PREFIX} tabId={definition.id} selected={selectedTab === definition.id}>
                        <div className="package-library__list" ref={selectedTab === definition.id ? listRegionRef : undefined}>
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
                            {!isLoading && !error && lists[definition.id].map((observationPackage, index) => (
                                <PackageRow
                                    key={observationPackage.id}
                                    observationPackage={observationPackage}
                                    statusWord={definition.id}
                                    selected={observationPackage.id === selectedPackageId}
                                    onSelect={() => onSelectPackage(observationPackage)}
                                    onDismiss={onDismissPackage ? () => onDismissPackage(observationPackage) : undefined}
                                    onReopen={onReopenPackage ? () => onReopenPackage(observationPackage) : undefined}
                                    onFocusCard={() => { focusedCardRef.current = { id: observationPackage.id, index }; }}
                                    onBlurCard={() => { focusedCardRef.current = null; }}
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
    /** Shows a Dismiss button (suggestions only) when given. */
    onDismiss?: () => void;
    /** Shows a Reopen button (complete packages only) when given. */
    onReopen?: () => void;
    /** Called when focus enters the card or its button. */
    onFocusCard: () => void;
    /** Called when focus moves to another element (not when the card is removed). */
    onBlurCard: () => void;
}

/**
 * One package card: the target name on a single line, and for a suggestion a confidence chip and a
 * Dismiss button; for a complete package a Reopen button. The buttons sit beside the card, not inside it.
 * @param props See {@link PackageRowProps}.
 * @return The card element.
 */
const PackageRow: React.FC<PackageRowProps> = ({
    observationPackage, statusWord, selected, onSelect, onDismiss, onReopen, onFocusCard, onBlurCard,
}) => {
    const suggestion = observationPackage.suggestion;
    const confidenceWord = suggestion ? confidenceLabel(suggestion.confidence) : null;
    const accessibleName = confidenceWord
        ? `${observationPackage.target_id}, ${statusWord} package, ${confidenceWord} confidence`
        : `${observationPackage.target_id}, ${statusWord} package`;
    /** Forgets the focused card only when focus goes to another element. */
    const handleBlur = (event: React.FocusEvent) => {
        if (event.relatedTarget) onBlurCard();
    };
    return (
        <div className="package-row-wrap" data-package-card={statusWord} onFocus={onFocusCard} onBlur={handleBlur}>
            <Card
                title={observationPackage.target_id}
                headerContent={confidenceWord ? <StatusChip status="info" label={confidenceWord} /> : undefined}
                selected={selected}
                onClick={onSelect}
                ariaLabel={accessibleName}
                className="package-row"
            />
            {statusWord === 'suggested' && onDismiss && (
                <button
                    type="button"
                    className="btn btn--tiny"
                    aria-label={`Dismiss ${observationPackage.target_id}`}
                    onClick={onDismiss}
                >
                    Dismiss
                </button>
            )}
            {statusWord === 'complete' && onReopen && (
                <button
                    type="button"
                    className="btn btn--tiny"
                    aria-label={`Reopen ${observationPackage.target_id}`}
                    onClick={onReopen}
                >
                    Reopen
                </button>
            )}
        </div>
    );
};
