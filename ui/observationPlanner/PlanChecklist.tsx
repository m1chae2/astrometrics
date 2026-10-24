/**
 * @fileoverview The checklist of the night plan: the fixes the user means to
 * try, with a "Tick" button for each, and the items that came back.
 *
 * The checklist is drawn as stored. Whether a ticked item comes back is the
 * library's decision: it arrives as a `checklistReturn` on a statement of the
 * target report, with its reason, and is shown here as given.
 */

import React from 'react';
import { useTargetReport } from '../common/queries/qualityAdvisoryQueries';
import type { ChecklistItem } from '../common/types/backendTypes';
import { formatPlanningTime } from '../common/components/PlanningTimeIndicator';

export interface PlanChecklistProps {
    /** The checklist items, in the order stored. */
    items: ChecklistItem[];
    /** The current target, whose report may hold items that came back. */
    targetId: string;
    /** True while a plan write is running. */
    busy: boolean;
    /** Ticks an item. */
    onTick: (itemId: string) => void;
}

/**
 * Writes the capture setting of an item as short text.
 * @param item The item.
 * @return Text such as `ASI533, L, 60 s`, or an empty string when the item has no setting.
 */
function settingText(item: ChecklistItem): string {
    const setting = item.captureSetting;
    if (!setting) return '';
    const parts: string[] = [setting.camera];
    if (setting.filterName) parts.push(setting.filterName);
    if (setting.exposureSeconds !== null && setting.exposureSeconds !== undefined) parts.push(`${setting.exposureSeconds} s`);
    return parts.join(', ');
}

/**
 * The checklist.
 * @param props The items, the current target and the tick callback.
 * @return The checklist section.
 */
export const PlanChecklist: React.FC<PlanChecklistProps> = ({ items, targetId, busy, onTick }) => {
    const report = useTargetReport(targetId);
    const returned = (report.data?.statements ?? []).filter((statement) => statement.checklistReturn);

    return (
        <section className="plan-section" aria-label="Plan checklist">
            <h4>Checklist</h4>
            {items.length === 0 ? (
                <p className="quality-state">No checklist items. A recommendation sent to the plan that is not a capture setting appears here.</p>
            ) : (
                <ul className="plan-checklist">
                    {items.map((item, index) => {
                        const itemKey = item.itemId ?? `${item.rule}-${index}`;
                        const setting = settingText(item);
                        return (
                            <li key={itemKey} className="plan-checklist__item" data-ticked={item.tickedAt ? 'true' : 'false'}>
                                <span className="plan-checklist__rule">{item.rule}</span>
                                {setting && <span className="plan-checklist__setting">{setting}</span>}
                                {item.note && <span className="plan-checklist__note">{item.note}</span>}
                                {item.tickedAt && <span>Ticked {formatPlanningTime(item.tickedAt)}</span>}
                                <button
                                    type="button"
                                    className="btn btn--tiny"
                                    disabled={busy || !item.itemId}
                                    aria-label={`${item.tickedAt ? 'Tick again' : 'Tick'}: ${item.rule}`}
                                    onClick={() => item.itemId && onTick(item.itemId)}
                                >
                                    {item.tickedAt ? 'Tick again' : 'Tick'}
                                </button>
                            </li>
                        );
                    })}
                </ul>
            )}
            {returned.length > 0 && (
                <div className="plan-checklist__returned">
                    <h5>Came back for {targetId}</h5>
                    <ul>
                        {returned.map((statement) => (
                            <li key={`${statement.rule}-${statement.checklistReturn?.itemId}`}>
                                <strong>{statement.rule}</strong>: {statement.checklistReturn?.reason}
                                {statement.checklistReturn?.tickedAt
                                    ? ` (ticked ${formatPlanningTime(statement.checklistReturn.tickedAt)})`
                                    : ''}
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </section>
    );
};
