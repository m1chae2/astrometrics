/**
 * @fileoverview The night plan: the stored plan of the library, chosen from the
 * list, with its queue entries (dither folded in), its checklist and its
 * framing.
 *
 * The user picks a plan from the list returned by `plan:get`, or presses
 * "Create tonight's plan". Edits go through `plan:edit` with the revision the
 * panel last read; a conflict reloads the plan and says so, and a refusal shows
 * the library's reason until the user dismisses it. Other windows' changes
 * arrive through `plan:changed` (handled in `App.tsx`), which marks the plan
 * reads stale.
 */

import React, { useRef, useState } from 'react';
import { SectionPanel } from '../common/components/SectionPanel';
import { ConfirmDialog } from '../common/components/ConfirmDialog';
import { QualityPanelState } from '../common/components/quality/QualityPanelState';
import { useTargetContext } from '../common/context/TargetContext';
import { navigateToElement } from '../common/utils/displayCoordinator';
import type { ExposureRequest } from '../common/types/backendTypes';
import { PlanChecklist } from './PlanChecklist';
import { PlanFraming } from './PlanFraming';
import { PlanQueueEntry } from './PlanQueueEntry';
import type { NightPlanController } from './hooks/useNightPlan';

/**
 * The night plan panel.
 * @param props The plan controller from `useNightPlan` (the display holds it so it can tell when the plan list has loaded).
 * @return The panel.
 */
export const NightPlanPanel: React.FC<{ plan: NightPlanController }> = ({ plan }) => {
    const { selectedTarget } = useTargetContext();
    const [removal, setRemoval] = useState<{ entryId: string; trigger: HTMLElement | null } | null>(null);
    const selectReference = useRef<HTMLSelectElement>(null);
    const queue = plan.session?.queue ?? [];

    /** Moves an entry one place by sending the whole new order. */
    const moveEntry = (entryId: string, direction: -1 | 1) => {
        const ids = queue.map((entry) => entry.id);
        const index = ids.indexOf(entryId);
        const target = index + direction;
        if (index < 0 || target < 0 || target >= ids.length) return;
        [ids[index], ids[target]] = [ids[target], ids[index]];
        void plan.editQueue({ order: ids });
    };

    /** Saves new exposures for one entry. */
    const saveExposures = (entryId: string, requests: ExposureRequest[]) =>
        plan.editQueue({ set_exposures: { [entryId]: requests } });

    /** Asks the Planetarium to center on the current target. */
    const showFramingOnSky = () => {
        void navigateToElement({
            targetDisplay: 'Planetarium',
            targetElement: 'sky',
            action: 'showOnSky',
            payload: { targetId: selectedTarget },
        });
    };

    let body: React.ReactNode;
    if (plan.plansLoading || plan.sessionLoading) body = <QualityPanelState state="loading" />;
    else if (plan.plansFailed) {
        body = <QualityPanelState state="error" subject="night plan list" onRetry={plan.reloadPlans} />;
    } else if (plan.sessionFailed) {
        body = <QualityPanelState state="error" subject="night plan" onRetry={plan.reloadSession} />;
    } else if (!plan.session) {
        body = (
            <QualityPanelState
                state="empty"
                emptyMessage="No night plan yet. Press Create tonight's plan to make one."
            />
        );
    } else {
        body = (
            <>
                <section className="plan-section" aria-label="Plan queue">
                    <h4>Queue</h4>
                    {queue.length === 0 ? (
                        <p className="quality-state">The queue is empty. Send a recommendation to the plan to add to it.</p>
                    ) : (
                        <ul className="plan-queue">
                            {queue.map((entry, index) => (
                                <PlanQueueEntry
                                    key={entry.id}
                                    entry={entry}
                                    isFirst={index === 0}
                                    isLast={index === queue.length - 1}
                                    busy={plan.busy}
                                    onMove={moveEntry}
                                    onRemove={(entryId, trigger) => setRemoval({ entryId, trigger })}
                                    onSaveExposures={saveExposures}
                                />
                            ))}
                        </ul>
                    )}
                </section>
                <PlanChecklist
                    items={plan.session.checklist ?? []}
                    targetId={selectedTarget}
                    busy={plan.busy}
                    onTick={(itemId) => void plan.tickItem(itemId)}
                />
                <PlanFraming framing={plan.session.framing} onShowOnSky={showFramingOnSky} />
            </>
        );
    }

    return (
        <SectionPanel title="Night plan" id="night-plan-panel" className="night-plan">
            <div className="night-plan__bar">
                <label>
                    Plan{' '}
                    <select
                        ref={selectReference}
                        className="dropdown"
                        value={plan.planId ?? ''}
                        onChange={(event) => plan.pickPlan(event.target.value)}
                        disabled={plan.plans.length === 0}
                    >
                        {plan.plans.length === 0 && <option value="">No plans</option>}
                        {plan.plans.map((summary) => (
                            <option key={summary.id} value={summary.id}>
                                {summary.nightDate}, {summary.entryCount} {summary.entryCount === 1 ? 'entry' : 'entries'} ({summary.status})
                            </option>
                        ))}
                    </select>
                </label>
                <button type="button" className="btn btn--tiny" disabled={plan.busy} onClick={() => void plan.createPlan()}>
                    Create tonight&apos;s plan
                </button>
                {plan.session && <span className="night-plan__revision">Revision {plan.session.revision ?? 0}</span>}
            </div>
            {plan.notice && (
                <div className="night-plan__notice" role="alert" data-kind={plan.notice.kind}>
                    <span>{plan.notice.text}</span>
                    <button type="button" className="btn btn--tiny" onClick={plan.dismissNotice}>
                        Dismiss
                    </button>
                </div>
            )}
            {body}
            <ConfirmDialog
                open={removal !== null}
                title="Remove from plan"
                message="Take this entry out of the night plan?"
                confirmLabel="Remove"
                onCancel={() => {
                    const trigger = removal?.trigger;
                    setRemoval(null);
                    window.setTimeout(() => trigger?.focus(), 0);
                }}
                onConfirm={() => {
                    const entryId = removal?.entryId;
                    setRemoval(null);
                    if (entryId) {
                        void plan.editQueue({ remove: [entryId] }).then(() => selectReference.current?.focus());
                    }
                }}
            />
        </SectionPanel>
    );
};
