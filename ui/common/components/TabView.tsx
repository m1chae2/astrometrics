/**
 * TabView Component
 *
 * A reusable tabbed interface component that allows switching between different content panels.
 * Manages active tab state and renders tab headers with associated content.
 */

import React, { useState } from 'react';
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

    return (
        <div className={`tab-view ${className}`}>
            {!noHeader && (
                <div className="tab-view__header">
                    {tabs.map(tab => (
                        <button
                            key={tab.id}
                            className={`tab-view__tab ${activeTabId === tab.id ? 'tab-view__tab--active' : ''}`}
                            onClick={() => handleTabClick(tab.id)}
                        >
                            {tab.label}
                        </button>
                    ))}
                </div>
            )}
            <div className="tab-view__content">
                {activeTab?.content}
            </div>
        </div>
    );
};
