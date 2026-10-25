/**
 * @fileoverview Tabs: a tab strip that follows the WAI-ARIA tab pattern (arrow keys, Home, End,
 * `aria-selected`), plus the matching TabPanel.
 */

import React, { useRef } from 'react';
import './sharedComponents.css';

export interface TabDefinition {
    /** Unique id of the tab. */
    id: string;
    /** Text of the tab. */
    label: string;
    /** Optional count shown after the label and read out with it, for example "Frames (3)". */
    count?: number;
}

export interface TabsProps {
    tabs: TabDefinition[];
    selectedId: string;
    onChange: (tabId: string) => void;
    /** Accessible name of the tab list. */
    ariaLabel: string;
    /** Prefix that makes the element ids unique when two tab strips share a page. */
    idPrefix: string;
}

/**
 * Builds the id of a tab button, so a TabPanel can point back to it.
 * @param idPrefix The strip's id prefix.
 * @param tabId The tab's id.
 * @return The element id.
 */
export function tabElementId(idPrefix: string, tabId: string): string {
    return `${idPrefix}-tab-${tabId}`;
}

/**
 * Builds the id of a tab's panel.
 * @param idPrefix The strip's id prefix.
 * @param tabId The tab's id.
 * @return The element id.
 */
export function tabPanelElementId(idPrefix: string, tabId: string): string {
    return `${idPrefix}-panel-${tabId}`;
}

/**
 * Shows a tab strip. Only the selected tab is in the Tab key order; the arrow keys,
 * Home and End move between tabs and select the one they land on.
 *
 * @param props See {@link TabsProps}.
 * @return The tab list element.
 */
export const Tabs: React.FC<TabsProps> = ({ tabs, selectedId, onChange, ariaLabel, idPrefix }) => {
    const buttonRefs = useRef<Record<string, HTMLButtonElement | null>>({});

    /**
     * Moves selection and focus with the arrow keys, Home and End.
     * @param event The key event.
     * @param index Index of the tab that has focus.
     */
    const handleKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
        let nextIndex: number;
        if (event.key === 'ArrowRight') nextIndex = (index + 1) % tabs.length;
        else if (event.key === 'ArrowLeft') nextIndex = (index - 1 + tabs.length) % tabs.length;
        else if (event.key === 'Home') nextIndex = 0;
        else if (event.key === 'End') nextIndex = tabs.length - 1;
        else return;
        event.preventDefault();
        const nextTab = tabs[nextIndex];
        onChange(nextTab.id);
        buttonRefs.current[nextTab.id]?.focus();
    };

    return (
        <div className="tabs" role="tablist" aria-label={ariaLabel}>
            {tabs.map((tab, index) => {
                const isSelected = tab.id === selectedId;
                return (
                    <button
                        key={tab.id}
                        ref={(element) => { buttonRefs.current[tab.id] = element; }}
                        type="button"
                        role="tab"
                        id={tabElementId(idPrefix, tab.id)}
                        className="tabs__tab"
                        aria-selected={isSelected}
                        aria-controls={tabPanelElementId(idPrefix, tab.id)}
                        tabIndex={isSelected ? 0 : -1}
                        onClick={() => onChange(tab.id)}
                        onKeyDown={(event) => handleKeyDown(event, index)}
                    >
                        {tab.label}{tab.count !== undefined && ' '}
                        {tab.count !== undefined && <span className="tabs__count">({tab.count})</span>}
                    </button>
                );
            })}
        </div>
    );
};

export interface TabPanelProps {
    idPrefix: string;
    tabId: string;
    /** True when this panel's tab is the selected one. A hidden panel renders nothing. */
    selected: boolean;
    children: React.ReactNode;
}

/**
 * The content area of one tab. Renders nothing unless its tab is selected.
 * @param props See {@link TabPanelProps}.
 * @return The panel element, or null.
 */
export const TabPanel: React.FC<TabPanelProps> = ({ idPrefix, tabId, selected, children }) => {
    if (!selected) return null;
    return (
        <div
            role="tabpanel"
            id={tabPanelElementId(idPrefix, tabId)}
            aria-labelledby={tabElementId(idPrefix, tabId)}
        >
            {children}
        </div>
    );
};
