/**
 * TabView Component
 *
 * A reusable tabbed interface component that allows switching between different content panels.
 * Manages active tab state and renders the shared Tabs strip with the active tab's content.
 */

import React, { useId, useState } from 'react';
import { Tabs, tabPanelElementId, tabElementId } from './Tabs';
import './tabView.css';

export interface Tab {
    id: string;
    label: string;
    content: React.ReactNode;
}

interface TabViewProps {
    tabs: Tab[];
    defaultTabId?: string;
    activeTabId?: string;
    onTabChange?: (tabId: string) => void;
    className?: string;
    noHeader?: boolean;
}

/**
 * TabView - A component for displaying tabbed content
 */
export const TabView: React.FC<TabViewProps> = ({
    tabs,
    defaultTabId,
    activeTabId: externalActiveTabId,
    onTabChange,
    className = '',
    noHeader = false
}) => {
    const [localActiveTabId, setLocalActiveTabId] = useState<string>(
        defaultTabId || (tabs.length > 0 ? tabs[0].id : '')
    );

    const activeTabId = externalActiveTabId || localActiveTabId;

    const handleTabClick = (tabId: string) => {
        if (onTabChange) {
            onTabChange(tabId);
        } else {
            setLocalActiveTabId(tabId);
        }
    };

    const activeTab = tabs.find(tab => tab.id === activeTabId);

    const idPrefix = useId();

    return (
        <div className={`tab-view ${className}`}>
            {!noHeader && (
                <Tabs
                    className="tab-view__header"
                    tabs={tabs.map(({ id, label }) => ({ id, label }))}
                    selectedId={activeTabId}
                    onChange={handleTabClick}
                    ariaLabel="Tabs"
                    idPrefix={idPrefix}
                />
            )}
            <div
                className="tab-view__content"
                role={activeTab ? 'tabpanel' : undefined}
                id={activeTab ? tabPanelElementId(idPrefix, activeTab.id) : undefined}
                aria-labelledby={activeTab && !noHeader ? tabElementId(idPrefix, activeTab.id) : undefined}
            >
                {activeTab?.content}
            </div>
        </div>
    );
};
