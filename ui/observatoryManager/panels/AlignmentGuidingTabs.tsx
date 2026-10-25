/**
 * @file AlignmentGuidingTabs.tsx
 * @description One panel with two tabs, "Alignment" (the alignment attempt
 * table) and "Guiding target" (the bullseye plot of guiding errors). The two
 * used to be separate panels; sharing one panel frees a slot in the middle
 * row. The tabs follow the WAI-ARIA tab pattern.
 */

import React, { useState } from 'react';
import { SectionPanel } from '../../common/components/SectionPanel';
import { TabPanel, Tabs, type TabDefinition } from '../../common/components/Tabs';
import type { AlignmentAttempt, GuidingSample } from '../../common/types/backendTypes';
import { AlignmentStatus } from './AlignmentStatus';
import { GuidingTargetPlot } from './GuidingStatusPanel';

/** The two tabs of the panel. */
const TABS: TabDefinition[] = [
    { id: 'alignment', label: 'Alignment' },
    { id: 'guiding-target', label: 'Guiding target' },
];

/** Prefix that keeps the tab element ids unique on the page. */
const ID_PREFIX = 'alignment-guiding';

export interface AlignmentGuidingTabsProps {
    /** The alignment attempts the table lists. */
    alignmentAttempts: AlignmentAttempt[];
    /** The guiding samples the bullseye plots. */
    guidingHistory: GuidingSample[];
}

/**
 * The Alignment Status and Guiding Target panel.
 * @param props See {@link AlignmentGuidingTabsProps}.
 * @return The tabbed panel.
 */
export const AlignmentGuidingTabs: React.FC<AlignmentGuidingTabsProps> = ({ alignmentAttempts, guidingHistory }) => {
    const [selectedTab, setSelectedTab] = useState('alignment');
    return (
        <SectionPanel title="Alignment and Guiding" className="observatory-guiding-tabbed">
            <Tabs tabs={TABS} selectedId={selectedTab} onChange={setSelectedTab} ariaLabel="Alignment and guiding views" idPrefix={ID_PREFIX} />
            <div className="observatory-guiding-tabbed__body">
                <TabPanel idPrefix={ID_PREFIX} tabId="alignment" selected={selectedTab === 'alignment'}>
                    <AlignmentStatus alignmentAttempts={alignmentAttempts} />
                </TabPanel>
                <TabPanel idPrefix={ID_PREFIX} tabId="guiding-target" selected={selectedTab === 'guiding-target'}>
                    <GuidingTargetPlot history={guidingHistory} />
                </TabPanel>
            </div>
        </SectionPanel>
    );
};
